#!/usr/bin/env python3
"""Compare fresh, validated conversions at bounded memory limits. Local only."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import verify

# Read the kernel's high-water mark before the container exits. This includes
# all child processes and charged file cache, unlike per-process RSS sampling.
PROBE = r'''
import json, subprocess, sys, time
from pathlib import Path
root = Path('/sys/fs/cgroup')
def read(name):
    return (root / name).read_text().strip()
def counters(name):
    return dict((k, int(v)) for k, v in (line.split() for line in read(name).splitlines()))
job = Path(sys.argv[sys.argv.index('--job-dir') + 1])
assert (root / 'memory.peak').exists(), 'cgroup v2 memory.peak required'
before = counters('cpu.stat')
started = time.monotonic()
status = subprocess.call(['python', '-m', 'livesheet.container', *sys.argv[1:]])
report = dict(seconds=round(time.monotonic()-started, 3), exitCode=status,
    peakMemoryBytes=int(read('memory.peak')), memoryLimitBytes=int(read('memory.max')),
    swapLimitBytes=int(read('memory.swap.max')), swapCurrentBytes=int(read('memory.swap.current')),
    memoryEvents=counters('memory.events'), cpuLimit=read('cpu.max'),
    cpuUsageSeconds=(counters('cpu.stat')['usage_usec']-before['usage_usec'])/1e6)
(job / 'resources.json').write_text(json.dumps(report, indent=2)+'\n')
sys.exit(status)
'''


def factory(memory):
    def command(image, storage):
        args = verify.container(image, storage)
        args[args.index('--memory') + 1] = memory
        # Equal memory and memory-swap disables swap. No image rebuild needed.
        return args[:-1] + ['--memory-swap', memory, '--entrypoint', 'python',
                            image, '-c', PROBE]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--private', type=Path, default=verify.ROOT / '.private/reviewed')
    parser.add_argument('--ids', nargs='+', default=['bella', 'peaceful-sleep', 'cuddy'])
    parser.add_argument('--limits', nargs='+', choices=['2g', '3g', '4g'], default=['3g', '4g'])
    parser.add_argument('--repeats', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--image', default='bettermuse-livesheet:local')
    args = parser.parse_args()
    out = args.output.resolve()
    # Never silently turn a benchmark into a cached-result validation.
    out.mkdir(parents=True, exist_ok=False)
    info = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}']))
    report = dict(complete=False, cpus=2, swapEnabled=False,
                  host={k: info.get(k) for k in ('Architecture', 'NCPU', 'MemTotal', 'OperatingSystem')},
                  rows=[])
    with verify.pinned_image(args.image) as image:
        report['image'] = image
        for repeat in range(args.repeats):
            # Reverse the order on alternate repeats to reduce order bias.
            limits = args.limits if repeat % 2 == 0 else list(reversed(args.limits))
            for memory in limits:
                for ident in args.ids:
                    if shutil.disk_usage(out).free < 10 * 1024**3:
                        raise RuntimeError('Stopped to preserve the 10 GiB storage floor')
                    run = out / f'{repeat+1}-{memory}-{ident}'
                    print(f'Run {repeat+1}: {ident}, {memory}, 2 CPUs, no swap', flush=True)
                    config = SimpleNamespace(output=run, private=args.private, ids=[ident])
                    verify.verify(config, image, factory(memory))
                    result = verify.load(run / 'report.json')['rows'][0]
                    job = run / 'jobs' / ident
                    resources = job / 'resources.json'
                    row = dict(id=ident, repeat=repeat+1, memory=memory,
                               passed=result['passed'], reused=result['reused'])
                    if resources.exists():
                        row.update(verify.load(resources))
                    else:
                        row['measurementError'] = 'Container exited without resource report; inspect log for OOM or other failure'
                    if (job / 'result.json').exists():
                        row['pages'] = verify.load(job / 'result.json')['pageCount']
                    if result.get('error'):
                        row['validationError'] = result['error']
                    report['rows'].append(row)
                    verify.save(out / 'report.json', report)
                    print(json.dumps(row), flush=True)
    report['complete'] = True
    report['passed'] = all(r['passed'] is True and not r['reused']
                           and not r.get('measurementError')
                           and r.get('exitCode') == 0
                           and r.get('memoryEvents', {}).get('oom', 0) == 0
                           for r in report['rows'])
    verify.save(out / 'report.json', report)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
