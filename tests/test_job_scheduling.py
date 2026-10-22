"""Lifecycle races use real process groups, without invoking recognition tools."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from livesheet.server import Jobs, atomic_json, now, parse_args, terminate


def until(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('Timed out waiting for lifecycle transition')


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Jobs(Path(self.temp.name), start=False)
        self.processes = {}
        self.real_launch = self.store.launch
        self.patch('livesheet.server.shutil.disk_usage', return_value=SimpleNamespace(free=100 * 1024**3))
        self.patch('livesheet.server.WATCH_INTERVAL', .05)
        self.patch('livesheet.server.verify_result')
        self.patch('livesheet.server.Jobs.launch', side_effect=self.launch)

    def tearDown(self):
        self.store.close()

    def patch(self, *args, **kwargs):
        p = patch(*args, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def launch(self, folder, log, runtime):
        # A per-job finish file permits deterministic overlap and exit ordering.
        process = subprocess.Popen([sys.executable, '-c', '''
from pathlib import Path
import sys, time
p = Path(sys.argv[1]) / 'finish'
while not p.exists(): time.sleep(.02)
sys.exit(int(p.read_text()))
''', str(folder)], stdout=log, stderr=log, start_new_session=True)
        self.processes.setdefault(folder.name, []).append(process)
        return process

    def add_job(self):
        ident = uuid.uuid4().hex
        folder = self.store.root / ident
        folder.mkdir()
        (folder / 'source.pdf').write_bytes(b'preserved source')
        atomic_json(folder / 'job.json', dict(id=ident, state='queued', createdAt=now(), updatedAt=now()))
        return ident

    def start(self):
        for thread in self.store.threads:
            thread.start()

    def running(self, ident):
        return until(lambda: self.store.active.get(ident))

    def finish(self, ident, code=0):
        (self.store.root / ident / 'finish').write_text(str(code))
        return until(lambda: self.store.read(ident)['state'] in ('ready', 'failed'))

    def test_two_claims_third_waits_cancel_retry_keeps_peer_and_attempts_separate(self):
        first, second, third = [self.add_job() for _ in range(3)]
        self.start()
        old = self.running(first)
        peer = self.running(second)
        self.assertEqual(self.store.read(third)['state'], 'queued')
        self.store.action(first, 'cancel')
        self.store.action(first, 'retry')
        until(lambda: len(self.processes[first]) == 2)
        self.assertIsNotNone(old.poll())
        self.assertIsNone(peer.poll())
        self.assertIs(self.store.active[second], peer)
        self.assertEqual(self.store.read(first)['state'], 'running')
        self.assertEqual(self.store.read(third)['state'], 'queued')
        self.finish(first)
        self.running(third)
        self.finish(second)
        self.finish(third)
        until(lambda: not self.store.active)
        self.assertFalse(list(self.store.root.glob('runtime-*')))

    def test_delete_running_job_does_not_publish_or_affect_peer(self):
        first, second, third = [self.add_job() for _ in range(3)]
        self.start()
        deleted = self.running(first)
        peer = self.running(second)
        self.store.action(first, 'delete')
        self.running(third)
        self.assertIsNotNone(deleted.poll())
        self.assertIsNone(peer.poll())
        self.assertFalse((self.store.root / first).exists())
        self.finish(second)
        self.finish(third)

    def test_failure_frees_slot_and_invalid_output_still_fails_validation(self):
        first, second, third = [self.add_job() for _ in range(3)]
        self.start()
        self.running(first)
        peer = self.running(second)
        self.finish(first, 1)
        self.assertEqual(self.store.read(first)['error'], 'preparation_failed')
        self.running(third)
        self.assertIsNone(peer.poll())
        with patch('livesheet.server.verify_result', side_effect=ValueError('bad bundle')):
            self.finish(third)
        self.assertEqual(self.store.read(third)['error'], 'invalid_preparation_result')
        self.finish(second)
        self.assertEqual(self.store.read(second)['state'], 'ready')

    def test_launch_failure_does_not_remove_worker_slot(self):
        first, second = [self.add_job() for _ in range(2)]
        with patch.object(self.store, 'launch', side_effect=[OSError('launch failed'), self.launch]):
            # Exercise launch failure directly, then restore the real test launcher.
            self.store.run_next()
        self.assertEqual(self.store.read(first)['error'], 'worker_unavailable')
        self.start()
        self.running(second)
        self.finish(second)

    def test_corrupt_active_job_is_quarantined_and_peer_survives(self):
        first, second = [self.add_job() for _ in range(2)]
        self.start()
        bad = self.running(first)
        peer = self.running(second)
        (self.store.root / first / 'job.json').write_text('{bad')
        self.assertEqual([j['id'] for j in self.store.list()], [second])
        self.assertIsNotNone(bad.poll())
        self.assertIsNone(peer.poll())
        self.finish(second)
        self.assertTrue(list(self.store.root.glob('quarantine-*/source.pdf')))

    def test_shutdown_stops_both_leaves_queued_work_and_requeues_only_interrupted(self):
        first, second, third, cancelled = [self.add_job() for _ in range(4)]
        self.store.action(cancelled, 'cancel')
        self.start()
        processes = [self.running(first), self.running(second)]
        with self.assertRaises(BlockingIOError):
            Jobs(self.store.root, start=False)
        self.store.close()
        self.assertTrue(all(p.poll() is not None for p in processes))
        self.assertTrue(all(not t.is_alive() for t in self.store.threads))
        self.assertEqual(self.store.active, {})
        with self.assertRaisesRegex(ValueError, 'service_stopping'):
            self.store.action(cancelled, 'retry')
        self.store = Jobs(self.store.root, start=False)
        for ident in (first, second, third):
            self.assertEqual(self.store.read(ident)['state'], 'queued')
            self.assertEqual((self.store.root / ident / 'source.pdf').read_bytes(), b'preserved source')
        self.assertEqual(self.store.read(cancelled)['state'], 'cancelled')
        self.start()
        self.running(first)
        self.running(second)
        self.finish(first)
        self.running(third)
        self.finish(second)
        self.finish(third)

    def test_job_environments_are_private(self):
        seen = []
        with patch('livesheet.server.subprocess.Popen', side_effect=lambda *a, **kw: seen.append(kw)):
            for ident in (self.add_job(), self.add_job()):
                runtime = self.store.root / f'runtime-{ident}'
                runtime.mkdir()
                self.real_launch(self.store.root / ident, None, runtime)
        for key in ('HOME', 'TMPDIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'PDF_EXPORT_CACHE'):
            self.assertNotEqual(seen[0]['env'][key], seen[1]['env'][key])
        self.assertTrue(all(s['start_new_session'] for s in seen))
        self.assertTrue(all(s['pass_fds'] == (self.store.file_lock.fileno(),) for s in seen))

    def test_worker_count_is_bounded(self):
        self.assertEqual(parse_args([])[0].workers, 2)
        self.assertEqual(parse_args(['--workers', '1'])[0].workers, 1)
        with self.assertRaises(ValueError):
            Jobs(self.store.root, workers=3)


class ProcessGroupTests(unittest.TestCase):
    def test_cancel_kills_descendant_that_ignores_term_after_leader_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'heartbeat'
            code = '''
import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', "import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); p=Path(" + repr(sys.argv[1]) + ");\\nwhile True: p.write_text(str(time.monotonic())); time.sleep(.02)"])
time.sleep(60)
'''
            process = subprocess.Popen([sys.executable, '-c', code, str(marker)], start_new_session=True)
            try:
                until(marker.exists)
                terminate(process)
                value = marker.read_text()
                time.sleep(.15)
                self.assertEqual(marker.read_text(), value)
                self.assertIsNotNone(process.poll())
            finally:
                terminate(process)


@unittest.skipUnless(sys.platform == 'linux', 'Linux subreaper lifecycle')
class SupervisorTests(unittest.TestCase):
    def test_owns_separate_tool_sessions_after_worker_crash_and_parent_death(self):
        # Run the actual supervisor with a tiny substitute worker. That worker
        # deliberately escapes into a new session, just like Audiveris does.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            tool = folder / 'tool.py'
            tool.write_text("import signal,time,sys\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\np=Path(sys.argv[1])\nwhile True: p.write_text(str(time.monotonic())); time.sleep(.02)\n")
            worker = folder / 'worker.py'
            worker.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]],start_new_session=True)\ntime.sleep(float(sys.argv[3]))\n")
            wrapper = folder / 'supervisor.py'
            wrapper.write_text('''
import os, subprocess, sys
from livesheet import job_runner
real = subprocess.Popen
args = sys.argv[1:]
job_runner.subprocess.Popen = lambda *a, **kw: real([sys.executable, *args], **kw)
sys.argv = ['runner', str(os.getppid())]
raise SystemExit(job_runner.main())
''')
            for mode in ('worker_exit', 'cancel', 'parent_crash'):
                with self.subTest(mode=mode):
                    marker = folder / mode
                    command = [sys.executable, str(wrapper), str(worker), str(tool), str(marker), '1' if mode == 'worker_exit' else '60']
                    parent = None
                    if mode == 'parent_crash':
                        parent = subprocess.Popen([sys.executable, '-c', 'import subprocess,sys,time; subprocess.Popen(sys.argv[1:],start_new_session=True); time.sleep(60)', *command])
                        process = None
                    else:
                        process = subprocess.Popen(command, start_new_session=True)
                    try:
                        until(marker.exists)
                        if mode == 'cancel':
                            terminate(process)
                        elif mode == 'parent_crash':
                            parent.kill()
                            parent.wait(timeout=5)
                        else:
                            process.wait(timeout=5)
                            self.assertEqual(process.returncode, 0)
                        # Give the parent monitor a full polling interval. A
                        # surviving detached tool would keep changing this file.
                        time.sleep(.4)
                        value = marker.read_text()
                        time.sleep(.15)
                        self.assertEqual(marker.read_text(), value)
                    finally:
                        if process:
                            terminate(process)
                        if parent and parent.poll() is None:
                            parent.kill()
                            parent.wait()
