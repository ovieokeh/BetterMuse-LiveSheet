"""Reconstruct simple one-lane piano bars from complete vector notation.

No duration is inferred from spacing. Heads, stems, flags/beams, dots and rests
supply exact durations; each staff must fill the printed meter. Ambiguous voices,
unknown music symbols and unproven curves decline. Original exports are retained.
"""

from copy import deepcopy
from fractions import Fraction as F
import xml.etree.ElementTree as E
import pymupdf
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl
from livesheet.pdf_music_fonts import SMUFL_FONTS, HEADS, CLEFS, ACCIDENTALS
from livesheet.pdf_clef_recovery import ClefEvidence, timeline, active
from livesheet.pdf_context_recovery import geometry, tie_curve, split_tie_curve
from livesheet.pdf_vector_recovery import verticals, beam_count
from livesheet.pdf_whole_recovery import remap

RESTS = {
    "\ue4e4": ("half", F(2)),
    "\ue4e5": ("quarter", F(1)),
    "\ue4e6": ("eighth", F(1, 2)),
}
FLAGS = {"\ue240": "up", "\ue241": "down"}
TYPES = {
    F(4): "whole",
    F(2): "half",
    F(1): "quarter",
    F(1, 2): "eighth",
    F(1, 4): "16th",
}


def meter_from_pdf(g):
    meters = []
    used = []
    for staff, (top, bottom) in enumerate(g["bounds"], 1):
        heads = [h for h in g["local"] if h["staff"] == staff]
        if not heads:
            raise Decline("simple-empty-staff")
        first = min(h["x"] for h in heads)
        ds = [
            s
            for s in g["symbols"]
            if s["font"] in SMUFL_FONTS
            and "\ue080" <= s["c"] <= "\ue089"
            and g["bar"]["left"] * g["sx"] < s["x"] < first
            and top - g["gap"] * 0.3 < s["y"] < bottom + g["gap"] * 0.4
        ]
        if not ds:
            meters.append(None)
            continue
        if len(ds) != 2:
            raise Decline("simple-meter-not-two-digits")
        ds.sort(key=lambda s: s["y"])
        a, b = ds
        if (
            a["font"] != b["font"]
            or abs((a["x"] + a["w"] / 2) - (b["x"] + b["w"] / 2)) > g["gap"] * 0.25
        ):
            raise Decline("simple-meter-not-aligned")
        # Digits have different baselines, especially in handwritten fonts.
        # Require separate upper/lower staff halves and agreement on both staffs.
        if (
            not -0.2 < (a["y"] - top) / g["gap"] < 1.3
            or not 2.6 < (b["y"] - top) / g["gap"] < 4.3
        ):
            raise Decline("simple-meter-placement")
        value = (ord(a["c"]) - 0xE080, ord(b["c"]) - 0xE080)
        if value[0] not in range(1, 10) or value[1] not in (2, 4, 8):
            raise Decline("simple-meter-value")
        meters.append(value)
        used += ds
    if meters[0] != meters[1]:
        raise Decline("simple-staff-meters-disagree")
    return meters[0], used


