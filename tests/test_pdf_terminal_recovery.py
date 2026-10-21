from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as E
from livesheet.pdf_terminal_recovery import omitted_region, verify_barlines, validate_traversal
from livesheet.pdf_rhythm_repair import Decline


def region_fixture():
    root=E.fromstring('<sheet><page><system id="1"><part><staff/><staff/></part><stack left="10" right="100"/><stack left="100" right="200" special="CAUTIONARY" duration="0"/></system></page></sheet>')
    mapping=dict(measures=[dict(page=1,system='1',right=100)],pages=[dict(systems=[dict(id='1',staves=[dict(lines=[[[10,20],[200,20]]]),dict(lines=[[[10,120],[200,120]]])])])])
    return root,mapping


def bar_fixture():
    return dict(gap=10,sx=1,bar=dict(left=100,right=200),bounds=[(20,60),(120,160)],drawings=[dict(stroke=(0,0,0),fill=None,width=1,items=[('l',(x,20),(x,160))]) for x in (100,200)])


class TerminalTests(unittest.TestCase):
    def region(self,root,mapping):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'score.omr'
            with ZipFile(p,'w') as z:z.writestr('sheet#1/sheet#1.xml',E.tostring(root))
            return omitted_region(p,mapping)

    def test_only_empty_adjacent_final_stack_is_candidate(self):
        root,mapping=region_fixture();original=deepcopy(mapping)
        self.assertEqual(self.region(root,mapping),dict(page=1,system='1',left=100,right=200))
        self.assertEqual(mapping,original)
        for bad in ('ordinary','duration','slot','gap','not-end','wrong-system','wrong-page','one-staff'):
            with self.subTest(bad=bad):
                r,m=region_fixture();stack=r.findall('.//stack')[-1]
                if bad=='ordinary':stack.attrib.pop('special')
                if bad=='duration':stack.set('duration','1')
                if bad=='slot':E.SubElement(stack,'slot')
                if bad=='gap':stack.set('left','110')
                if bad=='not-end':m['pages'][0]['systems'][0]['staves'][1]['lines'][0][-1][0]=250
                if bad=='wrong-system':m['measures'][0]['system']='2'
                if bad=='wrong-page':m['measures'][0]['page']=2
                if bad=='one-staff':r.find('.//part').remove(r.findall('.//staff')[-1])
                with self.assertRaises(Decline):self.region(r,m)

    def test_bar_boundaries_must_cover_both_staffs(self):
        verify_barlines(bar_fixture())
        for bad in ('missing','stem','middle','narrow','filled'):
            with self.subTest(bad=bad):
                g=bar_fixture()
                if bad=='missing':g['drawings'].pop()
                if bad=='stem':g['drawings'][1]['items']=[('l',(200,20),(200,60))]
                if bad=='middle':g['drawings'].append(dict(stroke=(0,0,0),fill=None,width=1,items=[('l',(150,20),(150,160))]))
                if bad=='narrow':g['bar']['right']=140
                if bad=='filled':g['drawings'][1]['fill']=(0,0,0)
                with self.assertRaises(Decline):verify_barlines(g)

    def test_final_bar_does_not_bypass_performance_order_guard(self):
        report=dict(expectedMeasureVisits=[0,1,0,2])
        validate_traversal(dict(measures=[dict(id='m'+str(i)) for i in [0,1,0,2]]),report)
        for actual in ([0,1,0],[0,1,2],[0,2,0,1],[0,1,0,2,2]):
            with self.subTest(actual=actual),self.assertRaisesRegex(ValueError,'terminal-performance-order'):
                validate_traversal(dict(measures=[dict(id='m'+str(i)) for i in actual]),report)

