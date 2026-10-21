#!/usr/bin/env python3
"""Prepare one uploaded PDF in an isolated local job directory.

Reuses catalogue recognition/export/validation, without reading or modifying the
catalogue. Exporters receive immutable settings for this job.
"""
import argparse
from copy import deepcopy
from dataclasses import replace
import traceback
import hashlib
import json
import os
from pathlib import Path
import signal
import resource
import subprocess

import pymupdf
from livesheet import recognition as pipeline
from livesheet import playback as playback
from livesheet import companion as native

from livesheet.pdf_preflight import inspect_pdf, processing_copy


def finalize_web_performance(performance, job_id):
    """Finalize job-private URLs and the full digest before native enrichment."""
    performance["id"] = job_id
    for i, page in enumerate(performance["pages"]):
        page["url"] = f"/api/local-pdf/jobs/{job_id}/files/page-{i+1}.webp"
    performance.pop("revision", None)
    performance["revision"] = hashlib.sha256(native.encode(performance)).hexdigest()


def repaired_package(
    folder,
    entry,
    source,
    working_source,
    mapping,
    baseline,
    baseline_companion,
    progress,
    exports,
):
    from livesheet.pdf_experimental import prepare_candidate, PROFILE, fingerprint

    # Keep intermediate evidence for diagnosis; it is never a delivery fallback.
    baseline_raw = native.encode(baseline_companion)
    if len(baseline_raw) > 16 * 1024 * 1024:
        raise ValueError("companion_size_limit")
    pipeline.atomic_json(folder / "baseline-score.json", baseline)
    (folder / "baseline-companion.json").write_bytes(baseline_raw)
    report = dict(
        profile=PROFILE,
        status="failed",
        musicalReview="unreviewed",
        algorithmSHA256=fingerprint(),
        reason="candidate_failed_validation",
    )
    exports = replace(exports, cache=folder / "experimental/performance")
    try:
        candidate, recognition, report = prepare_candidate(
            working_source, mapping, folder / "experimental", progress
        )
        progress("checking_result")
        experimental_entry = {**entry, "sha256": pipeline.sha(working_source)}
        output = folder / "experimental/web"
        playback.prepare(
            experimental_entry, candidate, True, recognition, output, config=exports
        )
        performance = json.loads((output / "score/score.json").read_text())
        pipeline.validate_package(performance, len(baseline["pages"]))
        # Additional export guard: do not lose existing written measure visits.
        if [m["id"] for m in performance["measures"]] != [
            m["id"] for m in baseline["measures"]
        ]:
            raise ValueError("repair-changed-performance-order")
        if sum("box" not in n for n in performance["notes"]) > sum(
            "box" not in n for n in baseline["notes"]
        ):
            raise ValueError("repair-increased-unmapped-playback")
        if working_source.read_bytes() != source.read_bytes():
            performance["provenance"]["processingSourceSHA256"] = pipeline.sha(
                working_source
            )
        if baseline.get("provenance", {}).get("terminalRecovery"):
            performance["provenance"]["terminalRecovery"] = baseline["provenance"][
                "terminalRecovery"
            ]
        if baseline.get("provenance", {}).get("repeatRecovery"):
            performance["provenance"]["repeatRecovery"] = baseline["provenance"][
                "repeatRecovery"
            ]
        performance["sourceSHA256"] = entry["sha256"]
        candidate["sha256"] = entry["sha256"]
        pipeline.atomic_json(recognition / "mapping.json", candidate)
        timing = playback.export_performance(
            Path(candidate["musicxmlFiles"][0]),
            exports.cache / "score",
            True,
            config=exports,
        )[:3]
        report.update(
            status="applied" if report["changedMeasureIndices"] else "unchanged"
        )
        report["checks"].update(
            packageValidated=True,
            performanceOrderPreserved=True,
            noMoreUnmappedPlayback=True,
        )
        performance["provenance"]["preparationProfile"] = dict(
            name=PROFILE,
            status=report["status"],
            algorithmSHA256=report["algorithmSHA256"],
            changedMeasureIndices=report["changedMeasureIndices"],
        )
        finalize_web_performance(performance, folder.name)
        companion = native.prepare(
            "score", entry, source, performance, None, recognition, timing
        )
        if len(native.encode(companion)) > 16 * 1024 * 1024:
            raise ValueError("companion_size_limit")
        # Only now may callers select the candidate. Original page images are shared.
        pipeline.atomic_json(folder / "quality.json", report)
        return (
            performance,
            companion,
            {k: report[k] for k in ("profile", "status", "changedMeasureIndices")},
        )
    except Exception as error:
        traceback.print_exc()
        report.update(
            status="failed", reason=type(error).__name__ + ": " + str(error)
        )
        pipeline.atomic_json(folder / "quality.json", report)
        raise ValueError("repair_validation_failed") from error


