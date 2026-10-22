import hashlib
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import threading
import subprocess
import socket
import sys
import time
from unittest.mock import patch
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pymupdf
from livesheet.server import Jobs, Handler, BoundedHTTPServer, atomic_json, preflight


def pdf_bytes(pages=1):
    with pymupdf.open() as pdf:
        for _ in range(pages): pdf.new_page()
        return pdf.tobytes()


class LocalPDFServerTests(unittest.TestCase):
    def test_profile_persists_retry_and_idempotency(self):
        data = pdf_bytes()
        headers = {'Content-Type': 'application/pdf', 'X-Preparation-Profile': 'experimental-v1', 'Idempotency-Key': 'local-test'}
        status, raw = self.request('/jobs', 'POST', data, headers)
        self.assertEqual(status, 202)
        job = json.loads(raw); ident = job['id']
        self.assertEqual(job['profile'], 'experimental-v1')
        self.assertEqual(json.loads(self.request('/jobs', 'POST', data, headers)[1])['id'], ident)
        self.assertEqual(self.request('/jobs', 'POST', data, {**headers, 'X-Preparation-Profile': 'standard'})[0], 400)
        self.assertEqual(self.request('/jobs', 'POST', data, {**headers, 'X-Preparation-Profile': 'unknown'})[0], 400)
        self.store.update(ident, state='failed')
        self.assertEqual(self.store.action(ident, 'retry')['profile'], 'experimental-v1')
        self.assertEqual(self.upload()['profile'], 'experimental-v1')
        folder = self.store.root / ident
        (folder / 'quality.json').write_text('{}')
        self.assertEqual(self.request(f'/jobs/{ident}/files/quality.json')[0], 404)
        self.store.update(ident, state='ready')
        self.assertEqual(self.request(f'/jobs/{ident}/files/quality.json')[0], 200)
        self.assertEqual(self.request(f'/jobs/{ident}/files/baseline-score.json')[0], 404)

    def test_legacy_jobs_resume_without_reprocessing_and_retry_uses_current_pipeline(self):
        data = pdf_bytes()
        headers = {'Content-Type': 'application/pdf', 'Idempotency-Key': 'legacy-job'}
        job = json.loads(self.request('/jobs', 'POST', data, headers)[1])
        ident = job['id']
        self.store.update(ident, state='ready', profile='standard')
        resumed = json.loads(self.request('/jobs', 'POST', data, headers)[1])
        self.assertEqual(resumed['id'], ident)
        self.assertEqual(resumed['state'], 'ready')
        self.assertEqual(resumed['profile'], 'standard')
        self.store.update(ident, state='failed')
        retried = self.store.action(ident, 'retry')
        self.assertEqual(retried['profile'], 'experimental-v1')
        self.assertEqual(retried['state'], 'queued')

    def test_live_status_has_elapsed_time_and_current_stage(self):
        job = self.upload()
        self.store.update(job['id'], state='running', startedAt='2026-01-01T00:00:00+00:00')
        atomic_json(self.store.root / job['id'] / 'stage.json', dict(stage='recognizing', dpi=400))
        status = self.store.public(self.store.read(job['id']))
        self.assertGreater(status['elapsedSeconds'], 180)
        self.assertEqual(status['stage'], 'recognizing')
        self.assertNotIn('elapsedSeconds', self.store.public(self.store.update(job['id'], state='failed')))
        atomic_json(self.store.root / job['id'] / 'failure.json', dict(error='incomplete-playback'))
        self.assertEqual(self.store.public(self.store.read(job['id']))['error'], 'incomplete_playback')

    def test_low_disk_rejects_upload_without_saving_source(self):
        before = len(self.store.list())
        with patch('livesheet.server.shutil.disk_usage', return_value=SimpleNamespace(free=1)):
            status, raw = self.request('/jobs', 'POST', pdf_bytes(), {'Content-Type': 'application/pdf'})
        self.assertEqual(status, 507)
        self.assertEqual(json.loads(raw)['error'], 'storage_low')
        self.assertEqual(len(self.store.list()), before)

    def setUp(self):
        # Unit tests control the disk budget; real integration keeps the real floor.
        disk = patch('livesheet.server.shutil.disk_usage', return_value=SimpleNamespace(free=100 * 1024**3))
        disk.start()
        self.addCleanup(disk.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Jobs(Path(self.temp.name), start=False)
        self.server = BoundedHTTPServer(('127.0.0.1', 0), Handler)
        self.server.jobs = self.store
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.store.close(); self.temp.cleanup()

    def request(self, path, method='GET', data=None, headers=None):
        try: response = urlopen(Request(self.base + path, method=method, data=data, headers=headers or {}))
        except HTTPError as error: response = error
        return response.status, response.read()

    def upload(self):
        status, raw = self.request('/jobs', 'POST', pdf_bytes(), {'Content-Type': 'application/pdf', 'X-Filename': 'test.pdf'})
        self.assertEqual(status, 202)
        return json.loads(raw)

    def test_native_auth_ownership_and_idempotent_recovery(self):
        web_id = self.upload()['id']
        self.server.native_token = 'a' * 64
        self.server.native_hosts = {f'127.0.0.1:{self.server.server_port}'}
        headers = {'Authorization': 'Bearer ' + self.server.native_token,
                   'Content-Type': 'application/pdf', 'Idempotency-Key': 'a' * 8 + '-' + 'b' * 27}
        self.assertEqual(self.request('/jobs')[0], 403)
        self.assertEqual(self.request('/jobs', headers={**headers, 'Origin': self.base})[0], 403)
        data = pdf_bytes()
        status, raw = self.request('/jobs', 'POST', data, headers)
        self.assertEqual(status, 202)
        job = json.loads(raw)
        self.assertNotIn('owner', job)
        self.assertNotIn('idempotencyKey', job)
        self.assertEqual(json.loads(self.request('/jobs', 'POST', data, headers)[1])['id'], job['id'])
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(self.request('/jobs', 'POST', pdf_bytes(2), headers)[0], 400)
        self.assertEqual(self.request(f'/jobs/{web_id}', headers=headers)[0], 404)
        self.assertEqual(self.request(f'/jobs/{web_id}/files/source.pdf', headers=headers)[0], 404)
        self.assertEqual(self.request(f'/jobs/{web_id}', 'DELETE', headers=headers)[0], 404)
        self.assertEqual(len(json.loads(self.request('/jobs', headers=headers)[1])), 1)
        ident = job['id']
        self.server.native_token = 'b' * 64
        other = {'Authorization': 'Bearer ' + self.server.native_token}
        self.assertEqual(self.request(f'/jobs/{ident}', headers=other)[0], 404)
        del self.server.native_token
        self.assertEqual(self.request(f'/jobs/{ident}')[0], 404)
        # The idempotency record survives a process restart.
        self.store.close()
        self.store = Jobs(Path(self.temp.name), start=False)
        self.server.jobs = self.store
        self.server.native_token = 'a' * 64
        self.assertEqual(json.loads(self.request('/jobs', 'POST', data, headers)[1])['id'], ident)

    def test_upload_cancel_retry_delete_and_source_integrity(self):
        job = self.upload(); ident = job['id']
        source = (self.store.root / ident / 'source.pdf').read_bytes()
        self.assertEqual(self.request(f'/jobs/{ident}/files/source.pdf'), (200, source))
        self.assertEqual(self.request(f'/jobs/{ident}/files/companion.json')[0], 404)
        self.assertEqual(json.loads(self.request(f'/jobs/{ident}/cancel', 'POST')[1])['state'], 'cancelled')
        self.assertEqual(json.loads(self.request(f'/jobs/{ident}/retry', 'POST')[1])['state'], 'queued')
        self.assertEqual(self.request(f'/jobs/{ident}', 'DELETE')[0], 200)
        self.assertFalse((self.store.root / ident).exists())
        self.assertEqual(self.request(f'/jobs/{ident}')[0], 404)

    def test_download_preserves_exact_companion_bytes_and_stays_job_scoped(self):
        ident = self.upload()['id']; folder = self.store.root / ident
        raw = b'{"schemaVersion":2,"test":"precise bytes"}\n'
        (folder / 'companion.json').write_bytes(raw)
        atomic_json(folder / 'result.json', {})
        self.store.update(ident, state='ready')
        status, downloaded = self.request(f'/jobs/{ident}/files/companion.json')
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(raw).digest(), hashlib.sha256(downloaded).digest())
        self.assertEqual(self.request(f'/jobs/{ident}/files/job.json')[0], 404)
        self.assertEqual(self.request(f'/jobs/{ident}/files/../../server.lock')[0], 404)
        self.assertEqual(self.request('/jobs/not-an-id')[0], 404)

    def test_invalid_locked_oversized_and_cross_origin_uploads_rejected(self):
        self.assertEqual(self.request('/jobs', 'POST', b'bad', {'Content-Type': 'application/pdf'})[0], 400)
        self.assertEqual(self.request('/jobs', 'POST', b'bad', {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('/jobs', 'POST', pdf_bytes(21), {'Content-Type': 'application/pdf'})[0], 400)
        with pymupdf.open(stream=pdf_bytes(), filetype='pdf') as pdf:
            locked = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader')
        self.assertEqual(self.request('/jobs', 'POST', locked, {'Content-Type': 'application/pdf'})[0], 400)
        self.assertEqual(self.request('/jobs', headers={'Host': 'attacker.example'})[0], 403)
        self.assertEqual(self.request('/jobs', 'POST', pdf_bytes(), {'Content-Type': 'application/pdf', 'Origin': 'https://attacker.example'})[0], 403)
        self.assertEqual(self.store.list(), [])

    def test_cancelling_running_job_stops_worker_and_retry_does_not_get_overwritten(self):
        real_popen = subprocess.Popen
        spawned = []
        def sleeping_worker(*args, **kwargs):
            process = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            spawned.append(process)
            return process
        ident = self.upload()['id']
        with patch('livesheet.server.subprocess.Popen', side_effect=sleeping_worker):
            self.store.threads[0].start()
            deadline = time.monotonic() + 5
            while ident not in self.store.active and time.monotonic() < deadline: time.sleep(.02)
            self.assertIn(ident, self.store.active)
            self.store.action(ident, 'cancel')
            self.assertIsNotNone(spawned[0].poll())
            self.assertEqual(self.store.read(ident)['state'], 'cancelled')
            self.store.action(ident, 'retry')
            deadline = time.monotonic() + 5
            while len(spawned) < 2 and time.monotonic() < deadline: time.sleep(.02)
            self.assertEqual(len(spawned), 2)
            self.assertEqual(self.store.read(ident)['state'], 'running')
            self.store.action(ident, 'cancel')

    def test_preview_is_available_before_recognition_and_origin_port_is_checked(self):
        job = self.upload()
        self.assertTrue(job['previewAvailable'])
        status, raw = self.request(f"/jobs/{job['id']}/files/preview.webp")
        self.assertEqual(status, 200)
        self.assertEqual(raw[:4], b'RIFF')
        self.assertEqual(self.request('/jobs', headers={'Origin': 'http://127.0.0.1:9999'})[0], 403)
        self.assertEqual(self.request('/jobs', headers={'Origin': self.base})[0], 200)
        self.assertEqual(self.request('/jobs', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_preflight_timeout_and_busy_upload_leave_no_partial_job(self):
        with patch('livesheet.server.subprocess.run', side_effect=subprocess.TimeoutExpired('check', 20)):
            status, raw = self.request('/jobs', 'POST', pdf_bytes(), {'Content-Type': 'application/pdf'})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)['error'], 'pdf_check_timeout')
        self.assertFalse(list(self.store.root.glob('incoming-*')))
        self.assertFalse(self.store.list())
        self.store.submitting.acquire()
        try:
            self.assertEqual(self.request('/jobs', 'POST', pdf_bytes(), {'Content-Type': 'application/pdf'})[0], 429)
        finally: self.store.submitting.release()

    def test_retry_checks_storage_and_discards_stale_outputs(self):
        job = self.upload(); ident = job['id']; folder = self.store.root / ident
        self.store.action(ident, 'cancel')
        atomic_json(folder / 'result.json', {'stale': True})
        atomic_json(folder / 'failure.json', {'stale': True})
        with patch('livesheet.server.STORE_LIMIT', 1):
            self.assertEqual(self.request(f'/jobs/{ident}/retry', 'POST')[0], 507)
        self.assertEqual(self.store.read(ident)['state'], 'cancelled')
        self.assertEqual(self.request(f'/jobs/{ident}/retry', 'POST')[0], 200)
        self.assertFalse((folder / 'result.json').exists())
        self.assertFalse((folder / 'failure.json').exists())

    def test_storage_watchdog_stops_a_growing_job(self):
        ident = self.upload()['id']
        real_popen = subprocess.Popen
        processes = []
        def sleeper(*args, **kwargs):
            process = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            processes.append(process)
            return process
        with patch('livesheet.server.subprocess.Popen', side_effect=sleeper), \
             patch('livesheet.server.disk_bytes', return_value=3 * 1024**3), \
             patch('livesheet.server.WATCH_INTERVAL', .05):
            self.store.threads[0].start()
            deadline = time.monotonic() + 5
            while self.store.read(ident)['state'] != 'failed' and time.monotonic() < deadline: time.sleep(.02)
            self.assertEqual(self.store.read(ident)['error'], 'job_storage_limit')
            self.assertIsNotNone(processes[0].poll())

    def test_slow_preflight_does_not_lock_job_status(self):
        started = threading.Event(); release = threading.Event()
        def slow_check(folder):
            started.set(); release.wait(5); return 1
        results = []
        with patch('livesheet.server.preflight', side_effect=slow_check):
            thread = threading.Thread(target=lambda: results.append(self.upload()))
            thread.start()
            self.assertTrue(started.wait(3))
            try:
                self.assertEqual(self.request('/jobs')[0], 200)
                self.assertEqual(self.request('/health')[0], 200)
            finally:
                release.set(); thread.join(5)
        self.assertEqual(len(results), 1)

    def test_connection_deadline_closes_a_stalled_header(self):
        with patch('livesheet.server.REQUEST_SECONDS', .1):
            client = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
            try:
                client.sendall(b'GET /health HTTP/1.1\r\nHost:')
                # A half-written header cannot reserve a request slot indefinitely.
                while client.recv(4096): pass
            finally: client.close()
        self.assertEqual(self.request('/health')[0], 200)

    def test_restart_recovers_interrupted_job_and_retention_removes_only_old_finished(self):
        first = self.upload()['id']; second = self.upload()['id']
        self.store.update(first, state='running')
        self.store.update(second, state='failed')
        old = self.store.read(second); old['updatedAt'] = '2020-01-01T00:00:00+00:00'
        atomic_json(self.store.root / second / 'job.json', old)
        self.store.close()
        self.store = Jobs(Path(self.temp.name), start=False)
        self.server.jobs = self.store
        self.assertEqual(self.store.read(first)['state'], 'queued')
        self.assertFalse((self.store.root / second).exists())
        self.assertTrue((self.store.root / first / 'source.pdf').exists())

    def test_corrupt_job_is_quarantined_without_stopping_other_jobs(self):
        bad=self.upload()['id'];good=self.upload()['id']
        (self.store.root/bad/'job.json').write_text('{interrupted')
        self.assertEqual([j['id'] for j in self.store.list()],[good])
        self.assertFalse((self.store.root/bad).exists())
        quarantined=list(self.store.root.glob('quarantine-*'))
        self.assertEqual(len(quarantined),1)
        self.assertTrue((quarantined[0]/'source.pdf').exists())
        self.assertEqual(self.request(f'/jobs/{bad}')[0],404)

    def test_worker_exit_zero_with_partial_outputs_is_not_ready(self):
        ident=self.upload()['id'];folder=self.store.root/ident
        real_popen=subprocess.Popen
        def partial_worker(*args,**kwargs):
            (folder/'result.json').write_text('{}')
            return real_popen([sys.executable,'-c','pass'],**kwargs)
        with patch('livesheet.server.subprocess.Popen',side_effect=partial_worker):
            self.store.threads[0].start();deadline=time.monotonic()+5
            while self.store.read(ident)['state'] not in {'ready','failed'} and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(self.store.read(ident)['state'],'failed')
            self.assertEqual(self.store.read(ident)['error'],'invalid_preparation_result')
            self.assertEqual(self.request(f'/jobs/{ident}/files/score.json')[0],404)

    def test_shutdown_interrupts_active_job_and_restart_requeues_it(self):
        ident=self.upload()['id'];real_popen=subprocess.Popen;processes=[]
        def sleeper(*args,**kwargs):
            p=real_popen([sys.executable,'-c','import time; time.sleep(60)'],**kwargs);processes.append(p);return p
        with patch('livesheet.server.subprocess.Popen',side_effect=sleeper):
            self.store.threads[0].start();deadline=time.monotonic()+5
            while not processes and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(processes);self.store.close()
            self.assertIsNotNone(processes[0].poll())
        self.store=Jobs(Path(self.temp.name),start=False);self.server.jobs=self.store
        self.assertEqual(self.store.read(ident)['state'],'queued')


if __name__ == '__main__': unittest.main()