class TerminalRecoveryIntegrationTests(unittest.TestCase):
    def fixture(self):
        from test_pdf_whole_recovery import fixture
        from test_pdf_repeat_recovery import write_xml
        e,_=fixture()
        for staff in e.mapping['pages'][0]['systems'][0]['staves']:
            for line in staff['lines']:line[-1][0]=330
        e.mapping['pages'][0]['width']=360;e.vector.proof.page_sizes=[(360,240)]
        e.mapping.update(sha256='synthetic',summary={},notes=[],measures=[dict(number=str(i+1),page=1,system='1',left=10+100*i,right=110+100*i,start=i*4,duration=4) for i in range(2)])
        # Third bar is wider; a plain cautionary-signature area is insufficient.
        for staff in e.mapping['pages'][0]['systems'][0]['staves']:
            for line in staff['lines']:line[-1][0]=330
        e.vector.symbols=[[dict(x=x,y=y,w=16,font='Leland',c='\ue0a2') for x in (60,260) for y in (60,150)]]
        e.vector.symbols[0]+=[dict(x=30,y=y,w=10,font='Leland',c='\ue084') for y in (30,50,130,150)]
        e.vector.proof.glyphs=[[dict(x=s['x']/360,y=s['y']/240,kind='whole',font='Leland') for s in e.vector.symbols[0] if s['c']=='\ue0a2']]
        for events in e.events.values():events[0].update(x=10,right=20)
        e.vector.proof.drawings=[[dict(stroke=(0,0,0),fill=None,width=1,items=[('l',(x,20),(x,160))]) for x in (210,330)]]
        r=E.fromstring('''<score-partwise><part id="P1"><measure number="1"><attributes><divisions>2</divisions><staves>2</staves><clef number="1"><sign>G</sign><line>2</line></clef><clef number="2"><sign>F</sign><line>4</line></clef></attributes><barline location="left"><repeat direction="forward"/></barline></measure><measure number="2"><barline location="left"><ending number="1" type="start"/></barline><barline location="right"><ending number="1" type="stop"/><repeat direction="backward"/></barline></measure></part></score-partwise>''')
        # Retained note events are deliberately arbitrary: this stage must not
        # reinterpret or repair anything in earlier measures.
        for i,m in enumerate(r.find('part')):
            m.insert(1 if i==0 else 0,E.fromstring('<note><pitch><step>D</step><octave>4</octave></pitch><duration>8</duration><voice>1</voice><type>whole</type><staff>1</staff></note>'))
            e.mapping['notes'].append(dict(measureIndex=i,page=1,system='1',staff=1,onset=0,box=[60+100*i,55,16,10]))
        omr=E.fromstring('<sheet><page><system id="1"><part><staff/><staff/></part><stack left="10" right="110"/><stack left="110" right="210"/><stack left="210" right="330" duration="0" special="CAUTIONARY"/></system></page></sheet>')
        evidence=[dict(page=1,id='1',gap=10,top=20,firstHead=60,bars=[dict(index=i,left=a,right=b) for i,(a,b) in enumerate(((10,110),(110,210),(210,330)))],chars=[dict(text=n,box=[x+4,-33,x+12,-14]) for n,x in (('1',110),('2',210))],lines=[((110,-40),(210,-40)),((110,-40),(110,10)),((210,-40),(210,10)),((210,-45),(330,-45)),((210,-45),(210,5))])]
        # The second ending is slightly higher to keep its left hook distinct
        # from the first ending's right hook.
        evidence[0]['chars'][1]['box']=[214,-38,222,-19]
        return e,r,omr,evidence

    def run_recovery(self,e,r,omr,evidence,folder):
        from unittest.mock import patch
        from test_pdf_repeat_recovery import write_xml
        from livesheet.pdf_terminal_recovery import recover
        source=folder/'source.mxl';write_xml(source,r)
        omrfile=folder/'source.omr'
        with ZipFile(omrfile,'w') as z:z.writestr('sheet#1/sheet#1.xml',E.tostring(omr))
        original=source.read_bytes()
        def engine(pdf,mapping):
            result=deepcopy(e);result.mapping=mapping;result.vector.proof.mapping=mapping
            return result
        with patch('livesheet.pdf_terminal_recovery.ClefEvidence',side_effect=engine),patch('livesheet.pdf_repeat_recovery.read_evidence',return_value=evidence):
            result,report=recover(source,folder/'candidate.mxl',Path('unused.pdf'),e.mapping,omrfile)
        self.assertEqual(source.read_bytes(),original)
        return result,report

    def test_whole_final_bar_added_without_changing_previous_notes(self):
        from livesheet.pdf_rhythm_repair import load_mxl
        e,r,omr,ev=self.fixture();before=deepcopy(e.mapping)
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);result,report=self.run_recovery(e,r,omr,ev,folder)
            new,_=load_mxl(folder/'candidate.mxl')
        self.assertEqual(report['expectedMeasureVisits'],[0,1,0,2])
        self.assertEqual(report['addedNotes'],2)
        self.assertEqual(len(result['measures']),3)
        self.assertEqual([(n.findtext('pitch/step'),n.findtext('pitch/octave'),n.findtext('duration')) for n in new.findall('part/measure')[-1].findall('note')],[('E','4','8'),('B','2','8')])
        self.assertEqual(e.mapping,before)
        for a,b in zip(r.findall('.//note'),new.findall('.//note')):self.assertEqual(E.tostring(a),E.tostring(b).strip())

    def test_cautionary_label_alone_never_adds_music(self):
        for bad in ('no-heads','one-staff','mixed','meter','unknown-symbol','tie','barline','ending'):
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as d:
                e,r,o,ev=self.fixture()
                if bad=='no-heads':e.vector.symbols[0]=[s for s in e.vector.symbols[0] if s['x']<210]
                if bad=='one-staff':e.vector.symbols[0]=[s for s in e.vector.symbols[0] if not(s['x']>210 and s['y']>100)]
                if bad=='mixed':e.vector.symbols[0][2]['c']='\ue0a4'
                if bad=='meter':e.vector.symbols[0][-1]['c']='\ue083'
                if bad=='unknown-symbol':e.vector.symbols[0].append(dict(x=240,y=60,w=8,font='Text',c='8'))
                if bad=='tie':e.vector.proof.drawings[0].append(dict(items=[('c',(252,150),(245,130),(220,130),(215,150))]))
                if bad=='barline':e.vector.proof.drawings[0].pop()
                if bad=='ending':ev[0]['chars'].pop()
                before=deepcopy(e.mapping)
                with self.assertRaises(Decline):self.run_recovery(e,r,o,ev,Path(d))
                self.assertEqual(e.mapping,before)
