# Conversion concurrency benchmark — 2026-10-22

## Decision

Two simultaneous conversions fit the tested 3 GiB worker budget. A dedicated
4 GB server remains a reasonable pilot option, with headroom reserved for the
OS and API and a target-host check before release. Two CPU cores delivered about
66 conversions/hour for this workload; four cores delivered about 85/hour at
the same memory limit. Raising the four-core memory limit to 6 GiB did not
improve performance. Prioritize CPU capacity over extra RAM for this workload.

This is evidence for two workers on these inputs, not the maximum possible
concurrency or a guarantee for arbitrary PDFs. All 30 fresh conversions across
the three reviewed inputs passed the unchanged musical checks. There were no
swap or memory-limit events. No production concurrency setting was changed.

## Scope

This benchmark measures conversion-worker capacity. The later scheduler change
enables two workers in the local HTTP service. See the service lifecycle checks
in [verification.md](verification.md#real-service-lifecycle). No hosted deployment
has been made.

## Method

- Runtime image: `sha256:6cecb5d3645b16ebba27a8c13e6a6b9d85f14814d67aedd4c604776a9f203512`.
- Linux AMD64 containers on ARM64 Docker Desktop, with no network or source
  mount. Docker reports 11 CPUs and about 17.5 GiB total memory.
- Each three-job batch runs in one container. Its CPU quota and memory limit
  apply to the combined workload, not separately to each worker. No swap.
- Compared 1 and 2 workers at 2 CPUs/3 GiB and 4 CPUs/6 GiB. A follow-up checks
  2 workers at 4 CPUs/3 GiB to separate CPU capacity from extra memory.
- The three inputs are Bella's Lullaby (2 pages), Peaceful Sleep (5 pages), and
  Cuddy's Serenade (2 pages). Each configuration gets two fresh batches. The
  second reverses both queue order and configuration order.
- Each worker has private job storage, compiler caches, HOME, XDG directories
  and TMPDIR. The source PDFs remain immutable. Normal prebuilt tool caches are
  seeded from the pinned image. No prepared result is reused for processing.
- Expected references stay outside the container. The existing verifier checks
  source/reference hashes, result integrity, exact reviewed note events, timing,
  velocity, tempo and playback order after processing. It cannot rerun a failed
  benchmark job. Its `reused` field describes checking the just-created output.
- Measurements include aggregate cgroup memory peak and CPU usage. Peak memory
  includes all child processes and charged file cache. Per-job times separate
  queue waiting from active processing. Completion starts at batch submission.

## Main results

Throughput is total completed jobs divided by total batch duration across two
rounds, expressed per hour. Processing and completion times are means across six
jobs per configuration. Memory peak is the maximum across the two rounds.

| Shared budget | Workers | Jobs/hour | Processing/job | Completion from submission | Peak memory |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2 CPUs / 3 GiB | 1 | 51.39 | 70.05 s | 141.14 s | 1.38 GiB |
| 2 CPUs / 3 GiB | 2 | 66.03 | 95.95 s | 124.14 s | 1.73 GiB |
| 4 CPUs / 6 GiB | 1 | 52.72 | 68.28 s | 137.57 s | 1.29 GiB |
| 4 CPUs / 6 GiB | 2 | 84.10 | 71.58 s | 92.08 s | 1.86 GiB |

All 24 main-comparison conversions passed the existing musical references and
result checks. There were no swap, memory-limit or out-of-memory events.

On two cores, two workers increased throughput by about 28%. Each active job
took about 37% longer, but less queue waiting reduced average completion time
for the three-job burst by about 12%. CPU use averaged 1.83 cores for the whole
parallel batch, versus 1.37 serially. The shared CPU quota frequently throttled
the parallel workload.

On four cores, two workers increased throughput by about 60% over one worker.
Active processing time stayed close to serial, and average burst completion
time fell by about 33%. Average CPU use rose from 1.40 to 2.33 cores. Some CPU
capacity is idle at the end of each batch when only its final job remains.

The two-worker four-core batches took 122.30 and 134.55 seconds. The two-core
batches took 157.17 and 169.94 seconds. Queue order affects finite-batch time;
the report does not select only the fastest ordering.

## CPU-only follow-up

The four-core / 3 GiB follow-up used the same two-worker setup, inputs, image,
validation and queue orders, with fresh processing. All six conversions passed.

| Shared budget | Workers | Jobs/hour | Processing/job | Completion from submission | Peak memory |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4 CPUs / 3 GiB | 2 | 84.81 | 71.24 s | 91.85 s | 1.94 GiB |

The batches took 121.26 and 133.43 seconds, essentially matching 122.30 and
134.55 seconds at 6 GiB. No swap or memory-limit events occurred. Average CPU
usage was 2.33 cores. The improved throughput over two cores is attributable to
the CPU budget in this experiment; extra RAM was not required for that gain.

Compared with two workers on two cores, four cores delivered about 28% more
throughput and reduced active processing time by about 26%. It did not double
throughput. A higher-priced server should be evaluated against this measured
benefit, with actual host performance confirmed before purchasing more capacity.

## Limits and deployment implications

- These are resource quotas on the Mac's Docker VM, not measurements on a
  physical small cloud server. Different CPUs and provider contention change
  actual throughput. The CPU limits are quotas, not core affinity.
- Hourly figures are equivalents of short, fully queued three-score batches.
  They are neither a one-hour endurance test nor a service-level guarantee.
  A continuously busy queue, different job mix or larger scores can differ.
- The tests cover three reviewed PDFs, not 24 distinct musical inputs. They do
  not test two maximum-complexity 20-page uploads together or three-plus workers.
- Reserve memory and CPU for the OS, API, upload inspection and monitoring. The
  benchmark excludes HTTP upload traffic and measures conversion workers only.
- No runtime limits, recognition rules, validators or server concurrency were
  changed by this benchmark. The separate scheduler work preserves isolation and tests
  cancellation, retries, restart recovery and resource-pressure failure handling.

## Evidence and reproduction

Run the command in [verification.md](verification.md#local-concurrency-benchmark).
Select a budget explicitly on hosts too small for the default matrix.

- `artifacts/concurrency-benchmark-20261022/report.json`: eight main batches.
- Each batch has detailed reference checks in `report.json`, a `batch.log`,
  per-job worker logs and timings, and aggregate `jobs/resources.json`.
- `artifacts/concurrency-cpu-check-20261022/report.json`: CPU-only follow-up.
- `artifacts/summarize_concurrency.py`: local aggregation used for the table.

The follow-up can be repeated with:

```sh
.venv/bin/python tools/benchmark_concurrency.py \
  --output artifacts/concurrency-cpu-new-run --config 4 3g 2
```

Raw artifacts and private scores remain local and ignored by Git.
