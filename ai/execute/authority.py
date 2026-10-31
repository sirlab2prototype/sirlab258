"""Local coordination only: never proof of exclusive network/controller access."""
import fcntl
import json
from pathlib import Path
from ai.execute.safety import SafetyError


class CommandLease:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None
        self.owner = None
        self.quarantined = False

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
            stream.close()
            raise

    def _write(self, status):
        self.stream.seek(0)
        self.stream.truncate()
        json.dump({"owner": self.owner, "status": status}, self.stream)
        self.stream.flush()

    def quarantine(self):
        self.quarantined = True
        if self.stream: self._write("quarantined")

    def release(self, *, confirmed_stopped):
        if not confirmed_stopped or self.quarantined: raise SafetyError("cannot_release_unconfirmed_owner")
        if self.stream:
            self._write("released")
            fcntl.flock(self.stream, fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None
            self.owner = None
