import tempfile
from pathlib import Path
import unittest
from copy import deepcopy
from livesheet.testing.pdf_performance_fixtures import CASES, create, authored
from livesheet.pdf_reference import read_score, compare
from livesheet.testing.check_pdf_performance import selection

class FixtureTests(unittest.TestCase):
    def test_every_source_has_independent_written_answers(self):
        with tempfile.TemporaryDirectory() as d:
            for name in CASES:
                with self.subTest(name=name):
                    path=Path(d)/(name+'.musicxml');expected=create(name,path)
                    self.assertFalse(compare(expected,read_score(path))['differences'])
                    self.assertTrue(authored(name)['notes'])
    def test_opening_and_midbar_expected_seconds(self):
        a=authored('opening-tempo')
        self.assertEqual([round(n['start'],6) for n in a['notes'] if n['staff']==1][:8],[0,.3,.6,.9,1.2,1.5,1.8,2.1])
        b=authored('midbar-tempo')
        self.assertEqual([n['start'] for n in b['notes'] if n['staff']==1],[0,.75,1.5,2,2.5,3,3.5,4.5])
    def test_repeats_ties_octave_and_hold_are_declared(self):
        self.assertEqual([m['id'] for m in authored('alternate-endings')['measures']],['m0','m1','m0','m2'])
        tied=authored('tied-chords')['notes'];self.assertEqual(len(tied),7)
        self.assertEqual(next(n for n in tied if n['midi']==48)['endBeat'],8)
        self.assertEqual([n['midi'] for n in authored('midbar-ottava')['notes'] if n['staff']==1],[72,74,88,89,91,89,76,72])
        self.assertAlmostEqual(authored('fermata-release')['measures'][1]['start'],3.)
    def test_holdout_and_family_split_are_enforced(self):
        entries=[dict(id=n+'--v',family=n,split=s.get('split','development')) for n,s in CASES.items()]
        manifest=dict(entries=entries)
        self.assertEqual(len(selection(manifest,None,False)),8)
        with self.assertRaisesRegex(ValueError,'sealed'):selection(manifest,['compound-rests--v'],False)
        self.assertEqual(len(selection(manifest,['compound-rests--v'],True)),1)
        with self.assertRaisesRegex(ValueError,'Unknown'):selection(manifest,['not-a-case'],False)
        manifest['entries'].append(dict(id='bad-variant',family='opening-tempo',split='holdout'))
        with self.assertRaisesRegex(ValueError,'crosses'):selection(manifest,None,False)

if __name__=='__main__':unittest.main()

class CachedRunnerTests(unittest.TestCase):
    def test_cached_result_with_standard_baseline_is_evaluated_without_a_worker(self):
        import json
        from unittest.mock import patch
        from types import SimpleNamespace
        from livesheet.testing.check_pdf_performance import evaluate, sha
        from test_pdf_performance_reference import sample
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);case=base/'cases/example';case.mkdir(parents=True);job=base/'jobs/example';job.mkdir(parents=True)
            def save(p,v):p.write_text(json.dumps(v))
            save(base/'manifest.json',dict(complete=True,renderer='test-renderer',fixturesSHA256=sha(__import__('livesheet.testing.pdf_performance_fixtures', fromlist=['']).__file__),
                entries=[dict(id='example',family='example',variant='leland',split='development',path='cases/example',referenceValid=True,visualReview='passed',hashes={'score.pdf':'source-hash'})]))
            save(case/'reference-performance.json',sample());save(job/'score.json',sample());save(job/'baseline-score.json',sample())
            save(job/'quality.json',dict(status='fallback'));save(job/'benchmark-inputs.json',dict(pdfSHA256='source-hash',algorithm={},engine={'musescore':'test-renderer'}))
            midi=job/'performance/score/score.mid';midi.parent.mkdir(parents=True);midi.write_bytes(b'mocked')
            with patch('livesheet.testing.check_pdf_performance.shutil.disk_usage',return_value=SimpleNamespace(free=100*1024**3)),patch('livesheet.testing.check_pdf_performance.fingerprint',return_value={}),patch('livesheet.testing.check_pdf_performance.tools_key',return_value={'musescore':'test-renderer'}),patch('livesheet.testing.check_pdf_performance.verify_files',return_value=case),patch('livesheet.testing.check_pdf_performance.read_midi',return_value=sample()),patch('livesheet.testing.check_pdf_performance.checked_run') as worker:
                self.assertTrue(evaluate(base,None,False));worker.assert_not_called()
            report=json.loads((base/'evaluation.json').read_text());self.assertEqual(report['counts'],{'passed':1})
            self.assertIn('baselineResult',report['rows'][0])
