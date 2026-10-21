import unittest
from copy import deepcopy
from fractions import Fraction as F
import xml.etree.ElementTree as E
from livesheet.pdf_pedal_recovery import printed_span, replace_span
from livesheet.pdf_rhythm_repair import Decline


def fixture():
    m=E.fromstring('''<measure><attributes><divisions>1</divisions></attributes>
      <direction placement="below"><direction-type><pedal type="start" line="no"/></direction-type><staff>1</staff><sound damper-pedal="yes"/></direction>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration></note>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>1</duration></note>
      <note><pitch><step>E</step><octave>5</octave></pitch><duration>1</duration></note>
      <note><pitch><step>F</step><octave>5</octave></pitch><duration>1</duration></note></measure>''')
    anchors=[dict(node=n,onset=i,x=100+i*50) for i,n in enumerate(m.findall('note'))]
    geo=dict(gap=10,bar=dict(left=50,right=300),sx=1,bounds=[(50,90),(170,210)],
             symbols=[dict(font='Leland',c='\ue650',x=96,y=130,w=30)],
             drawings=[dict(stroke=(0,0,0),fill=None,width=1,items=[('l',(130,130),(243,130))])])
    return m,anchors,geo


class PedalTests(unittest.TestCase):
    def test_line_release_is_anchored_to_a_note_without_changing_notes(self):
        m,a,g=fixture();before=E.tostring(m);start,stop,proof=printed_span(g,a)
        self.assertEqual((start['onset'],stop['onset']),(0,3))
        new=replace_span(m,start,stop,m.find('direction'),F(1))
        self.assertEqual(E.tostring(m),before)
        self.assertEqual([E.tostring(n) for n in new.findall('note')],[E.tostring(n) for n in m.findall('note')])
        self.assertEqual([n.get('type') for n in new.findall('direction/direction-type/pedal')],['start','stop'])
        self.assertEqual(list(new).index(new.findall('direction')[1])+1,list(new).index(new.findall('note')[3]))

    def test_missing_or_ambiguous_source_never_infers_release(self):
        for bad in ('unknown-font','duplicate-mark','duplicate-line','missing-line','endpoint','staff-line','slope','ambiguous-attack','system-edge'):
            with self.subTest(bad=bad):
                m,a,g=fixture()
                if bad=='unknown-font':g['symbols'][0]['font']='unknown'
                if bad=='duplicate-mark':g['symbols']*=2
                if bad=='duplicate-line':g['drawings']*=2
                if bad=='missing-line':g['drawings']=[]
                if bad=='endpoint':g['drawings'][0]['items']=[('l',(130,130),(220,130))]
                if bad=='staff-line':g['symbols'][0]['y']=90
                if bad=='slope':g['drawings'][0]['items']=[('l',(130,130),(243,135))]
                if bad=='ambiguous-attack':a.append(dict(node=a[-1]['node'],x=251,onset=3.5))
                if bad=='system-edge':g['bar']['right']=244
                with self.assertRaises(Decline):printed_span(g,a)

    def test_conflicting_instructions_are_not_replaced(self):
        for bad in ('offset','stop','sound','combined','different-onset'):
            m,a,g=fixture();d=m.find('direction')
            if bad=='offset':E.SubElement(d,'offset').text='-1'
            if bad=='stop':d.find('.//pedal').set('type','stop')
            if bad=='sound':d.find('sound').set('tempo','100')
            if bad=='combined':E.SubElement(d.find('direction-type'),'wedge',type='crescendo')
            if bad=='different-onset':m.remove(d);m.insert(3,d)
            before=E.tostring(m)
            with self.assertRaises(Decline):replace_span(m,a[0],a[-1],d,F(1))
            self.assertEqual(before,E.tostring(m))

if __name__=='__main__':unittest.main()