def decode(engine, index, measure, divisions, key, meter):
    g = geometry(engine, index)
    gap = g["gap"]
    left = g["bar"]["left"] * g["sx"]
    right = g["bar"]["right"] * g["sx"]
    if measure.get("implicit") == "yes":
        raise Decline("simple-pickup")
    if not meter:
        raise Decline("simple-meter-unknown")
    budget = F(meter[0] * 4, meter[1])
    used = set()
    groups = []
    lines = verticals(g["drawings"], gap)
    if any(h["font"] == "MScore3" for h in g["local"]):
        # Older PDF generators report unscaled stroke widths. Only the rest-only
        # path below may use these segments; existing note identities must agree.
        lines = verticals(
            [
                dict(d, width=min(float(d.get("width") or 0), gap * 0.2))
                for d in g["drawings"]
            ],
            gap,
        )
    local = [
        s for s in g["symbols"] if left < s["x"] < right and s["font"] in SMUFL_FONTS
    ]
    heads = sorted(g["local"], key=lambda h: (h["staff"], h["x"], h["y"]))
    if not heads or any(h["font"] not in SMUFL_FONTS for h in heads):
        raise Decline("simple-head-font")

    def sid(s):
        return (s["font"], s["c"], s["x"], s["y"])

    def consume(s):
        k = sid(s)
        if k in used:
            raise Decline("simple-symbol-used-twice")
        used.add(k)

    for h in heads:
        if not (
            (1.4 if h["kind"] == "whole" else 1.0) * gap
            < h["w"]
            < (2.0 if h["kind"] == "whole" else 1.6) * gap
        ):
            raise Decline("simple-cue-or-unsupported-head-size")
        consume(h)
        if h["kind"] == "whole":
            stem = None
            direction = None
            base = F(4)
        else:
            candidates = []
            for li, (x, y0, y1) in enumerate(lines):
                if y0 - gap * 0.3 < h["y"] < y1 + gap * 0.3:
                    if abs(x - h["x"]) < gap * 0.3 and y1 - h["y"] > 2 * gap:
                        candidates.append((li, "down"))
                    if abs(x - h["x"] - h["w"]) < gap * 0.3 and h["y"] - y0 > 2 * gap:
                        candidates.append((li, "up"))
            if len(candidates) != 1:
                raise Decline("simple-stem-ambiguous")
            stem, direction = candidates[0]
            base = F(2) if h["kind"] == "half" else F(1)
            x, y0, y1 = lines[stem]
            tip = y0 if direction == "up" else y1
            flags = [
                s
                for s in local
                if s["c"] in FLAGS
                and abs(s["x"] - x) < gap * 0.3
                and abs(s["y"] - tip) < gap * 0.4
            ]
            beams = beam_count(g["drawings"], lines[stem], direction, gap)
            if (
                len(flags) > 1
                or flags
                and (FLAGS[flags[0]["c"]] != direction or beams)
                or h["kind"] == "half"
                and (flags or beams)
            ):
                raise Decline("simple-flag-or-beam-conflict")
            if flags:
                consume(flags[0])
                base = F(1, 2)
            elif beams:
                base = F(1, 2**beams)
        dots = [
            s
            for s in local
            if s["c"] == "\ue1e7"
            and h["x"] + h["w"] - 0.1 * gap < s["x"] < h["x"] + h["w"] + gap
            and abs(s["y"] - h["y"]) < gap * 0.6
        ]
        if len(dots) > 1:
            raise Decline("simple-ambiguous-dot")
        for s in dots:
            consume(s)
        duration = base * (F(3, 2) if dots else 1)
        same = [
            a
            for a in groups
            if a["staff"] == h["staff"] and a["stem"] == stem and stem is not None
        ]
        if len(same) > 1:
            raise Decline("simple-chord-ambiguous")
        if same:
            a = same[0]
            if a["duration"] != duration or abs(a["x"] - h["x"]) > gap * 0.25:
                raise Decline("simple-chord-conflict")
            a["heads"].append(h)
        else:
            groups.append(
                dict(
                    staff=h["staff"],
                    x=h["x"],
                    heads=[h],
                    stem=stem,
                    direction=direction,
                    base=base,
                    dots=bool(dots),
                    duration=duration,
                )
            )
    middle = (g["bounds"][0][1] + g["bounds"][1][0]) / 2
    for s in local:
        if s["c"] not in RESTS:
            continue
        staff = 1 if s["y"] < middle else 2
        top, bottom = g["bounds"][staff - 1]
        if not top - gap * 0.2 < s["y"] < bottom + gap * 0.2:
            raise Decline("simple-rest-placement")
        consume(s)
        kind, duration = RESTS[s["c"]]
        groups.append(
            dict(
                staff=staff,
                x=s["x"],
                heads=[],
                stem=None,
                direction=None,
                base=duration,
                dots=False,
                duration=duration,
                rest=kind,
            )
        )
    for staff in (1, 2):
        lane = sorted((a for a in groups if a["staff"] == staff), key=lambda a: a["x"])
        cursor = F(0)
        if any(b["x"] - a["x"] < gap for a, b in zip(lane, lane[1:])):
            raise Decline("simple-multiple-voices")
        accidental_state = {}
        for a in lane:
            a["onset"] = cursor
            cursor += a["duration"]
            a["pitches"] = []
            a["records"] = []
            for h in sorted(a["heads"], key=lambda h: -h["y"]):
                box = [
                    h["x"] / g["sx"],
                    (h["y"] - gap / 2) / g["sy"],
                    h["w"] / g["sx"],
                    gap / g["sy"],
                ]
                rec = dict(
                    measureIndex=index,
                    staff=staff,
                    page=g["bar"]["page"],
                    system=g["bar"]["system"],
                    box=box,
                    normalizedBox=[
                        v / d
                        for v, d in zip(
                            box, [g["page"]["width"], g["page"]["height"]] * 2
                        )
                    ],
                    mapping="printed-simple-lane",
                    pdfGlyphID=f"p{g['bar']['page']}-{h['x']:.4f}-{h['y']:.4f}",
                )
                pitch = engine.pitch_at(rec, None, key, accidental_state)
                a["pitches"].append(pitch)
                a["records"].append(rec)
        if cursor != budget:
            raise Decline("simple-lane-does-not-fill-meter")
    for s in local:
        if sid(s) in used:
            continue
        if s["c"] in CLEFS.get(s["font"], {}) or s["c"] in ACCIDENTALS.get(
            s["font"], {}
        ):
            continue
        if "\ue080" <= s["c"] <= "\ue089" and s["x"] < min(h["x"] for h in heads):
            continue
        raise Decline("simple-unaccounted-music-symbol")
    if any((a["duration"] * divisions).denominator != 1 for a in groups):
        raise Decline("simple-division-not-representable")
    return dict(
        index=index,
        geo=g,
        groups=groups,
        meter=meter,
        budget=budget,
        divisions=divisions,
        key=key,
    )


