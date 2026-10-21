"""Exercise real upload, cancellation, retry, restart and downloads inside Linux.

Run inside the verification container. It only listens on container loopback.
The caller supplies /data/service-input.pdf; all evidence stays in /data.
"""

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


def main():
    process = None
    with (ROOT / "service.log").open("a") as log:
        try:
            process = start(log)
            source = Path("/data/service-input.pdf").read_bytes()
            job = request(
                "/jobs",
                source,
                headers={
                    "Content-Type": "application/pdf",
                    "X-Filename": "Linux service check.pdf",
                },
            )
            ident = job["id"]
            until(
                ident,
                lambda j: j["state"] == "running" and j.get("stage") == "recognizing",
            )
            cancelled = request("/jobs/" + ident + "/cancel", b"")
            assert cancelled["state"] == "cancelled"
            request("/jobs/" + ident + "/retry", b"")
            until(
                ident,
                lambda j: j["state"] == "running" and j.get("stage") == "recognizing",
            )
            stop(process)
            process = None
            process = start(log)
            ready = until(ident, lambda j: j["state"] == "ready", timeout=1200)
            folder = ROOT / "storage" / ident
            verify_result(folder)
            original = request("/jobs/" + ident + "/files/source.pdf")
            assert original == source
            # Check exact response bytes rather than a decoded/re-encoded JSON object.
            with urllib.request.urlopen(
                BASE + "/jobs/" + ident + "/files/companion.json"
            ) as response:
                companion = response.read()
            assert (
                hashlib.sha256(companion).hexdigest()
                == ready["result"]["companionSHA256"]
            )
            score = request("/jobs/" + ident + "/files/score.json")
            assert score["ready"]
            for i in range(ready["pageCount"]):
                page = request("/jobs/" + ident + f"/files/page-{i + 1}.webp")
                assert page == (folder / f"web/score/page-{i + 1}.webp").read_bytes()
            result = dict(
                job=ident,
                ready=True,
                cancelledAndRetried=True,
                recoveredAfterRestart=True,
                downloadsVerified=True,
                notes=len(score["notes"]),
            )
            (ROOT / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result), flush=True)
        finally:
            stop(process)


if __name__ == "__main__":
    main()
