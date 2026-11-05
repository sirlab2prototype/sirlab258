"""Local coordination only: never proof of exclusive network/controller access."""
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from ai.execute.safety import SafetyError


class CommandLease:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None
        self.owner = None
        self.quarantined = False
        self.persistence_error = None

    def acquire(self, owner):
        if self.stream is not None: raise SafetyError("lease_already_held")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stream.seek(0)
            existing = stream.read()
            if existing and json.loads(existing).get("status") != "released":
                raise SafetyError("previous_owner_unresolved_requires_manual_reconciliation")
            self.stream, self.owner = stream, owner
            self._write("held")
        except Exception:
            self.stream, self.owner = None, None
            stream.close()
            raise

    def _write(self, status):
        self.stream.seek(0)
        self.stream.truncate()
        json.dump({"owner": self.owner, "status": status}, self.stream)
        self.stream.flush()
        import os
        os.fsync(self.stream.fileno())

    def require_owner(self, owner):
        if self.stream is None or self.owner != owner or self.quarantined:
            raise SafetyError("command_lease_lost")
        import os
        actual = self.path.stat()
        held = os.fstat(self.stream.fileno())
        if (actual.st_dev,actual.st_ino)!=(held.st_dev,held.st_ino):
            raise SafetyError("command_lease_replaced")
        self.stream.seek(0)
        record=json.load(self.stream)
        if record.get("owner")!=owner or record.get("status")!="held":
            raise SafetyError("command_lease_changed")

    def quarantine(self):
        self.quarantined = True
        if self.stream:
            try:self._write("quarantined")
            except OSError as exc:
                # Keep the flock and in-memory quarantine; an already durable
                # held marker also blocks restart. Persistence failure is not a stop.
                self.persistence_error = str(exc)

    def release(self, *, confirmed_stopped):
        if not confirmed_stopped or self.quarantined: raise SafetyError("cannot_release_unconfirmed_owner")
        if self.stream:
            self._write("released")
            fcntl.flock(self.stream, fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None
            self.owner = None

    @classmethod
    def reconcile_verified_stop(cls, *, path, operator, evidence_sha256):
        """Manually clear an abandoned lease after independent stop verification."""
        if not operator or not evidence_sha256:
            raise ValueError("operator and stop evidence are required")
        path = Path(path)
        if not path.exists():
            return False  # QP-only recovery before any ACT session.
        with path.open("r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            old = json.load(stream)
            if old.get("status") == "released":
                return False
            if old.get("status") not in ("held", "quarantined"):
                raise RuntimeError("Unknown ACT lease state")
            record = {"owner": old.get("owner"), "status": "released",
                      "reconciled_from": old["status"], "reconciled_by": operator,
                      "stop_evidence_sha256": evidence_sha256,
                      "reconciled_utc": datetime.now(timezone.utc).isoformat()}
            stream.seek(0); stream.truncate()
            json.dump(record, stream)
            stream.flush(); os.fsync(stream.fileno())
        return True
