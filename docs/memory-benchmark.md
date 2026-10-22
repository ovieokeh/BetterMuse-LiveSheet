# Worker memory benchmark — 2026-10-22

## Decision

The reviewed-score tests support piloting a dedicated 4 GB worker server with
one conversion at a time and a 3 GiB container limit. Increasing the worker
limit to 4 GiB did not improve these conversion times. Confirm performance and
whole-host memory headroom on the selected server before public uploads.

This is a local capacity test, not a cloud throughput guarantee or a claim that
all PDFs fit. Keep the Coolify control plane on the existing management server.
An 8 GB worker node remains an option for measured memory pressure or future
parallel workers. At the time of this memory benchmark, the service processed jobs serially.

The later [concurrency benchmark](concurrency-benchmark.md) also tested two
workers within a shared 3 GiB budget. It found CPU capacity, rather than extra
RAM, improved throughput for these reviewed inputs. The HTTP service
now supports two workers after the separate scheduler and lifecycle checks.

## Method

- Pinned runtime image: `sha256:6cecb5d3645b16ebba27a8c13e6a6b9d85f14814d67aedd4c604776a9f203512`.
- Linux AMD64 container on ARM64 Docker Desktop; two CPU cores per container.
- Compared 3 GiB and 4 GiB worker limits. Swap disabled in both.
- Three reviewed scores, two runs per limit. Reversed limit order in round two.
- New job storage and fresh recognition for every run. Normal prebuilt compiler
  caches were seeded from the image. No prepared musical results were reused.
- Existing reference hashes, result integrity, exact reviewed note events,
  timing, velocity, tempo and playback-order checks remained active.
- The kernel's cgroup v2 `memory.peak` measures the whole container, including
  child processes and charged file cache. Memory events were recorded before
  container exit. Time covers the worker, including cache initialization,
  recognition, repairs, export and delivery validation. It excludes uploads,
  queue waiting and the host's independent reference comparison.
- Other local services remained running. They were not load generators. This
  was not a physical 4 GB host test. OS and API headroom remain to be measured
  on the target host.

## Reviewed scores

Times are means of two fresh runs. Peak is the highest of all four runs per
score. GiB means 1,073,741,824 bytes; provider RAM labels may differ.

| Score | Pages | 3 GiB limit | 4 GiB limit | Highest peak |
| --- | ---: | ---: | ---: | ---: |
| Bella's Lullaby | 2 | 92.42 s | 95.45 s | 1.33 GiB |
| Peaceful Sleep | 5 | 71.90 s | 74.10 s | 0.92 GiB |
| Cuddy's Serenade | 2 | 36.45 s | 36.54 s | 0.97 GiB |

All 12 runs passed. All exact reviewed event comparisons passed. No swap,
memory-limit events, or out-of-memory events occurred. The small timing
differences do not establish that less memory is faster. Two repeats are not
enough for a statistically precise speed comparison.

The three-score mix averaged about 67 seconds per conversion at 3 GiB. That
corresponds to roughly 54 conversions/hour if identical work keeps one worker
continuously occupied. It is not a forecast for arbitrary uploads or a shared
cloud CPU. Page count alone does not predict cost: Bella requires a higher-DPI
recognition retry and takes longer than the five-page score.

## Page-limit stress check

A separate local input repeats the five-page Peaceful Sleep PDF four times.
All 20 pages were checked pixel-for-pixel against their source pages. It stays
in ignored artifacts. This tests resource use for a longer input, not a broader
set of independently reviewed notation or maximum image complexity.

The 3 GiB run completed in 297.56 seconds, peaking at 1.11 GiB. It produced a
valid 20-page bundle with 3,084 note events. No swap or memory-limit events
occurred. This output passed the normal pipeline and bundle integrity checks;
it does not have an independently authored 20-page musical reference.

The 4 GiB run completed in 277.71 seconds, peaking at 1.16 GiB. It also produced
a valid 20-page bundle with 3,084 events, with no swap or memory-limit events.
There was one stress run per limit. This roughly 7% timing difference cannot be
attributed to extra RAM: neither run approached its limit, and the reviewed-score
repeats showed the opposite timing direction. The evidence supports adequate
memory at 3 GiB, not a precise performance advantage for either limit.

## Evidence and reproduction

- `artifacts/memory-benchmark-20261022/report.json`: all 12 measurements.
- Each run directory contains the unchanged verifier's detailed comparisons,
  worker log, result and `resources.json`.
- `artifacts/memory-stress-20261022/report.json`: page-limit resource results.
- `artifacts/memory-stress-input/input.json`: stress input hashes and construction.
- `artifacts/run_memory_stress.py`: local stress runner, using the same probe.

See [verification.md](verification.md#local-memory-benchmark) for the reusable
benchmark command. Private reviewed PDFs are required for the default cases.
No runtime limits, recognition rules, validation thresholds, app settings or
hosting resources were changed by this benchmark.