def recover_incomplete_repeats(folder, entry, result, progress, settings):
    exports = settings.playback
    """Recover repeats only when the initial export was rejected."""
    from livesheet.pdf_repeat_recovery import recover

    recognition = settings.cache / "score"
    if result["recognitionDPI"] != 300:
        recognition /= f"dpi-{result['recognitionDPI']}"
    original = json.loads((recognition / "mapping.json").read_text())
    if len(original["musicxmlFiles"]) != 1:
        return result
    target = recognition / "repeat-recovery"
    target.mkdir(exist_ok=True)
    try:
        progress("checking_repeats")
        mapping, report = recover(
            Path(original["musicxmlFiles"][0]),
            target / "score.mxl",
            folder / "score.pdf",
            original,
        )
        pipeline.atomic_json(target / "mapping.json", mapping)
        pipeline.atomic_json(target / "report.json", report)
        # An independently calculated traversal must agree with the actual export.
        timing = playback.export_performance(
            target / "score.mxl", exports.cache / "score", True, config=exports
        )
        if [e["element"] for e in timing[4]] != report["expectedMeasureVisits"]:
            raise ValueError("repeat-performance-order-mismatch")
        staged_root = folder / "repeat-recovery-web"
        playback.prepare(entry, mapping, True, target, staged_root, config=exports)
        performance = json.loads((staged_root / "score/score.json").read_text())
        performance["provenance"]["repeatRecovery"] = report
        pipeline.validate_package(performance, entry["pageCount"])
        pipeline.atomic_json(staged_root / "score/score.json", performance)
        pipeline.promote(staged_root / "score", exports.public / "score")
        return {
            **result,
            "status": "ready",
            "error": None,
            "repeatRecovery": report,
            "recognitionRoot": str(target),
        }
    except Exception as error:
        traceback.print_exc()
        pipeline.atomic_json(target / "failure.json", dict(error=str(error)))
        return result


def prepare_job(folder, audiveris, musescore):
    from livesheet.pdf_pipeline.config import JobConfig, Toolchain, job_exports

    tools = Toolchain.configured(audiveris, musescore, pipeline.TESSDATA)
    config = JobConfig(folder, tools)
    tools.validate()
    version = subprocess.check_output(
        [tools.musescore, "--version"], text=True, timeout=30
    ).strip()
    previous_signals = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        _prepare_job(config, job_exports(config, version))
    finally:
        for sig, handler in previous_signals.items():
            signal.signal(sig, handler)


