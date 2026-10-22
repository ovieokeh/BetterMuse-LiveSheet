# Verification

## Fast checks

```sh
python3 tools/bootstrap.py
docker build --platform linux/amd64 --target test -t bettermuse-livesheet-test:local .
python3 tools/test.py
```

The tests import the installed package inside the image. They do not mount the
LiveSheet or BetterMuse source tree. The clean suite discovers 249 tests; 38 need
private regression excerpts and are explicitly skipped when absent.

For the complete local set, supply the private excerpts in `tests/data/pdf-quality`
and run `python3 tools/test.py --private`. They are mounted read-only and excluded
from both Git and Docker builds. The full private run must have zero skips.

## End-to-end musical checks

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/python tools/verify.py
# Optional private listening-review cases:
.venv/bin/python tools/verify.py --private .private/reviewed
```

Each recognition worker runs in the installed runtime image with no network and
no source mount. It receives only the PDF. Expected notes and musical references
remain outside the worker's mount. The host verifier checks reference hashes,
completed bundle integrity, timed attacks, pitch, duration, velocity, tempo,
measure timing and playback order.

The manifest includes 33 generated PDFs: 30 original cases across three font/layout
variants, plus three corrected-meter cases. Where available, the separate printed
reference removes hidden numeric dynamic overrides while retaining visible marks.
Original reference bytes are preserved. Source MusicXML is checked for consistent
bar lengths before recognition. The three malformed original meter cases are
explicit diagnostics, not successful playback references. The positive set is
30 generated PDFs, including all three corrected-meter cases. Three optional
reviewed PDFs bring the positive total to 33.

`tests/corpus/reference-diagnostics.json` records the original source hashes,
exact defects and valid replacements. Any new invalid reference, changed defect,
or invalid replacement blocks the run. The Petaluma diagnostic must fail with
the overfull-measure guard and no completed delivery. An unrelated failure does
not pass. The other two originals retain their historical comparisons as
diagnostic checks only. Neither counts toward playback accuracy.

Original-reference discrepancies are still reported separately, never called
perfect recognition. Positive cases require unchanged baseline playback and
passing printed-reference comparisons. Diagnostics must meet their separately
reported expectations before the overall gate passes.

Three optional private reviewed PDFs add exact event checks for Bella's Lullaby,
Peaceful Sleep and Cuddy's Serenade. These are development regression examples,
not a blind estimate of general PDF accuracy. A green run does not imply flawless
playback for every upload.

`artifacts/verification/report.json` records the image ID, case results, timings
and all comparisons. Reruns reuse a result only when the input and image identity
match and the committed bundle still validates. Use a new `--output` directory to
force a fresh run. `--ids` selects a subset for investigation, not a full release.

## Local memory benchmark

```sh
.venv/bin/python tools/benchmark_memory.py \
  --output artifacts/memory-benchmark-new-run \
  --limits 3g 4g --repeats 2
```

The output directory must be new. Each case gets isolated storage and fresh
recognition. The default cases are the three private reviewed scores. Use
`--ids` to select other corpus cases. References and source hashes are checked by
the same verifier used for release checks. No validation thresholds change.

The containers have two CPUs and no swap. An in-container probe records the
cgroup v2 memory high-water mark, memory-limit events, CPU usage and worker wall
time before exit. Peak memory includes child processes and charged file cache.
Missing measurements are failures, not zero memory usage. Repeats reverse the
memory-limit order. Source PDFs and results stay in ignored local artifacts.

This measures worker memory requirements. It does not emulate a whole small
server or predict another CPU's speed. Leave room for the operating system,
Docker and the API. Test the actual host before opening public uploads.

See [memory-benchmark.md](memory-benchmark.md) for the 2026-10-22 sizing results.

## Local concurrency benchmark

```sh
.venv/bin/python tools/benchmark_concurrency.py \
  --output artifacts/concurrency-benchmark-new-run
