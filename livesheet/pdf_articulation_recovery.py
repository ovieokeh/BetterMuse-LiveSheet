"""Reject staccatos only where a visible printed word proves punctuation.

A final period in an embedded text font supplies identity. Its rendered ink
supplies position. No match, ambiguous ink, or missing note geometry keeps the
original articulation. Other articulation types are never changed.
"""

from copy import deepcopy
from pathlib import Path
import math
import pymupdf
from PIL import Image
from livesheet.pdf_music_fonts import HEADS
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl


def font_key(name):
    return name.split("+")[-1].replace(" ", "").replace("-", "")


def printed_periods(document, page):
    if page.rotation or page.cropbox != page.mediabox:
        return []
    embedded = {
        font_key(f[3])
        for f in page.get_fonts()
        if f[1] != "n/a" and document.extract_font(f[0])[3]
    }
    found = []
    for span in page.get_texttrace():
        if (
            font_key(span["font"]) not in embedded
            or span["font"] in HEADS
            or span.get("type") != 0
            or span.get("opacity", 1) != 1
            or tuple(span["dir"]) != (1.0, 0.0)
        ):
            continue
        chars = span["chars"]
        text = "".join(chr(c[0]) for c in chars)
        # An isolated dot is not proof of punctuation. Require a word.
        if len(chars) < 3 or not text.endswith(".") or not text[:-1].isalpha():
            continue
        _, glyph, origin, box = chars[-1]
        size = span["size"]
        if glyph <= 0 or size <= 0:
            continue
        clip = pymupdf.Rect(box)
        if not page.rect.contains(clip) or clip.is_empty:
            continue
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(4, 4),
            clip=clip,
            colorspace=pymupdf.csGRAY,
            alpha=False,
        )
        ink = (
            Image.frombytes("L", (pix.width, pix.height), pix.samples)
            .point(lambda value: 255 if value < 100 else 0)
            .getbbox()
        )
        if ink is None:
            continue
        x0, y0, x1, y1 = [
            (pix.x + ink[0]) / 4,
            (pix.y + ink[1]) / 4,
            (pix.x + ink[2]) / 4,
            (pix.y + ink[3]) / 4,
        ]
        # Decline clipping, adjacent letters, stems, and non-dot shapes.
        if (
            ink[0] == 0
            or ink[1] == 0
            or ink[2] == pix.width
            or ink[3] == pix.height
            or not 0.04 * size <= x1 - x0 <= 0.3 * size
            or not 0.04 * size <= y1 - y0 <= 0.3 * size
            or not 0.4 <= (x1 - x0) / (y1 - y0) <= 2.5
            or not origin[1] - 0.3 * size <= (y0 + y1) / 2 <= origin[1] + 0.1 * size
        ):
            continue
        found.append(dict(text=text, font=span["font"], box=[x0, y0, x1, y1]))
    return found


def repair_bar(measure, records, system, sx, sy, periods):
    new = deepcopy(measure)
    nodes = [n for n in new.findall("note") if n.find("pitch") is not None]
    if len(nodes) != len(records):
        raise Decline("punctuation-note-mapping-count")
    changes = []
    for node, record in zip(nodes, records):
        if not record.get("box") or not 1 <= record["staff"] <= len(system["staves"]):
            continue
        staff = system["staves"][record["staff"] - 1]
        top = staff["lines"][0][0][1] * sy
        gap = (staff["lines"][-1][0][1] * sy - top) / 4
        if gap <= 0:
            continue
        x = (record["box"][0] + record["box"][2] / 2) * sx
        for nt in node.findall("notations"):
            for arts in nt.findall("articulations"):
                for dot in arts.findall("staccato"):
                    try:
                        dx = x + float(dot.get("default-x", "0")) * gap / 10
                        dy = top - float(dot.attrib["default-y"]) * gap / 10
                    except (KeyError, ValueError):
                        continue
                    if not all(math.isfinite(v) for v in (dx, dy)):
                        continue
                    matches = [
                        p
                        for p in periods
                        if abs(dx - (p["box"][0] + p["box"][2]) / 2) < gap * 0.5
                        and abs(dy - (p["box"][1] + p["box"][3]) / 2) < gap * 0.3
                    ]
                    if len(matches) != 1:
                        continue
                    arts.remove(dot)
                    changes.append(
                        dict(
                            action="remove-text-period-staccato",
                            noteId=record["id"],
                            source=matches[0],
                        )
                    )
                if not len(arts):
                    nt.remove(arts)
            if not len(nt):
                node.remove(nt)
    return new, changes


def recover(source, target, pdf, mapping):
    root, name = load_mxl(source)
    parts = root.findall("part")
    if len(parts) != 1:
        raise Decline("punctuation-requires-one-part")
    report = dict(version="printed-punctuation-v1", accepted=[], declined=[])
    with pymupdf.open(pdf) as document:
        periods = [printed_periods(document, p) for p in document]
        for index, measure in enumerate(list(parts[0])):
            if measure.find(".//articulations/staccato") is None:
                continue
            bar = mapping["measures"][index]
            pi = bar["page"] - 1
            if not periods[pi]:
                continue
            page = mapping["pages"][pi]
            system = next(s for s in page["systems"] if s["id"] == bar["system"])
            records = [r for r in mapping["notes"] if r["measureIndex"] == index]
            try:
                new, changes = repair_bar(
                    measure,
                    records,
                    system,
                    document[pi].rect.width / page["width"],
                    document[pi].rect.height / page["height"],
                    periods[pi],
                )
                if changes:
                    parts[0].remove(measure)
                    parts[0].insert(index, new)
                    report["accepted"].append(dict(measureIndex=index, changes=changes))
            except Decline as error:
                report["declined"].append(dict(measureIndex=index, reason=str(error)))
    write_mxl(source, target, root, name)
    result = deepcopy(mapping)
    result["musicxmlFiles"] = [str(Path(target).resolve())]
    return result, report
