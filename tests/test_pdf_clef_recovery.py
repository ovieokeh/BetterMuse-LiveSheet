import json,copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from fractions import Fraction as F
from collections import Counter
import xml.etree.ElementTree as ET
from livesheet.pdf_clef_recovery import recover, timeline
from livesheet.pdf_vector_recovery import VectorRecovery
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
from livesheet.pdf_reference import read_score, signature

DATA=Path(__file__).parent/'data/pdf-quality/clef-recovery-fixtures.json'

@unittest.skipUnless(DATA.exists(), "private review fixtures not supplied")
class ClefRecoveryTests(unittest.TestCase):
    def cases(self):return json.loads(DATA.read_text())['cases']
    def run_case(self,case,mutate=None):
        c=copy.deepcopy(case);v=object.__new__(VectorRecovery);v.proof=object.__new__(PDFHeadEvidence)
        for k,val in c['proof'].items():setattr(v.proof,k,val)
        v.mapping=v.proof.mapping;v.symbols=c['symbols']
        root=ET.fromstring(c['xml'])
        if mutate:mutate(v,root)
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source.mxl';target=Path(d)/'fixed.mxl'
            with ZipFile(source,'w') as z:
                z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>');z.writestr('score.xml',ET.tostring(root))
            before=source.read_bytes()
            with patch('livesheet.pdf_clef_recovery.VectorRecovery',return_value=v):mapping,report=recover(source,target,Path('unused.pdf'),v.mapping)
            self.assertEqual(source.read_bytes(),before)
            actual=read_score(target)
            with ZipFile(target) as z:fixed=ET.fromstring(z.read('score.xml'))
        return mapping,report,actual,fixed
    def test_checked_notes_and_independent_controls(self):
        for case in self.cases():
            with self.subTest(case=case['label']):
                _,report,actual,_=self.run_case(case)
                self.assertIsNone(report['declined']);self.assertTrue(report['changes'])
                for got,want in zip(actual['parts'][0]['measures'],case['expected']):self.assertEqual(Counter(map(signature,got['notes'])),Counter(map(signature,want)))
    def test_mid_measure_boundary_and_carry_over(self):
        case=self.cases()[0];mapping,report,_,root=self.run_case(case)
        self.assertEqual(report['insertions'],[dict(measureIndex=0,staff=2,onset=.5,clef=('G','2'))])
        self.assertEqual(next(n['midi'] for n in mapping['notes'] if n['staff']==2 and n['measureIndex']==0 and n['onset']==0),39)
        self.assertIn((0,F(1,2),('G','2')),timeline(root)[0][2])
        for before,after in zip(case['proof']['mapping']['notes'],mapping['notes']):
            for key in ('box','onset','duration','voice','ties','id'):self.assertEqual(before[key],after[key])
    def test_missing_clef_never_guesses_change(self):
        def missing(v,r):v.symbols[0]=[s for s in v.symbols[0] if not (s['c']=='\ue19e' and s['x']>300 and s['y']>550)]
        _,report,_,_=self.run_case(self.cases()[0],missing);self.assertEqual(report['changes'],[])
    def test_duplicate_clef_declines(self):
        def duplicate(v,r):v.symbols[0]+=copy.deepcopy([s for s in v.symbols[0] if s['c']=='\ue19e' and s['x']>300 and s['y']>550])
        _,report,_,_=self.run_case(self.cases()[0],duplicate);self.assertEqual(report['changes'],[]);self.assertTrue(any(s['reason']=='duplicate-printed-clef' for s in report['scan']))
    def test_missing_accidental_declines(self):
        def missing(v,r):v.symbols[0]=[s for s in v.symbols[0] if not (s['c']=='\ue114' and s['x']>340 and s['y']>550)]
        _,report,_,_=self.run_case(self.cases()[0],missing);self.assertEqual(report['changes'],[]);self.assertIsNotNone(report['declined'])
    def test_bad_head_geometry_declines_whole_region(self):
        def wrong(v,r):next(n for n in v.mapping['notes'] if n['staff']==2 and n['onset']==1)['box'][1]+=8
        _,report,_,_=self.run_case(self.cases()[0],wrong);self.assertEqual(report['changes'],[]);self.assertIsNotNone(report['declined'])
    def test_correct_clefs_are_idempotent(self):
        for case in self.cases():
            mapping,_,_,root=self.run_case(case);corrected=copy.deepcopy(case);corrected['xml']=ET.tostring(root,encoding='unicode');corrected['proof']['mapping']=mapping
            _,report,_,_=self.run_case(corrected);self.assertEqual(report['changes'],[])
    def test_unsupported_transposition_declines(self):
        def transpose(v,r):ET.SubElement(r.find('part/measure/attributes'),'transpose')
        _,report,_,_=self.run_case(self.cases()[0],transpose);self.assertEqual(report['changes'],[]);self.assertIsNotNone(report['declined'])

    def test_repair_cannot_break_an_unresolved_tie(self):
        def broken(v,r):
            node=next(n for n in r.find('part/measure').findall('note') if n.findtext('staff')=='2' and n.findtext('pitch/step')=='B')
            node.insert(2,ET.Element('tie',type='start'))
            rec=next(n for n in v.mapping['notes'] if n['measureIndex']==0 and n['staff']==2 and n['onset']==.5);rec['ties']=['start']
        _,report,_,_=self.run_case(self.cases()[0],broken);self.assertEqual(report['changes'],[]);self.assertEqual(report['declined'],'clef-repair-would-break-tie')

if __name__=='__main__':unittest.main()
