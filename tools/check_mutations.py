"""Require eight deliberately wrong real exports to fail the playback comparator.

Run in the test image. Read-only generated corpus: /opt/livesheet-tests/corpus.
Writable evidence: /data/mutations. Nothing is uploaded.
"""

import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from livesheet.testing.check_pdf_performance_mutations import CASES
from livesheet.testing.check_pdf_printed_performance import printed_notation
from livesheet.testing.pdf_performance_reference import compare, read_reference

CORPUS = Path("/opt/livesheet-tests/corpus")
OUTPUT = Path("/data/mutations")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    jobs, references = [], {}
    cases = CASES + [
        (name, "articulation-dynamics")
        for name in ("reversed-dynamics", "missing-forte", "late-forte")
    ]
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    from livesheet.testing.prepare_pdf_reference_set import sha

    for entry in manifest["entries"]:
        for name, digest in entry["hashes"].items():
            if sha(CORPUS / entry["id"] / name) != digest:
                raise ValueError("Reference changed")
    for name, family in cases:
        folder = CORPUS / (family + "--leland")
        reference = folder / "printed-reference.json"
        if not reference.exists():
            reference = folder / "reference-performance.json"
        references[name] = json.loads(reference.read_text())
        root = ET.parse(folder / "source.musicxml").getroot()
        measure = root.find("part/measure")
        if name == "late-opening-tempo":
            direction = measure.find("direction")
            measure.remove(direction)
            measure.insert(list(measure).index(measure.findall("note")[4]), direction)
        elif name in ("replayed-ties", "missing-fermata"):
            tags = {"tie", "tied"} if name == "replayed-ties" else {"fermata"}
            for parent in root.iter():
                for child in list(parent):
                    if child.tag in tags:
                        parent.remove(child)
        elif name in ("missing-pedal", "missing-dynamics"):
            tag = "pedal" if name == "missing-pedal" else "dynamics"
            for bar in root.findall("part/measure"):
                for direction in bar.findall("direction"):
                    if direction.find(".//" + tag) is not None:
                        bar.remove(direction)
        else:
            root, _ = printed_notation(root)
            second = root.findall("part/measure")[1]
            direction = next(
                d
                for d in second.findall("direction")
                if d.find(".//dynamics") is not None
            )
            if name == "reversed-dynamics":
                for node in root.findall(".//dynamics"):
                    node[0].tag = "f" if node[0].tag == "p" else "p"
            else:
                second.remove(direction)
                if name == "late-forte":
                    second.insert(
                        list(second).index(second.findall("note")[1]), direction
                    )
        source = OUTPUT / (name + ".musicxml")
        ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
        jobs.append(
            {
                "in": str(source),
                "out": [str(OUTPUT / (name + ext)) for ext in (".mid", ".mpos")],
            }
        )
    batch = OUTPUT / "job.json"
    batch.write_text(json.dumps(jobs))
    with (OUTPUT / "export.log").open("w") as log:
        subprocess.run(
            ["/usr/local/bin/mscore", "-j", str(batch)],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
    reports = []
    for name, _ in cases:
        actual = read_reference(OUTPUT / (name + ".mid"), OUTPUT / (name + ".mpos"))
        result = compare(references[name], actual)
        reports.append(
            dict(mutation=name, detected=not result["passed"], comparison=result)
        )
    (OUTPUT / "report.json").write_text(json.dumps(reports, indent=2))
    if not all(r["detected"] for r in reports):
        raise ValueError("A deliberate defect escaped detection")
    print("All eight real-export mutations detected")


if __name__ == "__main__":
    main()