def build_bar(measure, plan):
    if len(measure.findall("attributes")) > 1:
        raise Decline("simple-mid-bar-attributes")
    new = E.Element("measure", dict(measure.attrib))
    a = deepcopy(measure.find("attributes"))
    if a is None:
        a = E.Element("attributes")
    for n in list(a):
        if n.tag in ("time", "clef"):
            a.remove(n)
        elif n.tag not in ("divisions", "key", "staves", "staff-details"):
            raise Decline("simple-unsupported-attributes")
    if a.find("divisions") is None:
        E.SubElement(a, "divisions").text = str(plan["divisions"])
    time = E.SubElement(a, "time")
    E.SubElement(time, "beats").text = str(plan["meter"][0])
    E.SubElement(time, "beat-type").text = str(plan["meter"][1])
    if a.find("staves") is None:
        E.SubElement(a, "staves").text = "2"
    for staff in (1, 2):
        kinds = {
            p["clef"]
            for group in plan["groups"]
            if group["staff"] == staff
            for p in group["pitches"]
        }
        if len(kinds) != 1:
            raise Decline("simple-changing-clef")
        kind = kinds.pop()
        c = E.SubElement(a, "clef", number=str(staff))
        E.SubElement(c, "sign").text = kind[0]
        E.SubElement(c, "line").text = kind[1]
    new.append(a)
    cursor = F(0)
    for child in measure:
        if child.tag == "direction":
            if (
                cursor and id(child) not in plan.get("openingTempos", set())
            ) or child.find("offset") is not None:
                raise Decline("simple-timed-direction")
            new.append(deepcopy(child))
        elif child.tag == "print":
            new.insert(0, deepcopy(child))
        elif child.tag not in ("attributes", "note", "backup", "forward", "barline"):
            raise Decline("simple-unsupported-metadata")
        if (
            child.tag in ("forward", "backup")
            or child.tag == "note"
            and child.find("chord") is None
        ):
            cursor += (
                F(child.findtext("duration", "0"))
                / plan["divisions"]
                * (-1 if child.tag == "backup" else 1)
            )
        if child.tag == "note" and any(
            c.tag
            not in (
                "pitch",
                "rest",
                "chord",
                "duration",
                "voice",
                "type",
                "dot",
                "accidental",
                "stem",
                "staff",
                "beam",
                "tie",
                "notations",
            )
            for c in child
        ):
            raise Decline("simple-unsupported-note-detail")
        if child.tag == "note" and set(child.attrib) - {
            "default-x",
            "default-y",
            "print-object",
        }:
            raise Decline("simple-unsupported-note-attributes")
        if child.tag == "note" and child.get("print-object", "yes") != "yes":
            raise Decline("simple-hidden-note")
        if child.tag == "note" and (
            child.find("grace") is not None
            or child.find("time-modification") is not None
            or any(n.tag not in ("slur", "tied") for n in child.findall("notations/*"))
        ):
            raise Decline("simple-expressive-or-tuplet-note")
    records = []
    for staff in (1, 2):
        if staff == 2:
            E.SubElement(E.SubElement(new, "backup"), "duration").text = str(
                int(plan["budget"] * plan["divisions"])
            )
        for group in sorted(
            (g for g in plan["groups"] if g["staff"] == staff), key=lambda g: g["x"]
        ):
            group["nodes"] = []
            for j, pitch in enumerate(group["pitches"] or [None]):
                n = E.SubElement(
                    new,
                    "note",
                    {
                        "default-x": str(
                            (
                                group["x"] / plan["geo"]["sx"]
                                - plan["geo"]["bar"]["left"]
                            )
                            * 10
                            / (plan["geo"]["gap"] / plan["geo"]["sx"])
                        )
                    },
                )
                if j:
                    E.SubElement(n, "chord")
                if pitch:
                    p = E.SubElement(n, "pitch")
                    E.SubElement(p, "step").text = pitch["step"]
                    if pitch["alter"]:
                        E.SubElement(p, "alter").text = str(pitch["alter"])
                    E.SubElement(p, "octave").text = str(pitch["octave"])
                    records.append(group["records"][j])
                else:
                    E.SubElement(n, "rest")
                E.SubElement(n, "duration").text = str(
                    int(group["duration"] * plan["divisions"])
                )
                E.SubElement(n, "voice").text = str(staff)
                E.SubElement(n, "type").text = TYPES[group["base"]]
                if group["dots"]:
                    E.SubElement(n, "dot")
                if pitch and pitch["explicitAccidental"]:
                    E.SubElement(n, "accidental").text = {
                        -1: "flat",
                        0: "natural",
                        1: "sharp",
                    }[pitch["alter"]]
                if group["direction"]:
                    E.SubElement(n, "stem").text = group["direction"]
                E.SubElement(n, "staff").text = str(staff)
                group["nodes"].append(n)
    for b in measure.findall("barline"):
        if b.get("location") == "left":
            new.insert(list(new).index(new.find("note")), deepcopy(b))
        else:
            new.append(deepcopy(b))
    return new, records


