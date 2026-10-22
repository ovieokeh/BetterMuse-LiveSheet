# Architecture and extension points

## Job flow

1. `server.py` validates uploads, saves the original, creates the initial preview,
   queues work and reports progress. At most two workers run at a time.
2. `job_runner.py` supervises a private process group. `worker.py` coordinates
   one isolated job. Neither owns an HTTP request.
3. `recognition.py` drives Audiveris and export recovery. PyMuPDF supplies page
   inspection, previews and supporting printed evidence.
4. `pdf_experimental.py` coordinates optional source-backed repair candidates.
   The explicit registry in `pdf_pipeline/stages.py` defines ordered stages.
   Rhythm, repeat, terminal-measure and final octave handling retain their
   dedicated orchestration. They have not been forced into a generic plugin API.
5. `playback.py` uses MuseScore exports to build timed notes, measures and page
   coordinates for the website.
6. `companion.py` enriches that result with notation, staff, expression and
   analysis information for the existing native practice engine.
7. `pdf_pipeline/artifacts.py` writes the completion manifest last. The service
   verifies the bundle before marking the job ready.

The same PDF produces the same musical interpretation as the extraction baseline.
Package paths and fingerprints changed during extraction. Reference answers did
not. No score-specific musical rules were introduced for the split.

## Module boundaries

| Module | Owns |
| --- | --- |
| `server` | Upload limits, queue, retention, cancellation, retry and restart |
| `job_runner` | Parent liveness and per-attempt process-group ownership |
| `worker` | Per-job orchestration and packaging |
| `pdf_pipeline/config` | Immutable per-job tool and output settings |
| `pdf_pipeline/stages` | Stage order, proposal isolation and diagnostic outcomes |
| `pdf_pipeline/cache` | Cache validation |
| `pdf_pipeline/artifacts` | Atomic output commit and verification |
| `pdf_*_recovery` | Evidence-based notation changes |
| `testing` | Reference creation, comparison and benchmark helpers |
| `vendor/audiveris` | Narrow, pinned Java source overlays with upstream notices |

A repair receives an immutable input and proposes a separate output. A decline
keeps the prior candidate. Unexpected stage errors abort candidate preparation;
the job fails without a deliverable completion marker. Intermediate packages
are retained as diagnostic evidence, never as a fallback delivery. Diagnostic records distinguish
applied, unchanged and declined results.

## Adding a repair

1. Capture the defect as a minimal reproducible test, with independent expected
   events. Keep private score excerpts outside Git.
2. Define the printed evidence needed to justify the change.
3. Add controls where that evidence is missing or contradictory. Those must
   decline rather than guess.
4. Add a stage with explicit dependencies where the stage contract fits.
   Record why a separate orchestration path is necessary otherwise.
5. Check both gains and regressions. An improvement cannot offset a newly broken
   assertion in another measure or score.
6. Keep human listening as an additional check for musical behavior not yet
   represented by the reference suite.

## Container boundary

The runtime installs a Python package. It does not mount either repository's
source tree. All external tool downloads happen during bootstrap/build. Standalone workers
are tested with networking disabled. Compose permits host access through a
loopback-only port; its default bridge does not enforce an outbound firewall. Compiler
classes are built into the image and copied into a locked writable cache at boot.
The existing builder verifies their manifests before use.

The test image adds only generated/public fixtures and test harnesses. Optional
private data is mounted read-only for local tests, never baked into an image.

Pinned application assets and Python dependencies reduce drift. Ubuntu package
indexes are not snapshot-pinned, so rebuilding months later may produce different
OS package revisions. Keep the tested image digest with release evidence.

This remains a single-host local service. A public deployment still needs an
authenticated front door, per-user access and quotas, transport security,
operational monitoring, storage/retention policy, and distribution-license review.
Container isolation is one boundary; it is not a complete hostile-upload sandbox.

## Scheduler ownership

Queue claims and state changes share one lock. Each active job keeps its own
process reservation until its process group stops and its temporary directory is
removed. An immediate retry can queue but cannot start during that cleanup.
Ready output still requires the existing full artifact validation.

Each attempt gets separate HOME, XDG settings, TMPDIR and compiler cache paths.
They live in disposable `runtime-*` directories under job storage, so the disk
watchdog counts them. Shutdown joins both runners before releasing the store lock.

The supervisor inherits the store lock. On Linux it also adopts orphaned tool
processes, including Audiveris tools in separate sessions. If the server crashes,
it kills and reaps all of its descendants when its parent disappears. Another server cannot open the store
until those supervisors exit. A whole-container restart also removes all old
processes. Startup removes abandoned temporary directories and requeues running
jobs. It never requeues a cancelled job.

Concurrency remains capped at two. This is a measured pilot setting, not a promise
that two arbitrary maximum-size PDFs fit every memory budget. CPU/RAM remain
bounded by the container. `--workers 1` is available for constrained hosts.