def _prepare_job(config, settings):
    exports_config = settings.playback
    from livesheet.pdf_pipeline.artifacts import atomic_bytes, commit_result

    folder = config.folder
    audiveris = config.tools.audiveris
    # A previous completion cannot certify a new or interrupted attempt.
    (folder / "completion.json").unlink(missing_ok=True)

    def progress(stage, dpi=None):
        pipeline.atomic_json(folder / "stage.json", dict(stage=stage, dpi=dpi))

    def interrupted(*_):
        pipeline.stop.set()
        # Audiveris deliberately has its own process group in the shared runner.
        # Do not acquire the runner's lock from a signal handler: the signal
        # may interrupt that same thread while it holds the lock.
        for process in tuple(pipeline.processes):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024**2, 256 * 1024**2))
    source = folder / "source.pdf"
    progress("checking_pdf")
    pages = inspect_pdf(source)
    (settings.results / "scores").mkdir(parents=True, exist_ok=True)
    previews = folder / "previews"
    previews.mkdir(exist_ok=True)
    working_source = folder / "score.pdf"
    normalized = processing_copy(source, working_source)
    with pymupdf.open(working_source) as pdf:
        for i, page in enumerate(pdf):
            page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(
                previews / f"page-{i+1}.png"
            )
    entry = dict(
        id="score",
        format="pdf",
        assetPath="score.pdf",
        sha256=pipeline.sha(working_source),
        pageCount=pages,
        previews=[f"previews/page-{i+1}.png" for i in range(pages)],
    )
    engine = pipeline.sha(config.tools.audiveris_jar) + pipeline.sha(
        settings.tessdata / "eng.traineddata"
    )
    adapter = "".join(
        pipeline.sha(Path(__file__).parent / name)
        for name in [
            "score_recognition.py",
            "playback.py",
            "companion.py",
            "worker.py",
            "recognition.py",
            "pdf_staff_evidence.py",
            "pdf_music_fonts.py",
            "pdf_pipeline/config.py",
            "pdf_pipeline/artifacts.py",
            "pdf_pipeline/cache.py",
        ]
    )
    adapter += exports_config.engine_version + pipeline.export_recovery.fingerprint()
    from livesheet.pdf_experimental import fingerprint

    adapter += json.dumps(fingerprint(), sort_keys=True)
    args = argparse.Namespace(
        recover_terminal_measure=True,
        audiveris=audiveris,
        pdf_dpi=None,
        retry_profiles=[400],
        timeout=900,
        step_timeout=180,
        min_free_gb=10,
        retry_failed=True,
        force=False,
        rerun_recognition=False,
    )
    result = pipeline.prepare_with_retries(
        entry, args, engine, adapter, progress, config=settings
    )
    if (
        result["status"] == "failed"
        and result.get("error") == "incomplete-playback"
    ):
        result = recover_incomplete_repeats(folder, entry, result, progress, settings)
    if result["status"] != "ready":
        raise ValueError(result.get("error", "preparation_failed"))
    progress("packaging")
    performance = json.loads((exports_config.public / "score/score.json").read_text())
    pipeline.validate_package(performance, pages)
    recognition = settings.cache / "score"
    if result["recognitionDPI"] != 300:
        recognition /= f"dpi-{result['recognitionDPI']}"
    if result.get("recognitionRoot"):
        recognition = Path(result["recognitionRoot"])
    mapping = json.loads((recognition / "mapping.json").read_text())
    processing_mapping = deepcopy(mapping)
    sources = [Path(p) for p in mapping["musicxmlFiles"]]
    exports = [
        playback.export_performance(
            p,
            (
                exports_config.cache / "score"
                if len(sources) == 1
                else exports_config.cache / f"score/movement-{i+1}"
            ),
            True,
            config=exports_config,
        )
        for i, p in enumerate(sources)
    ]
    timing = (playback.join_performances(exports) if len(exports) > 1 else exports[0])[
        :3
    ]
    if normalized:
        # Recognition coordinates are fractions of the displayed page. Uniform
        # scaling preserves them. Bind delivery to the untouched original PDF.
        original_hash = pipeline.sha(source)
        performance["provenance"]["processingSourceSHA256"] = entry["sha256"]
        performance["sourceSHA256"] = original_hash
        entry["sha256"] = original_hash
        mapping["sha256"] = original_hash
        pipeline.atomic_json(recognition / "mapping.json", mapping)
    finalize_web_performance(performance, folder.name)
    companion = native.prepare(
        "score", entry, source, performance, None, recognition, timing
    )
    performance, companion, preparation = repaired_package(
        folder,
        entry,
        source,
        working_source,
        processing_mapping,
        performance,
        companion,
        progress,
        exports_config,
    )
    if result.get("repeatRecovery"):
        preparation["baselineRepaired"] = True
    # The native installer imposes the same payload size ceiling.
    raw = native.encode(companion)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("companion_size_limit")
    atomic_bytes(folder / "companion.json", raw)
    pipeline.atomic_json(folder / "score.json", performance)
    pipeline.atomic_json(
        folder / "result.json",
        dict(
            sourceSHA256=entry["sha256"],
            companionSHA256=native.digest(raw),
            pageCount=pages,
            notes=len(companion["notes"]),
            duration=companion["duration"],
            issues=performance["issues"],
            musicalReview="unreviewed",
            preparation=preparation,
            capabilities=dict(
                playback=True,
                notePositions=all(n.get("box") for n in companion["notes"]),
                hands=all(
                    n.get("staff") in (0, 1) and n.get("part") == 0
                    for n in companion["notes"]
                )
                and {n.get("staff") for n in companion["notes"]} == {0, 1},
                expression=len(companion["notation"]) == 1,
            ),
            recognitionDPI=result["recognitionDPI"],
            attempts=result.get("attempts", []),
            repeatRecovery=result.get("repeatRecovery"),
        ),
    )
    commit_result(folder)
    (folder / "failure.json").unlink(missing_ok=True)
    progress("ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--audiveris", type=Path, default=pipeline.AUDIVERIS)
    parser.add_argument("--musescore", default=playback.MSCORE)
    args = parser.parse_args()
    try:
        prepare_job(args.job_dir, args.audiveris, args.musescore)
    except Exception as error:
        pipeline.atomic_json(args.job_dir / "failure.json", dict(error=str(error)))
        raise


if __name__ == "__main__":
    main()
