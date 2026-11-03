"""Recording telemetry only; never calls an SDK or changes command ownership."""
import uuid


class ActionContext:
    def __init__(self, now_ns):
        self.stream_id = uuid.uuid4().hex
        self.sequence = -1
        self.phase, self.transition_ns = "QP_TELEOP", now_ns
        self.home_generation, self.last_unrecordable_home_ns = 0, 0
        self.reason = "startup"

    def transition(self, phase, now_ns, reason):
        self.phase, self.transition_ns, self.reason = phase, now_ns, reason

    def home_requested(self, now_ns):
        self.home_generation += 1
        # Sticky across heartbeat loss and re-ARM. A Recorder spanning this time
        # must reject the take even if it only sees a later QP heartbeat.
        self.last_unrecordable_home_ns = now_ns
        self.transition("HOME_REQUESTED", now_ns, "live_qdes_and_replay_semantics_unverified")

    def envelope(self, now_ns):
        self.sequence += 1
        return {"version": 1, "stream_id": self.stream_id, "sequence": self.sequence,
                "stamp_ns": now_ns, "phase": self.phase, "transition_ns": self.transition_ns,
                "home_generation": self.home_generation,
                "last_unrecordable_home_ns": self.last_unrecordable_home_ns,
                "arrival_verified": False, "reason": self.reason, "sample": None}
