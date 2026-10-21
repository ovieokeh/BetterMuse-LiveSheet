"""Printed music may disambiguate OCR; an OCR guess alone never removes text."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pymupdf
from livesheet import pdf_ocr_guard as guard


class Page:
    cropbox = mediabox = pymupdf.Rect(0, 0, 100, 100)
    rotation = 0
    rect = pymupdf.Rect(0, 0, 100, 100)

    def __init__(self, font="MScore", code=0xE109, embedded=True):
        self.spans = [
            dict(
                font=font,
                dir=(1.0, 0.0),
                type=0,
                chars=[(code, 10, (20, 30), (20, 20, 25, 40))],
            )
        ]
        self.embedded = {font} if embedded else set()

    def get_texttrace(self):
        return self.spans

    def get_text(self, kind):
        return {
            "blocks": [
                {
                    "lines": [
                        {
                            "spans": [
                                dict(
                                    font=s["font"],
                                    chars=[{"c": chr(c[0])} for c in s["chars"]],
                                )
                                for s in self.spans
                            ]
                        }
                    ]
                }
            ]
        }


class OCRGuardTests(unittest.TestCase):
    def test_known_embedded_rest_is_evidence_but_unknown_or_unembedded_is_not(self):
        for font, code, embedded, count in [
            ("MScore", 0xE109, True, 1),
            ("Leland", 0xE4E6, True, 1),
            ("Unknown", 0xE109, True, 0),
            ("MScore", ord("7"), True, 0),
            ("MScore", 0xE109, False, 0),
        ]:
            with self.subTest(font=font, code=code, embedded=embedded):
                page = Page(font, code, embedded)
                self.assertEqual(len(guard.page_rests(page, page.embedded)), count)

    def test_rotated_hidden_or_missing_glyph_is_not_evidence(self):
        for edit in [
            lambda p: setattr(p, "rotation", 90),
            lambda p: setattr(p, "cropbox", pymupdf.Rect(10, 0, 100, 100)),
            lambda p: p.spans[0].update(opacity=0),
            lambda p: p.spans[0].update(dir=(0, 1)),
            lambda p: p.spans[0].update(type=3),
            lambda p: p.spans[0].update(
                chars=[(0xE109, 0, (20, 30), (20, 20, 25, 40))]
            ),
        ]:
            page = Page()
            edit(page)
            self.assertEqual(guard.page_rests(page, page.embedded), [])

    def test_printed_text_overlap_declines_but_distant_measure_number_survives(self):
        page = Page()
        number = dict(
            font="FreeSerif",
            dir=(1.0, 0.0),
            type=0,
            chars=[(ord("7"), 3, (20, 30), (20, 20, 25, 40))],
        )
        page.spans.append(number)
        self.assertEqual(guard.page_rests(page, page.embedded), [])
        number["chars"] = [(ord("7"), 3, (70, 30), (70, 20, 75, 40))]
        self.assertEqual(len(guard.page_rests(page, page.embedded)), 1)

    def test_no_evidence_keeps_normal_command_and_does_not_compile(self):
        with patch.object(guard, "build_overlay") as compile:
            self.assertEqual(
                guard.command(Path("/engine"), {"rests": []}, Path("/unused")),
                (["/engine"], {}),
            )
            compile.assert_not_called()

    def test_evidence_and_command_are_isolated_and_resolution_independent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            guard, "build_overlay", return_value=Path("/classes")
        ):
            root = Path(tmp)
            for name, page in [("a", 1), ("b", 2)]:
                evidence = dict(
                    sourceSHA256=name,
                    rests=[dict(page=page, bounds=[0.2, 0.2, 0.05, 0.2])],
                )
                command, env = guard.command(
                    Path("/engine/bin/Audiveris"), evidence, root / name
                )
                self.assertIn("Audiveris", command)
                data = Path(env["BETTERMUSE_PRINTED_RESTS"]).read_text()
                self.assertIn("# sourceSHA256=" + name, data)
                self.assertEqual(int(data.splitlines()[1].split("\t")[0]), page)
            self.assertNotEqual(
                (root / "a/printed-rests.tsv").read_text(),
                (root / "b/printed-rests.tsv").read_text(),
            )


if __name__ == "__main__":
    unittest.main()
