import json
from pathlib import Path
import tempfile
import unittest
from livesheet.monitor import inspect


class MonitorTests(unittest.TestCase):
    def test_healthy_empty_service_and_unhealthy_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(inspect(Path(folder),healthy=True,free_bytes=20*1024**3)['healthy'])
            self.assertEqual(inspect(Path(folder),healthy=False,free_bytes=20*1024**3)['problems'],['service_unhealthy'])

    def test_pressure_and_repeated_failures_without_private_details(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for i,state in enumerate(['queued','failed','failed','failed']):
                path=root/str(i);path.mkdir()
                (path/'job.json').write_text(json.dumps(dict(state=state,updatedAt='2026-01-01T00:00:00+00:00',filename='private.pdf',owner='private')))
            from datetime import datetime
            at=datetime.fromisoformat('2026-01-01T00:11:00+00:00').timestamp()
            result=inspect(root,healthy=True,free_bytes=14*1024**3,at=at)
            self.assertEqual(result['problems'],['disk_pressure','queue_delayed','repeated_preparation_failures'])
            self.assertNotIn('private',json.dumps(result))
