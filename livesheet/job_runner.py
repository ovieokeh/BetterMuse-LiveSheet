"""Supervise one conversion, including tools that start their own process group."""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from livesheet.container import seed_cache


def descendants():
    """Return only this supervisor's descendants, never another job's tools."""
    if sys.platform == 'linux':
        parents = {}
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rsplit(')', 1)[1].split()
                parents[int(path.parent.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                pass
    else:
        listing = subprocess.Popen(['ps', '-axo', 'pid=,ppid='], stdout=subprocess.PIPE, text=True)
        rows, _ = listing.communicate()
        parents = dict(tuple(map(int, row.split())) for row in rows.splitlines())
        parents.pop(listing.pid, None)
    owned = {os.getpid()}
    while True:
        children = {pid for pid, parent in parents.items() if parent in owned}
        expanded = owned | children
        if expanded == owned:
            return owned - {os.getpid()}
        owned = expanded


def reap_tools():
    # Linux reparents orphan grandchildren to us, including Audiveris's session.
    # Kill/reap repeatedly to cover descendants created during termination.
    while True:
        children = descendants()
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if not pid:
                    break
            except ChildProcessError:
                break
        if not children:
            return
        time.sleep(.02)


def main():
    parent = int(sys.argv[1])
    if os.getpgrp() != os.getpid():
        raise RuntimeError('PDF runner requires its own process group')
    if sys.platform == 'linux':
        # Keep orphan tools owned by this job if their immediate parent exits.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), 'Cannot supervise PDF descendants')
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if stopping or os.getppid() != parent:
            return 130
        if Path('/opt/livesheet-cache/export-fix').is_dir():
            seed_cache()
        if stopping or os.getppid() != parent:
            return 130
        process = subprocess.Popen([sys.executable, '-m', 'livesheet.worker', *sys.argv[2:]])
        while not stopping and os.getppid() == parent:
            try:
                return process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                pass
        return 130
    finally:
        reap_tools()


if __name__ == '__main__':
    raise SystemExit(main())
