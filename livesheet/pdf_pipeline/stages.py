"""Ordered repair stages with isolated proposals and explicit diagnostic outcomes.

A decline keeps the previous notation and mapping. Unexpected exceptions abort
candidate preparation; the caller's existing validated-baseline fallback applies.
Musical decisions stay in the individual repair modules.
"""

from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
import hashlib
import json
import shutil
import time

from livesheet.pdf_rhythm_repair import Decline, load_mxl


@dataclass(frozen=True)
class RepairStage:
    key: str
    module: str
    purpose: str
    requires: tuple[str, ...] = ()


# Deliberately explicit, not a dynamic plugin discovery mechanism. Order is part
# of the algorithm fingerprint. Later stages consume earlier accepted evidence.
STAGES = (
    RepairStage(
        "attributeRecovery", "pdf_attribute_recovery", "Printed opening meter and clefs"
    ),
    RepairStage(
        "simpleRecovery",
        "pdf_simple_recovery",
        "Complete printed rhythmic lanes",
        ("attributeRecovery",),
    ),
    RepairStage(
        "ledgerRecovery",
        "pdf_ledger_recovery",
        "Source-proven ledger pitches",
        ("simpleRecovery",),
    ),
    RepairStage(
        "wholeRecovery",
        "pdf_whole_recovery",
        "Missing isolated whole-note chords",
        ("ledgerRecovery",),
    ),
    RepairStage(
        "contextRecovery",
        "pdf_context_recovery",
        "Held voices and paired ties",
        ("wholeRecovery",),
    ),
    RepairStage(
        "fermataRecovery",
        "pdf_fermata_recovery",
        "Printed fermatas",
        ("contextRecovery",),
    ),
    RepairStage(
        "articulationRecovery",
        "pdf_articulation_recovery",
        "Printed text punctuation",
        ("fermataRecovery",),
    ),
    RepairStage(
        "dynamicRecovery",
        "pdf_dynamic_recovery",
        "Printed dynamic letters",
        ("articulationRecovery",),
    ),
    RepairStage(
        "tempoRecovery",
        "pdf_tempo_recovery",
        "Source-aligned tempo markings",
        ("dynamicRecovery",),
    ),
    RepairStage(
        "tupletRecovery",
        "pdf_tuplet_recovery",
        "Source-proven tuplets",
        ("tempoRecovery",),
    ),
)


def validate_order(stages):
    seen = set()
    for stage in stages:
        if stage.key in seen or not set(stage.requires) <= seen:
            raise ValueError("invalid-repair-stage-order: " + stage.key)
        seen.add(stage.key)


def notation_digest(path):
    # ZIP timestamps do not indicate a musical change.
    from xml.etree.ElementTree import tostring

    root, _ = load_mxl(path)
    return hashlib.sha256(tostring(root)).hexdigest()


def run_stage(
    stage, source, target, pdf, mapping, operation=None, *, decline_policy="keep"
):
    """Run one proposal without exposing mutable input state to the repair."""
    if decline_policy not in ("keep", "abort"):
        raise ValueError("invalid-stage-decline-policy")
    operation = operation or import_module("livesheet." + stage.module).recover
    if source.resolve() == target.resolve():
        raise ValueError("repair-stage-input-output-alias")
    original_bytes = source.read_bytes()
    original = deepcopy(mapping)
    started = time.monotonic()
    before = notation_digest(source)
    # A retry must not accept an output left behind by an earlier invocation.
    target.unlink(missing_ok=True)
    try:
        proposed, report = operation(source, target, pdf, deepcopy(original))
        if not isinstance(proposed, dict) or not isinstance(report, dict):
            raise ValueError("invalid-repair-stage-result: " + stage.key)
        after = notation_digest(target)  # Missing/corrupt output is not a decline.
        if len(proposed.get("measures", [])) != len(original.get("measures", [])):
            raise ValueError("repair-stage-changed-measure-inventory: " + stage.key)
        outcome = "applied" if before != after else "unchanged"
    except Decline as error:
        if decline_policy == "abort":
            raise
        shutil.copy2(source, target)
        proposed = original
        report = dict(accepted=[], declined=str(error))
        after = before
        outcome = "declined"
    finally:
        try:
            input_changed = source.read_bytes() != original_bytes
        except FileNotFoundError:
            input_changed = True
        if input_changed:
            from livesheet.pdf_pipeline.artifacts import atomic_bytes

            atomic_bytes(source, original_bytes)
            raise ValueError("repair-stage-mutated-input: " + stage.key)
    mapping_changed = {k: v for k, v in proposed.items() if k != "musicxmlFiles"} != {
        k: v for k, v in original.items() if k != "musicxmlFiles"
    }
    proposed["musicxmlFiles"] = [str(Path(target).resolve())]
    diagnostic = dict(
        stage=stage.key,
        purpose=stage.purpose,
        outcome=outcome,
        mappingChanged=mapping_changed,
        elapsedSeconds=round(time.monotonic() - started, 3),
        inputNotationSHA256=before,
        outputNotationSHA256=after,
        evidence=report,
    )
    return proposed, report, diagnostic


def run_stages(source, pdf, mapping, folder, stages=STAGES):
    validate_order(stages)
    current = deepcopy(mapping)
    reports = {}
    diagnostics = []
    for stage in stages:
        target = folder / (stage.key + ".mxl")
        current, report, diagnostic = run_stage(stage, source, target, pdf, current)
        reports[stage.key] = report
        diagnostics.append(diagnostic)
        (folder / (stage.key + ".json")).write_text(json.dumps(report, indent=2) + "\n")
        source = target
    (folder / "stages.json").write_text(
        json.dumps(dict(schemaVersion=1, stages=diagnostics), indent=2) + "\n"
    )
    return source, current, reports