def require_rest_only(engine, index, measure, plan):
    """Without a printed meter, only insert an omitted, explicitly printed rest.

    All existing pitched notes must match the source in pitch, value and chord.
    No pitches, durations, clefs or note counts may change on this path.
    """
    nodes = [n for n in measure.findall("note") if n.find("pitch") is not None]
    records = [r for r in engine.mapping["notes"] if r["measureIndex"] == index]
    checked = engine.vector.proof(index, nodes)
    if not checked.get("verified"):
        raise Decline("simple-rest-head-coverage")
    if not any(g.get("rest") for g in plan["groups"]):
        raise Decline("simple-no-printed-rest")
    if measure.findall("note/rest") or measure.findall("forward"):
        raise Decline("simple-rest-already-present")
    pairs = list(zip(nodes, records))
    used = set()
    moved = False
    for group in plan["groups"]:
        for pitch, rec in zip(group["pitches"], group["records"]):
            found = [
                (j, n, r)
                for j, (n, r) in enumerate(pairs)
                if r["staff"] == group["staff"]
                and abs(r["box"][0] - rec["box"][0])
                < plan["geo"]["gap"] / plan["geo"]["sx"] * 0.35
                and abs(
                    (r["box"][1] + r["box"][3] / 2)
                    - (rec["box"][1] + rec["box"][3] / 2)
                )
                < plan["geo"]["gap"] / plan["geo"]["sy"] * 0.3
            ]
            if len(found) != 1 or found[0][0] in used:
                raise Decline("simple-rest-head-identity")
            j, n, r = found[0]
            used.add(j)
            actual = (
                n.findtext("pitch/step"),
                int(n.findtext("pitch/alter", "0")),
                int(n.findtext("pitch/octave")),
            )
            if (
                actual != (pitch["step"], pitch["alter"], pitch["octave"])
                or F(n.findtext("duration")) / plan["divisions"] != group["duration"]
            ):
                raise Decline("simple-rest-note-conflict")
            if F(str(r["onset"])) != group["onset"]:
                moved = True
    if len(used) != len(nodes) or not moved:
        raise Decline("simple-rest-no-complete-change")
    for staff in (1, 2):
        voices = {n.findtext("voice", "1") for n, r in pairs if r["staff"] == staff}
        if len(voices) != 1:
            raise Decline("simple-rest-multiple-voices")


