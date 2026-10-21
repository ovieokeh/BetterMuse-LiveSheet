"""Recovery preserves its inputs and rejects incomplete/corrupted derived output."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livesheet import audiveris_export_recovery as recovery


def write_score(path, divisions):
    with ZipFile(path, 'w') as archive:
        archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr('score.xml', f'<score-partwise><part id="P1"><measure number="1"><attributes><divisions>{divisions}</divisions></attributes></measure></part></score-partwise>')


class ExportRecoveryTests(unittest.TestCase):
    def test_zero_divisions_triggers_reexport_without_an_engine_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'score.mxl'
            write_score(path, 0)
            self.assertTrue(recovery.needed('Export complete', [path]))
            from livesheet.score_recognition import read_mxl
            with self.assertRaisesRegex(ValueError, 'Invalid MusicXML divisions'):
                read_mxl(path)
            write_score(path, 4)
            self.assertFalse(recovery.needed('Export complete', [path]))

    def test_rejects_reexport_that_still_has_zero_divisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); launcher=folder/'app/Contents/MacOS/Audiveris'
            jar=launcher.parent.parent/'app/audiveris.jar';jar.parent.mkdir(parents=True);jar.write_bytes(b'engine')
            (folder/'score.omr').write_bytes(b'original')
            write_score(folder/'score.mxl', 0)
            def run(command, log, timeout, env):
                write_score(log.parent/'score.mxl', 0)
                log.write_text('Export complete')
            with patch.object(recovery,'build_overlay',return_value=folder/'classes'), self.assertRaisesRegex(ValueError,'zero divisions'):
                recovery.recover(folder,'score',launcher,[dict(file='score.mxl')],run,{})
            self.assertFalse((folder/'export-recovery/recovery.json').exists())

    def test_only_targets_wedge_null_pointer_failure(self):
        self.assertFalse(recovery.needed('java.lang.NullPointerException unrelated'))
        self.assertFalse(recovery.needed('PartwiseBuilder$WedgeIterators normal'))
        self.assertTrue(recovery.needed('java.lang.NullPointerException\nPartwiseBuilder$WedgeIterators.push'))

    def test_warning_ids_are_sheet_local_and_deduplicated(self):
        log = '\n'.join(f'BM_WEDGE_OMITTED page={{Page#{p}.1}} measure=Measure{{#{m}}} wedge=40 reason=x'
                        for p,m in [(1,3),(1,4),(1,4),(2,3)])
        self.assertEqual(recovery.omissions(log), [dict(page=1,wedgeId='40',measures=['3','4']),
                                                   dict(page=2,wedgeId='40',measures=['3'])])

    def test_cache_integrity_and_originals_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); launcher=folder/'app/Contents/MacOS/Audiveris'
            jar=launcher.parent.parent/'app/audiveris.jar';jar.parent.mkdir(parents=True);jar.write_bytes(b'engine')
            (folder/'score.omr').write_bytes(b'original project')
            (folder/'score.mxl').write_bytes(b'original export')
            exports=[dict(file='score.mxl',sha256=recovery.sha(folder/'score.mxl'))]
            calls=[]
            def run(command,log,timeout,env):
                calls.append(command)
                self.assertNotIn('-transcribe',command)
                self.assertEqual(Path(command[-1]).read_bytes(),b'original project')
                write_score(log.parent/'score.mxl', 1)
                log.write_text('BM_WEDGE_OMITTED page={Page#1.1} measure=Measure{#2} wedge=4 reason=x')
            with patch.object(recovery,'build_overlay',return_value=folder/'classes'):
                paths,record=recovery.recover(folder,'score',launcher,exports,run,{})
                recovery.recover(folder,'score',launcher,exports,run,{})
                self.assertEqual(len(calls),1)
                paths[0].write_bytes(b'tampered export')
                recovery.recover(folder,'score',launcher,exports,run,{})
                self.assertEqual(len(calls),2)
                (folder/'score.omr').write_bytes(b'changed project')
                # Changed input cannot reuse the earlier derived export.
                with self.assertRaises(AssertionError):
                    recovery.recover(folder,'score',launcher,exports,run,{})
                self.assertEqual(len(calls),3)
            self.assertEqual((folder/'score.mxl').read_bytes(),b'original export')
            self.assertEqual(record['inputOmrSHA256'], __import__('hashlib').sha256(b'original project').hexdigest())
            self.assertEqual(len(record['omittedWedges']),1)

    def test_rejects_missing_movement_or_export_exception(self):
        for failure in ['missing movement','exception']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp); launcher=folder/'app/Contents/MacOS/Audiveris'
                jar=launcher.parent.parent/'app/audiveris.jar';jar.parent.mkdir(parents=True);jar.write_bytes(b'engine')
                (folder/'score.omr').write_bytes(b'original')
                (folder/'score.mvt1.mxl').write_bytes(b'original')
                def run(command,log,timeout,env):
                    log.write_text('Error visiting Measure: Exception' if failure=='exception' else 'complete')
                with patch.object(recovery,'build_overlay',return_value=folder/'classes'), self.assertRaises(ValueError):
                    recovery.recover(folder,'score',launcher,[dict(file='score.mvt1.mxl')],run,{})
                self.assertFalse((folder/'export-recovery/recovery.json').exists())


if __name__ == '__main__':
    unittest.main()
