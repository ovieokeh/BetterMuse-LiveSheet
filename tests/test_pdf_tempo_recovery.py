from copy import deepcopy
import unittest
import xml.etree.ElementTree as E
from livesheet.pdf_tempo_recovery import repair_opening, QUARTER
from livesheet.pdf_rhythm_repair import Decline


def fixture():
    m=E.fromstring('''<measure number="1"><attributes><divisions>2</divisions></attributes>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration></note>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>1</duration></note>
      <direction placement="above"><direction-type><words>Andante</words></direction-type>
      <direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>88</per-minute></metronome></direction-type>
      <staff>1</staff><sound tempo="88"/></direction>
      <note><pitch><step>E</step><octave>5</octave></pitch><duration>1</duration></note></measure>''')
    spans=[dict(text='Andante (',font='Text',origin=(95,80)),
           dict(text=QUARTER,font='LelandText',origin=(135,80)),
           dict(text=' = 88)',font='Text',origin=(140,80))]
    return m,[dict(spans=spans)]


def run(m,lines):
    return repair_opening(m,lines,left=60,first_x=100,top=100,gap=10)


class TempoTests(unittest.TestCase):
    def test_source_proven_opening_preserves_every_note_and_direction(self):
        m,lines=fixture();before=E.tostring(m)
        new,report=run(m,lines)
        self.assertEqual(report['fromBeat'],'1');self.assertEqual(report['bpm'],88)
        self.assertLess(list(new).index(new.find('direction')),list(new).index(new.find('note')))
        self.assertEqual([E.tostring(n) for n in m.findall('note')],[E.tostring(n) for n in new.findall('note')])
        self.assertEqual(E.tostring(m.find('direction')),E.tostring(new.find('direction')))
        self.assertEqual(E.tostring(m),before)
        with self.assertRaisesRegex(Decline,'already-at-start'):run(new,lines)

    def test_genuine_mid_measure_printed_tempo_is_not_moved(self):
        m,lines=fixture()
        for s in lines[0]['spans']:s['origin']=(s['origin'][0]+80,80)
        with self.assertRaises(Decline):run(m,lines)

    def test_unknown_ambiguous_or_conflicting_evidence_declines(self):
        for case in ('scan','font','value','unit','dot','duplicate','below','far-above','two-tempos','offset','extra-sound','other-direction','no-words','leading-rest'):
            with self.subTest(case=case):
                m,lines=fixture();d=m.find('direction');metro=d.find('.//metronome')
                if case=='scan':lines=[]
                if case=='font':lines[0]['spans'][1]['font']='Unknown'
                if case=='value':lines[0]['spans'][2]['text']=' = 99)'
                if case=='unit':metro.find('beat-unit').text='half'
                if case=='dot':E.SubElement(metro,'beat-unit-dot')
                if case=='duplicate':lines.append(deepcopy(lines[0]))
                if case in ('below','far-above'):
                    for s in lines[0]['spans']:s['origin']=(s['origin'][0],110 if case=='below' else 0)
                if case=='two-tempos':m.append(deepcopy(d))
                if case=='offset':E.SubElement(d,'offset').text='-2'
                if case=='extra-sound':d.find('sound').set('dynamics','80')
                if case=='other-direction':E.SubElement(d.find('direction-type'),'wedge',type='crescendo')
                if case=='no-words':d.find('.//words').text=''
                if case=='leading-rest':E.SubElement(m.find('note'),'rest')
                original=E.tostring(m)
                with self.assertRaises(Decline):run(m,lines)
                self.assertEqual(original,E.tostring(m))

if __name__=='__main__':unittest.main()

class PrintedTempoTimelineTests(unittest.TestCase):
    def test_midbar_mark_uses_exact_note_anchor_for_supported_fonts(self):
        from livesheet.pdf_tempo_recovery import printed_tempos
        for font in ('LelandText','BravuraText','PetalumaText'):
            _,lines=fixture()
            for s in lines[0]['spans']:s['origin']=(s['origin'][0]+105,80)
            lines[0]['spans'][1]['font']=font
            anchors=[dict(x=100,onset=0),dict(x=150,onset=1),dict(x=200,onset=2)]
            found=printed_tempos(lines,left=60,right=250,top=100,gap=10,anchors=anchors)
            self.assertEqual([(m['bpm'],m['onset']) for m in found],[(88,2)])
            anchors.append(dict(x=201,onset=3))
            with self.assertRaisesRegex(Decline,'anchor-ambiguous'):
                printed_tempos(lines,left=60,right=250,top=100,gap=10,anchors=anchors)

    def test_omitted_tempo_is_added_without_changing_notes(self):
        from fractions import Fraction as F
        from livesheet.pdf_tempo_recovery import repair_measure_tempos
        m,_=fixture();m.remove(m.find('direction'));before=[E.tostring(n) for n in m.findall('note')]
        anchors=[dict(node=n,onset=i*.5,x=100+i*30) for i,n in enumerate(m.findall('note'))]
        marks=[dict(bpm=80,onset=0,x=95,y=80),dict(bpm=120,onset=1,x=160,y=80)]
        new,changes=repair_measure_tempos(m,marks,anchors,F(2))
        self.assertEqual([E.tostring(n) for n in new.findall('note')],before)
        self.assertEqual([c['bpm'] for c in changes],[80,120])
        self.assertEqual(list(new).index(new.findall('direction')[1]),4)
        self.assertEqual(len(m.findall('direction')),0)

    def test_conflicting_or_offset_existing_tempo_is_never_overwritten(self):
        from fractions import Fraction as F
        from livesheet.pdf_tempo_recovery import repair_measure_tempos
        for conflict in ('value','offset','duplicate'):
            m,_=fixture();anchors=[dict(node=n,onset=i*.5,x=100+i*30) for i,n in enumerate(m.findall('note'))]
            marks=[dict(bpm=88,onset=0,x=95,y=80)]
            if conflict=='value':marks[0]['bpm']=99
            if conflict=='offset':E.SubElement(m.find('direction'),'offset').text='1'
            if conflict=='duplicate':marks.append(dict(bpm=88,onset=1,x=160,y=80))
            before=E.tostring(m)
            with self.assertRaises(Decline):repair_measure_tempos(m,marks,anchors,F(2))
            self.assertEqual(E.tostring(m),before)
