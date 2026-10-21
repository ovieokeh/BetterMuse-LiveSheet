import copy
import json
from fractions import Fraction as F
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from livesheet.pdf_vector_recovery import VectorRecovery, Decline, printed_tie
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
from livesheet.pdf_reference import read_score, signature

DATA=Path(__file__).parent/'data/pdf-quality'


@unittest.skipUnless(DATA.exists(), "private review fixtures not supplied")
class VectorRecoveryTests(unittest.TestCase):
    def fixture(self,number):
        fixture=next(c for c in json.loads((DATA/'vector-recovery-fixtures.json').read_text())['cases'] if c['measure']==number)
        engine=object.__new__(VectorRecovery);engine.proof=object.__new__(PDFHeadEvidence)
        for k,v in fixture['proof'].items():setattr(engine.proof,k,copy.deepcopy(v))
        engine.mapping=engine.proof.mapping;engine.symbols=copy.deepcopy(fixture['symbols'])
        return engine,ET.fromstring(fixture['measureXML'])

    def events(self,measure):
        root=ET.Element('score-partwise');part=ET.SubElement(root,'part',id='P1');part.append(copy.deepcopy(measure))
        attrs=ET.Element('attributes');ET.SubElement(attrs,'divisions').text='4';part[0].insert(0,attrs)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'score.musicxml';ET.ElementTree(root).write(path)
            return read_score(path)['parts'][0]['measures'][0]['notes']

    def recover(self,engine,measure):return engine.recover(0,measure,F(4),1,{'1':('G','2'),'2':('F','4')})

    def test_recovers_missing_single_note_whole_bass_line_and_polyphony(self):
        references=json.loads((DATA/'peaceful-sleep-listening-review.json').read_text())['measures']
        for number,added in [(7,1),(8,4),(64,0),(73,4)]:
            engine,m=self.fixture(number);before=ET.tostring(m);new,records,report=self.recover(engine,m)
            expected=next(r['notes'] for r in references if r['index']==number-1)
            self.assertEqual(Counter(map(signature,self.events(new))),Counter(map(signature,expected)))
            self.assertEqual(report['addedNotes'],added)
            self.assertEqual(len(records),len(expected))
            self.assertEqual(ET.tostring(m),before,'Input XML must remain untouched')

    def test_missing_source_rest_or_beam_declines(self):
        for kind in ['rest','beam']:
            engine,m=self.fixture(7)
            if kind=='rest':engine.proof.symbols=[[]]
            else:engine.proof.drawings[0]=[d for d in engine.proof.drawings[0] if not (d.get('fill') and len(d['items'])==4)]
            with self.assertRaises(Decline):self.recover(engine,m)

    def test_unclassified_accidental_before_missing_note_declines(self):
        engine,m=self.fixture(7)
        # Missing final upper B: place an accidental immediately before it.
        new=max((s for s in engine.symbols[0] if s['c']=='\ue12d' and s['y']<290),key=lambda s:s['x'])
        engine.symbols[0].append(dict(new,c='\ue10e',x=new['x']-2))
        with self.assertRaisesRegex(Decline,'accidental'):self.recover(engine,m)

    def test_no_unused_head_geometry_no_note_recovery(self):
        engine,m=self.fixture(73)
        for head in engine.mapping['pages'][0]['heads']:head['mapped']=True
        with self.assertRaisesRegex(Decline,'unused-geometry'):self.recover(engine,m)

    def test_duplicate_unused_head_is_ambiguous(self):
        engine,m=self.fixture(7)
        head=next(h for h in engine.mapping['pages'][0]['heads'] if not h['mapped'])
        engine.mapping['pages'][0]['heads'].append(copy.deepcopy(head))
        with self.assertRaisesRegex(Decline,'unique-unused'):self.recover(engine,m)

    def test_pitch_conflict_does_not_change_existing_note(self):
        engine,m=self.fixture(7);m.find('note/pitch/octave').text='7'
        with self.assertRaisesRegex(Decline,'pitch-conflicts'):self.recover(engine,m)

    def test_midbar_directions_are_not_relocated(self):
        engine,m=self.fixture(64);ET.SubElement(m,'direction')
        with self.assertRaisesRegex(Decline,'mid-bar-metadata'):self.recover(engine,m)

    def test_tie_requires_printed_curve(self):
        engine,m=self.fixture(7);engine.proof.drawings[0]=[d for d in engine.proof.drawings[0] if not any(i[0]=='c' for i in d['items'])]
        _,_,report=self.recover(engine,m)
        self.assertEqual(report['tieChanges'],[])

    def test_duplicate_tie_curve_is_not_accepted(self):
        engine,m=self.fixture(7)
        curves=[d for d in engine.proof.drawings[0] if len(d['items'])==2 and all(i[0]=='c' for i in d['items'])]
        engine.proof.drawings[0].extend(copy.deepcopy(curves))
        _,_,report=self.recover(engine,m);self.assertEqual(report['tieChanges'],[])

    def test_open_curve_is_not_a_tie(self):
        engine,m=self.fixture(7)
        for drawing in engine.proof.drawings[0]:
            if len(drawing['items'])==2 and all(i[0]=='c' for i in drawing['items']):
                drawing['items'][1][1][1]+=5
        _,_,report=self.recover(engine,m);self.assertEqual(report['tieChanges'],[])

    def test_original_geometry_is_preserved_for_every_retained_head(self):
        engine,m=self.fixture(7);before={n['omrHeadID']:n['box'] for n in engine.mapping['notes']}
        _,records,_=self.recover(engine,m)
        after={n['omrHeadID']:n['box'] for n in records}
        self.assertEqual({k:after[k] for k in before},before)
        self.assertEqual(len(after),len(records))

    def test_upper_sixteenth_beams_must_match_written_duration(self):
        engine,m=self.fixture(8)
        # Removing beam polygons cannot turn recognized sixteenths into quarters.
        engine.proof.drawings[0]=[d for d in engine.proof.drawings[0] if not (d.get('fill') and len(d['items'])==4)]
        with self.assertRaises(Decline):self.recover(engine,m)

    def independent_cases(self):
        for c in json.loads((DATA/'independent-tie-fixtures.json').read_text())['cases']:
            engine=object.__new__(VectorRecovery);engine.proof=object.__new__(PDFHeadEvidence)
            for k,v in c['proof'].items():setattr(engine.proof,k,copy.deepcopy(v))
            engine.mapping=engine.proof.mapping;engine.symbols=copy.deepcopy(c['symbols'])
            yield engine,ET.fromstring(c['measureXML']),ET.fromstring(c['expectedXML'])

    def test_ties_recovered_on_six_independent_movements(self):
        cases=list(self.independent_cases());self.assertEqual(len(cases),6)
        for engine,broken,expected in cases:
            fixed,records,report=engine.recover_ties(0,broken)
            self.assertTrue(report['tieChanges'])
            self.assertEqual(Counter(map(signature,self.events(fixed))),Counter(map(signature,self.events(expected))))
            self.assertEqual(records,engine.mapping['notes'])
            with self.assertRaises(Decline):engine.recover_ties(0,fixed)

    def test_independent_ties_need_unambiguous_original_curves(self):
        for engine,broken,_ in self.independent_cases():
            engine.proof.drawings[0]=[d for d in engine.proof.drawings[0] if not any(i[0]=='c' for i in d['items'])]
            with self.assertRaises(Decline):engine.recover_ties(0,broken)

    def test_tie_cannot_bridge_a_gap_in_time(self):
        for engine,broken,_ in self.independent_cases():
            for n in engine.mapping['notes']:n['onset']=float(n['onset'])*10
            with self.assertRaises(Decline):engine.recover_ties(0,broken)

if __name__=='__main__':unittest.main()
