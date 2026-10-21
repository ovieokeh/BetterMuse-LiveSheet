"""Preparation safety checks independent of the local score corpus and engines."""
import copy
import hashlib
import math
import mido
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livesheet import recognition as pipeline
from livesheet.score_recognition import read_mxl, read_omr
from livesheet import playback as playback


def package():
    return dict(ready=True, format='pdf', pages=[{}], duration=1, issues=[],
                notes=[dict(id='n0', midi=60, velocity=80, start=0, duration=1, page=0, measureId='m0')],
                measures=[dict(id='m0', start=0, end=1, page=0, box=[0,0,1,1])], cursors=[])


class PreparationTests(unittest.TestCase):
    def test_rejects_missing_source_page(self):
        with self.assertRaisesRegex(ValueError, 'page count'):
            pipeline.validate_package(package(), 2)

    def test_rejects_unsafe_or_disconnected_timeline(self):
        edits = [lambda p: p['notes'].append(copy.deepcopy(p['notes'][0])),
                 lambda p: p['notes'][0].update(midi=150),
                 lambda p: p['notes'][0].update(velocity=float('nan')),
                 lambda p: p['notes'][0].update(measureId='missing'),
                 lambda p: p['notes'][0].update(duration=2),
                 lambda p: p['measures'][0].update(end=0),
                 lambda p: p['measures'][0].update(box=[0,0,2,2])]
        for edit in edits:
            data = package(); edit(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                pipeline.validate_package(data, 1)

    def test_repeats_allow_multiple_measure_visits(self):
        data=package(); data['duration']=2
        data['measures'].append(dict(data['measures'][0], start=1, end=2))
        data['notes'].append(dict(data['notes'][0], id='n1', start=1))
        pipeline.validate_package(data, 1)

    def test_failed_rebuild_preserves_existing_pages_and_package(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); source=root/'source'; source.write_bytes(b'original')
            public=root/'public'; target=public/'example'; target.mkdir(parents=True)
            before=json.dumps({**package(),'sourceSHA256':pipeline.sha(source)}); (target/'score.json').write_text(before)
            (target/'page-1.webp').write_bytes(b'old usable page')
            entry=dict(id='example', assetPath='source', sha256=pipeline.sha(source), format='musicxml', pageCount=1)
            args=SimpleNamespace(force=False,retry_failed=True,min_free_gb=0)
            def fail(entry, mapping, enabled, folder, staging, **kwargs):
                out=staging/entry['id'];out.mkdir(parents=True)
                (out/'page-1.webp').write_bytes(b'broken new page')
                raise RuntimeError('engine failed')
            with patch.object(pipeline,'CACHE',root/'cache'), patch.object(pipeline,'RESULTS',root/'results'), patch.object(pipeline,'STAGING',root/'staging'), patch.object(pipeline.playback,'BUNDLE',root), patch.object(pipeline.playback,'PUBLIC',public), patch.object(pipeline.playback,'prepare',side_effect=fail):
                outcome=pipeline.process_entry(entry,args,'engine','adapter')
            self.assertEqual(outcome['status'],'failed')
            self.assertTrue(outcome['previousReady'])
            self.assertEqual((target/'score.json').read_text(),before)
            self.assertEqual((target/'page-1.webp').read_bytes(),b'old usable page')
            self.assertTrue((root/'results/scores/example.json').exists())

    def test_interruption_is_retryable_without_retry_failed(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); source=root/'source'; source.write_bytes(b'original')
            target=root/'public/example';target.mkdir(parents=True)
            (target/'score.json').write_text(json.dumps({**package(),'sourceSHA256':pipeline.sha(source)}))
            entry=dict(id='example',assetPath='source',sha256=pipeline.sha(source),format='musicxml',pageCount=1)
            args=SimpleNamespace(force=False,retry_failed=False,min_free_gb=0)
            def interrupt(*args, **kwargs):
                pipeline.stop.set();raise RuntimeError('interrupted')
            try:
                with patch.object(pipeline,'CACHE',root/'cache'), patch.object(pipeline,'RESULTS',root/'results'), patch.object(pipeline,'STAGING',root/'staging'), patch.object(playback,'CACHE',root/'playback'),patch.object(playback,'BUNDLE',root), patch.object(playback,'PUBLIC',root/'public'):
                    with patch.object(playback,'prepare',side_effect=interrupt):
                        first=pipeline.process_entry(entry,args,'engine','adapter')
                    self.assertEqual(first['status'],'interrupted')
                    pipeline.stop.clear()
                    with patch.object(playback,'prepare',side_effect=RuntimeError('another attempt')) as retry:
                        second=pipeline.process_entry(entry,args,'engine','adapter')
                    retry.assert_called_once();self.assertEqual(second['status'],'failed')
            finally:
                pipeline.stop.clear()

    def test_interrupted_promotion_recovers_job_local_or_legacy_backup(self):
        from livesheet.pdf_pipeline.config import ExportConfig, RecognitionConfig
        for layout in ('public', 'staging'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                source = root/'source'; source.write_bytes(b'original')
                backup = root/layout/'example.previous'; backup.mkdir(parents=True)
                before = json.dumps({**package(), 'sourceSHA256':pipeline.sha(source)})
                (backup/'score.json').write_text(before)
                (backup/'page-1.webp').write_bytes(b'usable')
                config = RecognitionConfig(root/'cache', root/'results', root/'staging', root/'ocr',
                    ExportConfig(root, root/'performance', root/'public', root/'trial', 'unused', 'test'))
                entry = dict(id='example', assetPath='source', sha256=pipeline.sha(source), format='musicxml', pageCount=1)
                args = SimpleNamespace(force=False, retry_failed=True, min_free_gb=0)
                with patch.object(playback, 'prepare', side_effect=RuntimeError('new export failed')):
                    result = pipeline.process_entry(entry, args, 'engine', 'adapter', config=config)
                self.assertTrue(result['previousReady'])
                self.assertEqual((root/'public/example/score.json').read_text(), before)
                self.assertEqual((root/'public/example/page-1.webp').read_bytes(), b'usable')
                self.assertFalse(backup.exists())

    def test_fresh_failed_conversion_still_produces_readable_pages(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); source=root/'source'; source.write_bytes(b'original')
            Image.new('RGB',(4,4),'white').save(root/'page.png')
            entry=dict(id='example', assetPath='source', sha256=pipeline.sha(source), format='musicxml', pageCount=1, previews=['page.png'])
            args=SimpleNamespace(force=False,retry_failed=True,min_free_gb=0)
            original=playback.prepare
            def fail_playback(entry, mapping, enabled, folder, staging, **kwargs):
                if enabled: raise RuntimeError('engine failed')
                return original(entry,mapping,enabled,folder,staging, **kwargs)
            with patch.object(pipeline,'CACHE',root/'cache'), patch.object(pipeline,'RESULTS',root/'results'), patch.object(pipeline,'STAGING',root/'staging'), patch.object(playback,'CACHE',root/'playback'),patch.object(playback,'BUNDLE',root), patch.object(playback,'PUBLIC',root/'public'), patch.object(playback,'prepare',side_effect=fail_playback):
                outcome=pipeline.process_entry(entry,args,'engine','adapter')
            self.assertEqual(outcome['status'],'failed')
            output=json.loads((root/'public/example/score.json').read_text())
            self.assertFalse(output['ready']); self.assertEqual(output['notes'],[])
            self.assertEqual(len(output['pages']),1)
            self.assertTrue((root/'public/example/page-1.webp').exists())

    def test_changed_source_does_not_reuse_old_playback_on_failure(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); source=root/'source'; source.write_bytes(b'new edition')
            Image.new('RGB',(4,4),'white').save(root/'page.png')
            target=root/'public/example';target.mkdir(parents=True)
            (target/'score.json').write_text(json.dumps({**package(),'sourceSHA256':'old edition'}))
            Image.new('RGB',(4,4),'black').save(target/'page-1.webp','WEBP',lossless=True)
            entry=dict(id='example',assetPath='source',sha256=pipeline.sha(source),format='musicxml',pageCount=1,previews=['page.png'])
            args=SimpleNamespace(force=False,retry_failed=True,min_free_gb=0)
            original=playback.prepare
            def fail_playback(entry,mapping,enabled,folder,staging, **kwargs):
                if enabled: raise RuntimeError('new edition failed')
                return original(entry,mapping,enabled,folder,staging, **kwargs)
            with patch.object(pipeline,'CACHE',root/'cache'),patch.object(pipeline,'RESULTS',root/'results'),patch.object(pipeline,'STAGING',root/'staging'),patch.object(playback,'CACHE',root/'playback'),patch.object(playback,'BUNDLE',root),patch.object(playback,'PUBLIC',root/'public'),patch.object(playback,'prepare',side_effect=fail_playback):
                result=pipeline.process_entry(entry,args,'engine','adapter')
            output=json.loads((target/'score.json').read_text())
            self.assertFalse(result['previousReady']);self.assertFalse(output['ready'])
            self.assertEqual(output['sourceSHA256'],entry['sha256'])
            with Image.open(target/'page-1.webp') as image:self.assertEqual(image.getpixel((0,0)),(255,255,255))

    def test_failed_directory_promotion_rolls_back(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); staged=root/'new'; staged.mkdir()
            target=root/'public/piece';target.mkdir(parents=True)
            (target/'old').write_text('usable')
            original=Path.rename
            def fail_new(path,destination):
                if path==staged: raise OSError('simulated failure')
                return original(path,destination)
            with patch.object(pipeline,'STAGING',root/'unavailable'), patch.object(Path,'rename',fail_new), self.assertRaises(OSError):
                pipeline.promote(staged,target)
            self.assertEqual((target/'old').read_text(),'usable')
            self.assertFalse((root/'unavailable').exists())
            self.assertFalse(target.with_name('piece.previous').exists())

    def test_changed_cached_page_is_not_reused_as_a_complete_package(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); page=root/'page-1.webp';page.write_bytes(b'original')
            prior={'pageSHA256':{page.name:pipeline.sha(page)}}
            self.assertTrue(pipeline.pages_intact(root/'score.json',prior))
            page.write_bytes(b'changed')
            self.assertFalse(pipeline.pages_intact(root/'score.json',prior))

    def test_page_reuse_requires_identical_pixels(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); public=root/'public'; (public/'piece').mkdir(parents=True)
            source=root/'source.png'; existing=public/'piece/page-1.webp'; out=root/'out.webp'
            Image.new('RGB',(4,4),'white').save(source)
            Image.new('RGB',(4,4),'white').save(existing,'WEBP',lossless=True)
            with patch.object(playback,'PUBLIC',public):
                playback.image_page(source,out,'/playback/piece/page-1.webp')
                self.assertEqual(out.read_bytes(),existing.read_bytes())
                Image.new('RGB',(4,4),'black').save(source)
                playback.image_page(source,out,'/playback/piece/page-1.webp')
            with Image.open(out) as image: self.assertEqual(image.getpixel((0,0)),(0,0,0))
            with Image.open(existing) as image: self.assertEqual(image.getpixel((0,0)),(255,255,255))

    def test_cautionary_and_split_measures_keep_correct_stack_geometry(self):
        # Audiveris repeats stack IDs for courtesy signatures and split repeats.
        xml='''<sheet><picture width="100" height="100"/><scale><interline main="10"/></scale><page><system id="1">
        <stack id="4" left="0" right="40"><slot id="1" time-offset="0"/></stack>
        <stack id="4" left="40" right="45" special="CAUTIONARY"/>
        <stack id="4" left="45" right="100" special="SECOND_HALF"/>
        <part><staff id="1"/><measure id="4"/><measure id="4C"/><measure id="X4"/></part>
        </system></page></sheet>'''
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'sample.omr'
            with ZipFile(path,'w') as archive: archive.writestr('sheet#1/sheet#1.xml',xml)
            _, measures=read_omr(path)
        self.assertEqual([(m['left'],m['right']) for m in measures],[(0,40),(45,100)])

    def test_supported_profiles_accept_a4_without_removing_the_pixel_limit(self):
        for dpi in [300,400,450,600]:
            key,limit=pipeline.recognition_profile(dpi)
            pixels=math.ceil(595.28*dpi/72)*math.ceil(841.89*dpi/72)
            self.assertLessEqual(pixels,limit)
            self.assertLessEqual(limit,40000000)
        self.assertNotEqual(pipeline.recognition_profile(600)[0],'600dpi-v1',
                            'the previously rejected 600 DPI attempts must be invalidated')

    def test_long_scores_get_bounded_time_and_short_timeout_failures_can_resume(self):
        budget=pipeline.recognition_timeout(27)
        self.assertGreater(budget,900)
        self.assertLessEqual(pipeline.recognition_timeout(200),3600)
        self.assertEqual(pipeline.recognition_timeout(27,30),30)
        failure={'error':'engine timed out after 900s'}
        self.assertTrue(pipeline.longer_timeout_available(failure,budget))
        self.assertFalse(pipeline.longer_timeout_available(failure,900))
        self.assertFalse(pipeline.longer_timeout_available({'error':'incomplete-playback'},budget))

    def test_partial_recognition_requires_matching_source_profile_and_intact_archive(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source=root/'source.pdf';book=root/'source.omr'
            prior={'fingerprint':'matching','status':'failed','error':'engine timed out after 900s'}
            with ZipFile(book,'w') as archive:
                archive.writestr('book.xml',f'<book path="{source}"><sheet number="1"/><sheet number="2"/></book>')
            self.assertTrue(pipeline.resumable_book(book,source,2,prior,'matching'))
            self.assertFalse(pipeline.resumable_book(book,source,2,prior,'changed-engine'))
            self.assertFalse(pipeline.resumable_book(book,root/'other.pdf',2,prior,'matching'))
            self.assertFalse(pipeline.resumable_book(book,source,3,prior,'matching'))
            prior['recognitionFingerprint']='same-recognizer-and-input'
            self.assertTrue(pipeline.resumable_book(book,source,2,prior,'new-mapper','same-recognizer-and-input'))
            self.assertFalse(pipeline.resumable_book(book,source,2,prior,'new-mapper','different-recognizer'))
            with ZipFile(book,'w') as archive:
                archive.writestr('book.xml',f'<book path="{source}"><sheet number="1"><steps>LOAD BINARY PAGE</steps></sheet><sheet number="2"/></book>')
            self.assertFalse(pipeline.resumable_book(book,source,2,prior,'matching'),
                             'a book can pass ZIP checks while referring to missing sheet data')
            book.write_bytes(b'interrupted zip write')
            self.assertFalse(pipeline.resumable_book(book,source,2,prior,'matching'))

    def test_step_timeouts_retry_once_when_the_budget_increases(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'attempt').mkdir();log=root/'attempt/run.log'
            log.write_text('WARN Timeout 120 seconds for step BEAMS\n')
            self.assertTrue(pipeline.retry_step_timeout({},root,300))
            self.assertFalse(pipeline.retry_step_timeout({'recognitionStepTimeout':300},root,300))
            log.write_text('Error while exporting a measure\n')
            self.assertFalse(pipeline.retry_step_timeout({},root,300))

    def test_renderer_omission_is_rejected_before_packaging(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source=root/'source.mxl';source.write_bytes(b'fixture')
            midi=mido.MidiFile();track=mido.MidiTrack();midi.tracks.append(track)
            track.append(mido.Message('note_on',note=60,velocity=80,time=0))
            track.append(mido.Message('note_off',note=60,time=480))
            midi.save(root/'score.mid')
            (root/'score.mpos').write_text('<score><elements><element id="0" page="0" x="0" y="0" sx="10" sy="10"/><element id="1" page="0" x="0" y="20" sx="10" sy="10"/></elements><events><event elid="0" position="0"/></events></score>')
            fingerprint=hashlib.sha256(source.read_bytes()+b'web-score-v3-written-notes-fixture').hexdigest()
            (root/'fingerprint.txt').write_text(fingerprint)
            with patch.object(playback,'ENGINE_VERSION','fixture'), patch.object(playback.subprocess,'run') as renderer, self.assertRaisesRegex(ValueError,'incomplete-playback'):
                playback.export_performance(source,root,True)
            renderer.assert_not_called()

    def test_resolution_retries_stop_at_first_valid_conversion(self):
        args=SimpleNamespace(pdf_dpi=None,retry_profiles=[400,600])
        outcomes=[dict(id='sample',status='failed',recognitionDPI=300,error='incomplete'),
                  dict(id='sample',status='ready',recognitionDPI=400)]
        with tempfile.TemporaryDirectory() as name, patch.object(pipeline,'RESULTS',Path(name)), patch.object(pipeline,'process_entry',side_effect=outcomes) as prepare:
            result=pipeline.prepare_with_retries({'id':'sample','format':'pdf'},args,'engine','adapter')
        self.assertEqual(result['status'],'ready');self.assertEqual(prepare.call_count,2)
        self.assertEqual(prepare.call_args_list[1].args[1].pdf_dpi,400)
        self.assertEqual([a['recognitionDPI'] for a in result['attempts']],[300,400])

    def test_explicit_profile_and_existing_usable_score_do_not_trigger_fallback(self):
        for dpi,previous_ready in [(400,False),(None,True)]:
            args=SimpleNamespace(pdf_dpi=dpi,retry_profiles=[400,600])
            outcome=dict(id='sample',status='failed',previousReady=previous_ready)
            with patch.object(pipeline,'process_entry',return_value=outcome) as prepare:
                pipeline.prepare_with_retries({'id':'sample','format':'pdf'},args,'engine','adapter')
            self.assertEqual(prepare.call_count,1)

    def test_movements_keep_repeats_and_tempo_boundaries_independent(self):
        element={'page':0,'rawBox':[0,0,10,10]}
        first=([dict(id='n0',start=0,beat=0),dict(id='n1',start=1,beat=1)],2,
               [(0,0,1000000)],{0:element},[dict(element=0,start=0),dict(element=0,start=1)])
        second=([dict(id='n0',start=0,beat=0),dict(id='n1',start=1,beat=2)],2,
                [(0,0,500000),(1,2,1000000)],{0:element},[dict(element=0,start=0)])
        notes,duration,tempos,elements,events=playback.join_performances([first,second])
        self.assertEqual(duration,4)
        self.assertEqual([n['start'] for n in notes],[0,1,2,3])
        self.assertEqual(len({n['id'] for n in notes}),4)
        self.assertEqual([e['element'] for e in events],[0,0,1])
        self.assertEqual(playback.beat_at(2,tempos),2)
        self.assertEqual(playback.beat_at(3,tempos),4)
        self.assertEqual(playback.beat_at(4,tempos),5)
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'score.mpos';playback.save_positions(path,elements,events)
            self.assertEqual(playback.positions(path),(elements,events))
        self.assertEqual(second[0][0]['start'],0,'joining must not mutate movement input')

    def test_multiple_parts_share_measure_timeline_without_losing_notes(self):
        note='<note default-x="10"><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note>'
        xml=f'<score-partwise><part id="P1"><measure number="1">{note}</measure></part><part id="P2"><measure number="1">{note}</measure></part></score-partwise>'
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'sample.mxl'
            with ZipFile(path,'w') as archive:
                archive.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
                archive.writestr('score.xml',xml)
            measures, notes=read_mxl(path)
        self.assertEqual(len(measures),1)
        self.assertEqual([n['part'] for n in notes],[0,1])
        self.assertEqual([n['start'] for n in notes],[0,0])
        self.assertEqual(len({n['id'] for n in notes}),2)

    def test_part_identity_survives_other_parts_being_absent_from_a_system(self):
        omr='''<sheet><picture width="100" height="100"/><scale><interline main="10"/></scale><page><system id="1">
        <sig><inters><head id="10" staff="1" pitch="0"><bounds x="10" y="10" w="4" h="4"/></head></inters>
        <relations><relation source="20" target="10"><containment/></relation></relations></sig>
        <stack id="1" left="0" right="100"><slot id="1" time-offset="0"/></stack>
        <part id="3"><staff id="1"/><measure id="1"><voice id="1"><slots><entry><key>1</key>
        <value status="BEGIN" chord="20"/></entry></slots></voice></measure></part></system></page></sheet>'''
        xml='''<score-partwise><part id="P3"><measure number="1"><note default-x="10">
        <pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note></measure></part></score-partwise>'''
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            with ZipFile(root/'sample.omr','w') as archive: archive.writestr('sheet#1/sheet#1.xml',omr)
            with ZipFile(root/'sample.mxl','w') as archive:
                archive.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
                archive.writestr('score.xml',xml)
            _,omr_measures=read_omr(root/'sample.omr');_,notes=read_mxl(root/'sample.mxl')
        self.assertEqual(omr_measures[0]['events'][0]['part'],2)
        self.assertEqual(notes[0]['part'],2)


if __name__=='__main__': unittest.main()
