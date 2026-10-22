"""Small read-only service check for a Coolify scheduled task.

Outputs aggregate counts only. Exit 1 lets Coolify's existing scheduled-task
failure notifications report a problem. It does not restart or mutate jobs.
"""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
import time
from urllib.request import urlopen


def inspect(root, *, healthy, free_bytes, at=None):
    at = time.time() if at is None else at
    problems = [] if healthy else ['service_unhealthy']
    counts = Counter()
    oldest_queue = 0
    recent_failures = 0
    for path in root.glob('*/job.json'):
        try:
            job = json.loads(path.read_text())
            age = at - datetime.fromisoformat(job['updatedAt']).timestamp()
            counts[job['state']] += 1
            if job['state'] == 'queued':
                oldest_queue = max(oldest_queue, age)
            if job['state'] == 'running' and at - datetime.fromisoformat(job['startedAt']).timestamp() > 2700:
                problems.append('job_exceeded_deadline')
            if job['state'] == 'failed' and age < 900:
                recent_failures += 1
        except FileNotFoundError:
            continue  # A user may delete their job during this read.
        except (ValueError, KeyError, TypeError):
            problems.append('invalid_job_metadata')
    if free_bytes < 15 * 1024**3:
        problems.append('disk_pressure')
    if oldest_queue > 600:
        problems.append('queue_delayed')
    if recent_failures >= 3:
        problems.append('repeated_preparation_failures')
    return dict(healthy=not problems, problems=sorted(set(problems)),
                jobs=dict(counts), oldestQueuedSeconds=max(0, int(oldest_queue)),
                recentFailures=recent_failures, freeBytes=free_bytes)


def main():
    import os
    root = Path(os.environ.get('LIVESHEET_STORAGE', '/data/jobs'))
    try:
        with urlopen('http://127.0.0.1:3176/health', timeout=5) as response:
            healthy = json.load(response).get('healthy') is True
    except Exception:
        healthy = False
    report = inspect(root, healthy=healthy, free_bytes=shutil.disk_usage(root).free)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['healthy'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