def opening_tempos(pdf, measure, plan):
    """An opening metronome may be displaced by false time-signature notes."""
    from livesheet.pdf_tempo_recovery import printed_tempos

    g = plan["geo"]
    anchors = [
        dict(x=a["x"], onset=a["onset"])
        for a in plan["groups"]
        if a["staff"] == 1 and a["heads"]
    ]
    with pymupdf.open(pdf) as doc:
        lines = [
            l
            for b in doc[g["bar"]["page"] - 1].get_text("dict")["blocks"]
            for l in b.get("lines", [])
        ]
    marks = printed_tempos(
        lines,
        left=g["bar"]["left"] * g["sx"],
        right=g["bar"]["right"] * g["sx"],
        top=g["bounds"][0][0],
        gap=g["gap"],
        anchors=anchors,
    )
    allowed = set()
    for d in measure.findall("direction"):
        metros = d.findall("direction-type/metronome")
        sounds = d.findall("sound")
        if len(metros) != 1 or len(sounds) != 1:
            continue
        if any(c.tag not in ("direction-type", "staff", "sound") for c in d) or any(
            c.tag not in ("metronome", "words")
            for dt in d.findall("direction-type")
            for c in dt
        ):
            continue
        if (
            [c.tag for c in metros[0]] != ["beat-unit", "per-minute"]
            or metros[0].findtext("beat-unit") != "quarter"
            or set(sounds[0].attrib) != {"tempo"}
        ):
            continue
        if d.get("placement") != "above" or d.findtext("staff", "1") != "1":
            continue
        value = metros[0].findtext("per-minute")
        if (
            sounds[0].get("tempo") == value
            and len([m for m in marks if str(m["bpm"]) == value and m["onset"] == 0])
            == 1
        ):
            allowed.add(id(d))
    return allowed