```

This runs the three private reviewed scores with one and two workers under two
aggregate budgets: 2 CPUs/3 GiB and 4 CPUs/6 GiB. Each batch runs inside one
container, so concurrent jobs share the stated budget. Swap remains disabled.
Each job has private storage, compiler caches and desktop-tool settings.
The default matrix needs a host with at least four CPUs and more than 6 GiB
available to Docker. On a smaller host, select matching budgets explicitly:
`--config 2 3g 1 --config 2 3g 2`. The tool rejects budgets above host capacity.

Two rounds reverse both configuration order and queue order. All processing is
fresh. After processing, the existing verifier checks those outputs without
rerunning missing or failed jobs. Its `reused` flag refers to this validation
step, not reuse of results from a previous benchmark. References are never
mounted into the worker container.

Results include total batch time, aggregate peak memory, CPU usage, individual
processing time, queue wait and completion time from batch submission. This is
a conversion-capacity experiment. It does not implement or validate concurrent
dispatch, cancellation or recovery in the production HTTP service.

See [concurrency-benchmark.md](concurrency-benchmark.md) for the measured results
and the additional four-core / 3 GiB comparison.

## Deliberately broken exports

The test image contains `/opt/livesheet-tools/check_mutations.py`. Run it with a
writable `/data` mount, no network, and `--entrypoint python`. It exports eight
intentionally wrong MusicXML variants with the pinned MuseScore and requires the
comparator to reject every one: delayed opening tempo, replayed ties, missing
fermata/pedal/dynamics, reversed dynamics, missing forte, and late forte.

This checks that the comparator detects defects, not merely that current outputs
match one another. Unit tests additionally mutate attacks, pitch, timing,
duration, velocity, tempo, repeat order and final duration.

## Real service lifecycle

`/opt/livesheet-tools/check_service.py` in the test image reads
`/data/service-input.pdf`. It starts the installed service on container loopback,
uploads two real PDFs concurrently, checks a third waits, cancels and retries
one while its peer continues, and tests both graceful restart and an abrupt
server-only crash. It checks process groups are gone before restart, then verifies
both output bundles and original/companion/page downloads. Evidence is
written to `/data/service-check/`.

All containers used for these checks run non-root, with a read-only root
filesystem, bounded CPU/memory/process counts and writable test storage only.

### Two-worker local checkpoint — 2026-10-22

- Full Linux suite: 259 tests passed, zero skips. This includes private fixtures
  and lifecycle tests with real processes, detached tool sessions and forced exits.
- Two simultaneous five-page Peaceful Sleep conversions ran through the HTTP API
  under the existing shared 2 CPU / 4 GiB container limit. A third upload queued.
- Cancelling and immediately retrying one job left its peer running. A graceful
  service stop and an abrupt server-only crash both stopped descendant tools.
  Both interrupted jobs recovered. The cancelled queued job stayed cancelled.
- Both recovered bundles passed normal validation and download hash checks.
  All 771 reviewed note events matched exactly in each result. The existing
  timing, tempo and printed-reference comparisons also passed.
- The local website and authenticated device APIs now report two healthy workers.
  All 45 pre-existing jobs retained their states and source/output hashes.
- The first integration attempt was refused by the unchanged 10 GiB disk guard.
  Removing disposable LiveSheet build cache restored space; the rerun passed.

Evidence: `artifacts/two-worker-service-20261022/` contains image identities,
source hashes, the unit-test summary, lifecycle report, musical comparisons and
local cutover verification. `artifacts/unit-tests/report.json` records the suite.

This validates local Linux scheduling and lifecycle behavior. It is not a cloud
load test or a guarantee that two arbitrary maximum-complexity PDFs fit 4 GiB.
No musical rules, reference answers or validation thresholds changed in this pass.


## Release evidence

See `extraction-verification.json` for this checkpoint's results and tested image.
Detailed logs and private review evidence remain in ignored `artifacts/`.

## Single-pipeline migration: corrected diagnosis

On 2026-10-21, removing delivery fallback exposed a rejected candidate for
`meter-ties--petaluma-wide`. This was initially described as an unresolved repair
bug. Inspection on 2026-10-22 showed that diagnosis was incorrect.

The frozen original source and printed PDF put four quarter-note beats and a
whole bass note in bar 3, while the preceding 3/4 signature is still in force.
Audiveris omitted the time signatures. Restoring the printed meter makes the
inconsistency detectable. The overfull-measure guard correctly rejects this
candidate; the old fallback hid the inconsistency by returning the initial
export with missing meter information.

A corrected fixture family already existed as `meter-ties-v2`, with the missing
4/4 change explicitly printed in bar 3. All three corrected font/layout variants
pass. The original source files, PDFs, answers and hash manifest remain frozen.
No production rule, validation threshold or fallback changed in this follow-up.

The original Petaluma case now checks the specific safe rejection. The benchmark
also verifies reference validity independently, before comparing recognition.
This is a correction to the test setup and its diagnosis, not a new claim that
the malformed original has become playable.

Evidence: `artifacts/single-pipeline-verification/report.json` and its
`jobs/meter-ties--petaluma-wide/` folder contain the current classification,
intermediate candidates and rejection record.

### Migration verification result — 2026-10-21

- 240 Linux tests passed with zero skips, including rejection and legacy-job retry checks.
- The original run reported 35 of 36 checks passing. The apparent repair failure
  was subsequently traced to the malformed test source, as explained above.
- Bella's Lullaby, Peaceful Sleep and Cuddy's Serenade matched their reviewed
  musical events exactly. Generated/reference expectations were not changed.
- Fresh website and authenticated native uploads omitted preparation headers.
  Both used the repair pipeline, verified output integrity, and survived restart.
- All 38 migrated jobs retained their results and access boundaries.
- Website request tests, TypeScript, targeted lint and Spanish coverage passed.
- BetterMuse Dev launched on Trill. Its existing binary needs no change: the
  default service path now includes repairs. Existing device companions were not
  regenerated. Physical-device import/listening remains a manual check.

Evidence: `artifacts/single-pipeline-clients.json`,
`artifacts/single-pipeline-verification/report.json`, and
`artifacts/single-pipeline-linux-tests.log`. All work remains local.

### Reference-validity follow-up — 2026-10-22

- 249 Linux tests passed with zero skips, including nine new reference checks.
- All 33 positive cases passed revalidation against the unchanged runtime image.
  Existing successful bundles were reused only after identity and integrity checks.
- Three malformed-source diagnostics remain explicitly separate from that count.
  The rejected Petaluma case was rerun and produced the exact expected validation
  failure, with no completion marker. The two historical comparisons also passed.
- Bella's Lullaby, Peaceful Sleep and Cuddy's Serenade still matched every reviewed
  event. No source/reference answer was edited and no runtime changes were needed.
