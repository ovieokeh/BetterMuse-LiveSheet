import copy
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from livesheet.pdf_repeat_recovery import brackets, recover, read_evidence, Decline
from livesheet.pdf_reference import read_score, compare


def evidence():
    def bracket(a,b,label=None,left=False,right=False):
        lines=[((a,40),(b,40))]
        if left:lines.append(((a,40),(a,90)))
        if right:lines.append(((b,40),(b,90)))
        chars=[dict(text=label,box=[a+4,47,a+12,66])] if label else []
        return lines,chars
    a,ac=bracket(400,500,'1',True)
    b,bc=bracket(10,97,right=True)
    c,cc=bracket(103,300,'2',True,True)
    return [dict(page=1,id='1',gap=10,top=100,firstHead=30,
                 bars=[dict(index=i,left=i*100,right=(i+1)*100) for i in range(5)],lines=a,chars=ac),
            dict(page=1,id='2',gap=10,top=100,firstHead=30,
                 bars=[dict(index=i+5,left=i*100,right=(i+1)*100) for i in range(3)],lines=b+c,chars=bc+cc)]


def source_xml():
    root=ET.Element('score-partwise',version='4.0');part=ET.SubElement(root,'part',id='P1')
    for i in range(8):
        m=ET.SubElement(part,'measure',number=str(i+1))
        if i==0:ET.SubElement(ET.SubElement(m,'attributes'),'divisions').text='1'
        n=ET.SubElement(m,'note');p=ET.SubElement(n,'pitch');ET.SubElement(p,'step').text='C';ET.SubElement(p,'octave').text='4';ET.SubElement(n,'duration').text='4'
        if i==1:ET.SubElement(ET.SubElement(m,'barline',location='left'),'repeat',direction='forward')
        for at,loc,num,kind in [(4,'left','8','start'),(4,'right','8','discontinue'),(5,'left','2','start'),(5,'right','2','stop'),(6,'left','2','start'),(7,'right','2','stop')]:
            if i==at:ET.SubElement(ET.SubElement(m,'barline',location=loc),'ending',number=num,type=kind)
        if i==5:ET.SubElement(m.find("barline[@location='right']"),'repeat',direction='backward')
    return root


