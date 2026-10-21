from copy import deepcopy
from fractions import Fraction as F
import unittest
import xml.etree.ElementTree as E
from test_pdf_whole_recovery import fixture
from livesheet.pdf_whole_recovery import recover_bar
from livesheet.pdf_fermata_recovery import repair_bar
from livesheet.pdf_rhythm_repair import Decline

CLEFS={1:[(0,F(0),('G','2'))],2:[(0,F(0),('F','4'))]}


def completed():
    engine,m=fixture(True)
    new,records,_=recover_bar(engine,0,m,F(2),0,CLEFS)
    for n,r in zip(new.findall('note'),records):r['onset']=0 if n.findtext('staff')=='2' else r['onset']
    engine.mapping['notes']=records
    return engine,new


class FermataTests(unittest.TestCase):
    def test_removes_only_false_hold_over_recovered_whole(self):
        e,m=completed();node=m.findall('note')[-2]
        E.SubElement(E.SubElement(node,'notations'),'fermata',type='inverted',**{'default-y':'-20'})
        before=E.tostring(m);new,changes=repair_bar(e,0,m)
        self.assertEqual([c['action'] for c in changes],['remove-false-hold'])
        self.assertIsNone(new.find('note/notations/fermata'));self.assertEqual(before,E.tostring(m))
        for n in m.findall('note'):
            for nt in n.findall('notations'):n.remove(nt)
        self.assertEqual(E.tostring(new),E.tostring(m))

    def test_absence_or_arbitrary_position_is_not_enough_to_delete_a_hold(self):
        for kind in ('no-position','different-position','no-recovered-glyph','printed-hold'):
            with self.subTest(kind=kind):
                e,m=completed();n=m.findall('note')[-2];f=E.SubElement(E.SubElement(n,'notations'),'fermata',type='inverted',**{'default-y':'-20'})
                if kind=='no-position':f.attrib.pop('default-y')
                if kind=='different-position':f.set('default-y','-55')
                if kind=='no-recovered-glyph':e.mapping['notes'][-1].pop('pdfGlyphID')
                if kind=='printed-hold':e.vector.symbols[0].append(dict(c='\ue4c1',font='Leland',x=25,y=155,w=22))
                with self.assertRaisesRegex(Decline,'no-proven'):repair_bar(e,0,m)

    def test_adds_genuine_printed_fermata_and_is_idempotent(self):
        e,m=completed();e.vector.symbols[0].append(dict(c='\ue4c0',font='Leland',x=25,y=130,w=22))
        new,changes=repair_bar(e,0,m)
        self.assertEqual([c['action'] for c in changes],['add-printed-hold'])
        self.assertEqual(new.findall('note')[-2].find('notations/fermata').get('type'),'upright')
        with self.assertRaisesRegex(Decline,'no-proven'):repair_bar(e,0,new)

    def test_dot_inside_printed_fermata_is_not_a_staccato(self):
        e,m=completed();e.vector.symbols[0].append(dict(c='\ue4c0',font='Leland',x=25,y=130,w=22))
        nt=E.SubElement(m.findall('note')[-2],'notations');arts=E.SubElement(nt,'articulations')
        E.SubElement(arts,'staccato',**{'default-y':'-10'})
        E.SubElement(arts,'accent')
        new,changes=repair_bar(e,0,m)
        self.assertIsNone(new.find('note/notations/articulations/staccato'))
        self.assertIsNotNone(new.find('note/notations/articulations/accent'))
        self.assertIn('remove-fermata-dot-staccato',[c['action'] for c in changes])
        # A separate printed position is not the dot inside the fermata.
        m.find('note/notations/articulations/staccato').set('default-y','-30')
        new,_=repair_bar(e,0,m)
        self.assertIsNotNone(new.find('note/notations/articulations/staccato'))

    def test_printed_hold_on_a_moving_note_preserves_its_duration(self):
        e,m=completed();r=e.mapping['notes'][2];n=m.findall('note')[2]
        before=E.tostring(n.find('duration'));x,y,w,h=r['box']
        e.vector.symbols[0].append(dict(c='\ue4c0',font='Leland',x=x+w/2-11,y=y+h/2-20,w=22))
        new,changes=repair_bar(e,0,m)
        self.assertIsNotNone(new.findall('note')[2].find('notations/fermata'))
        self.assertEqual(E.tostring(new.findall('note')[2].find('duration')),before)
        self.assertEqual([c['action'] for c in changes],['add-printed-hold'])

    def test_ambiguous_duplicate_marks_are_not_added(self):
        e,m=completed();s=dict(c='\ue4c0',font='Leland',x=25,y=130,w=22);e.vector.symbols[0].extend([s,deepcopy(s)])
        with self.assertRaisesRegex(Decline,'no-proven'):repair_bar(e,0,m)


if __name__=='__main__':unittest.main()
