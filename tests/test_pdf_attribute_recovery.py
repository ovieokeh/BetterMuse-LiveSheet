import unittest
from copy import deepcopy
from unittest.mock import patch
import xml.etree.ElementTree as E
from types import SimpleNamespace
from livesheet.pdf_attribute_recovery import opening_attributes
from livesheet.pdf_rhythm_repair import Decline


def fixture(font='Petaluma'):
    m=E.fromstring('''<measure><attributes><divisions>2</divisions><staves>2</staves><clef number="1"><sign>G</sign><line>2</line></clef><staff-details/></attributes>
    <note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration><staff>1</staff></note></measure>''')
    g=dict(gap=10,sx=1,bar=dict(left=10,page=1,system='1'),bounds=[(50,90),(150,190)],local=[dict(staff=1,x=100),dict(staff=2,x=100)],symbols=[])
    offsets=(0,4) if font=='Petaluma' else (1,3)
    for top in (50,150):
        g['symbols'] += [dict(font=font,c='\ue084',x=50,y=top+o*10) for o in offsets]
    engine=SimpleNamespace(events={(1,'1',1):[dict(x=15,right=35,kind=('G','2'))],(1,'1',2):[dict(x=15,right=35,kind=('F','4'))]})
    return engine,m,g


class AttributeTests(unittest.TestCase):
    def test_restores_only_missing_attributes_from_both_staffs(self):
        for font in ('Leland','Bravura','Petaluma'):
            e,m,g=fixture(font);before=E.tostring(m)
            with patch('livesheet.pdf_attribute_recovery.geometry',return_value=g):
                new,changes=opening_attributes(e,m)
                self.assertEqual(new.findtext('attributes/time/beats'),'4')
                self.assertEqual(new.findtext('attributes/clef[@number="2"]/sign'),'F')
                self.assertEqual(E.tostring(m),before)
                self.assertEqual(E.tostring(m.find('note')),E.tostring(new.find('note')))
                self.assertEqual(len(changes),2)
                _,again=opening_attributes(e,new);self.assertEqual(again,[])

    def test_conflicting_or_incomplete_evidence_declines_atomically(self):
        for bad in ('font','missing-digit','three-digits','misaligned','off-staff','different-meter','no-head','duplicate-clef','existing-meter','existing-clef','existing-pitch','pickup'):
            with self.subTest(bad=bad):
                e,m,g=fixture()
                if bad=='font':g['symbols'][0]['font']='unknown'
                if bad=='missing-digit':g['symbols'].pop()
                if bad=='three-digits':g['symbols'].append(deepcopy(g['symbols'][0]))
                if bad=='misaligned':g['symbols'][0]['x']+=5
                if bad=='off-staff':g['symbols'][0]['y']+=5
                if bad=='different-meter':g['symbols'][2]['c']='\ue083'
                if bad=='no-head':g['local'].pop()
                if bad=='duplicate-clef':e.events[(1,'1',2)]*=2
                if bad=='existing-meter':m.find('attributes').append(E.fromstring('<time><beats>3</beats><beat-type>4</beat-type></time>'))
                if bad=='existing-clef':m.find('attributes').append(E.fromstring('<clef number="2"><sign>G</sign><line>2</line></clef>'))
                if bad=='existing-pitch':m.find('note/staff').text='2'
                if bad=='pickup':m.set('implicit','yes')
                before=E.tostring(m)
                with patch('livesheet.pdf_attribute_recovery.geometry',return_value=g),self.assertRaises(Decline):opening_attributes(e,m)
                self.assertEqual(before,E.tostring(m))

if __name__=='__main__':unittest.main()

class ClefOctaveTests(unittest.TestCase):
    def fixture(self):
        root=E.fromstring('''<score-partwise><part><measure><attributes><divisions>1</divisions><clef><sign>G</sign><line>2</line><clef-octave-change>1</clef-octave-change></clef></attributes><note><pitch><step>C</step><octave>6</octave></pitch><duration>1</duration></note></measure></part></score-partwise>''')
        page=dict(width=300,height=300,systems=[dict(id='1',staves=[dict(lines=[[[10,50+i*10],[200,50+i*10]] for i in range(5)])])])
        e=SimpleNamespace(mapping=dict(pages=[page],notes=[dict(measureIndex=0,staff=1,box=[100,60,10,10])]),
                          events={(1,'1',1):[dict(x=15,right=35,gap=10,kind=('G','2'))]},
                          vector=SimpleNamespace(proof=SimpleNamespace(page_sizes=[(300,300)]),symbols=[[dict(x=15,y=80,c='\ue050',font='Petaluma')]]),
                          pitch_at=lambda *args:dict(clef=('G','2'),step='C',octave=5,alter=0))
        return root,e

    def test_source_proven_octave_error_updates_notes_and_clef_together(self):
        from livesheet.pdf_attribute_recovery import repair_false_clef_octave
        root,e=self.fixture()
        self.assertTrue(repair_false_clef_octave(e,root))
        self.assertEqual(root.findtext('.//pitch/octave'),'5')
        self.assertIsNone(root.find('.//clef-octave-change'))
        self.assertFalse(repair_false_clef_octave(e,root))

    def test_genuine_octave_label_or_unexplained_pitch_keeps_original(self):
        from livesheet.pdf_attribute_recovery import repair_false_clef_octave
        for bad in ('eight','unknown-label','pitch','alter','clef','later-clef','unmapped','transpose'):
            root,e=self.fixture()
            if bad in ('eight','unknown-label'):e.vector.symbols[0].append(dict(x=22,y=40,c='8' if bad=='eight' else '?',font='Text'))
            if bad=='pitch':root.find('.//pitch/step').text='D'
            if bad=='alter':E.SubElement(root.find('.//pitch'),'alter').text='1'
            if bad=='clef':e.events[(1,'1',1)][0]['kind']=('F','4')
            if bad=='later-clef':root.find('.//attributes').append(deepcopy(root.find('.//clef')))
            if bad=='unmapped':e.mapping['notes']=[]
            if bad=='transpose':E.SubElement(root.find('.//attributes'),'transpose')
            before=E.tostring(root)
            with self.subTest(bad=bad),self.assertRaises(Decline):repair_false_clef_octave(e,root)
            self.assertEqual(before,E.tostring(root))
