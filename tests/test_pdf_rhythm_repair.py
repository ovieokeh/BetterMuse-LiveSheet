import copy
from fractions import Fraction as F
import unittest
import xml.etree.ElementTree as ET
from livesheet.pdf_rhythm_repair import solve, rebuild, Decline


def note(pitch,staff,stem,x,duration,voice='1',chord=False,rest=False):
    n=ET.Element('note',{'default-x':str(x)})
    if chord:ET.SubElement(n,'chord')
    if rest:ET.SubElement(n,'rest')
    else:
        p=ET.SubElement(n,'pitch');ET.SubElement(p,'step').text=pitch;ET.SubElement(p,'octave').text='4'
    ET.SubElement(n,'duration').text=str(duration)
    ET.SubElement(n,'voice').text=voice
    ET.SubElement(n,'type').text={1:'eighth',2:'quarter',3:'quarter',4:'half',8:'whole'}[duration]
    if duration==3:ET.SubElement(n,'dot')
    if stem:ET.SubElement(n,'stem').text=stem
    ET.SubElement(n,'staff').text=str(staff)
    return n


def move(m,tag,duration):ET.SubElement(ET.SubElement(m,tag),'duration').text=str(duration)


def broken_measure():
    # Divisions=2. A complete upper rhythm is split into false voices; the
    # lower bass and melody are serialized. All pitches/lengths stay intact.
    m=ET.Element('measure',number='1')
    m.append(note('E',1,'down',10,2));move(m,'backup',2);move(m,'forward',4)
    m.append(note('',1,None,30,1,rest=True));m.append(note('E',1,'down',40,1));m.append(note('E',1,'down',50,4))
    move(m,'backup',10)
    m.append(note('C',2,'down',10,3));m.append(note('G',2,'up',20,1));m.append(note('B',2,'up',30,1));m.append(note('G',2,'up',40,1));m.append(note('G',2,'up',50,4))
    move(m,'backup',10);move(m,'forward',5)
    m.append(note('C',2,'down',40,1));m.append(note('C',2,'down',50,4))
    return m


class RhythmRepairTests(unittest.TestCase):
    def test_unique_aligned_lanes_recover_timing_without_changing_notes(self):
        m=broken_measure();originals=list(m.findall('note'))
        musical=[(id(n),ET.tostring(n.find('pitch')),n.findtext('duration')) for n in originals if n.find('pitch') is not None]
        groups,lanes=solve(m,F(2),F(4))
        self.assertEqual([g['newOnset'] for g in groups if g['staff']=='1'],[0,1,F('3/2'),2])
        up=lanes[('2','up')]
        self.assertEqual([g['newOnset'] for g in up],[F('1/2'),1,F('3/2'),2])
        rebuild(m,lanes,F(2))
        self.assertEqual(sorted(musical),sorted((id(n),ET.tostring(n.find('pitch')),n.findtext('duration')) for n in m.findall('note') if n.find('pitch') is not None))

    def test_shortened_supporting_note_cannot_be_fixed_by_stretching(self):
        m=broken_measure();m.findall('note')[6].find('duration').text='2'
        with self.assertRaises(Decline):solve(m,F(2),F(4))

    def test_incomplete_anchor_declines(self):
        m=broken_measure();m.findall('note')[0].find('duration').text='1'
        with self.assertRaises(Decline):solve(m,F(2),F(4))

    def test_ambiguous_rest_and_midbar_direction_decline(self):
        m=broken_measure();m.append(note('',2,None,15,1,rest=True))
        with self.assertRaises(Decline):solve(m,F(2),F(4))
        m=broken_measure();ET.SubElement(m,'direction')
        with self.assertRaises(Decline):solve(m,F(2),F(4))

    def test_grace_and_tuplets_are_not_guessed(self):
        for tag in ('grace','time-modification'):
            m=broken_measure();ET.SubElement(m.find('note'),tag)
            with self.assertRaises(Decline):solve(m,F(2),F(4))

    def test_corrected_measure_is_left_alone(self):
        m=broken_measure();_,lanes=solve(m,F(2),F(4));rebuild(m,lanes,F(2))
        with self.assertRaisesRegex(Decline,'not-overfull'):solve(m,F(2),F(4))


def whole_bass_measure():
    m=ET.Element('measure',number='1')
    m.append(note('G',1,'down',17,4))
    for pitch,x,duration in [('E',51,1),('G',86,1),('B',120,1),('B',155,4)]:
        m.append(note(pitch,2,'up',x,duration))
    move(m,'backup',11);move(m,'forward',7)
    m.append(note('E',1,'down',155,4))
    move(m,'backup',11);m.append(note('C',2,None,13,8))
    return m


