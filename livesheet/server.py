#!/usr/bin/env python3
"""Local PDF jobs with an opt-in private device listener. No public hosting."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlparse
import uuid

from livesheet.pdf_preflight import MAX_BYTES
from livesheet.pdf_pipeline.artifacts import verify_result
from livesheet.recognition import atomic_json, AUDIVERIS
from livesheet.playback import MSCORE

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"ready", "failed", "cancelled"}
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
FREE_FLOOR = 10 * 1024**3
JOB_LIMIT = 2 * 1024**3
STORE_LIMIT = 8 * 1024**3
WATCH_INTERVAL = 5
REQUEST_SECONDS = 60


def disk_bytes(folder):
    size = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                size += path.stat().st_size
        except FileNotFoundError:
            pass  # Atomic worker output may be replaced during the scan.
    return size


def tail(path, limit):
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read(limit)


class BoundedHTTPServer(ThreadingHTTPServer):
    """Bound slow clients and concurrent request memory, even on loopback."""

    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def preflight(folder):
    report = folder / "inspection.json"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m", "livesheet.pdf_preflight",
                str(folder / "source.pdf"),
                "--preview",
                str(folder / "preview.webp"),
                "--result",
                str(report),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("pdf_check_timeout") from None
    try:
        if report.stat().st_size > 4096:
            raise ValueError()
        data = json.loads(report.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        raise ValueError("pdf_locked_or_invalid") from None
    if result.returncode or "error" in data:
        raise ValueError(data.get("error", "pdf_locked_or_invalid"))
    return data["pageCount"]


def now():
    return datetime.now(timezone.utc).isoformat()


def terminate(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        pass


class Jobs:
    def __init__(self, root, audiveris=AUDIVERIS, musescore=MSCORE, start=True):
        self.root, self.audiveris, self.musescore = root.resolve(), audiveris, musescore
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.file_lock = (self.root / "server.lock").open("w")
        fcntl.flock(self.file_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.process = None
        self.active = None
        self.submitting = threading.BoundedSemaphore(1)
        for folder in self.root.glob("incoming-*"):
            shutil.rmtree(folder)
        for job in self.list():
            if job["state"] == "running":
                self.update(job["id"], state="queued")
            elif (
                job["state"] == "ready"
                and (self.root / job["id"] / "completion.json").exists()
            ):
                try:
                    verify_result(self.root / job["id"])
                except (OSError, ValueError, KeyError, TypeError):
                    self.update(
                        job["id"], state="failed", error="invalid_preparation_result"
                    )
        self.cleanup()
        self.thread = threading.Thread(target=self.work, daemon=True)
        if start:
            self.thread.start()

    def read(self, ident):
        if not JOB_ID.fullmatch(ident):
            raise FileNotFoundError()
        job = json.loads((self.root / ident / "job.json").read_text())
        if (
            not isinstance(job, dict)
            or job.get("id") != ident
            or job.get("state") not in TERMINAL | {"queued", "running"}
        ):
            raise ValueError("invalid_job_metadata")
        for key in ("createdAt", "updatedAt"):
            if datetime.fromisoformat(job[key]).tzinfo is None:
                raise ValueError("invalid_job_timestamp")
        return job

    def update(self, ident, **fields):
        job = self.read(ident)
        job.update(fields, updatedAt=now())
        atomic_json(self.root / ident / "job.json", job)
        return job

    def list(self):
        with self.lock:
            jobs = []
            for folder in self.root.iterdir():
                if (
                    not JOB_ID.fullmatch(folder.name)
                    or not (folder / "job.json").exists()
                ):
                    continue
                try:
                    jobs.append(self.read(folder.name))
                except FileNotFoundError:
                    continue
                except (ValueError, KeyError, TypeError, UnicodeError):
                    # Preserve source/evidence privately. Unknown ownership must
                    # never become an anonymous web job. Other jobs can proceed.
                    if self.active == folder.name:
                        terminate(self.process)
                    quarantine = self.root / (
                        "quarantine-" + folder.name + "-" + uuid.uuid4().hex
                    )
                    folder.rename(quarantine)
                    os.utime(quarantine, None)
                    logging.warning(
                        "Quarantined unreadable PDF job metadata: %s", folder.name
                    )
            return sorted(jobs, key=lambda job: job["createdAt"])

    def public(self, job):
        job = dict(job)
        for key in ("owner", "idempotencyKey"):
            job.pop(key, None)
        folder = self.root / job["id"]
        job["previewAvailable"] = (folder / "preview.webp").is_file()
        stage = folder / "stage.json"
        if job["state"] == "running" and stage.exists():
            job.update(json.loads(stage.read_text()))
        if job["state"] in ("queued", "running"):
            start = (
                job.get("startedAt") if job["state"] == "running" else job["createdAt"]
            )
            job["elapsedSeconds"] = max(
                0,
                int(
                    time.time()
                    - datetime.fromisoformat(start or job["updatedAt"]).timestamp()
                ),
            )
        failure = folder / "failure.json"
        if job["state"] == "failed" and failure.exists():
            # Expose a stable, user-safe reason, never the diagnostic traceback.
            if json.loads(failure.read_text()).get("error") == "incomplete-playback":
                job["error"] = "incomplete_playback"
        result = folder / "result.json"
        if job["state"] == "ready" and result.exists():
            job["result"] = json.loads(result.read_text())
        return job

    def check_capacity(self):
        if sum(j["state"] not in TERMINAL for j in self.list()) >= 10:
            raise ValueError("queue_full")
        if (
            shutil.disk_usage(self.root).free < FREE_FLOOR
            or disk_bytes(self.root) >= STORE_LIMIT
        ):
            raise ValueError("storage_low")

    def submit(self, data, filename, owner=None, key=None, profile="standard"):
        if profile not in {"standard", "experimental-v1"}:
            raise ValueError("invalid_preparation_profile")
        if not self.submitting.acquire(blocking=False):
            raise ValueError("upload_busy")
        try:
            return self._submit(data, filename, owner, key, profile)
        finally:
            self.submitting.release()

    def _submit(self, data, filename, owner, key, profile):
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            if key:
                for existing in self.list():
                    if (
                        existing.get("owner") == owner
                        and existing.get("idempotencyKey") == key
                    ):
                        if (
                            existing.get("sourceSHA256") != digest
                            or existing.get("profile", "standard") != profile
                        ):
                            raise ValueError("idempotency_conflict")
                        return self.public(existing)
            self.check_capacity()
            ident = uuid.uuid4().hex
            folder = self.root / f"incoming-{ident}"
            folder.mkdir(mode=0o700)
        try:
            (folder / "source.pdf").write_bytes(data)
            (folder / "worker.log").write_text("")
            pages = preflight(folder)
            with self.lock:
                self.check_capacity()
                job = dict(
                    id=ident,
                    filename=filename[:180],
                    state="queued",
                    pageCount=pages,
                    createdAt=now(),
                    updatedAt=now(),
                    owner=owner,
                    idempotencyKey=key,
                    sourceSHA256=digest,
                    profile=profile,
                )
                atomic_json(folder / "job.json", job)
                folder.rename(self.root / ident)
                return self.public(job)
        finally:
            if folder.exists():
                shutil.rmtree(folder)

    def action(self, ident, action):
        with self.lock:
            job = self.read(ident)
            if action == "retry":
                if job["state"] not in {"failed", "cancelled"}:
                    raise ValueError("job_not_retryable")
                self.check_capacity()
                (self.root / ident / "stage.json").unlink(missing_ok=True)
                (self.root / ident / "result.json").unlink(missing_ok=True)
                (self.root / ident / "completion.json").unlink(missing_ok=True)
                (self.root / ident / "failure.json").unlink(missing_ok=True)
                return self.update(ident, state="queued", error=None)
            if self.active == ident:
                terminate(self.process)
            if action == "delete":
                shutil.rmtree(self.root / ident)
                return dict(deleted=True)
            if job["state"] in TERMINAL:
                return job
            return self.update(ident, state="cancelled")

    def cleanup(self):
        with self.lock:
            for folder in self.root.glob("quarantine-*"):
                if folder.is_dir() and time.time() - folder.stat().st_mtime > 7 * 86400:
                    shutil.rmtree(folder)
            for job in self.list():
                age = time.time() - datetime.fromisoformat(job["updatedAt"]).timestamp()
                if job["state"] in TERMINAL and age > 7 * 86400:
                    shutil.rmtree(self.root / job["id"])

    def work(self):
        last_cleanup = 0
        while not self.closed.wait(0.5):
            if time.monotonic() - last_cleanup > 3600:
                self.cleanup()
                last_cleanup = time.monotonic()
            with self.lock:
                job = next((j for j in self.list() if j["state"] == "queued"), None)
                if not job:
                    continue
                ident = job["id"]
                folder = self.root / ident
                (folder / "completion.json").unlink(missing_ok=True)
                (folder / "failure.json").unlink(missing_ok=True)
                self.update(ident, state="running", error=None, startedAt=now())
                self.active = ident
                log = (folder / "worker.log").open("a")
                try:
                    self.process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m", "livesheet.worker",
                            "--job-dir",
                            str(folder),
                            "--profile",
                            job.get("profile", "standard"),
                            "--audiveris",
                            str(self.audiveris),
                            "--musescore",
                            self.musescore,
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except Exception:
                    log.close()
                    self.active = None
                    self.update(ident, state="failed", error="worker_unavailable")
                    continue
            failure = None
            deadline = time.monotonic() + 2400
            try:
                while True:
                    try:
                        code = self.process.wait(timeout=WATCH_INTERVAL)
                        break
                    except subprocess.TimeoutExpired:
                        with self.lock:
                            if (
                                not folder.exists()
                                or self.read(ident)["state"] != "running"
                            ):
                                continue
                            if time.monotonic() > deadline:
                                failure = "preparation_timeout"
                            elif shutil.disk_usage(self.root).free < FREE_FLOOR:
                                failure = "storage_low"
                            elif disk_bytes(folder) > JOB_LIMIT:
                                failure = "job_storage_limit"
                            if failure:
                                terminate(self.process)
                                code = 1
                                break
            finally:
                log.close()
            with self.lock:
                self.active = None
                self.process = None
                if self.closed.is_set():
                    return  # Restart will requeue interrupted work.
                if not folder.exists() or self.read(ident)["state"] != "running":
                    continue
                if code == 0:
                    try:
                        verify_result(folder)
                    except (OSError, ValueError, KeyError, TypeError):
                        failure = "invalid_preparation_result"
                if code == 0 and failure is None:
                    self.update(ident, state="ready")
                else:
                    self.update(
                        ident, state="failed", error=failure or "preparation_failed"
                    )

    def close(self):
        self.closed.set()
        terminate(self.process)
        if self.thread.is_alive():
            self.thread.join(timeout=10)
        self.file_lock.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "BetterMuseLocalPDF/1"

    def setup(self):
        self.request.settimeout(30)
        super().setup()

        def expire():
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        self.deadline = threading.Timer(REQUEST_SECONDS, expire)
        self.deadline.daemon = True
        self.deadline.start()

    def finish(self):
        try:
            super().finish()
        finally:
            self.deadline.cancel()

    def send(self, status, value, content_type="application/json"):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self):
        try:
            host = urlparse("http://" + self.headers.get("Host", ""))
            origin = self.headers.get("Origin")
            token = getattr(self.server, "native_token", None)
            if token:
                return (
                    len(self.headers.get_all("Host", [])) == 1
                    and host.netloc in self.server.native_hosts
                    and not origin
                    and not self.headers.get("Sec-Fetch-Site")
                    and hmac.compare_digest(
                        self.headers.get("Authorization", ""), "Bearer " + token
                    )
                )
            return (
                len(self.headers.get_all("Host", [])) == 1
                and host.hostname in {"localhost", "127.0.0.1", "::1"}
                and not host.username
                and self.headers.get("Sec-Fetch-Site") != "cross-site"
                and (not origin or origin == "http://" + host.netloc)
            )
        except ValueError:
            return False

    def dispatch(self):
        if not self.allowed():
            return self.send(403, dict(error="local_only"))
        store = self.server.jobs
        token = getattr(self.server, "native_token", None)
        self.owner = hashlib.sha256(token.encode()).hexdigest() if token else None
        parts = urlparse(self.path).path.strip("/").split("/")
        try:
            if self.command == "GET" and parts == ["health"]:
                return self.send(
                    200,
                    dict(
                        local=True,
                        worker=store.thread.is_alive(),
                        toolsAvailable=store.audiveris.is_file()
                        and Path(store.musescore).is_file(),
                        maxBytes=MAX_BYTES,
                        maxPages=20,
                        retentionDays=7,
                        preparationProfiles=["standard", "experimental-v1"],
                    ),
                )
            if parts == ["jobs"]:
                if self.command == "GET":
                    return self.send(
                        200,
                        [
                            store.public(j)
                            for j in reversed(store.list())
                            if j.get("owner") == self.owner
                        ],
                    )
                if self.command == "POST":
                    if (
                        self.headers.get("Content-Type", "").split(";")[0]
                        != "application/pdf"
                    ):
                        return self.send(415, dict(error="pdf_required"))
                    if (
                        self.headers.get("Transfer-Encoding")
                        or len(self.headers.get_all("Content-Length", [])) != 1
                    ):
                        return self.send(400, dict(error="invalid_upload_length"))
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_BYTES:
                        return self.send(413, dict(error="pdf_size_limit"))
                    self.connection.settimeout(30)
                    data = self.rfile.read(size)
                    if len(data) != size:
                        return self.send(400, dict(error="incomplete_upload"))
                    key = self.headers.get("Idempotency-Key")
                    if self.owner and (
                        not key or not re.fullmatch(r"[a-f0-9-]{36}", key)
                    ):
                        return self.send(400, dict(error="idempotency_key_required"))
                    return self.send(
                        202,
                        store.submit(
                            data,
                            unquote(self.headers.get("X-Filename", "Score.pdf")),
                            self.owner,
                            key,
                            self.headers.get("X-Preparation-Profile", "standard"),
                        ),
                    )
            if len(parts) >= 2 and parts[0] == "jobs" and JOB_ID.fullmatch(parts[1]):
                with store.lock:
                    value, mime = self.job_response(store, parts, parts[1])
                return self.send(200, value, mime)
            return self.send(404, dict(error="not_found"))
        except FileNotFoundError:
            return self.send(404, dict(error="not_found"))
        except ValueError as error:
            reason = str(error)
            status = (
                429
                if reason in {"queue_full", "upload_busy"}
                else 507 if reason == "storage_low" else 400
            )
            return self.send(status, dict(error=reason))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            import traceback

            traceback.print_exc()
            return self.send(500, dict(error="request_failed"))

    def job_response(self, store, parts, ident):
        job = store.read(ident)
        if job.get("owner") != self.owner:
            raise FileNotFoundError()
        if len(parts) == 2:
            if self.command == "GET":
                return store.public(job), "application/json"
            if self.command == "DELETE":
                return store.action(ident, "delete"), "application/json"
        if (
            len(parts) == 3
            and self.command == "POST"
            and parts[2] in {"cancel", "retry"}
        ):
            return store.public(store.action(ident, parts[2])), "application/json"
        if len(parts) == 4 and parts[2] == "files" and self.command == "GET":
            name = parts[3]
            folder = store.root / ident
            files = {
                "source.pdf": (folder / "source.pdf", "application/pdf"),
                "worker.log": (folder / "worker.log", "text/plain; charset=utf-8"),
                "preview.webp": (folder / "preview.webp", "image/webp"),
            }
            if job["state"] == "ready":
                files.update(
                    {
                        "score.json": (folder / "score.json", "application/json"),
                        "companion.json": (
                            folder / "companion.json",
                            "application/json",
                        ),
                    }
                )
                for extra in (
                    "baseline-score.json",
                    "baseline-companion.json",
                    "quality.json",
                ):
                    if (folder / extra).is_file():
                        files[extra] = (folder / extra, "application/json")
                if re.fullmatch(r"page-[1-9][0-9]*\.webp", name):
                    files[name] = (folder / "web/score" / name, "image/webp")
            if name not in files:
                raise FileNotFoundError()
            path, mime = files[name]
            raw = tail(path, 65536) if name == "worker.log" else path.read_bytes()
            if name == "worker.log":
                logs = sorted((folder / "recognition").rglob("run.log"))
                raw = raw[-65536:] + b"".join(
                    b"\n--- "
                    + str(p.relative_to(folder)).encode()
                    + b" ---\n"
                    + tail(p, 32768)
                    for p in logs[-4:]
                )
            return raw, mime
        raise FileNotFoundError()

    do_GET = dispatch
    do_POST = dispatch
    do_DELETE = dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3176)
    parser.add_argument("--bind", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1", help="Use 0.0.0.0 only inside a container with a loopback-only host port")
    parser.add_argument(
        "--storage", type=Path, default=Path(os.environ.get("LIVESHEET_STORAGE", "data/jobs"))
    )
    parser.add_argument("--audiveris", type=Path, default=AUDIVERIS)
    parser.add_argument("--musescore", default=MSCORE)
    parser.add_argument(
        "--native-host", help="Opt-in private LAN IPv4 address; never 0.0.0.0"
    )
    parser.add_argument("--native-port", type=int, default=3177)
    parser.add_argument(
        "--native-name", help="Optional local mDNS hostname, e.g. phoenix.local"
    )
    parser.add_argument("--native-token-file", type=Path)
    args = parser.parse_args()
    token = None
    if args.native_host:
        address = ipaddress.ip_address(args.native_host)
        if (
            address.version != 4
            or not address.is_private
            or address.is_unspecified
            or address.is_loopback
        ):
            parser.error("native-host must be a private LAN IPv4 address")
        if not args.native_token_file:
            parser.error("native-token-file is required")
        if args.native_token_file.stat().st_mode & 0o077:
            parser.error("token file must be private (0600)")
        token = args.native_token_file.read_text().strip()
        if not re.fullmatch(r"[a-f0-9]{64}", token):
            parser.error("token must be 32 random bytes encoded as hex")
        if args.native_name and not re.fullmatch(
            r"[a-zA-Z0-9-]+\.local", args.native_name
        ):
            parser.error("native-name must be a local mDNS hostname")
    jobs = Jobs(args.storage, args.audiveris, args.musescore)
    os.umask(0o077)
    server = BoundedHTTPServer((args.bind, args.port), Handler)
    server.jobs = jobs
    native = None
    if token:
        native = BoundedHTTPServer((args.native_host, args.native_port), Handler)
        native.jobs, native.native_token = jobs, token
        native.native_hosts = {f"{args.native_host}:{args.native_port}"}
        if args.native_name:
            native.native_hosts.add(f"{args.native_name}:{args.native_port}")
        threading.Thread(target=native.serve_forever, daemon=True).start()
        print(
            f"Private device preview: http://{args.native_name or args.native_host}:{args.native_port}",
            flush=True,
        )
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print(f"Local PDF preparation: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        jobs.close()
        server.server_close()
        if native:
            native.shutdown()
            native.server_close()


if __name__ == "__main__":
    main()
