import unittest
from copy import deepcopy
from fractions import Fraction as F
import xml.etree.ElementTree as E
from livesheet.pdf_dynamic_recovery import repair_bar
from livesheet.pdf_rhythm_repair import Decline


def fixture():
    m=E.fromstring('''<measure><direction placement="above"><direction-type><dynamics><p/></dynamics></direction-type><staff>1</staff><sound dynamics="56"/></direction><note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration></note></measure>''')
    geo=dict(gap=10,sx=1,bounds=[(100,140),(200,240)],bar=dict(left=20,right=150),symbols=[dict(x=78,y=90,font='Petaluma',c='\ue522')])
    return m,geo,[dict(x=80,onset=0)]


class DynamicTests(unittest.TestCase):
    def test_corrects_letter_and_discards_only_its_derived_loudness(self):
        m,g,a=fixture();before=E.tostring(m);new,changes=repair_bar(m,g,a,F(1))
        self.assertEqual(E.tostring(m),before);self.assertIsNotNone(new.find('.//dynamics/f'))
        self.assertIsNone(new.find('.//sound'));self.assertEqual(changes[0]['toMark'],'f')
        self.assertEqual(E.tostring(m.find('note')),E.tostring(new.find('note')))
        _,again=repair_bar(new,g,a,F(1));self.assertEqual(again,[])

    def test_source_proven_matching_letter_normalizes_omr_override(self):
        for value in ('40','56','90','106'):
            m,g,a=fixture();g['symbols'][0]['c']='\ue520';m.find('.//sound').set('dynamics',value)
            before=E.tostring(m);new,changes=repair_bar(m,g,a,F(1))
            self.assertEqual(before,E.tostring(m));self.assertIsNotNone(new.find('.//dynamics/p'))
            self.assertIsNone(new.find('.//sound'))
            self.assertEqual(changes[0]['removedNumericOverrides'],[value])
            self.assertEqual(repair_bar(new,g,a,F(1))[1],[])


    def test_uncertain_marks_or_timing_never_override_existing_dynamics(self):
        for bad in ('unknown','absent','duplicate','compound','offset','below','sound','onset','combined'):
            m,g,a=fixture();d=m.find('direction')
            if bad=='unknown':g['symbols'][0]['font']='unknown'
            if bad=='absent':g['symbols']=[]
            if bad=='duplicate':g['symbols']*=2
            if bad=='compound':g['symbols'].append(dict(x=86,y=90,font='Petaluma',c='\ue521'))
            if bad=='offset':E.SubElement(d,'offset').text='1'
            if bad=='below':d.set('placement','below')
            if bad=='sound':d.find('sound').set('tempo','90')
            if bad=='onset':a[0]['onset']=1
            if bad=='combined':E.SubElement(d.find('direction-type'),'wedge',type='crescendo')
            before=E.tostring(m)
            with self.subTest(bad=bad),self.assertRaises(Decline):repair_bar(m,g,a,F(1))
            self.assertEqual(before,E.tostring(m))

if __name__=='__main__':unittest.main()
