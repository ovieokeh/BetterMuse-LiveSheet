"""Exercise real upload, cancellation, retry, restart and downloads inside Linux.

Run inside the verification container. It only listens on container loopback.
The caller supplies /data/service-input.pdf; all evidence stays in /data.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

from livesheet.container import seed_cache
from livesheet.pdf_pipeline.artifacts import verify_result

ROOT = Path("/data/service-check")
BASE = "http://127.0.0.1:3196"
ROOT.mkdir(exist_ok=True)
seed_cache()


def request(path, data=None, method=None, headers=None):
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers=headers or {}
    )
    with urllib.request.urlopen(req, timeout=35) as response:
        raw = response.read()
        return (
            json.loads(raw)
            if "json" in response.headers.get("Content-Type", "")
            else raw
        )


def start(log):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "livesheet.server",
            "--port",
            "3196",
            "--storage",
            str(ROOT / "storage"),
            "--audiveris",
            os.environ["PDF_AUDIVERIS"],
            "--musescore",
            os.environ["PDF_MUSESCORE"],
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Service failed to start")
        try:
            request("/health")
            return process
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("Service startup")


def until(ident, predicate, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request("/jobs/" + ident)
        if predicate(job):
            return job
        if job["state"] == "failed":
            raise RuntimeError(job.get("error"))
        time.sleep(0.3)
    raise TimeoutError("Job did not reach expected state")


def stop(process):
    if process and process.poll() is None:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)


def active_groups():
    groups = set()
    for entry in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = entry.read_bytes().split(b"\0")
            if b"livesheet.job_runner" in args and str(ROOT).encode() in b" ".join(args):
                groups.add(int(entry.parent.name))
        except (OSError, ValueError):
            pass
    return groups


def descendant_groups(roots):
    parents, groups = {}, {}
    for entry in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = entry.read_text().rsplit(')', 1)[1].split()
            pid = int(entry.parent.name)
            parents[pid], groups[pid] = int(fields[1]), int(fields[2])
        except (OSError, ValueError, IndexError):
            pass
    owned = set(roots)
    while True:
        expanded = owned | {pid for pid, parent in parents.items() if parent in owned}
        if expanded == owned:
            return {groups[pid] for pid in owned if pid in groups}
        owned = expanded


def group_alive(group):
    for entry in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = entry.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group and fields[0] != "Z":
                return True
        except (OSError, ValueError, IndexError):
            pass
    return False


def wait_stopped(groups):
    deadline = time.monotonic() + 10
    while any(group_alive(g) for g in groups):
        if time.monotonic() > deadline:
            raise AssertionError("Orphan conversion survived service shutdown")
        time.sleep(.1)
    with (ROOT / "storage/server.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def recognizing(ident):
    return until(ident, lambda j: j["state"] == "running" and j.get("stage") == "recognizing")


def main():
    process = None
    with (ROOT / "service.log").open("a") as log:
        try:
            process = start(log)
            assert request("/health")["workerCount"] == 2
            source = Path("/data/service-input.pdf").read_bytes()
            def upload():
                return request("/jobs", source, headers={
                    "Content-Type": "application/pdf", "X-Filename": "Linux service check.pdf"})["id"]
            first, second = upload(), upload()
            recognizing(first)
            peer = recognizing(second)
            assert len(active_groups()) == 2
            queued = upload()
            assert request("/jobs/" + queued)["state"] == "queued"
            assert request("/jobs/" + queued + "/cancel", b"")["state"] == "cancelled"
            assert request("/jobs/" + first + "/cancel", b"")["state"] == "cancelled"
            unchanged = request("/jobs/" + second)
            assert unchanged["state"] == "running" and unchanged["startedAt"] == peer["startedAt"]
            request("/jobs/" + first + "/retry", b"")
            recognizing(first)
            groups = active_groups()
            assert len(groups) == 2
            groups = descendant_groups(groups)
            stop(process)
            process = None
            wait_stopped(groups)
            process = start(log)
            recognizing(first)
            recognizing(second)
            groups = active_groups()
            assert len(groups) == 2
            # Also test an abrupt server-only crash. Supervisors must kill tools
            # and retain the store lock until the old attempts stop.
            groups = descendant_groups(groups)
            process.kill()
            process.wait(timeout=10)
            process = None
            wait_stopped(groups)
            process = start(log)
            verified = []
            for ident in (first, second):
                ready = until(ident, lambda j: j["state"] == "ready", timeout=1200)
                folder = ROOT / "storage" / ident
                verify_result(folder)
                assert request("/jobs/" + ident + "/files/source.pdf") == source
                with urllib.request.urlopen(BASE + "/jobs/" + ident + "/files/companion.json") as response:
                    companion = response.read()
                assert hashlib.sha256(companion).hexdigest() == ready["result"]["companionSHA256"]
                score = request("/jobs/" + ident + "/files/score.json")
                assert score["ready"]
                for i in range(ready["pageCount"]):
                    page = request("/jobs/" + ident + f"/files/page-{i + 1}.webp")
                    assert page == (folder / f"web/score/page-{i + 1}.webp").read_bytes()
                verified.append(dict(job=ident, ready=True, notes=len(score["notes"])))
            assert request("/jobs/" + queued)["state"] == "cancelled"
            result = dict(jobs=verified, workerCount=2, queuedThird=True,
                          cancelledAndRetried=True, peerUnaffected=True,
                          recoveredAfterRestart=True, recoveredAfterCrash=True,
                          noOrphanTools=True, downloadsVerified=True)
            (ROOT / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result), flush=True)
        finally:
            groups = descendant_groups(active_groups())
            stop(process)
            wait_stopped(groups)


if __name__ == "__main__":
    main()
