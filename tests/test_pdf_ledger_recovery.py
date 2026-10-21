from copy import deepcopy
from fractions import Fraction as F
import unittest
import xml.etree.ElementTree as E
from test_pdf_whole_recovery import fixture as whole_fixture
from livesheet.pdf_ledger_recovery import repair_bar
from livesheet.pdf_rhythm_repair import Decline


def fixture():
    e,m=whole_fixture()
    e.vector.symbols[0]=e.vector.symbols[0][:8]
    e.mapping['pages'][0]['width']=360;e.vector.proof.page_sizes=[(360,240)]
    e.mapping['measures'][0]['right']=350
    for staff in e.mapping['pages'][0]['systems'][0]['staves']:
        for line in staff['lines']:line[-1][0]=350
    for j,(n,r,h) in enumerate(zip(m.findall('note'),e.mapping['notes'],e.vector.symbols[0])):
        h['x']=30+36*j;r['box'][0]=h['x']
        E.SubElement(n,'stem').text='down'
        n.find('duration').text='2'
        if j==6:
            h['y']=10;r['box'][1]=10
            n.find('pitch/step').text='G';n.find('pitch/octave').text='5'
            n.find('duration').text='3';E.SubElement(n,'dot')
        x,y=h['x'],h['y']
        e.vector.proof.drawings[0].append(dict(stroke=(0,0,0),fill=None,width=1,items=[('l',(x,y),(x,y+30))]))
        e.vector.proof.drawings[0].append(dict(fill=(0,0,0),items=[('l',(x-10,y+28),(x+11,y+28)),('l',(x+11,y+28),(x+11,y+32)),('l',(x+11,y+32),(x-10,y+32)),('l',(x-10,y+32),(x-10,y+28))]))
    h=e.vector.symbols[0][6]
    e.vector.proof.drawings[0].append(dict(stroke=(0,0,0),fill=None,width=1,items=[('l',(h['x']-3,10),(h['x']+15,10))]))
    e.vector.proof.glyphs=[[dict(x=h['x']/360,y=h['y']/240,kind='filled',font='Leland') for h in e.vector.symbols[0]]]
    return e,m


class LedgerTests(unittest.TestCase):
    def run_bar(self,e,m):
        return repair_bar(e,0,m,F(4),0,{1:[(0,F(0),('G','2'))]},F(4))

    def test_one_head_and_false_dot_corrected_with_original_intact(self):
        e,m=fixture();before=E.tostring(m);mapping=deepcopy(e.mapping)
        new,records,proof=self.run_bar(e,m)
        n=new.findall('note')[6]
        self.assertEqual((n.findtext('pitch/step'),n.findtext('pitch/octave'),n.findtext('duration')),('A','5','2'))
        self.assertIsNone(n.find('dot'))
        self.assertEqual(sum(int(n.findtext('duration')) for n in new.findall('note')),16)
        self.assertEqual(proof['printedBeams'],8)
        self.assertEqual(records[6]['box'][1],5)
        for i in range(8):
            if i!=6:self.assertEqual(E.tostring(new.findall('note')[i]),E.tostring(m.findall('note')[i]))
        self.assertEqual(E.tostring(m),before);self.assertEqual(e.mapping,mapping)

    def test_uncertain_evidence_declines_without_mutation(self):
        for bad in ('ledger','stem','beam','dot','vector-dot','head','font','other-pitch','pitch-distance','no-displacement','duration','pickup','tie','later-direction','voice','chord'):
            with self.subTest(bad=bad):
                e,m=fixture();n=m.findall('note')[6];h=e.vector.symbols[0][6]
                if bad=='ledger':e.vector.proof.drawings[0].pop()
                if bad=='stem':e.vector.proof.drawings[0].pop(12)
                if bad=='beam':e.vector.proof.drawings[0].pop(13)
                if bad=='dot':e.vector.symbols[0].append(dict(x=h['x']+15,y=h['y'],w=2,c='\ue1e7',font='Leland'))
                if bad=='vector-dot':e.vector.proof.drawings[0].append(dict(fill=(0,0,0),items=[('c',(h['x']+15,10),(h['x']+16,9),(h['x']+17,11),(h['x']+15,10))]))
                if bad=='head':e.vector.symbols[0].pop()
                if bad=='font':h['font']='unknown'
                if bad=='other-pitch':m.findall('note')[0].find('pitch/step').text='D'
                if bad=='pitch-distance':n.find('pitch/step').text='E'
                if bad=='no-displacement':e.mapping['notes'][6]['box'][1]=5
                if bad=='duration':n.find('duration').text='4'
                if bad=='pickup':m.set('implicit','yes')
                if bad=='tie':E.SubElement(n,'tie',type='start')
                if bad=='later-direction':E.SubElement(m,'direction')
                if bad=='voice':m.findall('note')[0].find('voice').text='2'
                if bad=='chord':E.SubElement(n,'chord')
                before=E.tostring(m);mapping=deepcopy(e.mapping)
                with self.assertRaises(Decline):self.run_bar(e,m)
                self.assertEqual(E.tostring(m),before);self.assertEqual(e.mapping,mapping)
