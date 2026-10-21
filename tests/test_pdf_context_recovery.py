from copy import deepcopy
from collections import defaultdict
from fractions import Fraction as F
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
from livesheet.pdf_context_recovery import whole_tie_pair, held_voice_context, unequal_pitch_slurs, split_tie_curve, recover
from livesheet.pdf_whole_recovery import recover_bar
from livesheet.pdf_clef_recovery import ClefEvidence
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
from livesheet.pdf_rhythm_repair import Decline
from test_pdf_whole_recovery import fixture as whole_fixture


def pair_fixture():
    lines=lambda top:[[[0,top+10*j],[280,top+10*j]] for j in range(5)]
    mapping=dict(measures=[dict(page=1,system='1',left=10,right=140),dict(page=1,system='1',left=140,right=280)],pages=[dict(width=300,height=240,systems=[dict(id='1',staves=[dict(lines=lines(20)),dict(lines=lines(120))])])],notes=[])
    symbols=[];glyphs=[];drawings=[];measures=[]
    pitches=[(1,'G',4,50),(1,'D',5,30),(2,'B',2,150),(2,'D',3,140)]
    for i,x in enumerate((30,160)):
        m=E.Element('measure',number=str(i+1));measures.append(m)
        for staff,step,octave,y in pitches:
            symbols.append(dict(c='\ue0a2',font='Leland',x=x,y=y,w=16))
            glyphs.append(dict(kind='whole',font='Leland',x=x/300,y=y/240))
        kept=pitches[1:] if i==0 else [pitches[2]]
        last_staff=None
        for staff,step,octave,y in kept:
            if last_staff is not None and last_staff!=staff:E.SubElement(E.SubElement(m,'backup'),'duration').text='8'
            n=E.SubElement(m,'note',{'default-x':str(x)})
            if staff==last_staff:E.SubElement(n,'chord')
            p=E.SubElement(n,'pitch');E.SubElement(p,'step').text=step;E.SubElement(p,'octave').text=str(octave)
            E.SubElement(n,'duration').text='8';E.SubElement(n,'voice').text='1' if staff==1 else '5';E.SubElement(n,'type').text='whole';E.SubElement(n,'staff').text=str(staff)
            mapping['notes'].append(dict(measureIndex=i,staff=staff,onset=0,duration=4,voice='1' if staff==1 else '5',box=[x,y-5,16,10],page=1,system='1'))
            last_staff=staff
    for staff,step,octave,y in pitches:
        side=1 if y in (50,150) else -1;cy=y+side*4
        drawings.append(dict(fill=(0,0,0),items=[('c',(48,cy),(80,cy+side*10),(130,cy+side*10),(158,cy)),('c',(158,cy),(130,cy+side*12),(80,cy+side*12),(48,cy))]))
    proof=object.__new__(PDFHeadEvidence);proof.mapping=mapping;proof.page_sizes=[(300,240)];proof.glyphs=[glyphs];proof.drawings=[drawings]
    engine=object.__new__(ClefEvidence);engine.mapping=mapping;engine.vector=SimpleNamespace(proof=proof,symbols=[symbols]);engine.events=defaultdict(list)
    engine.events[(1,'1',1)]=[dict(x=0,right=8,kind=('G','2'))];engine.events[(1,'1',2)]=[dict(x=0,right=8,kind=('F','4'))]
    return engine,measures


CLEFS={1:[(0,F(0),('G','2'))],2:[(0,F(0),('F','4'))]}


