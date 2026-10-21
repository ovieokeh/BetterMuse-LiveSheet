from copy import deepcopy
from collections import defaultdict
from fractions import Fraction as F
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as E
from livesheet.pdf_whole_recovery import recover_bar
from livesheet.pdf_clef_recovery import ClefEvidence
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
from livesheet.pdf_rhythm_repair import Decline


def fixture(partial=False):
    lines=lambda top:[[[10,top+10*j],[220,top+10*j]] for j in range(5)]
    page=dict(width=240,height=240,systems=[dict(id='1',staves=[dict(lines=lines(20)),dict(lines=lines(120))])])
    mapping=dict(measures=[dict(page=1,system='1',left=10,right=220)],pages=[page],notes=[])
    m=E.Element('measure',number='1');a=E.SubElement(m,'attributes');E.SubElement(a,'divisions').text='2'
    glyphs=[];symbols=[]
    def head(x,y,kind):
        glyphs.append(dict(x=x/240,y=y/240,kind=kind,font='Leland'))
        symbols.append(dict(x=x,y=y,w=16 if kind=='whole' else 12,font='Leland',c='\ue0a2' if kind=='whole' else '\ue0a4'))
    def note(x,y,staff,kind,onset,step,octave,voice):
        n=E.SubElement(m,'note',{'default-x':str(x)});p=E.SubElement(n,'pitch');E.SubElement(p,'step').text=step;E.SubElement(p,'octave').text=str(octave)
        E.SubElement(n,'duration').text='8' if kind=='whole' else '1';E.SubElement(n,'voice').text=voice;E.SubElement(n,'type').text=kind if kind=='whole' else 'eighth';E.SubElement(n,'staff').text=str(staff)
        mapping['notes'].append(dict(measureIndex=0,staff=staff,onset=onset,box=[x,y-5,16 if kind=='whole' else 12,10],page=1,system='1'))
    for i in range(8):head(30+22*i,60,'filled');note(30+22*i,60,1,'filled',i/2,'E',4,'1')
    head(28,150,'whole');head(28,140,'whole')
    if partial:
        b=E.SubElement(m,'backup');E.SubElement(b,'duration').text='8';note(28,150,2,'whole',0,'B',2,'5')
    proof=object.__new__(PDFHeadEvidence);proof.mapping=mapping;proof.page_sizes=[(240,240)];proof.glyphs=[glyphs];proof.drawings=[[]]
    engine=object.__new__(ClefEvidence);engine.mapping=mapping;engine.vector=SimpleNamespace(proof=proof,symbols=[symbols]);engine.events=defaultdict(list)
    engine.events[(1,'1',1)]=[dict(x=0,right=8,kind=('G','2'))];engine.events[(1,'1',2)]=[dict(x=0,right=8,kind=('F','4'))]
    return engine,m


class WholeRecoveryTests(unittest.TestCase):
    def run_bar(self,engine,m):return recover_bar(engine,0,m,F(2),0,{1:[(0,F(0),('G','2'))],2:[(0,F(0),('F','4'))]})

    def test_missing_chord_uses_source_geometry_without_any_omr_heads(self):
        e,m=fixture();before=E.tostring(m);new,records,report=self.run_bar(e,m)
        self.assertEqual(report['addedNotes'],2);self.assertTrue(report['heads']['verified'])
        self.assertEqual(E.tostring(m),before)
        bass=[n for n in new.findall('note') if n.findtext('staff')=='2']
        self.assertEqual([(n.findtext('pitch/step'),n.findtext('pitch/octave'),n.findtext('duration')) for n in bass],[('B','2','8'),('D','3','8')])
        self.assertIsNone(bass[0].find('chord'));self.assertIsNotNone(bass[1].find('chord'))
        self.assertEqual(new.findtext('backup/duration'),'8')
        self.assertEqual(records[:8],e.mapping['notes']);self.assertTrue(all(r.get('pdfGlyphID') for r in records[8:]))

    def test_extends_partial_chord_without_an_extra_attack_or_voice(self):
        e,m=fixture(True);new,records,report=self.run_bar(e,m)
        self.assertEqual(report['addedNotes'],1);self.assertEqual(len(new.findall('backup')),1)
        last=new.findall('note')[-1];self.assertIsNotNone(last.find('chord'));self.assertEqual(last.findtext('voice'),'5')
        self.assertEqual(records[:-1],e.mapping['notes'])

    def test_explicit_accidental_is_read_from_source(self):
        e,m=fixture();e.vector.symbols[0].append(dict(x=12,y=140,w=8,font='Leland',c='\ue260'))
        new,_,_=self.run_bar(e,m);self.assertEqual(new.findall('note')[-1].findtext('pitch/alter'),'-1')

    def test_possible_cross_bar_tie_declines(self):
        e,m=fixture();e.vector.proof.drawings[0]=[dict(items=[('c',(25,146),(15,135),(1,135),(-10,146))])]
        with self.assertRaisesRegex(Decline,'tie'):self.run_bar(e,m)

    def test_moving_staff_multiple_columns_unknown_font_and_clef_conflict_decline(self):
        for kind in ('moving','column','font','clef','dot','duplicate','unmapped','duration','cue','printed-rest'):
            with self.subTest(kind=kind):
                e,m=fixture(kind=='duration')
                if kind=='moving':e.vector.symbols[0][-1]['c']='\ue0a4'
                if kind=='column':e.vector.symbols[0][-1]['x']+=20
                if kind=='font':e.vector.symbols[0][-1]['font']='unknown'
                if kind=='clef':e.events[(1,'1',2)][0]['kind']=('G','2')
                if kind=='dot':e.vector.symbols[0].append(dict(x=48,y=140,w=2,font='Leland',c='\ue1e7'))
                if kind=='duplicate':e.vector.symbols[0].append(deepcopy(e.vector.symbols[0][-1]))
                if kind=='unmapped':e.mapping['notes'][0].pop('box')
                if kind=='duration':m.findall('note')[-1].find('duration').text='4'
                if kind=='cue':e.vector.symbols[0][-1]['w']=8
                if kind=='printed-rest':e.vector.symbols[0].append(dict(x=100,y=150,w=8,font='Leland',c='\ue4e5'))
                with self.assertRaises(Decline):self.run_bar(e,m)


if __name__=='__main__':unittest.main()
