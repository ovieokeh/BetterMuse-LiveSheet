import unittest
from copy import deepcopy
from livesheet.pdf_staff_evidence import staves


def fixture(font='Bravura'):
    drawings=[dict(color=(0,0,0),width=.5,items=[('l',(20,100+i*5),(90,100+i*5))]) for i in range(5)]
    symbols=[dict(font=font,c='\ue050',x=21,y=115),dict(font=font,c='\uf4bc',x=45,y=110)]
    return drawings,symbols


class StaffEvidenceTests(unittest.TestCase):
    def test_short_staff_requires_five_lines_clef_and_aligned_note(self):
        for font in ('Bravura','Petaluma'):
            d,s=fixture(font);found=staves(d,s)
            self.assertEqual(len(found),1);self.assertEqual(found[0]['lengthInGaps'],14)
        for bad in ('line','spacing','clef','head','unknown','off-step','background'):
            with self.subTest(bad=bad):
                d,s=fixture()
                if bad=='line':d.pop()
                if bad=='spacing':d[2]['items']=[('l',(20,112),(90,112))]
                if bad=='clef':s[0]['y']=110
                if bad=='head':s.pop()
                if bad=='unknown':s[1]['font']='unknown'
                if bad=='off-step':s[1]['y']=111.25
                if bad=='background':
                    for x in d:x['color']=None
                self.assertEqual(staves(d,s),[])

    def test_adjacent_measure_segments_are_one_full_length_staff(self):
        d,s=fixture()
        d.extend(dict(color=(0,0,0),width=.5,items=[('l',(90,100+i*5),(300,100+i*5))]) for i in range(5))
        found=staves(d,s)
        self.assertEqual(len(found),1);self.assertEqual(found[0]['lengthInGaps'],56)

    def test_duplicate_clef_declines_instead_of_guessing(self):
        d,s=fixture();s.append(deepcopy(s[0]));self.assertEqual(staves(d,s),[])

if __name__=='__main__':unittest.main()
