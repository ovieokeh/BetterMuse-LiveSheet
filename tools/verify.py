#!/usr/bin/env python3
"""Run the installed container without a source mount or reference-data access."""

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livesheet.testing.pdf_performance_reference import compare, read_midi
from livesheet.pdf_pipeline.artifacts import verify_result
from livesheet.testing.pdf_reference_validation import classify_references, verification_summary, diagnostic_outcome

ROOT = Path(__file__).resolve().parents[1]


def load(p):
    return json.loads(p.read_text())


def save(p, d):
    p.write_text(json.dumps(d, indent=2) + "\n")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def container(image, storage):
    return [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cpus",
        "2",
        "--memory",
        "4g",
        "--pids-limit",
        "512",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev,size=512m",
        "--mount",
        f"type=bind,src={storage},dst=/data",
        image,
    ]


@contextmanager
def pinned_image(name):
    # A temporary tag keeps Docker's immutable manifest available if a developer
    # rebuilds the usual local tag while a long benchmark is still running.
    pin = "livesheet-verification:" + uuid.uuid4().hex
    subprocess.run(["docker", "image", "tag", name, pin], check=True)
    try:
        identity = subprocess.check_output(
            ["docker", "image", "inspect", pin, "--format", "{{.Id}}"], text=True
        ).strip()
        yield identity
    finally:
        subprocess.run(
            ["docker", "image", "rm", pin],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", default="bettermuse-livesheet:local")
    p.add_argument("--private", type=Path)
    p.add_argument("--ids", nargs="+")
    p.add_argument("--output", type=Path, default=ROOT / "artifacts/verification")
    args = p.parse_args()
    with pinned_image(args.image) as image:
        return verify(args, image)


def verify(args, image):
    out = args.output.resolve()
    storage = out / "jobs"
    storage.mkdir(parents=True, exist_ok=True)
    cases = []
    references = {}
    for corpus in [ROOT / "tests/corpus"] + (
        [args.private.resolve()] if args.private else []
    ):
        entries = load(corpus / "manifest.json")["entries"]
        for entry in entries:
            folder = corpus / entry["id"]
            for name, digest in entry["hashes"].items():
                if sha(folder / name) != digest:
                    raise ValueError("Reference changed: " + str(folder / name))
            cases.append((entry, folder))
        if set(references) & {e["id"] for e in entries}:
            raise ValueError("Duplicate reference IDs")
        references.update(classify_references(entries, corpus))
    if args.ids:
        assert set(args.ids) <= {e["id"] for e, f in cases}, "Unknown case"
        cases = [(e, f) for e, f in cases if e["id"] in args.ids]
    rows = []
    report = dict(
        complete=False, image=image, sourceMounted=False, network="none", rows=rows
    )
    for entry, folder in cases:
        ident = entry["id"]
        job = storage / ident
        job.mkdir(exist_ok=True)
        key = dict(image=image, source=sha(folder / "score.pdf"))
        stamp = job / "verification-inputs.json"
        started = time.monotonic()
        reusable = stamp.exists() and load(stamp) == key
        if reusable:
            try:
                verify_result(job)
            except Exception:
                reusable = False
        row = dict(id=ident, image=image, reused=reusable, reference=references[ident])
        try:
            if not reusable:
                shutil.copy2(folder / "score.pdf", job / "source.pdf")
                save(stamp, key)
                with (job / "container.log").open("w") as log:
                    subprocess.run(
                        container(image, storage)
                        + [
                            "worker",
                            "--job-dir",
                            "/data/" + ident,
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=2500,
                        check=True,
                    )
            verify_result(job)
            actual = load(job / "score.json")
            quality = load(job / "quality.json")
            midi = job / (
                "experimental/performance/score/score.mid"
                if quality["status"] in ("applied", "unchanged")
                else "performance/score/score.mid"
            )
            actual["tempos"] = read_midi(midi)["tempos"]
            row["baseline"] = compare(load(folder / "baseline.json"), actual)
            row["originalReference"] = compare(
                load(folder / "reference-performance.json"), actual
            )
            if (folder / "printed-reference.json").exists():
                row["printedReference"] = compare(
                    load(folder / "printed-reference.json"), actual
                )
            if entry["family"] == "private-reviewed":
                keys = ("midi", "start", "duration", "velocity", "measureId")
                expected = load(folder / "baseline.json")
                row["exactReviewedEvents"] = Counter(
                    tuple(n.get(k) for k in keys) for n in expected["notes"]
                ) == Counter(tuple(n.get(k) for k in keys) for n in actual["notes"])
            row["passed"] = (
                row["baseline"]["passed"]
                and row.get("printedReference", {"passed": True})["passed"]
                and row.get("exactReviewedEvents", True)
            )
        except Exception as error:
            row.update(passed=False, error=str(error))
        if row["reference"]["kind"] == "invalid-source-diagnostic":
            row["diagnosticComparisonPassed"] = row.pop("passed")
            row["diagnosticCheckPassed"] = diagnostic_outcome(
                row["reference"], row["diagnosticComparisonPassed"], job
            )
            row["passed"] = None
        row["seconds"] = round(time.monotonic() - started, 2)
        rows.append(row)
        save(out / "report.json", report)
        status = (f"diagnostic check: {row['diagnosticCheckPassed']}"
                  if row["passed"] is None else row["passed"])
        print(ident, status, row.get("error", ""), flush=True)
    report.update(
        complete=True,
        **verification_summary(rows),
    )
    save(out / "report.json", report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
