"""Upload regressions: oversized page geometry and synthetic accompaniment."""
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import pymupdf
from livesheet.pdf_preflight import inspect_pdf, processing_copy, processing_scale
from livesheet.playback import written_notes_source


class PreparationReliabilityTests(unittest.TestCase):
    def test_large_rotated_crop_is_scaled_without_changing_original(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = [Path(folder) / n for n in ('source.pdf', 'score.pdf')]
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=3000, height=4500)
                page.insert_text((400, 700), 'Original music', fontsize=80)
                page.set_cropbox(pymupdf.Rect(100, 200, 2900, 4300))
                page.set_rotation(90)
                pdf.save(source)
            original = source.read_bytes()
            self.assertEqual(inspect_pdf(source, Path(folder) / 'preview.webp'), 1)
            self.assertTrue(processing_copy(source, target))
            self.assertEqual(source.read_bytes(), original)
            with pymupdf.open(source) as before, pymupdf.open(target) as after:
                a, b = before[0], after[0]
                self.assertAlmostEqual(a.rect.width/a.rect.height, b.rect.width/b.rect.height, places=5)
                self.assertLess(b.rect.width*b.rect.height*(400/72)**2, 20_000_000)
                # The displayed crop and rotation must match, not just aspect ratio.
                x = a.get_pixmap(matrix=pymupdf.Matrix(processing_scale(a.rect), processing_scale(a.rect)))
                y = b.get_pixmap()
                self.assertEqual((x.width, x.height), (y.width, y.height))
                from PIL import ImageChops, ImageStat
                diff = ImageStat.Stat(ImageChops.difference(x.pil_image(), y.pil_image()))
                self.assertLess(max(diff.mean), 1)
                self.assertIn('Original music', b.get_text())

    def test_normal_pages_remain_byte_identical_and_extreme_pages_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = [Path(folder) / n for n in ('source.pdf', 'score.pdf')]
            with pymupdf.open() as pdf:
                pdf.new_page(); pdf.save(source)
            self.assertFalse(processing_copy(source, target))
            self.assertEqual(source.read_bytes(), target.read_bytes())
            source.unlink()
            with pymupdf.open() as pdf:
                pdf.new_page(width=15000, height=20000); pdf.save(source)
            with self.assertRaisesRegex(ValueError, 'pdf_page_size_limit'): inspect_pdf(source)

    def test_only_harmony_is_removed_from_export_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = [Path(folder) / n for n in ('score.mxl', 'written.musicxml')]
            xml = '<score-partwise><part><measure><harmony><root><root-step>A</root-step></root><kind>major</kind></harmony><direction><direction-type><dynamics><p/></dynamics></direction-type></direction><note><pitch><step>A</step><octave>3</octave></pitch><duration>4</duration><tie type="start"/></note></measure></part></score-partwise>'
            with ZipFile(source, 'w') as archive:
                archive.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="music.xml"/></rootfiles></container>')
                archive.writestr('music.xml', xml)
            original = source.read_bytes()
            written_notes_source(source, target)
            result = ET.parse(target)
            self.assertIsNone(result.find('.//harmony'))
            self.assertEqual(ET.tostring(result.find('.//note')), ET.tostring(ET.fromstring(xml).find('.//note')))
            self.assertIsNotNone(result.find('.//dynamics/p'))
            self.assertEqual(source.read_bytes(), original)

if __name__ == '__main__': unittest.main()