def without_false_header_slurs(measure, plan, mapping):
    """Drop a same-bar OMR slur attached to a false time-signature note.

    A real printed curve still blocks the whole bar in recover(). Neither end of a
    real tie/slur is removed merely because the recognizer gave it the wrong pitch.
    """
    new = deepcopy(measure)
    g = plan["geo"]
    _, digits = meter_from_pdf(g)
    nodes = [n for n in new.findall("note") if n.find("pitch") is not None]
    records = [r for r in mapping["notes"] if r["measureIndex"] == plan["index"]]
    if len(nodes) != len(records):
        raise Decline("simple-note-record-count")
    slurs = {}
    for n, r in zip(nodes, records):
        for slur in n.findall("notations/slur"):
            slurs.setdefault(slur.get("number", "1"), []).append((n, r, slur))
    for chain in slurs.values():
        if len(chain) != 2 or {s.get("type") for n, r, s in chain} != {"start", "stop"}:
            continue
        false = False
        for n, r, s in chain:
            if not r.get("box"):
                continue
            x, y, w, h = r["box"]
            x *= g["sx"]
            y = (y + h / 2) * g["sy"]
            if any(
                abs(head["x"] - x) < g["gap"] * 0.4
                and abs(head["y"] - y) < g["gap"] * 0.4
                for head in g["local"]
            ):
                continue
            if any(
                d["x"] - g["gap"] * 0.3 < x < d["x"] + d["w"] + g["gap"] * 0.3
                and abs(d["y"] - y) < g["gap"] * 2
                for d in digits
            ):
                false = True
        if false:
            for n, r, s in chain:
                n.find("notations").remove(s)
    # Keep the original direction objects' identity for the proven opening tempo.
    plan["openingTempos"] = {
        id(b) for a, b in zip(measure, new) if id(a) in plan.get("openingTempos", set())
    }
    return new


