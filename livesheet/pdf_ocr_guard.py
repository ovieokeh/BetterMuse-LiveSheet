"""Keep source-proven eighth rests out of OCR's isolated-'7' text guesses.

Only embedded, explicitly supported music encodings provide evidence. Scans,
unknown fonts, rotated pages and overlapping printed text are left unchanged.
"""

import hashlib
import json
import os
from pathlib import Path
import pymupdf

from livesheet.pdf_music_fonts import SMUFL_FONTS, encoded_music_spans
from livesheet.pdf_pipeline.config import AudiverisRuntime
from livesheet.audiveris_export_recovery import build_overlay

ROOT = Path(__file__).resolve().parent
SOURCES = tuple(
    ROOT / "vendor/audiveris" / name
    for name in ("TesseractOCR.java", "PrintedRestGuard.java")
)
RESTS = {"MScore": 0xE109, **{font: 0xE4E6 for font in SMUFL_FONTS}}


def fingerprint():
    return hashlib.sha256(
        b"".join(
            p.read_bytes()
            for p in (Path(__file__), ROOT / "pdf_music_fonts.py", *SOURCES)
        )
    ).hexdigest()


def page_rests(page, embedded):
    if (
        page.rotation
        or page.rect.tl != pymupdf.Point(0, 0)
        or page.cropbox != page.mediabox
    ):
        return []
    modern = any(
        s["font"] == "MScore3"
        for s in encoded_music_spans(page.get_text("rawdict")["blocks"])
    )
    candidates, text = [], []
    for span in page.get_texttrace():
        font = "MScore3" if modern and span["font"] == "MScore" else span["font"]
        if (
            tuple(span["dir"]) != (1.0, 0.0)
            or span.get("type") != 0
            or span.get("opacity", 1) != 1
        ):
            continue
        for code, glyph, origin, box in span["chars"]:
            rect = pymupdf.Rect(box)
            if glyph > 0 and span["font"] in embedded and code == RESTS.get(font):
                if rect.is_empty or not page.rect.contains(rect):
                    continue
                candidates.append(dict(font=font, code=code, box=list(rect)))
            elif code >= 32 and not chr(code).isspace() and font not in RESTS:
                text.append(rect)
    # Source text at the same location makes a decision ambiguous. Preserve OCR.
    return [
        c
        for c in candidates
        if not any((pymupdf.Rect(c["box"]) & t).get_area() > 0 for t in text)
    ]


def inspect(pdf):
    pdf = Path(pdf)
    pages = []
    with pymupdf.open(pdf) as document:
        for number, page in enumerate(document, 1):
            embedded = {
                f[3].split("+")[-1]
                for f in page.get_fonts()
                if f[1] != "n/a" and document.extract_font(f[0])[3]
            }
            for rest in page_rests(page, embedded):
                x0, y0, x1, y1 = rest.pop("box")
                pages.append(
                    dict(
                        page=number,
                        **rest,
                        bounds=[
                            x0 / page.rect.width,
                            y0 / page.rect.height,
                            (x1 - x0) / page.rect.width,
                            (y1 - y0) / page.rect.height,
                        ]
                    )
                )
    return dict(
        version="printed-eighth-rest-ocr-v1",
        sourceSHA256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        adapterSHA256=fingerprint(),
        rests=pages,
    )


def command(launcher, evidence, folder):
    """Build a process-local recognition overlay only when the PDF proves rests."""
    if not evidence["rests"]:
        return [str(launcher)], {}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "printed-rests.tsv"
    rows = ["# sourceSHA256=" + evidence["sourceSHA256"]]
    rows += [
        "\t".join([str(r["page"]), *(format(v, ".17g") for v in r["bounds"])])
        for r in evidence["rests"]
    ]
    path.write_text("\n".join(rows) + "\n")
    (folder / "printed-rests.json").write_text(json.dumps(evidence, indent=2) + "\n")
    runtime = AudiverisRuntime.from_launcher(launcher)
    classes = build_overlay(launcher, sources=SOURCES, cache_name="ocr-guard")
    return [
        str(runtime.java),
        "-Xmx2g",
        "-XX:ActiveProcessorCount=2",
        "--add-exports=java.desktop/sun.awt.image=ALL-UNNAMED",
        "--enable-native-access=ALL-UNNAMED",
        "-cp",
        os.pathsep.join([str(classes), str(runtime.jar.parent / "*")]),
        "Audiveris",
    ], {"BETTERMUSE_PRINTED_RESTS": str(path.resolve())}
