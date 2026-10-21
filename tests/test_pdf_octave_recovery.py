import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from livesheet.pdf_octave_recovery import segments, Decline

DATA=Path(__file__).parent/'data/pdf-quality/octave-line-fixture.json'

class Document(list):
    def __enter__(self):return self
    def __exit__(self,*args):pass

class Page:
    def __init__(self,data):self.data=data;self.rect=SimpleNamespace(width=data['width'],height=data['height'])
    def get_drawings(self):return self.data['drawings']
    def get_text(self,kind):return dict(blocks=[dict(lines=[dict(spans=self.data['spans'])])])

@unittest.skipUnless(DATA.exists(), "private review fixtures not supplied")
class OctaveRecoveryTests(unittest.TestCase):
    def fixture(self):return json.loads(DATA.read_text())
    def run_segments(self,data):
        with patch('livesheet.pdf_octave_recovery.pymupdf.open',return_value=Document(Page(p) for p in data['pages'])):
            return segments(Path('unused.pdf'),data['mapping'])
    def test_explicit_two_spans_eight_system_segments(self):
        spans=self.run_segments(self.fixture());self.assertEqual(len(spans),8)
        self.assertEqual([s['ends'] for s in spans],[False,False,False,False,True,False,False,True])
        self.assertEqual([s['page'] for s in spans],[4,4,4,5,5,5,5,5])
    def test_known_fonts_and_reversed_hook_drawing_preserve_segments(self):
        for font in ('Leland','Bravura','Petaluma'):
            d=self.fixture()
            for p in d['pages']:
                for span in p['spans']:span['font']=font
                for drawing in p['drawings']:
                    drawing['items']=[(item[0],item[2],item[1]) if item[0]=='l' and abs(item[1][0]-item[2][0])<.01 else item
                                      for item in drawing['items']]
            spans=self.run_segments(d)
            self.assertEqual(len(spans),8)
            self.assertEqual([s['ends'] for s in spans],[False,False,False,False,True,False,False,True])
    def test_tall_handwritten_text_box_uses_actual_adjacent_line_height(self):
        d=self.fixture()
        for p in d['pages']:
            for span in p['spans']:
                span['font']='Petaluma'
                for c in span['chars']:
                    c['bbox'][1]+=4;c['bbox'][3]+=4
        self.assertEqual(len(self.run_segments(d)),8)

    def test_missing_terminal_hook_declines(self):
        d=self.fixture();drawings=d['pages'][-1]['drawings']
        d['pages'][-1]['drawings']=[x for x in drawings if not any(i[0]=='l' and abs(i[1][0]-i[2][0])<.01 for i in x['items'])]
        with self.assertRaises(Decline):self.run_segments(d)
    def test_missing_continuation_label_declines(self):
        d=self.fixture();d['pages'][-1]['spans'].pop(0)
        with self.assertRaises(Decline):self.run_segments(d)
    def test_duplicate_line_declines(self):
        d=self.fixture();d['pages'][3]['drawings']*=2
        with self.assertRaises(Decline):self.run_segments(d)
    def test_unrecognized_font_is_not_interpreted(self):
        d=self.fixture()
        for p in d['pages']:
            for s in p['spans']:s['font']='Unknown'
        self.assertEqual(self.run_segments(d),[])
    def test_solid_line_is_not_an_octave_dashed_line(self):
        d=self.fixture()
        for p in d['pages']:
            for drawing in p['drawings']:drawing['dashes']='[] 0'
        with self.assertRaises(Decline):self.run_segments(d)

if __name__=='__main__':unittest.main()
