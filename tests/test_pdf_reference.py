import copy
import json
from pathlib import Path
import tempfile
import unittest
from livesheet.pdf_reference import read_score, compare, signature
from livesheet.testing.pdf_reference_fixtures import FIXTURES, create_fixture
from livesheet.testing.prepare_pdf_reference_set import validate_splits
CONFIG = Path(__file__).parent / "data/pdf-quality/reference-set-v1.json"


class ReferenceTests(unittest.TestCase):
    def fixture(self, ident='dotted-overlap'):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.musicxml'
            expected=create_fixture(ident,path)
            return expected,read_score(path)

    def test_declared_answers_match_all_fixtures(self):
        for ident in FIXTURES:
            with self.subTest(ident=ident):
                expected,actual=self.fixture(ident)
                self.assertFalse(compare(expected,actual)['differences'])

    def test_wrong_rhythm_is_not_hidden_by_correct_pitches_or_counts(self):
        expected,actual=self.fixture()
        actual['parts'][0]['measures'][0]['notes'][2]['onset']='5/2'
        report=compare(expected,actual)
        self.assertEqual(report['metrics']['exactWrittenEvents']['missingOrChanged'],1)
        self.assertEqual(report['metrics']['exactWrittenEvents']['extraOrChanged'],1)
        self.assertEqual(report['differences'][0]['measureIndex'],0)

    def test_duplicates_and_missing_final_measure_are_counted(self):
        expected,actual=self.fixture('cross-bar-ties')
        actual['parts'][0]['measures'].pop()
        notes=actual['parts'][0]['measures'][0]['notes']
        notes.append(copy.deepcopy(notes[0]))
        report=compare(expected,actual)
        self.assertEqual(report['metrics']['exactWrittenEvents']['extraOrChanged'],1)
        self.assertTrue(report['differences'][-1]['missingMeasure'])
        self.assertEqual(report['recognizedPartMeasures'],1)

    def test_ties_and_staff_matter_voice_number_does_not(self):
        expected,actual=self.fixture()
        note=actual['parts'][0]['measures'][0]['notes'][2]
        note['voice']='99'
        self.assertFalse(compare(expected,actual)['differences'])
        note['ties']=[]
        self.assertEqual(compare(expected,actual)['metrics']['exactWrittenEvents']['missingOrChanged'],1)
        note['staff']=2
        self.assertEqual(compare(expected,actual)['metrics']['pitchAndOnset']['missingOrChanged'],1)

    def test_pickup_tuplets_and_repeat_marks(self):
        expected,actual=self.fixture('pickup-meter-change')
        self.assertEqual(actual['parts'][0]['measures'][0]['extent'],'1')
        self.assertTrue(actual['parts'][0]['measures'][0]['implicit'])
        _,triplets=self.fixture('tuplets-and-rests')
        self.assertEqual(triplets['parts'][0]['measures'][0]['notes'][2]['onset'],'2/3')
        expected,actual=self.fixture('repeat-endings')
        actual['parts'][0]['measures'][1]['repeatMarks']=[]
        report=compare(expected,actual)
        self.assertEqual(report['metrics']['exactWrittenEvents']['recall'],1)
        self.assertFalse(report['differences'][0]['repeatMarksMatch'])

    def test_namespace_division_changes_chords_and_grace(self):
        xml='''<score-partwise xmlns="urn:test"><part id="P1"><measure number="1">
        <attributes><divisions>2</divisions></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration></note>
        <note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration></note>
        <attributes><divisions>4</divisions></attributes>
        <note><grace/><pitch><step>F</step><octave>4</octave></pitch></note>
        <note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration></note>
        </measure></part></score-partwise>'''
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'score.xml';path.write_text(xml)
            notes=read_score(path)['parts'][0]['measures'][0]['notes']
        self.assertEqual([n['onset'] for n in notes],['0','0','1','1'])
        self.assertEqual([n['duration'] for n in notes],['1','1','0','1'])
        self.assertTrue(notes[2]['grace'])

    def test_fraction_normalization(self):
        note=dict(midi=60,onset=.5,duration=1,ties=[])
        self.assertEqual(signature(note),signature({**note,'onset':'1/2'}))

    @unittest.skipUnless(CONFIG.exists(), "private reference manifest not supplied")
    def test_split_leakage_fails(self):
        config=json.loads(CONFIG.read_text());validate_splits(config)
        config['pieces'][0]['family']=config['pieces'][-1]['family']
        with self.assertRaises(ValueError): validate_splits(config)

    def test_unsupported_notation_fails_closed(self):
        expected,actual=self.fixture();actual['unsupported']=['unpitched']
        with self.assertRaises(ValueError):compare(expected,actual)


class InvalidInputTests(unittest.TestCase):
    def test_zero_divisions_and_negative_cursor_are_rejected(self):
        for content in ['<attributes><divisions>0</divisions></attributes>',
                        '<attributes><divisions>1</divisions></attributes><backup><duration>1</duration></backup>']:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'bad.xml'
                path.write_text('<score-partwise><part><measure>'+content+'</measure></part></score-partwise>')
                with self.assertRaises(ValueError):read_score(path)

    def test_compressed_musicxml_container(self):
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.musicxml';expected=create_fixture('cross-bar-ties',path)
            mxl=Path(temp)/'source.mxl'
            with ZipFile(mxl,'w') as archive:
                archive.writestr('META-INF/container.xml','<container xmlns="urn:test"><rootfiles><rootfile full-path="nested/score.xml"/></rootfiles></container>')
                archive.writestr('nested/score.xml',path.read_bytes())
            self.assertFalse(compare(expected,read_score(mxl))['differences'])

if __name__=='__main__': unittest.main()
