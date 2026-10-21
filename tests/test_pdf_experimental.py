import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from livesheet.testing.check_pdf_experiment import regressions
from livesheet.pdf_experimental import check_change_scope
from livesheet.worker import repaired_package
from livesheet.playback import default_config


def score(notes):
    return dict(parts=[dict(measures=[dict(notes=[dict(staff=1,midi=str(n),onset='0',duration='1',grace=False,ties=[]) for n in notes])])])


class ExperimentalTests(unittest.TestCase):
    def test_lost_correct_and_new_wrong_cannot_cancel_out(self):
        self.assertTrue(regressions(score([60,64]), score([60]), score([64]))['lostCorrect'])
        self.assertTrue(regressions(score([60]), score([60]), score([60,61]))['newIncorrect'])
        self.assertTrue(regressions(score([60,64]), score([60]), score([60,64]))['improved'])

    def test_missing_baseline_measure_stays_visible_and_cannot_mask_regression(self):
        wanted = score([60]); wanted['parts'][0]['measures'].append(copy.deepcopy(wanted['parts'][0]['measures'][0]))
        self.assertFalse(regressions(wanted, score([60]), score([60]))['lostCorrect'])
        self.assertTrue(regressions(wanted, score([60]), score([61]))['lostCorrect'])

    def test_change_scope_rejects_unexplained_edit_and_repeat_change(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d)/'a.xml', Path(d)/'b.xml'
            xml='<score-partwise><part id="P1"><measure number="1"><attributes><divisions>1</divisions></attributes><note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note></measure></part></score-partwise>'
            a.write_text(xml); b.write_text(xml.replace('<step>C','<step>D'))
            with self.assertRaisesRegex(ValueError,'outside-declared'): check_change_scope(a,b,set())
            check_change_scope(a,b,{0})
            b.write_text(xml.replace('</measure>','<barline><repeat direction="backward"/></barline></measure>'))
            with self.assertRaisesRegex(ValueError,'changed-repeats'): check_change_scope(a,b,{0})

    def test_failed_candidate_keeps_evidence_but_never_delivers_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d); baseline=dict(notes=[dict(midi=60)],pages=[{}]); companion=dict(notes=[dict(midi=60)])
            with patch('livesheet.pdf_experimental.prepare_candidate',side_effect=ValueError('rejected')):
                with self.assertRaisesRegex(ValueError, 'repair_validation_failed'):
                    repaired_package(folder,{},folder/'source.pdf',folder/'score.pdf',{},baseline,companion,lambda _:None,default_config())
            self.assertFalse((folder/'completion.json').exists())
            self.assertEqual(json.loads((folder/'quality.json').read_text())['status'], 'failed')
            self.assertEqual(json.loads((folder/'baseline-score.json').read_text()),baseline)
            self.assertIn('rejected',json.loads((folder/'quality.json').read_text())['reason'])

    def test_invalid_candidate_export_is_not_delivered(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d); baseline=dict(notes=[],pages=[{}]); companion={}
            (folder/'score.pdf').write_bytes(b'placeholder')
            with patch('livesheet.pdf_experimental.prepare_candidate',return_value=({},folder,dict(profile='experimental-v1'))), patch('livesheet.playback.prepare',side_effect=ValueError('incomplete-playback')):
                with self.assertRaisesRegex(ValueError, 'repair_validation_failed'):
                    repaired_package(folder,{},folder/'source.pdf',folder/'score.pdf',{},baseline,companion,lambda _:None,default_config())
            self.assertFalse((folder/'completion.json').exists())
            self.assertIn('incomplete-playback', json.loads((folder/'quality.json').read_text())['reason'])

if __name__ == '__main__': unittest.main()
