"""Source-backed PDF repair stages shared by experiments and local uploads.

No score titles, benchmark answers or private corpus paths are used here.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
from livesheet.pdf_rhythm_repair import repair, write_mxl, load_mxl, Decline
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
from livesheet.score_recognition import read_mxl
from livesheet.playback import written_notes_source

from livesheet.pdf_pipeline.config import PREPARATION_PROFILE as PROFILE

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def retime_mapping(mapping, source, report, target):
    """Carry checked original head geometry through the explicit note permutation."""
    result = deepcopy(mapping)
    measures, notes = read_mxl(source)
    if len(measures) != len(mapping["measures"]):
        raise ValueError("Repair changed measure count")
    old_by_measure = {
        i: [n for n in mapping["notes"] if n["measureIndex"] == i]
        for i in range(len(measures))
    }
    for i, order in enumerate(report["noteOrder"]):
        if sorted(order) != list(range(len(old_by_measure[i]))):
            raise ValueError("Repair is not a one-to-one note permutation")
    originals = [
        old_by_measure[i][old_index]
        for i, order in enumerate(report["noteOrder"])
        for old_index in order
    ]
    if len(originals) != len(notes):
        raise ValueError("Repair changed note count")
    changed = {"id", "voice", "onset", "start"}
    for old, new in zip(originals, notes):
        if any(
            old[k] != new[k]
            for k in ["midi", "staff", "duration", "ties", "measureIndex"]
        ):
            raise ValueError("Repair changed pitch, duration, ties or staff")
        note = deepcopy(old)
        note["recognitionOriginalID"] = old["id"]
        note["recognitionOriginalOnset"] = old["onset"]
        for key in changed:
            note[key] = float(new[key]) if key == "onset" else new[key]
        note["mapping"] = "original-head-geometry+audited-note-permutation"
        new.clear()
        new.update(note)
    for old, new in zip(result["measures"], measures):
        old["start"] = new["start"]
        old["duration"] = new["duration"]
    result["notes"] = notes
    result["musicxmlFiles"] = [str(source.resolve())]
    result["rhythmRepair"] = dict(
        version=report["version"],
        accepted=report["accepted"],
        originalMusicXMLFiles=mapping["musicxmlFiles"],
    )
    save(target, result)
    return result


def candidate(source, pdf, mapping, folder, vector=False, clefs=False):
    folder.mkdir(parents=True, exist_ok=True)
    # Exactly the same written-notes cleanup as production MIDI export.
    written = written_notes_source(source, folder / "written-notes.musicxml")
    root, name = load_mxl(source)
    write_mxl(source, folder / "written-notes.mxl", ET.parse(written).getroot(), name)
    from livesheet.pdf_pipeline.stages import RepairStage, run_stage, run_stages

    stage_source, updated, stages = run_stages(
        folder / "written-notes.mxl", pdf, mapping, folder
    )
    report = repair(
        stage_source, folder / "candidate.mxl", PDFHeadEvidence(pdf, updated)
    )
    updated = retime_mapping(
        updated, folder / "candidate.mxl", report, folder / "mapping.json"
    )
    report.update(stages)
    stage_diagnostics = json.loads((folder / "stages.json").read_text())
    if vector:
        from livesheet.pdf_vector_recovery import recover

        shutil.copy2(folder / "candidate.mxl", folder / "rhythm-candidate.mxl")
        save(folder / "rhythm-mapping.json", updated)
        updated, recovered, diagnostic = run_stage(
            RepairStage(
                "vectorRecovery",
                "pdf_vector_recovery",
                "Printed vector notes and within-bar ties",
            ),
            folder / "rhythm-candidate.mxl",
            folder / "candidate.mxl",
            pdf,
            updated,
            recover,
            decline_policy="abort",
        )
        stage_diagnostics["stages"].append(diagnostic)
        recovered["algorithmSHA256"] = sha(ROOT / "livesheet/pdf_vector_recovery.py")
        report["vectorRecovery"] = recovered
        save(folder / "mapping.json", updated)
        save(folder / "vector-recovery.json", recovered)
    if clefs:
        from livesheet.pdf_clef_recovery import recover

        shutil.copy2(folder / "candidate.mxl", folder / "pre-clef.mxl")
        save(folder / "pre-clef-mapping.json", updated)
        updated, recovered, diagnostic = run_stage(
            RepairStage("clefRecovery", "pdf_clef_recovery", "Printed clef changes"),
            folder / "pre-clef.mxl",
            folder / "candidate.mxl",
            pdf,
            updated,
            recover,
            decline_policy="abort",
        )
        stage_diagnostics["stages"].append(diagnostic)
        recovered["algorithmSHA256"] = sha(ROOT / "livesheet/pdf_clef_recovery.py")
        report["clefRecovery"] = recovered
        save(folder / "mapping.json", updated)
        save(folder / "clef-recovery.json", recovered)
    from livesheet.pdf_pedal_recovery import recover as recover_pedal

    shutil.copy2(folder / "candidate.mxl", folder / "pre-pedal.mxl")
    updated, pedal, diagnostic = run_stage(
        RepairStage("pedalRecovery", "pdf_pedal_recovery", "Printed pedal spans"),
        folder / "pre-pedal.mxl",
        folder / "candidate.mxl",
        pdf,
        updated,
        recover_pedal,
        decline_policy="abort",
    )
    stage_diagnostics["stages"].append(diagnostic)
    save(folder / "stages.json", stage_diagnostics)
    report["pedalRecovery"] = pedal
    save(folder / "mapping.json", updated)
    save(folder / "pedal-recovery.json", pedal)
    report.update(
        sourceSHA256=sha(pdf),
        originalMusicXMLSHA256=sha(source),
        candidateSHA256=sha(folder / "candidate.mxl"),
        algorithmSHA256=sha(ROOT / "livesheet/pdf_rhythm_repair.py"),
        evidenceSHA256=sha(ROOT / "livesheet/pdf_rhythm_evidence.py"),
    )
    save(folder / "repair.json", report)
    return report


def fingerprint():
    names = [
        "pdf_pipeline/__init__.py",
        "pdf_pipeline/config.py",
        "pdf_pipeline/stages.py",
        "pdf_pipeline/artifacts.py",
        "pdf_pipeline/cache.py",
        "pdf_simple_recovery.py",
        "pdf_terminal_recovery.py",
        "pdf_ledger_recovery.py",
        "pdf_dynamic_recovery.py",
        "pdf_attribute_recovery.py",
        "pdf_pedal_recovery.py",
        "pdf_music_fonts.py",
        "pdf_staff_evidence.py",
        "pdf_experimental.py",
        "pdf_rhythm_repair.py",
        "pdf_rhythm_evidence.py",
        "pdf_repeat_recovery.py",
        "pdf_whole_recovery.py",
        "pdf_context_recovery.py",
        "pdf_fermata_recovery.py",
        "pdf_articulation_recovery.py",
        "pdf_tempo_recovery.py",
        "pdf_tuplet_recovery.py",
        "pdf_vector_recovery.py",
        "pdf_clef_recovery.py",
        "pdf_octave_recovery.py",
        "pdf_reference.py",
        "audit_pdf_recognition.py",
        "worker.py",
        "playback.py",
        "companion.py",
        "score_recognition.py",
        "recognition.py",
        "audiveris_export_recovery.py",
        "vendor/audiveris/PartwiseBuilder.java",
        "pdf_ocr_guard.py",
        "vendor/audiveris/TesseractOCR.java",
        "vendor/audiveris/PrintedRestGuard.java",
    ]
    return {name: sha(Path(__file__).parent / name) for name in names}


def changed_measures(report):
    return sorted(
        {
            m["measureIndex"]
            for m in report["accepted"]
            + report.get("vectorRecovery", {}).get("accepted", [])
            + report.get("attributeRecovery", {}).get("accepted", [])
            + report.get("ledgerRecovery", {}).get("accepted", [])
            + report.get("simpleRecovery", {}).get("accepted", [])
            + report.get("dynamicRecovery", {}).get("accepted", [])
            + report.get("articulationRecovery", {}).get("accepted", [])
            + report.get("pedalRecovery", {}).get("accepted", [])
            + report.get("wholeRecovery", {}).get("accepted", [])
            + report.get("contextRecovery", {}).get("accepted", [])
            + report.get("fermataRecovery", {}).get("accepted", [])
            + report.get("tempoRecovery", {}).get("accepted", [])
            + report.get("tupletRecovery", {}).get("accepted", [])
        }
        | {c["measureIndex"] for c in report.get("clefRecovery", {}).get("changes", [])}
    )


def check_change_scope(source, target, allowed):
    """Independent XML reader catches unexplained changes outside accepted bars."""
    from collections import Counter
    from livesheet.pdf_reference import read_score, signature

    before, after = read_score(source), read_score(target)
    if len(before["parts"]) != len(after["parts"]):
        raise ValueError("repair-changed-parts")
    for a, b in zip(before["parts"], after["parts"]):
        if len(a["measures"]) != len(b["measures"]):
            raise ValueError("repair-changed-measures")
        for i, (x, y) in enumerate(zip(a["measures"], b["measures"])):
            if x["repeatMarks"] != y["repeatMarks"]:
                raise ValueError("repair-changed-repeats")
            if i not in allowed and (
                Counter(map(signature, x["notes"]))
                != Counter(map(signature, y["notes"]))
                or x["extent"] != y["extent"]
            ):
                raise ValueError("repair-outside-declared-measures")


def prepare_candidate(pdf, mapping, folder, progress=lambda stage: None):
    """Build a separate candidate. Exceptions leave the baseline untouched."""
    from livesheet.audit_pdf_recognition import profile
    from livesheet.pdf_octave_recovery import recover as recover_octaves

    if len(mapping["musicxmlFiles"]) != 1:
        raise Decline("multiple-movements-not-supported")
    source = Path(mapping["musicxmlFiles"][0])
    root, _ = load_mxl(source)
    if len(root.findall("part")) != 1:
        raise Decline("multiple-parts-not-supported")
    folder.mkdir(parents=True, exist_ok=True)
    save(folder / "baseline-mapping.json", mapping)
    progress("repairing")
    report = candidate(source, pdf, mapping, folder, vector=True, clefs=True)
    check_change_scope(source, folder / "candidate.mxl", set(changed_measures(report)))
    updated = json.loads((folder / "mapping.json").read_text())
    written = folder / "candidate.mxl"
    progress("checking_octaves")
    octave = dict(status="declined", changes=[])
    delivery = folder / "delivery"
    delivery.mkdir(exist_ok=True)
    try:
        updated, octave = recover_octaves(
            written, delivery / "sounding.mxl", pdf, updated
        )
        octave["status"] = "applied"
    except Decline as error:
        octave["reason"] = str(error)
    save(delivery / "mapping.json", updated)
    before = profile(folder / "baseline-mapping.json")[0]
    after = profile(delivery / "mapping.json")[0]
    # These are suspicion flags, not a measure of musical accuracy.
    if {m["index"] for m in after["overfullMeasures"]} - {
        m["index"] for m in before["overfullMeasures"]
    }:
        raise ValueError("repair-introduced-overfull-measure")
    if after["unmappedNotes"] > before["unmappedNotes"]:
        raise ValueError("repair-increased-unmapped-notes")
    quality = dict(
        profile=PROFILE,
        algorithmSHA256=fingerprint(),
        status="candidate",
        musicalReview="unreviewed",
        sourceSHA256=sha(pdf),
        changedMeasureIndices=sorted(
            set(changed_measures(report))
            | {c["measureIndex"] for c in octave["changes"]}
        ),
        before={k: v for k, v in before.items() if k != "path"},
        after={k: v for k, v in after.items() if k != "path"},
        rhythm=report,
        octaves=octave,
        checks=dict(
            changeScope=True, noNewOverfullMeasures=True, noMoreUnmappedNotes=True
        ),
        scope="Structural and source-evidence checks only; not an accuracy certificate.",
    )
    save(folder / "quality.json", quality)
    return updated, delivery, quality
