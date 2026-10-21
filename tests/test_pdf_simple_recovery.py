from collections import defaultdict
from copy import deepcopy
from fractions import Fraction as F
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as E
from livesheet.pdf_clef_recovery import ClefEvidence
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
from livesheet.pdf_rhythm_repair import Decline
from livesheet.pdf_simple_recovery import decode, build_bar, meter_from_pdf, require_rest_only, without_false_header_slurs
from livesheet.testing.check_pdf_meter_revision import validate_budgets,corrected_spec
from livesheet.testing.pdf_performance_fixtures import CASES
from livesheet.pdf_context_recovery import geometry
from livesheet.pdf_vector_recovery import beam_count
from livesheet.pdf_music_fonts import encoded_music_spans


def fixture():
    lines=lambda top:[[[0,top+10*j],[260,top+10*j]] for j in range(5)]
    page=dict(width=300,height=280,systems=[dict(id='1',staves=[dict(lines=lines(30)),dict(lines=lines(170))])])
    mapping=dict(measures=[dict(page=1,system='1',left=0,right=260)],pages=[page],notes=[])
    symbols=[];glyphs=[];drawings=[]
    m=E.Element('measure',number='1');a=E.SubElement(m,'attributes');E.SubElement(a,'divisions').text='2'
    for s in (1,2):
        if s==2:E.SubElement(E.SubElement(m,'backup'),'duration').text='6'
        for x,y,kind,dur,step,octave,onset in ([(60,50,'half',2,'B',4,0),(210,55,'filled',1,'A',4,2)] if s==1 else [(60,180,'half',2,'F',3,0),(150,180,'half',2,'F',3,2)]):
            c='\ue0a3' if kind=='half' else '\ue0a4'
            symbols.append(dict(font='Leland',c=c,x=x,y=y,w=12));glyphs.append(dict(font='Leland',kind=kind,x=x/300,y=y/280))
            drawings.append(dict(stroke=(0,0,0),fill=None,width=1,items=[('l',(x,y),(x,y+35))]))
            n=E.SubElement(m,'note');p=E.SubElement(n,'pitch');E.SubElement(p,'step').text=step;E.SubElement(p,'octave').text=str(octave)
            E.SubElement(n,'duration').text=str(dur*2);E.SubElement(n,'voice').text=str(s);E.SubElement(n,'type').text='half' if kind=='half' else 'quarter';E.SubElement(n,'staff').text=str(s)
            mapping['notes'].append(dict(measureIndex=0,staff=s,onset=onset,box=[x,y-5,12,10],page=1,system='1'))
    symbols.append(dict(font='Leland',c='\ue4e5',x=150,y=50,w=10))
    for top in (30,170):
        for c,y in [('\ue084',top),('\ue084',top+40)]:symbols.append(dict(font='Leland',c=c,x=20,y=y,w=10))
    proof=object.__new__(PDFHeadEvidence);proof.mapping=mapping;proof.page_sizes=[(300,280)];proof.glyphs=[glyphs];proof.drawings=[drawings]
    engine=object.__new__(ClefEvidence);engine.mapping=mapping;engine.vector=SimpleNamespace(proof=proof,symbols=[symbols]);engine.events=defaultdict(list)
    engine.events[(1,'1',1)]=[dict(x=-20,right=-10,kind=('G','2'))];engine.events[(1,'1',2)]=[dict(x=-20,right=-10,kind=('F','4'))]
    return engine,m


