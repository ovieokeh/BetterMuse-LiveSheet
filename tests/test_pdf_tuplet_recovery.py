from copy import deepcopy
from fractions import Fraction as F
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as E
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
from livesheet.pdf_rhythm_repair import Decline
from livesheet.pdf_tuplet_recovery import repair_bar


def fixture():
    lines=lambda top:[[[0,top+10*j],[300,top+10*j]] for j in range(5)]
    mapping=dict(measures=[dict(page=1,system='1',left=0,right=300)],pages=[dict(width=320,height=240,systems=[dict(id='1',staves=[dict(lines=lines(20)),dict(lines=lines(120))])])],notes=[])
    m=E.Element('measure',number='1');symbols=[];glyphs=[];drawings=[]
    for i in range(8):
        n=E.SubElement(m,'note',{'default-x':str(20+i*30)})
        if i==0:E.SubElement(n,'rest')
        else:
            p=E.SubElement(n,'pitch');E.SubElement(p,'step').text='E';E.SubElement(p,'octave').text='4'
            mapping['notes'].append(dict(measureIndex=0,staff=1,onset=i/2,box=[20+i*30,55,12,10]))
            glyphs.append(dict(x=(20+i*30)/320,y=60/240,kind='filled',font='Leland'))
        E.SubElement(n,'duration').text='4' if i>=5 else '6'
        E.SubElement(n,'voice').text='1';E.SubElement(n,'type').text='eighth'
        if i>=5:
            t=E.SubElement(n,'time-modification');E.SubElement(t,'actual-notes').text='3';E.SubElement(t,'normal-notes').text='2'
            if i in (5,7):nt=E.SubElement(n,'notations');E.SubElement(nt,'tuplet',type='start' if i==5 else 'stop')
        E.SubElement(n,'stem').text='down';E.SubElement(n,'staff').text='1'
        if i>=4:
            E.SubElement(n,'beam',number='1').text='begin' if i==4 else 'end' if i==7 else 'continue'
            drawings.append(dict(stroke=(0,0,0),fill=None,width=1,items=[('l',(20+i*30,60),(20+i*30,90))]))
    E.SubElement(E.SubElement(m,'backup'),'duration').text='42'
    for i in (0,4):
        n=E.SubElement(m,'note',{'default-x':str(20+i*30)});p=E.SubElement(n,'pitch');E.SubElement(p,'step').text='B';E.SubElement(p,'octave').text='2'
        E.SubElement(n,'duration').text='24';E.SubElement(n,'voice').text='5';E.SubElement(n,'type').text='half';E.SubElement(n,'staff').text='2'
        mapping['notes'].append(dict(measureIndex=0,staff=2,onset=i/2,duration=2,box=[20+i*30,145,12,10]))
        glyphs.append(dict(x=(20+i*30)/320,y=150/240,kind='half',font='Leland'))
    beam=dict(stroke=None,fill=(0,0,0),width=None,items=[('l',(139,87.5),(231,87.5)),('l',(231,87.5),(231,92.5)),('l',(231,92.5),(139,92.5))])
    drawings.append(beam)
    symbols=[dict(c=c,font='Edwin-Roman',x=x,y=15,w=8) for c,x in [('1',50),('2',110),('3',170),('5',230)]]
    proof=object.__new__(PDFHeadEvidence);proof.mapping=mapping;proof.glyphs=[glyphs];proof.symbols=[[dict(kind='eighth-rest',x=20,y=40)]];proof.drawings=[drawings];proof.page_sizes=[(320,240)]
    return SimpleNamespace(mapping=mapping,proof=proof,symbols=[symbols]),m


class TupletRecoveryTests(unittest.TestCase):
    def test_fingering_triplet_restores_even_timing_and_matching_backup(self):
        v,m=fixture();before=E.tostring(m);new,report=repair_bar(v,0,m,F(12))
        self.assertEqual(E.tostring(m),before);self.assertEqual(report['correctedNotes'],3)
        self.assertEqual([n.findtext('duration') for n in new.findall('note')[:8]],['6']*8)
        self.assertEqual(new.findtext('backup/duration'),'48')
        self.assertIsNone(new.find('note/time-modification'));self.assertIsNone(new.find('note/notations/tuplet'))
        self.assertEqual([E.tostring(n) for n in m.findall('note')[8:]],[E.tostring(n) for n in new.findall('note')[8:]])

    def test_true_tuplet_number_near_beam_declines(self):
        v,m=fixture();v.symbols[0].append(dict(c='3',font='Edwin-Roman',x=190,y=105,w=8))
        with self.assertRaisesRegex(Decline,'number-near-beam'):repair_bar(v,0,m,F(12))

    def test_incomplete_or_ambiguous_source_cannot_rewrite_timing(self):
        for kind in ('beam','double-beam','rest','anchor','finger','peers','head','meter','rewind'):
            with self.subTest(kind=kind):
                v,m=fixture()
                if kind=='beam':v.proof.drawings[0].pop()
                if kind=='double-beam':
                    beam=deepcopy(v.proof.drawings[0][-1]);beam['items']=[(i[0],(i[1][0],i[1][1]-7),(i[2][0],i[2][1]-7)) for i in beam['items']];v.proof.drawings[0].append(beam)
                if kind=='rest':v.proof.symbols=[[]]
                if kind=='anchor':v.mapping['notes'][-1]['box'][0]+=20
                if kind=='finger':v.symbols[0]=[s for s in v.symbols[0] if s['c']!='3']
                if kind=='peers':v.symbols[0]=[s for s in v.symbols[0] if s['c']=='3']
                if kind=='head':v.proof.glyphs[0].pop()
                if kind=='meter':m.findall('note')[1].find('duration').text='12'
                if kind=='rewind':m.find('backup/duration').text='48'
                with self.assertRaises(Decline):repair_bar(v,0,m,F(12))


if __name__=='__main__':unittest.main()
