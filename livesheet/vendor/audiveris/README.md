# Audiveris compatibility overlays

`PartwiseBuilder.java` is from Audiveris **5.11.0**, with local changes dated
2026-10-19 and 2026-10-21. It retains the upstream AGPL-3.0-or-later notice; the full licence is
in `LICENSE`. This directory contains narrowly scoped source overlays for the pinned engine.

Upstream source:
https://github.com/Audiveris/audiveris/blob/5.11.0/app/src/main/java/org/audiveris/omr/score/PartwiseBuilder.java

Original source SHA-256: `490ce0a76e54be61778f887e6e5d5a00a478ab6e83454cf6cfdfba19ea8f6e61`.
Supported macOS arm64 and Ubuntu 24.04 x86_64 distributions share this
`audiveris.jar` SHA-256:
`9b35257edea808ec80a8bcd48882d174b107152d9640b5ca33e1365593cff2a5`.

## Change

`WedgeIterators` checks both anchors before adding either endpoint of a hairpin.
If an anchor is missing, untimed, or has no notes, the whole hairpin is omitted
from the derived MusicXML and logged with `BM_WEDGE_OMITTED`. Skipping both ends
avoids leaving an unclosed crescendo. Valid hairpins use the unchanged export path.
No pitch, note identity, measure, musical duration, or original recognition file is changed by the patch.
Other export errors remain failures. The ordinary package validation is unchanged.

### Exact duration grid (October 21)

Half/whole-only pages can have an internal duration divisor of 1 or 2.
Audiveris 5.11.0 computes a quarter's division count with integer division,
which produces zero. The MusicXML then cannot be read safely.

The derived exporter uses `lcm(originalDivisor, 4)` as its grid. It exports
all note, backup, forward, rest and sound durations on that same grid, directly
from the original rational durations. For example, a half note becomes two ticks
and a quarter becomes one tick. It does not change the musical rhythm.
Inexact or overflowing values raise errors instead of being rounded.
`BM_DURATION_GRID` and `durationGridRepairs` record the affected pages.

The recovery is also triggered by an explicit zero `<divisions>` value in an
original export. It rejects derived exports that still have zero divisions.
The mapping reader reports invalid divisions clearly even outside recovery.

## Build and use

Requires a full Java 17+ JDK available as `java` for compilation. The installed
Audiveris runtime runs the exporter. The bundled runtime itself has no compiler.
`PDF_COMPILER_JAVA` overrides the compiler's Java executable. `PDF_EXPORT_CACHE`
selects a writable cache; builds are protected by a process lock. The helper
discovers official Mac and Linux layouts. `PDF_AUDIVERIS_JAR` and
`PDF_AUDIVERIS_JAVA` can override those paths without relaxing the jar hash check.
The standalone Docker build bootstraps the pinned compiler and compiles both
overlays automatically. See the [repository quickstart](../../../README.md).
For direct host development, run `python3 tools/bootstrap.py` from the repository
root and set `PDF_EXPORT_CACHE` to that root's `.build/support` directory.

The helper verifies its SHA-256
`14c46ddc2e511c46c998da728f6a80f13a689c920f3832402f7ad150f6a6188d`
before compiling. It checks the engine version by hash and verifies every cached
class. The installed application is never patched. Overlay classes precede the original jars only in their designated subprocess.
The exporter and OCR guard use separate class directories.

`livesheet/recognition.py` automatically uses this recovery when the original
log contains the wedge null-pointer exception or an export has zero divisions. It reuses a copy of the cached
OMR project; it does not rerun image recognition. Results and logs are stored
in each recognition profile's `export-recovery/` directory. The cache records
input, engine, patch, and output hashes. The browser package records omitted
hairpins in provenance and adds `unplaced-dynamics-omitted` to its review issues.

Run the preparation tests:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*score*.py'
.venv/bin/python -m unittest discover -s tests -p 'test_audiveris_export_recovery.py'
```

This source and build recipe must accompany any applicable distribution of the
modified exporter. No publication is performed by these commands.

## Source-backed rest OCR guard (October 21)

`TesseractOCR.java` comes from the same Audiveris 5.11.0 tag:
https://github.com/Audiveris/audiveris/blob/5.11.0/app/src/main/java/org/audiveris/omr/text/TesseractOCR.java

Original SHA-256 after LF normalization:
`6391ef6cf07c13bdec93c6ddec027906a2635cbf4e7585bf9f029c6cdd336bc4`.
The upstream AGPL notice is retained. The sole functional change calls
`PrintedRestGuard.filter` after page-coordinate translation and before returning
recognized text. `PrintedRestGuard.java` is BetterMuse code under AGPL-3.0-or-later.

Linux OCR can read an eighth-rest glyph as the digit “7”. Text recognition then
wins over symbol recognition. Missing rests can prevent an entire moving voice
from receiving timing, so its notes disappear from the initial MusicXML.

`livesheet/pdf_ocr_guard.py` supplies evidence from explicit eighth-rest characters
in embedded, supported music fonts. It declines scans, unknown/unembedded fonts,
rotated/cropped pages, hidden text and overlapping ordinary text. Evidence has
normalized page coordinates and the input PDF hash. Each job writes its own
`printed-rests.tsv` and `printed-rests.json`.

The guard rejects only a complete OCR word “7” contained in exactly one matching
rest region. Other words in the same OCR line survive. Audiveris still recognizes
the rest and assigns voices; the guard invents no notes or timing. Every rejection
is logged as `BM_PRINTED_REST_OCR`. Without source evidence, the ordinary launcher
is unchanged. Recognition cache keys include the guard and source adapter hashes.

The same verified compiler, core jar check and process lock apply. Guard classes
are compiled under `PDF_EXPORT_CACHE/ocr-guard`. They do not replace the installed
engine or the export-recovery overlay. `BETTERMUSE_PRINTED_RESTS` is a per-process
internal input, not a public configuration option.

Run its Python and real-Java checks:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_pdf_ocr_guard.py'
.venv/bin/python -m unittest discover -s tests -p 'test_audiveris_ocr_guard.py'
```

These modified sources and their build recipe must accompany applicable
distribution, just like the exporter patch. Nothing is published by the tests.
