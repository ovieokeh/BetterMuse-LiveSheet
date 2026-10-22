"""Restricted pilot access and admission limits. No public account signup."""
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import time


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    daily_limit: int
    pending_limit: int

    @property
    def owner(self):
        return 'pilot:' + self.id


class PilotAccess:
    """Reload credentials on every request so revocation needs no restart.

    The server stores hashes only. Rotating a token preserves its principal ID
    and therefore job ownership. A missing or malformed registry fails closed.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.records()

    def records(self):
        if self.path.stat().st_mode & 0o077:
            raise ValueError('pilot credentials must be private (0600)')
        data = json.loads(self.path.read_text())
        records = data['credentials']
        if not isinstance(records, list) or not records:
            raise ValueError('pilot credentials required')
        for r in records:
            if (not re.fullmatch(r'[a-z0-9_-]{1,64}', r['id'])
                    or not re.fullmatch(r'[a-f0-9]{64}', r['sha256'])
                    or r['role'] not in {'tester', 'operator'}
                    or not isinstance(r['expiresAt'], (float, int))
                    or not 1 <= r['dailyLimit'] <= 100
                    or not 1 <= r['pendingLimit'] <= 2):
                raise ValueError('invalid pilot credential')
        return records

    def authenticate(self, authorization):
        if not re.fullmatch(r'Bearer [a-f0-9]{64}', authorization):
            return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        try:
            for r in self.records():
                if hmac.compare_digest(r['sha256'], digest) and r['expiresAt'] > time.time():
                    return Principal(r['id'], r['role'], r['dailyLimit'], r['pendingLimit'])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None


class PilotQuota:
    """Count admitted upload/retry attempts, including invalid PDF attempts.

    Ledger is separate from jobs: deleting a job cannot reset the quota.
    Caller holds Jobs.lock, which also protects pending job admission.
    """
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts (owner TEXT NOT NULL, at REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS attempts_owner_at ON attempts(owner, at)')
        Path(path).chmod(0o600)

    def admit(self, principal, jobs):
        if sum(j.get('owner') == principal.owner and j['state'] in {'queued', 'running'} for j in jobs) >= principal.pending_limit:
            raise ValueError('pilot_pending_limit')
        cutoff = time.time() - 86400
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE at < ?', (cutoff,))
            used = db.execute('SELECT count(*) FROM attempts WHERE owner = ?', (principal.owner,)).fetchone()[0]
            if used >= principal.daily_limit:
                raise ValueError('pilot_daily_limit')
            db.execute('INSERT INTO attempts VALUES (?, ?)', (principal.owner, time.time()))
