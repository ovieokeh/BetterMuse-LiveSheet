"""Initialize disposable compiler caches, then exec the service as PID 1."""
import fcntl
import os
from pathlib import Path
import shutil
import sys


def seed_cache():
    source = Path("/opt/livesheet-cache/export-fix")
    target = Path(os.environ["PDF_EXPORT_CACHE"])
    target.mkdir(parents=True, exist_ok=True)
    # Class manifests are checked by the existing overlay builder before use.
    # Seed only absent files. Never touch job storage or replace user results.
    with (target / ".seed.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for item in source.rglob("*"):
            destination = target / item.relative_to(source)
            if item.is_file() and not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".seed-partial")
                shutil.copy2(item, temporary)
                temporary.replace(destination)


def main():
    seed_cache()
    args = sys.argv[1:]
    command = "livesheet-prepare" if args and args[0] == "worker" else "livesheet-serve"
    if args and args[0] in ("worker", "serve"):
        args = args[1:]
    os.execvp(command, [command, *args])


if __name__ == "__main__":
    main()