def write_xml(path,root):
    with ZipFile(path,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        z.writestr('score.xml',ET.tostring(root))


class RepeatTests(unittest.TestCase):
    def test_multisystem_ending_and_independent_expected_route(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=Path(d)/'source.mxl',Path(d)/'result.mxl';write_xml(a,source_xml());raw=a.read_bytes()
            mapping=dict(sha256='source',measures=[{}]*8,musicxmlFiles=[str(a)],notes=[dict(id='unchanged',midi=60)])
            with patch('livesheet.pdf_repeat_recovery.read_evidence',return_value=evidence()):
                updated,report=recover(a,b,Path('unused.pdf'),mapping)
            self.assertEqual(report['expectedMeasureVisits'],[0,1,2,3,4,5,1,2,3,6,7])
            self.assertEqual(updated['notes'],mapping['notes']);self.assertEqual(a.read_bytes(),raw)
            before,after=read_score(a),read_score(b)
            for x,y in zip(before['parts'][0]['measures'],after['parts'][0]['measures']):self.assertEqual(x['notes'],y['notes'])
            self.assertEqual([(s['number'],s['start'],s['end']) for s in report['printedEndings']],[('1',4,5),('2',6,7)])
            with patch('livesheet.pdf_repeat_recovery.read_evidence',return_value=evidence()):
                _,second=recover(b,Path(d)/'again.mxl',Path('unused.pdf'),updated)
            self.assertEqual(read_score(b),read_score(Path(d)/'again.mxl'))

    def test_missing_label_fingering_and_missing_hook_decline(self):
        for mutation in ['label','fingering','multi-number','hook','continuation','boundary']:
            with self.subTest(mutation=mutation):
                e=evidence()
                if mutation=='label':e[0]['chars']=[]
                if mutation=='fingering':e[1]['chars'].append(dict(text='5',box=[104,47,112,66]))
                if mutation=='multi-number':e[0]['chars'].append(dict(text='2',box=[416,47,424,66]))
                if mutation=='hook':e[1]['lines'].pop()
                if mutation=='continuation':e[1]['firstHead']=-10
                if mutation=='boundary':e[1]['bars'][-1]['right']=360
                with self.assertRaises(Decline):brackets(e)

    def test_open_final_second_ending_requires_explicit_opt_in(self):
        e=evidence();e[-1]['lines'].pop()
        with self.assertRaisesRegex(Decline,'unclosed'):brackets(e)
        result=brackets(e,allow_terminal_open=True)
        self.assertTrue(result[-1]['openFinal'])
        self.assertEqual((result[-1]['start'],result[-1]['end']),(6,7))
        e.append(dict(e[-1],lines=[],chars=[],bars=[dict(index=8,left=0,right=100)]))
        with self.assertRaisesRegex(Decline,'unclosed'):brackets(e,allow_terminal_open=True)

    def test_unsupported_repeat_topology_does_not_write(self):
        for mode in ['times','extra','navigation','backward']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                r=source_xml()
                if mode=='times':r.find('.//repeat').set('times','3')
                if mode=='extra':ET.SubElement(r.find('part/measure'),'barline',location='left').append(ET.Element('repeat',direction='forward'))
                if mode=='navigation':ET.SubElement(r.find('part/measure'),'sound',dacapo='yes')
                if mode=='backward':r.findall('part/measure')[5].find("barline[@location='right']/repeat").set('direction','forward')
                a,b=Path(d)/'s.mxl',Path(d)/'t.mxl';write_xml(a,r)
                with patch('livesheet.pdf_repeat_recovery.read_evidence',return_value=evidence()),self.assertRaises(Decline):
                    recover(a,b,Path('unused.pdf'),dict(sha256='a',measures=[{}]*8,musicxmlFiles=[str(a)]))
                self.assertFalse(b.exists())

    def test_rescue_keeps_failure_on_wrong_traversal_or_invalid_package(self):
        from livesheet.worker import recover_incomplete_repeats
        from livesheet.recognition import default_config
        for mode in ('order','package'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                folder=Path(d); cache=folder/'recognition'; (cache/'score').mkdir(parents=True)
                mapping=dict(musicxmlFiles=[str(folder/'source.mxl')])
                (cache/'score/mapping.json').write_text(json.dumps(mapping))
                original=dict(status='failed',error='incomplete-playback',recognitionDPI=300)
                report=dict(expectedMeasureVisits=[0,1,0,2])
                visits=[0,1,2] if mode=='order' else [0,1,0,2]
                def prepare(*args, **kwargs):
                    target=folder/'repeat-recovery-web/score';target.mkdir(parents=True)
                    (target/'score.json').write_text(json.dumps(dict(provenance={})))
                with patch('livesheet.recognition.CACHE',cache), patch('livesheet.pdf_repeat_recovery.recover',return_value=(mapping,report)), patch('livesheet.playback.export_performance',return_value=([],0,[],{},[dict(element=i) for i in visits])), patch('livesheet.playback.prepare',side_effect=prepare), patch('livesheet.recognition.validate_package',side_effect=ValueError('bad-package')) as validate, patch('livesheet.recognition.promote') as promote:
                    result=recover_incomplete_repeats(folder,dict(pageCount=1),original,lambda *_:None,default_config())
                self.assertIs(result,original);promote.assert_not_called()
                if mode=='package':validate.assert_called_once()
                else:validate.assert_not_called()

    def test_wrong_source_identity_declines(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.pdf';p.write_bytes(b'not the source')
            with self.assertRaisesRegex(Decline,'identity'):read_evidence(p,dict(sha256='wrong'))

if __name__=='__main__':unittest.main()