def recover(source, target, pdf, mapping):
    root, name = load_mxl(source)
    if len(root.findall("part")) != 1:
        raise Decline("simple-single-piano-part-required")
    with pymupdf.open(pdf) as doc:
        if any(p.get_images() for p in doc):
            raise Decline("simple-raster-content")
    part = root.find("part")
    engine = ClefEvidence(pdf, mapping)
    clefs, keys = timeline(root)
    report = dict(version="printed-simple-lanes-v1", accepted=[], declined=[])
    plans = {}
    divisions = F(1)
    meter = None
    for i, m in enumerate(part):
        if m.findtext("attributes/divisions"):
            divisions = F(m.findtext("attributes/divisions"))
        if m.find("attributes/time") is not None:
            try:
                meter = (
                    int(m.findtext("attributes/time/beats")),
                    int(m.findtext("attributes/time/beat-type")),
                )
            except (ValueError, TypeError):
                meter = None
        try:
            printed, _ = meter_from_pdf(geometry(engine, i))
            if printed:
                meter = printed
            plan = decode(engine, i, m, divisions, keys[i], meter)
            if not printed or any(h["font"] == "MScore3" for h in plan["geo"]["local"]):
                require_rest_only(engine, i, m, plan)
            plan["openingTempos"] = opening_tempos(pdf, m, plan)
            plan["input"] = without_false_header_slurs(m, plan, mapping)
            plan["new"], plan["records"] = build_bar(plan["input"], plan)
            plans[i] = plan
        except Decline as exc:
            report["declined"].append(dict(measureIndex=i, reason=str(exc)))
    # Curves are handled as paired, source-proven ties only. Any other nearby
    # curve blocks the bar; never silently drop a slur or an uncertain tie.
    blocked = set()
    covered = set()
    ties = []
    for i, a in plans.items():
        b = plans.get(i + 1)
        if not b or a["geo"]["bar"]["page"] != b["geo"]["bar"]["page"]:
            continue
        if (
            list(part)[i].find("barline[@location='right']/repeat") is not None
            or list(part)[i + 1].find("barline[@location='left']/repeat") is not None
        ):
            continue
        ga, gb = a["geo"], b["geo"]
        cross = ga["bar"]["system"] != gb["bar"]["system"]
        for staff in (1, 2):
            last = max(
                (g for g in a["groups"] if g["staff"] == staff),
                key=lambda g: g["onset"],
            )
            first = min(
                (g for g in b["groups"] if g["staff"] == staff),
                key=lambda g: g["onset"],
            )
            for ah, ap, an in zip(
                sorted(last["heads"], key=lambda h: -h["y"]),
                last["pitches"],
                last["nodes"],
            ):
                for bh, bp, bn in zip(
                    sorted(first["heads"], key=lambda h: -h["y"]),
                    first["pitches"],
                    first["nodes"],
                ):
                    if any(ap[k] != bp[k] for k in ("step", "octave", "alter")):
                        continue
                    if cross:
                        outs = [
                            (j, side)
                            for j, d in enumerate(ga["drawings"])
                            if (side := split_tie_curve(d, ah, ga, True))
                        ]
                        ins = [
                            (j, side)
                            for j, d in enumerate(gb["drawings"])
                            if (side := split_tie_curve(d, bh, gb, False))
                        ]
                        matches = (
                            [outs[0][0], ins[0][0]]
                            if len(outs) == len(ins) == 1 and outs[0][1] == ins[0][1]
                            else []
                        )
                    else:
                        matches = [
                            j
                            for j, d in enumerate(ga["drawings"])
                            if tie_curve(d, ah, bh, ga["gap"])
                        ]
                    if not matches or (not cross and len(matches) != 1):
                        continue
                    ids = {(ga["bar"]["page"], j) for j in matches}
                    if ids & covered:
                        blocked.update((i, i + 1))
                        continue
                    covered |= ids
                    ties.append((i, i + 1, an, bn))
    for i, p in plans.items():
        g = p["geo"]
        gap = g["gap"]
        for j, d in enumerate(g["drawings"]):
            if (g["bar"]["page"], j) in covered:
                continue
            if any(
                item[0] == "c"
                and any(
                    h["x"] - gap * 2 < pt[0] < h["x"] + h["w"] + gap
                    and abs(pt[1] - h["y"]) < gap * 1.5
                    for h in g["local"]
                    for pt in (item[1], item[-1])
                )
                for item in d["items"]
            ):
                blocked.add(i)
    # Every existing tie or slur endpoint needs a source-proven replacement.
    for i, p in plans.items():
        expected = sum(
            len(n.findall("tie"))
            or len(n.findall("notations/tied"))
            or len(n.findall("notations/slur"))
            for n in p["input"].findall("note")
        )
        proven = sum(a == i for a, b, _, _ in ties) + sum(b == i for a, b, _, _ in ties)
        if expected > proven:
            blocked.add(i)
    old = None
    while old != blocked:
        old = set(blocked)
        for a, b, _, _ in ties:
            if a in blocked or b in blocked:
                blocked.update((a, b))
    for a, b, an, bn in ties:
        if a in blocked or b in blocked:
            continue
        for n, kind in ((an, "start"), (bn, "stop")):
            n.insert(list(n).index(n.find("duration")) + 1, E.Element("tie", type=kind))
            E.SubElement(E.SubElement(n, "notations"), "tied", type=kind)
    origins = []
    original_times = {}
    current = None
    for i, m in enumerate(part):
        if m.find("attributes/time") is not None:
            current = deepcopy(m.find("attributes/time"))
        original_times[i] = deepcopy(current)
    restore_time = False
    for i, m in enumerate(list(part)):
        if i not in plans or i in blocked:
            if (
                restore_time
                and m.find("attributes/time") is None
                and original_times[i] is not None
            ):
                attrs = m.find("attributes")
                if attrs is None:
                    attrs = E.Element("attributes")
                    m.insert(0, attrs)
                attrs.append(original_times[i])
            restore_time = False
            origins.extend(r for r in mapping["notes"] if r["measureIndex"] == i)
            if i in blocked:
                report["declined"].append(
                    dict(measureIndex=i, reason="simple-unproven-curve")
                )
            continue
        restore_time = True
        p = plans[i]
        part.remove(m)
        part.insert(i, p["new"])
        origins += p["records"]
        report["accepted"].append(
            dict(
                measureIndex=i,
                printedHeads=len(p["geo"]["local"]),
                meter=p["meter"],
                sourceRests=sum(bool(g.get("rest")) for g in p["groups"]),
            )
        )
    write_mxl(source, target, root, name)
    return remap(target, mapping, origins), report
