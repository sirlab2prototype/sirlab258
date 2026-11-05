"""Process lifetime exclusion for project QP/Home and the ACT guarded Driver.

This does not exclude arbitrary remote SDK programs; an external interlock must.
"""
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parents[4] / 'tmp/runtime/indy7_writer.lock'


class WriterLock:
    def __init__(self, owner, path=LOCK_PATH):
        self.path, self.owner, self.stream = Path(path), owner, None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stream.seek(0)
            old = stream.read()
            if old and json.loads(old).get('status') != 'released':
                raise RuntimeError('Unresolved Indy7 writer; independent stop verification/manual reconciliation required')
            self.stream = stream
            self._write('held')
        except BaseException:
            stream.close()
            raise

    def _write(self, status):
        self.stream.seek(0)
        old = self.stream.read()
        previous = json.loads(old) if old else {}
        audit_keys = ('reconciled_from', 'reconciled_by', 'stop_evidence_sha256', 'reconciled_utc')
        record = {key: previous[key] for key in audit_keys if key in previous}
        record.update(owner=self.owner, status=status)
        self.stream.seek(0); self.stream.truncate()
        json.dump(record, self.stream)
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self, confirmed=True):
        if self.stream:
            self._write('released' if confirmed else 'quarantined')
            self.stream.close(); self.stream = None

    @classmethod
    def reconcile_verified_stop(cls, *, path=LOCK_PATH, operator, evidence_sha256):
        """Manual reconciliation after an independent, fresh stop check.

        The caller must perform that check and confirm remote writer exclusion.
        The existing inode is preserved so waiting flock users cannot split locks.
        """
        if not operator or not evidence_sha256:
            raise ValueError('operator and stop evidence are required')
        with Path(path).open('r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            old = json.load(stream)
            if old.get('status') not in ('held', 'quarantined'):
                raise RuntimeError('No unresolved writer record to reconcile')
            record = {'owner': old.get('owner'), 'status': 'released',
                      'reconciled_from': old['status'], 'reconciled_by': operator,
                      'stop_evidence_sha256': evidence_sha256,
                      'reconciled_utc': datetime.now(timezone.utc).isoformat()}
            stream.seek(0); stream.truncate()
            json.dump(record, stream)
            stream.flush(); os.fsync(stream.fileno())