class WholeBassTests(unittest.TestCase):
    def test_whole_bass_is_simultaneous_with_moving_lane(self):
        m=whole_bass_measure();groups,lanes=solve(m,F(2),F(4))
        self.assertEqual([g['newOnset'] for g in lanes[('2','up')]],[F('1/2'),1,F('3/2'),2])
        self.assertEqual(lanes[('2','whole')][0]['newOnset'],0)
        rebuild(m,lanes,F(2))
        with self.assertRaisesRegex(Decline,'not-overfull'):solve(m,F(2),F(4))

    def test_missing_upper_note_cannot_be_filled_by_rhythm_repair(self):
        m=whole_bass_measure();m.findall('note')[5].find('duration').text='2';m.findall('note')[5].find('type').text='quarter';m.findall('backup')[-1].find('duration').text='9'
        with self.assertRaisesRegex(Decline,'incomplete-upper-anchor'):solve(m,F(2),F(4))

    def test_missing_moving_note_declines(self):
        m=whole_bass_measure();m.remove(m.findall('note')[1])
        with self.assertRaises(Decline):solve(m,F(2),F(4))

    def test_conflicting_half_note_column_declines(self):
        m=whole_bass_measure();m.findall('note')[5].set('default-x','145')
        with self.assertRaisesRegex(Decline,'half-note-alignment'):solve(m,F(2),F(4))

    def test_meter_and_whole_duration_must_agree(self):
        with self.assertRaisesRegex(Decline,'four-quarter'):solve(whole_bass_measure(),F(2),F(3))
        m=whole_bass_measure();m.findall('note')[-1].find('duration').text='6'
        with self.assertRaisesRegex(Decline,'written-duration'):solve(m,F(2),F(4))

    def test_ties_crossing_barline_cannot_change_voice(self):
        m=whole_bass_measure();ET.SubElement(m.findall('note')[-1],'tie',type='start')
        with self.assertRaisesRegex(Decline,'tie-crosses-boundary'):solve(m,F(2),F(4))


class PrintedHeadEvidenceTests(unittest.TestCase):
    def evidence(self):
        from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
        proof=object.__new__(PDFHeadEvidence)
        proof.mapping=dict(measures=[dict(page=1,system='1',left=0,right=100)],
            pages=[dict(width=100,height=100,systems=[dict(id='1',staves=[dict(lines=[[[0,y],[100,y]] for y in range(30,51,5)])])])],
            notes=[dict(measureIndex=0,box=[10,40,4,4])])
        proof.glyphs=[[dict(x=.1,y=.42,kind='filled',font='Leland')]]
        return proof

    def test_unique_printed_head_is_required(self):
        n=note('C',1,'down',10,1)
        proof=self.evidence()
        self.assertTrue(proof(0,[n])['verified'])
        proof.glyphs[0].append(copy.deepcopy(proof.glyphs[0][0]))
        self.assertFalse(proof(0,[n])['verified'])

    def test_wrong_shape_position_or_missing_glyph_declines(self):
        n=note('C',1,'down',10,1)
        for change in ['shape','position','missing']:
            proof=self.evidence()
            if change=='shape':proof.glyphs[0][0]['kind']='half'
            if change=='position':proof.glyphs[0][0]['y']=.5
            if change=='missing':proof.glyphs[0]=[]
            self.assertFalse(proof(0,[n])['verified'])

    def test_pdf_identity_mismatch_fails_before_extraction(self):
        import tempfile
        from pathlib import Path
        from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'not-the-score.pdf';p.write_bytes(b'wrong input')
            with self.assertRaisesRegex(ValueError,'identity'):
                PDFHeadEvidence(p,dict(sha256='not-this-file'))


@unittest.skipUnless(__import__("pathlib").Path(__file__).parent.joinpath("data/pdf-quality/whole-bass-evidence-fixture.json").exists(), "private review fixtures not supplied")
class PrintedWholeRhythmTests(unittest.TestCase):
    def fixture(self):
        import json
        from pathlib import Path
        from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
        data=json.loads((Path(__file__).parent/'data/pdf-quality/whole-bass-evidence-fixture.json').read_text())
        proof=object.__new__(PDFHeadEvidence)
        for key in ['mapping','glyphs','symbols','drawings','page_sizes']:setattr(proof,key,copy.deepcopy(data[key]))
        m=ET.fromstring(data['measureXML'])
        groups,_=solve(m,F(data['divisions']),F(4))
        return proof,m,groups

    def test_original_rest_stems_and_beam_support_timing(self):
        proof,m,groups=self.fixture()
        self.assertTrue(proof(0,[n for n in m.findall('note') if n.find('pitch') is not None])['verified'])
        self.assertTrue(proof.verify_whole_lanes(0,groups)['verified'])

    def test_rest_cannot_be_assumed(self):
        proof,_,groups=self.fixture();proof.symbols=[[]]
        self.assertFalse(proof.verify_whole_lanes(0,groups)['verified'])
        proof,_,groups=self.fixture();proof.symbols[0]*=2
        self.assertFalse(proof.verify_whole_lanes(0,groups)['verified'])

    def test_missing_or_double_beam_cannot_be_assumed_eighths(self):
        proof,_,groups=self.fixture()
        beams=[d for d in proof.drawings[0] if d.get('fill') and len(d['items'])==4]
        self.assertEqual(len(beams),1)
        proof.drawings[0].remove(beams[0]);self.assertFalse(proof.verify_whole_lanes(0,groups)['verified'])
        proof,_,groups=self.fixture();extra=copy.deepcopy(beams[0])
        for item in extra['items']:
            for point in item[1:]:point[1]+=4
        proof.drawings[0].append(extra)
        self.assertFalse(proof.verify_whole_lanes(0,groups)['verified'])

    def test_missing_stems_decline_even_when_heads_match(self):
        proof,_,groups=self.fixture();proof.drawings[0]=[d for d in proof.drawings[0] if not d.get('stroke')]
        self.assertFalse(proof.verify_whole_lanes(0,groups)['verified'])

    def test_whole_note_alignment_uses_printed_centres(self):
        proof,_,groups=self.fixture();proof.mapping['notes'][-1]['box'][0]-=20
        self.assertFalse(proof.verify_whole_lanes(0,groups)['verified'])

if __name__=='__main__':unittest.main()