class ContextTests(unittest.TestCase):
    def test_multiple_staff_meters_decline_before_any_repair(self):
        root=E.fromstring('<score-partwise><part><measure><attributes><time number="1"><beats>4</beats><beat-type>4</beat-type></time><time number="2"><beats>3</beats><beat-type>4</beat-type></time></attributes></measure></part></score-partwise>')
        with patch('livesheet.pdf_context_recovery.load_mxl',return_value=(root,'score.xml')):
            with self.assertRaisesRegex(Decline,'staff-meters'):recover(None,None,None,{})

    def pair(self,e,ms):return whole_tie_pair(e,0,ms,{0:F(2),1:F(2)},{0:0,1:0},CLEFS)

    def test_adjacent_chords_add_missing_heads_and_tie_every_pitch(self):
        e,ms=pair_fixture();before=[E.tostring(m) for m in ms];output,report=self.pair(e,ms)
        self.assertEqual(report['tiedPitches'],4);self.assertEqual([o[2]['addedNotes'] for o in output],[1,3])
        for (new,records,_),kind in zip(output,('start','stop')):
            self.assertEqual(len(records),4)
            self.assertTrue(all(n.findtext('duration')=='8' for n in new.findall('note')))
            self.assertEqual([n.find('tie').get('type') for n in new.findall('note')],[kind]*4)
            self.assertEqual([n.find('notations/tied').get('type') for n in new.findall('note')],[kind]*4)
            self.assertEqual({n.findtext('voice') for n in new.findall('note') if n.findtext('staff')=='1'},{'1'})
        self.assertEqual(before,[E.tostring(m) for m in ms])

    def test_tied_staff_preserves_independent_half_notes_on_other_staff(self):
        e,ms=pair_fixture();complete,_=self.pair(e,ms)
        e.mapping['notes']=[];ms=[]
        for mi,(m,records,_) in enumerate(complete):
            for n,r in zip(m.findall('note'),records):
                staff=int(n.findtext('staff'))
                r.update(measureIndex=mi,staff=staff,onset=0,duration=2 if staff==1 else 4,voice=n.findtext('voice'))
                for t in n.findall('tie'):n.remove(t)
                for nt in n.findall('notations'):n.remove(nt)
                if staff==1:
                    n.find('type').text='half';n.find('duration').text='4'
                e.mapping['notes'].append(r)
            backup=m.find('backup')
            forward=E.Element('forward');E.SubElement(forward,'duration').text='4'
            m.insert(list(m).index(backup),forward)
            ms.append(m)
        for gs in (e.vector.symbols[0],e.vector.proof.glyphs[0]):
            for g in gs:
                if (g['y']<100/240 if 'kind' in g else g['y']<100):
                    if 'kind' in g:g['kind']='half'
                    else:g['c']='\ue0a3'
        # Remove one bass chord member. The other staff is fully accounted for.
        missing=next(n for n in ms[0].findall('note') if n.findtext('staff')=='2')
        index=ms[0].findall('note').index(missing);ms[0].remove(missing);e.mapping['notes'].pop(index)
        remaining=next(n for n in ms[0].findall('note') if n.findtext('staff')=='2')
        remaining.remove(remaining.find('chord'))
        before=[[E.tostring(n) for n in m.findall('note') if n.findtext('staff')=='1'] for m in ms]
        output,report=self.pair(e,ms)
        self.assertEqual(report['tiedPitches'],2)
        for i,(m,_,_) in enumerate(output):
            self.assertEqual([E.tostring(n) for n in m.findall('note') if n.findtext('staff')=='1'],before[i])
            self.assertTrue(all(n.find('tie') is not None for n in m.findall('note') if n.findtext('staff')=='2'))

    def test_wholly_omitted_tied_staff_gets_one_fresh_voice_for_both_bars(self):
        e,ms=pair_fixture()
        for m in ms:
            for n in list(m):
                if n.tag=='backup' or n.tag=='note' and n.findtext('staff')=='2':m.remove(n)
        e.mapping['notes']=[r for r in e.mapping['notes'] if r['staff']!=2]
        output,report=self.pair(e,ms)
        voices=[]
        for (m,records,_),kind in zip(output,('start','stop')):
            bass=[n for n in m.findall('note') if n.findtext('staff')=='2']
            self.assertEqual(len(bass),2)
            self.assertTrue(all(n.find('tie').get('type')==kind for n in bass))
            voices.append({n.findtext('voice') for n in bass})
        self.assertEqual(voices[0],voices[1]);self.assertNotIn('1',voices[0])

    def test_pair_is_atomic_when_source_or_existing_music_conflicts(self):
        for kind in ('missing-curve','duplicate-curve','open-curve','pitch','repeat','onset','duration','outside-tie','outside-slur','font'):
            with self.subTest(kind=kind):
                e,ms=pair_fixture()
                if kind=='missing-curve':e.vector.proof.drawings[0].pop()
                if kind=='duplicate-curve':e.vector.proof.drawings[0].append(deepcopy(e.vector.proof.drawings[0][0]))
                if kind=='open-curve':e.vector.proof.drawings[0][0]['items'][1]=('c',(159,54),(130,66),(80,66),(49,54))
                if kind=='pitch':ms[0].find('note/pitch/step').text='A'
                if kind=='repeat':E.SubElement(E.SubElement(ms[0],'barline',location='right'),'repeat',direction='backward')
                if kind=='onset':e.mapping['notes'][0]['onset']=1
                if kind=='duration':e.mapping['notes'][0]['duration']=2
                if kind=='outside-tie':E.SubElement(ms[0].find('note'),'tie',type='stop')
                if kind=='outside-slur':E.SubElement(E.SubElement(ms[0].find('note'),'notations'),'slur',type='start',number='9')
                if kind=='font':e.vector.symbols[0][0]['font']='unknown'
                before=[E.tostring(m) for m in ms]
                with self.assertRaises(Decline):self.pair(e,ms)
                self.assertEqual(before,[E.tostring(m) for m in ms])

    def test_split_tie_requires_a_level_closed_curve_at_the_boundary(self):
        geo=dict(gap=10,sx=1,bar=dict(right=140),local=[])
        head=dict(x=30,y=50,w=16,staff=2)
        d=dict(fill=(0,0,0),items=[('c',(40,54),(65,64),(115,64),(138,54)),
                                      ('c',(138,54),(115,66),(65,66),(40,54))])
        self.assertEqual(split_tie_curve(d,head,geo,True),1)
        for bad in ('not-at-edge','open','wrong-pitch','deep'):
            with self.subTest(bad=bad):
                dd=deepcopy(d);hh=deepcopy(head);gg=deepcopy(geo)
                if bad=='not-at-edge':gg['bar']['right']=160
                if bad=='open':dd['items'][1]=('c',(138,54),(115,66),(65,66),(42,54))
                if bad=='wrong-pitch':hh['y']=70
                if bad=='deep':dd['items'][0]=('c',(40,54),(65,94),(115,94),(138,54))
                self.assertIsNone(split_tie_curve(dd,hh,gg,True))

    def test_closed_slur_on_unequal_pitches_is_not_a_tie(self):
        d=dict(fill=(0,0,0),items=[('c',(20,20),(40,0),(70,10),(90,40)),('c',(90,40),(70,12),(40,2),(20,20))])
        g=dict(gap=10,drawings=[d],heads=[dict(x=18,y=24,w=12,staff=1),dict(x=88,y=44,w=12,staff=1)])
        self.assertEqual(unequal_pitch_slurs(g),{0})
        g['heads'][1]['staff']=2;self.assertEqual(unequal_pitch_slurs(g),set())
        g['heads'][1]['staff']=1;g['heads'].append(deepcopy(g['heads'][0]));self.assertEqual(unequal_pitch_slurs(g),set())

    def held(self):
        e,m=whole_fixture();e.vector.symbols[0]=e.vector.symbols[0][:-2];e.vector.proof.glyphs[0]=e.vector.proof.glyphs[0][:-2]
        e.vector.symbols[0].append(dict(c='\ue0a2',font='Leland',x=28,y=40,w=16));e.vector.proof.glyphs[0].append(dict(kind='whole',font='Leland',x=28/240,y=40/240))
        for n,r in zip(m.findall('note'),e.mapping['notes']):
            E.SubElement(n,'stem').text='down';r.update(voice='1',duration=.5)
        return e,m

    def test_held_whole_gets_separate_voice_without_retiming_moving_line(self):
        e,m=self.held();context=held_voice_context(e,0,m,F(2));new,records,report=recover_bar(e,0,m,F(2),0,CLEFS,context=context)
        self.assertEqual(report['addedNotes'],1);self.assertEqual([E.tostring(n) for n in new.findall('note')[:-1]],[E.tostring(n) for n in m.findall('note')])
        last=new.findall('note')[-1];self.assertIsNone(last.find('chord'));self.assertNotEqual(last.findtext('voice'),'1');self.assertEqual(last.findtext('duration'),'8')
        self.assertEqual(new.findtext('backup/duration'),'8')

    def test_incomplete_moving_voice_cannot_prove_whole_onset(self):
        e,m=self.held();e.mapping['notes'][-1]['duration']=.25
        with self.assertRaisesRegex(Decline,'four-beats'):held_voice_context(e,0,m,F(2))


if __name__=='__main__':unittest.main()
