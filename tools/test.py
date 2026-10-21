#!/usr/bin/env python3
"""Run installed-package tests in the isolated test image."""

import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--private",
        action="store_true",
        help="Mount local tests/data excerpts read-only",
    )
    args = parser.parse_args()
    storage = ROOT / "artifacts/unit-tests"
    storage.mkdir(parents=True, exist_ok=True)
    command = [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cpus",
        "2",
        "--memory",
        "4g",
        "--pids-limit",
        "512",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev,size=512m",
        "--mount",
        f"type=bind,src={storage},dst=/data",
    ]
    if args.private:
        private = ROOT / "tests/data"
        if not private.is_dir():
            parser.error("Private fixtures are not present in tests/data")
        command += [
            "--mount",
            f"type=bind,src={private},dst=/opt/livesheet-tests/data,readonly",
        ]
    command += [
        "--entrypoint",
        "python",
        "bettermuse-livesheet-test:local",
        "-c",
        """
import json, unittest
from pathlib import Path
from livesheet.container import seed_cache
seed_cache()
suite = unittest.defaultTestLoader.discover('/opt/livesheet-tests')
result = unittest.TextTestRunner(verbosity=1).run(suite)
Path('/data/report.json').write_text(json.dumps(dict(
    run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
    skipped=len(result.skipped), passed=result.wasSuccessful()), indent=2))
raise SystemExit(not result.wasSuccessful())
""",
    ]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
