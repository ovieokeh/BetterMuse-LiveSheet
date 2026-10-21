from copy import deepcopy
import unittest
import xml.etree.ElementTree as E
import pymupdf
from livesheet.pdf_articulation_recovery import printed_periods, repair_bar
from livesheet.pdf_rhythm_repair import Decline


def fixture():
    measure = E.fromstring(
        '<measure><note><pitch><step>A</step><octave>3</octave></pitch><duration>2</duration><notations><articulations><staccato default-y="20"/><accent/></articulations></notations></note></measure>'
    )
    records = [dict(id="n1", staff=1, box=[100, 110, 10, 5])]
    system = dict(
        staves=[dict(lines=[[[0, y], [200, y]] for y in (120, 130, 140, 150, 160)])]
    )
    periods = [dict(text="dim.", font="Text", box=[104, 99, 106, 101])]
    return measure, records, system, periods


class PunctuationTests(unittest.TestCase):
    def test_only_matching_staccato_removed_input_other_articulations_unchanged(self):
        m, r, s, p = fixture()
        before = E.tostring(m)
        new, changes = repair_bar(m, r, s, 1, 1, p)
        self.assertEqual(len(changes), 1)
        self.assertIsNone(new.find(".//staccato"))
        self.assertIsNotNone(new.find(".//accent"))
        self.assertEqual(new.findtext("note/duration"), "2")
        self.assertEqual(E.tostring(m), before)
        self.assertEqual(repair_bar(new, r, s, 1, 1, p)[1], [])

    def test_genuine_or_unknown_position_staccatos_survive(self):
        for case in (
            "missing-y",
            "other-y",
            "other-x",
            "nan",
            "no-period",
            "duplicate",
            "missing-head",
        ):
            with self.subTest(case=case):
                m, r, s, p = fixture()
                dot = m.find(".//staccato")
                if case == "missing-y":
                    dot.attrib.pop("default-y")
                if case == "other-y":
                    dot.set("default-y", "40")
                if case == "other-x":
                    dot.set("default-x", "20")
                if case == "nan":
                    dot.set("default-y", "nan")
                if case == "no-period":
                    p = []
                if case == "duplicate":
                    p = p + deepcopy(p)
                if case == "missing-head":
                    r[0].pop("box")
                new, changes = repair_bar(m, r, s, 1, 1, p)
                self.assertEqual(E.tostring(new), E.tostring(m))
                self.assertEqual(changes, [])

    def test_mapping_mismatch_declines(self):
        m, r, s, p = fixture()
        with self.assertRaisesRegex(Decline, "mapping-count"):
            repair_bar(m, [], s, 1, 1, p)

    def test_visible_embedded_word_period_has_measured_ink(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=200)
            page.insert_font(
                fontname="EmbeddedText", fontbuffer=pymupdf.Font("tiit").buffer
            )
            page.insert_text((30, 50), "Dim.", fontname="EmbeddedText", fontsize=14)
            page.insert_text((30, 90), ".", fontname="EmbeddedText", fontsize=14)
            page.insert_text(
                (30, 130), "dim.", fontname="EmbeddedText", fontsize=14, fill_opacity=0
            )
            page.draw_circle((100, 100), 1, fill=(0, 0, 0))
            found = printed_periods(doc, page)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["text"], "Dim.")
            self.assertLess(found[0]["box"][2] - found[0]["box"][0], 4)
            page.set_rotation(90)
            self.assertEqual(printed_periods(doc, page), [])

    def test_unembedded_text_and_competing_ink_decline(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=200)
            page.insert_text((30, 50), "Dim.", fontname="heit", fontsize=14)
            self.assertEqual(printed_periods(doc, page), [])
            page.insert_font(
                fontname="EmbeddedText", fontbuffer=pymupdf.Font("tiit").buffer
            )
            page.insert_text((30, 100), "Dim.", fontname="EmbeddedText", fontsize=14)
            found = printed_periods(doc, page)
            self.assertEqual(len(found), 1)
            box = found[0]["box"]
            x = (box[0] + box[2]) / 2
            page.draw_line((x, 80), (x, 110), width=1)
            self.assertEqual(printed_periods(doc, page), [])


if __name__ == "__main__":
    unittest.main()
