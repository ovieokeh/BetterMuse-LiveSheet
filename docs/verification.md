# Verification

## Fast checks

```sh
python3 tools/bootstrap.py
docker build --platform linux/amd64 --target test -t bettermuse-livesheet-test:local .
python3 tools/test.py
```

The tests import the installed package inside the image. They do not mount the
LiveSheet or BetterMuse source tree. The clean suite discovers 235 tests; 38 need
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
Original reference bytes are preserved. Six original-reference discrepancies
remain known from before extraction; these are reported separately, never called
perfect recognition. The extraction gate requires unchanged baseline playback and
passing printed-reference comparisons.

Three optional private reviewed PDFs add exact event checks for Bella's Lullaby,
Peaceful Sleep and Cuddy's Serenade. These are development regression examples,
not a blind estimate of general PDF accuracy. A green run does not imply flawless
playback for every upload.

`artifacts/verification/report.json` records the image ID, case results, timings
and all comparisons. Reruns reuse a result only when the input and image identity
match and the committed bundle still validates. Use a new `--output` directory to
force a fresh run. `--ids` selects a subset for investigation, not a full release.

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
uploads a real PDF, cancels during recognition, retries, restarts during work,
waits for readiness and verifies original/companion/page downloads. Evidence is
written to `/data/service-check/`.

All containers used for these checks run non-root, with a read-only root
filesystem, bounded CPU/memory/process counts and writable test storage only.

## Release evidence

See `extraction-verification.json` for this checkpoint's results and tested image.
Detailed logs and private review evidence remain in ignored `artifacts/`.
