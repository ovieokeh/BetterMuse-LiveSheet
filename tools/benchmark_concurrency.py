#!/usr/bin/env python3
"""Measure fresh serial/parallel conversions under a shared container budget."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import uuid

import verify

# One container enforces the aggregate budget. Each worker is a separate process
# with private job storage, compiler cache and desktop-tool settings.
PROBE = r'''
from concurrent.futures import ThreadPoolExecutor, as_completed
import json, os, subprocess, sys, time
from pathlib import Path
root = Path('/sys/fs/cgroup')
def read(name): return (root/name).read_text().strip()
def counters(name):
    return dict((k,int(v)) for k,v in (line.split() for line in read(name).splitlines()))
assert (root/'memory.peak').exists(), 'cgroup v2 memory.peak required'
request=json.loads(Path('/data/batch-input.json').read_text())
before=counters('cpu.stat')
started=time.monotonic()
def run(ident):
    job=Path('/data')/ident
    temporary=Path('/tmp')/ident
    home=temporary/'home'
    home.mkdir(parents=True)
    env={**os.environ, 'HOME':str(home), 'TMPDIR':str(temporary),
         'XDG_CONFIG_HOME':str(home/'.config'), 'XDG_CACHE_HOME':str(home/'.cache'),
         'PDF_EXPORT_CACHE':str(job/'compiler-cache')}
    began=time.monotonic()
    with (job/'container.log').open('w') as log:
        result=subprocess.run(['python','-m','livesheet.container','worker','--job-dir',str(job)],
                              env=env,stdout=log,stderr=subprocess.STDOUT)
    ended=time.monotonic()
    row=dict(id=ident,exitCode=result.returncode,
             waitSeconds=round(began-started,3), processingSeconds=round(ended-began,3),
             completionSeconds=round(ended-started,3))
    (job/'timing.json').write_text(json.dumps(row,indent=2)+'\n')
    print(json.dumps(row),flush=True)
    return row
rows=[]
with ThreadPoolExecutor(max_workers=request['workers']) as pool:
    futures=[pool.submit(run,ident) for ident in request['ids']]
    for future in as_completed(futures): rows.append(future.result())
elapsed=time.monotonic()-started
report=dict(workers=request['workers'],seconds=round(elapsed,3),rows=rows,
    peakMemoryBytes=int(read('memory.peak')),memoryLimitBytes=int(read('memory.max')),
    swapLimitBytes=int(read('memory.swap.max')),swapCurrentBytes=int(read('memory.swap.current')),
    memoryEvents=counters('memory.events'),cpuLimit=read('cpu.max'),
    cpuStatDelta={k:v-before.get(k,0) for k,v in counters('cpu.stat').items()})
Path('/data/resources.json').write_text(json.dumps(report,indent=2)+'\n')
sys.exit(0 if all(r['exitCode']==0 for r in rows) else 1)
'''


def no_rerun(*_):
    raise RuntimeError('Benchmark output is missing or invalid; refusing a hidden rerun')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--private', type=Path, default=verify.ROOT / '.private/reviewed')
    p.add_argument('--image', default='bettermuse-livesheet:local')
    p.add_argument('--config', nargs=3, action='append', metavar=('CPUS','MEMORY','WORKERS'),
                   help='Repeat to select budgets, e.g. --config 2 3g 2')
    p.add_argument('--rounds', type=int, choices=(1,2), default=2)
    args = p.parse_args()
    try:
        configurations = ([(int(c),m,int(w)) for c,m,w in args.config] if args.config else
                          [(2,'3g',1),(2,'3g',2),(4,'6g',1),(4,'6g',2)])
    except ValueError:
        p.error('CPUS and WORKERS must be integers')
    if (len(set(configurations)) != len(configurations) or
        any(c not in (2,4) or m not in ('3g','6g') or w not in (1,2)
            for c,m,w in configurations)):
        p.error('Use unique configurations: 2 or 4 CPUs, 3g or 6g memory, 1 or 2 workers')
    entries = verify.load(args.private / 'manifest.json')['entries']
    ids = ['bella', 'peaceful-sleep', 'cuddy']
    for ident in ids:
        entry = next(e for e in entries if e['id'] == ident)
        for name, digest in entry['hashes'].items():
            if verify.sha(args.private / ident / name) != digest:
                raise ValueError('Reference changed: ' + ident + '/' + name)
    out = args.output.resolve()
    info = json.loads(subprocess.check_output(['docker','info','--format','{{json .}}']))
    if any(c > info['NCPU'] or int(m[0])*1024**3 >= info['MemTotal'] for c,m,w in configurations):
        p.error('Selected budget exceeds Docker host capacity; select smaller --config budgets')
    out.mkdir(parents=True, exist_ok=False)
    report = dict(complete=False, sharedBudget=True, swapEnabled=False, batches=[],
                  host={k:info.get(k) for k in ('Architecture','NCPU','MemTotal','OperatingSystem')})
    with verify.pinned_image(args.image) as image:
        report['image'] = image
        for repeat in range(1,args.rounds+1):
            order = ids if repeat == 1 else list(reversed(ids))
            configs = configurations if repeat == 1 else list(reversed(configurations))
            for cpus, memory, workers in configs:
                if shutil.disk_usage(out).free < 10*1024**3:
                    raise RuntimeError('Stopped to preserve the 10 GiB storage floor')
                name = f'{repeat}-{cpus}cpu-{memory}-{workers}workers'
                run = out/name
                storage = run/'jobs'
                storage.mkdir(parents=True)
                for ident in order:
                    job = storage/ident
                    job.mkdir()
                    source = args.private/ident/'score.pdf'
                    shutil.copy2(source,job/'source.pdf')
                    verify.save(job/'verification-inputs.json',dict(image=image,source=verify.sha(source)))
                verify.save(storage/'batch-input.json',dict(ids=order,workers=workers))
                print('Starting',name,'order:',','.join(order),flush=True)
                command = verify.container(image,storage)
                command[command.index('--cpus')+1] = str(cpus)
                command[command.index('--memory')+1] = memory
                container_name = 'livesheet-concurrency-'+uuid.uuid4().hex[:12]
                command = command[:-1]+['--name',container_name,'--memory-swap',memory,
                                        '--entrypoint','python',image,'-c',PROBE]
                try:
                    with (run/'batch.log').open('w') as log:
                        process = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=7500)
                    exit_code = process.returncode
                finally:
                    # Also remove the container on interruption or timeout.
                    subprocess.run(['docker','rm','-f',container_name],stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,check=False)
                # The existing verifier checks the fresh outputs. References were
                # never mounted into the container. Missing outputs cannot rerun.
                verify.verify(SimpleNamespace(output=run,private=args.private,ids=ids),image,no_rerun)
                validation = verify.load(run/'report.json')
                row = dict(name=name,repeat=repeat,cpus=cpus,memory=memory,workers=workers,
                           order=order,exitCode=exit_code,validationPassed=validation['passed'])
                if (storage/'resources.json').exists():
                    row.update(verify.load(storage/'resources.json'))
                    row['configurationVerified'] = (
                        row['memoryLimitBytes']==int(memory[0])*1024**3
                        and row['swapLimitBytes']==row['swapCurrentBytes']==0
                        and row['cpuLimit']==f'{cpus*100000} 100000')
                    row['conversionsPerHour'] = round(len(ids)*3600/row['seconds'],2)
                    row['passed'] = (exit_code==0 and validation['passed']
                                     and row['configurationVerified']
                                     and row['memoryEvents'].get('oom',0)==0)
                else:
                    row.update(passed=False,measurementError='Container exited without resource report')
                report['batches'].append(row)
                verify.save(out/'report.json',report)
                print(json.dumps(row),flush=True)
    report.update(complete=True,passed=all(r['passed'] for r in report['batches']))
    verify.save(out/'report.json',report)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
