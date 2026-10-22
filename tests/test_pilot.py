import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from livesheet.pilot import PilotAccess, PilotQuota, Principal
from livesheet.server import parse_args
import test_local_pdf_server as local
pdf_bytes = local.pdf_bytes


class PilotHTTPTests(unittest.TestCase):
    request = local.LocalPDFServerTests.request
    tearDown = local.LocalPDFServerTests.tearDown
    # Reuse the HTTP harness, not the local-only behavior tests.
    def setUp(self):
        local.LocalPDFServerTests.setUp(self)
        self.token = 'a' * 64
        self.credentials = Path(self.temp.name) / 'access.json'
        self.record = dict(id='alice', sha256=hashlib.sha256(self.token.encode()).hexdigest(),
                           role='tester', expiresAt=time.time()+3600, dailyLimit=3, pendingLimit=2)
        self.save_records([self.record])
        self.server.pilot_access = PilotAccess(self.credentials)
        self.server.pilot_hosts = {f'127.0.0.1:{self.server.server_port}'}
        self.store.pilot_quota = PilotQuota(self.store.root / 'quota.sqlite3')

    def save_records(self, records):
        self.credentials.write_text(json.dumps(dict(credentials=records)))
        self.credentials.chmod(0o600)

    def headers(self, key='12345678-1234-1234-1234-123456789abc'):
        return {'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/pdf', 'Idempotency-Key': key}

    def test_pilot_auth_revocation_and_rotation(self):
        self.assertEqual(self.request('/jobs')[0], 401)
        h = self.headers()
        self.assertEqual(self.request('/jobs', headers={**h, 'Origin': 'https://example.com'})[0],401)
        self.assertEqual(self.request('/jobs', headers={**h, 'Host': 'evil.test'})[0],403)
        self.assertEqual(self.request('/jobs', headers=h)[0],200)
        status, raw = self.request('/jobs','POST',pdf_bytes(),h)
        self.assertEqual(status,202)
        ident=json.loads(raw)['id']
        self.save_records([{**self.record,'id':'bob'}])
        for path, method in [(f'/jobs/{ident}','GET'),(f'/jobs/{ident}/files/source.pdf','GET'),(f'/jobs/{ident}/cancel','POST'),(f'/jobs/{ident}','DELETE')]:
            self.assertEqual(self.request(path,method,headers=h)[0],404)
        self.token='b'*64
        self.save_records([{**self.record,'sha256':hashlib.sha256(self.token.encode()).hexdigest()}])
        self.assertEqual(self.request('/jobs',headers=h)[0],401)
        self.assertEqual(self.request(f'/jobs/{ident}',headers=self.headers())[0],200)
        self.save_records([{**self.record,'expiresAt':0}])
        self.assertEqual(self.request('/jobs',headers=self.headers())[0],401)
        self.credentials.write_text('broken')
        self.assertEqual(self.request('/jobs',headers=h)[0],401)

    def test_pilot_limits_survive_deletion_and_restart(self):
        data=pdf_bytes()
        with patch('livesheet.server.preflight',return_value=1):
            status,raw=self.request('/jobs','POST',data,self.headers())
            self.assertEqual(status,202)
            ident=json.loads(raw)['id']
            self.assertEqual(self.request('/jobs','POST',data,self.headers())[0],202)
            h2=self.headers('22345678-1234-1234-1234-123456789abc')
            self.assertEqual(self.request('/jobs','POST',data,h2)[0],202)
            h3=self.headers('32345678-1234-1234-1234-123456789abc')
            self.assertEqual(self.request('/jobs','POST',data,h3)[0],429)
            self.assertEqual(self.request(f'/jobs/{ident}/cancel','POST',headers=self.headers())[0],200)
            self.assertEqual(self.request(f'/jobs/{ident}/retry','POST',headers=self.headers())[0],200)
            for j in self.store.list(): self.store.action(j['id'],'delete')
            self.store.pilot_quota=PilotQuota(self.store.root/'quota.sqlite3')
            self.assertEqual(self.request('/jobs','POST',data,h3)[0],429)

    def test_pilot_health_and_metrics(self):
        self.assertEqual(self.request('/health')[0],503) # workers deliberately stopped
        self.assertEqual(set(json.loads(self.request('/health')[1])),{'healthy'})
        self.assertEqual(self.request('/metrics',headers=self.headers())[0],403)
        self.save_records([{**self.record,'role':'operator'}])
        status,raw=self.request('/metrics',headers=self.headers())
        self.assertEqual(status,200)
        metrics=json.loads(raw)
        self.assertEqual(metrics['workersAlive'],0)
        self.assertEqual(metrics['jobs']['queued'],0)
        self.assertNotIn('filename',raw.decode())
        fake = SimpleNamespace(name='test', is_alive=lambda: True)
        with patch.object(self.store,'threads',[fake]), patch.object(self.store,'audiveris',self.credentials), patch.object(self.store,'musescore',str(self.credentials)):
            self.store.worker_heartbeats['test']=time.monotonic()
            self.assertEqual(self.request('/health')[0],200)
            self.store.worker_heartbeats['test']=time.monotonic()-91
            self.assertEqual(self.request('/health')[0],503)

class PilotConfigurationTests(unittest.TestCase):
    def test_fail_closed_configuration(self):
        for args in [['--pilot-host','example.com'], ['--pilot-credentials','missing']]:
            with self.assertRaises(SystemExit): parse_args(args)
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'tokens.json';p.write_text('{}');p.chmod(0o644)
            with self.assertRaises(ValueError): PilotAccess(p)