class SimpleRecoveryTests(unittest.TestCase):
    def test_explicit_rest_restores_onset_without_changing_pitched_notes(self):
        engine,m=fixture();before=E.tostring(m)
        plan=decode(engine,0,m,F(2),0,(4,4));require_rest_only(engine,0,m,plan)
        new,records=build_bar(m,plan)
        upper=[n for n in new.findall('note') if n.findtext('staff')=='1']
        self.assertEqual([(n.findtext('pitch/step'),n.findtext('duration')) for n in upper],[('B','4'),(None,'2'),('A','2')])
        self.assertEqual(E.tostring(m),before);self.assertEqual(len(records),4)
        self.assertEqual([g['onset'] for g in plan['groups'] if g['staff']==1 and g['heads']],[0,3])

    def test_both_staffs_must_show_same_meter(self):
        engine,_=fixture();self.assertEqual(meter_from_pdf(geometry(engine,0))[0],(4,4))
        engine.vector.symbols[0][-1]['c']='\ue088'
        with self.assertRaisesRegex(Decline,'disagree'):meter_from_pdf(geometry(engine,0))

    def test_incomplete_lane_or_duplicate_stem_declines(self):
        for kind in ('rest','stem','meter'):
            engine,m=fixture()
            if kind=='rest':engine.vector.symbols[0]=[s for s in engine.vector.symbols[0] if s['c']!='\ue4e5']
            if kind=='stem':engine.vector.proof.drawings[0].append(deepcopy(engine.vector.proof.drawings[0][0]))
            with self.subTest(kind=kind),self.assertRaises(Decline):decode(engine,0,m,F(2),0,(3,4) if kind=='meter' else (4,4))

    def test_rest_only_path_rejects_pitch_duration_voice_and_existing_rest_changes(self):
        for kind in ('pitch','duration','voice','rest'):
            engine,m=fixture();plan=decode(engine,0,m,F(2),0,(4,4))
            if kind=='pitch':m.find('note/pitch/step').text='C'
            if kind=='duration':m.find('note/duration').text='2'
            if kind=='voice':m.findall('note')[1].find('voice').text='3'
            if kind=='rest':E.SubElement(E.SubElement(m,'note'),'rest')
            with self.subTest(kind=kind),self.assertRaises(Decline):require_rest_only(engine,0,m,plan)

    def test_expression_lyrics_and_midbar_attributes_are_not_discarded(self):
        for kind in ('lyric','articulation','time','velocity','offset'):
            engine,m=fixture();plan=decode(engine,0,m,F(2),0,(4,4))
            if kind=='lyric':E.SubElement(m.find('note'),'lyric')
            if kind=='articulation':E.SubElement(E.SubElement(m.find('note'),'notations'),'articulations')
            if kind=='time':E.SubElement(m,'attributes')
            if kind=='velocity':m.find('note').set('dynamics','75')
            if kind=='offset':E.SubElement(E.SubElement(m,'direction'),'offset').text='2'
            with self.subTest(kind=kind),self.assertRaises(Decline):build_bar(m,plan)

    def test_white_background_is_not_a_beam_but_ambiguous_ink_still_declines(self):
        polygon=dict(fill=(1,1,1),items=[('l',(0,0),(300,0)),('l',(300,0),(300,600)),('l',(300,600),(0,600)),('l',(0,600),(0,0))])
        self.assertEqual(beam_count([polygon],(50,300,340),'up',10),0)
        polygon['fill']=(0,0,0)
        with self.assertRaises(Decline):beam_count([polygon],(50,300,340),'up',10)

    def test_only_slur_touching_false_meter_note_can_be_removed(self):
        engine,m=fixture();plan=decode(engine,0,m,F(2),0,(4,4))
        nodes=m.findall('note')
        for n,kind in ((nodes[0],'start'),(nodes[1],'stop')):
            E.SubElement(E.SubElement(n,'notations'),'slur',type=kind,number='1')
        kept=without_false_header_slurs(m,plan,engine.mapping)
        self.assertEqual(len(kept.findall('note/notations/slur')),2)
        engine.mapping['notes'][0]['box']=[22,44,10,10]
        cleaned=without_false_header_slurs(m,plan,engine.mapping)
        self.assertEqual(len(cleaned.findall('note/notations/slur')),0)
        self.assertEqual(len(m.findall('note/notations/slur')),2)

    def test_reference_amendment_rejects_original_overfull_bar(self):
        before=deepcopy(CASES['meter-ties'])
        with self.assertRaisesRegex(ValueError,'Measure 3'):validate_budgets(before)
        validate_budgets(corrected_spec())
        self.assertEqual(CASES['meter-ties'],before)

    def test_font_name_requires_encoding_evidence(self):
        def spans(chars):return encoded_music_spans([dict(lines=[dict(spans=[dict(font='MScore',chars=[dict(c=c) for c in chars])])])])
        self.assertEqual(spans('\ue0a3\ue050')[0]['font'],'MScore3')
        for chars in ('\ue12c\ue19e','\ue0a3','\ue0a3\ue050\ue12c'):
            self.assertEqual(spans(chars)[0]['font'],'MScore')

if __name__=='__main__':unittest.main()
