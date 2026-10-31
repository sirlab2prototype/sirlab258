"""Single owner of action chunks. No actuator/sink interface exists here."""
from dataclasses import dataclass
import numpy as np

from ai import dataset_contract as contract
from ai.observation_timing import FixedDeadline
from ai.serve.protocol import validate_chunk


@dataclass
class Prediction:
    session_id: str
    request_id: str
    reference_ns: int
    chunk: np.ndarray
    first_slot: int
    end_slot: int


class DryRunScheduler:
    """Schedule on the observation's 50ms reference grid; discard expired heads.

    Prediction i is assigned reference+i*period. This is an explicit replay
    convention, not evidence of physical SDK execution timing. Consume at most
    n_action_steps per response. Never hold/repeat the last command on starvation.
    """
    def __init__(self, chunk_size, n_action_steps, *, max_lateness_ms=20., prefetch_steps=3):
        if not 1 <= n_action_steps <= chunk_size or not 0 < max_lateness_ms < 50 or prefetch_steps < 1:
            raise ValueError("Invalid scheduler configuration")
        self.chunk_size, self.n_action_steps = chunk_size, n_action_steps
        self.period = 1_000_000_000 // contract.FPS
        self.max_lateness_ns = round(max_lateness_ms * 1e6)
        self.prefetch_steps = min(prefetch_steps, n_action_steps)
        self.session_id = None
        self.active = self.next_prediction = None
        self.pending = None
        self.state = "STOPPED"
        self.consumed = self.chunks = self.ignored = self.missed = 0
        self.last_schedule = None

    def start(self, session_id, anchor_ns):
        self.session_id = session_id
        self.anchor_ns = anchor_ns
        self.deadline = FixedDeadline(contract.FPS, anchor_ns)
        self.state = "WARMUP"
        self.active = self.next_prediction = self.pending = None
        self.consumed = self.chunks = self.ignored = self.missed = 0
        self.last_schedule = None

    def stop(self, reason="normal_stop"):
        self.active = self.next_prediction = self.pending = None
        self.state, self.reason = "STOPPED", reason

    def pause_observation(self, reason):
        """Discard executable tails; an in-flight prediction has unknown size here."""
        remaining = sum(max(0, p.end_slot-max(p.first_slot, self.deadline.next_slot))
                        for p in (self.active, self.next_prediction) if p is not None)
        self.stop(reason)
        return remaining

    def rewarm_observation(self):
        """Same session/grid, fresh inference only; preserve cumulative metrics."""
        if not self.is_empty_and_stopped or self.state != "STOPPED":
            raise RuntimeError("Observation resume requires an empty stopped scheduler")
        self.state = "WARMUP"

    @property
    def is_empty_and_stopped(self):
        return self.state in ("STOPPED", "FAILED") and self.active is None and self.next_prediction is None and self.pending is None

    def fail(self, reason):
        self.stop(reason)
        self.state = "FAILED"
        raise RuntimeError(reason)

    def needs_request(self, now_ns):
        if self.state not in ("WARMUP", "RUNNING") or self.pending or self.next_prediction:
            return False
        if self.active is None:
            return True
        next_slot = max(self.deadline.next_slot, (now_ns - self.anchor_ns) // self.period + 1)
        return self.active.end_slot - next_slot <= self.prefetch_steps

    def requested(self, request_id, reference_ns):
        if self.pending or self.state not in ("WARMUP", "RUNNING"):
            raise RuntimeError("Request already pending or episode stopped")
        if (reference_ns - self.anchor_ns) % self.period:
            self.fail("observation_reference_off_grid")
        self.pending = (request_id, reference_ns)

    def response(self, session_id, request_id, chunk, now_ns):
        if self.state not in ("WARMUP", "RUNNING") or session_id != self.session_id or not self.pending or request_id != self.pending[0]:
            self.ignored += 1
            return False
        try:
            validate_chunk(chunk, self.chunk_size)
        except ValueError:
            self.fail("invalid_prediction_output")
        reference_ns = self.pending[1]
        self.pending = None
        reference_slot = (reference_ns - self.anchor_ns) // self.period
        if self.state == "WARMUP":
            first_slot = (now_ns - self.anchor_ns) // self.period + 1
        else:
            first_slot = self.active.end_slot
            if self.anchor_ns + first_slot * self.period <= now_ns:
                self.fail("response_missed_action_handover")
        first_index = first_slot - reference_slot
        if first_index < 0 or first_index >= self.chunk_size:
            self.fail("prediction_expired_before_use")
        end_slot = first_slot + min(self.n_action_steps, self.chunk_size - first_index)
        prediction = Prediction(session_id, request_id, reference_ns, chunk, first_slot, end_slot)
        self.chunks += 1
        if self.state == "WARMUP":
            self.deadline.next_slot = first_slot
            self.active = prediction
            self.state = "RUNNING"
        else:
            self.next_prediction = prediction
        return True

    def tick(self, now_ns):
        if self.state != "RUNNING":
            return None
        schedule = self.deadline.consume(now_ns)
        if schedule is None:
            return None
        self.last_schedule = schedule
        self.missed += schedule["missed_deadlines"]
        if schedule["missed_deadlines"] or schedule["lateness_ns"] > self.max_lateness_ns:
            self.fail("action_deadline_missed")
        slot = schedule["slot"]
        if slot >= self.active.end_slot:
            if self.next_prediction is None:
                self.fail("inference_cannot_keep_up")
            self.active, self.next_prediction = self.next_prediction, None
        prediction = self.active
        if not prediction.first_slot <= slot < prediction.end_slot:
            self.fail("action_sequence_gap")
        index = (schedule["deadline_monotonic_ns"] - prediction.reference_ns) // self.period
        if not 0 <= index < self.chunk_size:
            self.fail("chunk_expired")
        self.consumed += 1
        return {"action": prediction.chunk[index].tolist(), "action_index": index,
                "session_id": self.session_id, "request_id": prediction.request_id,
                "observation_monotonic_ns": prediction.reference_ns,
                "prediction_age_ns": now_ns - prediction.reference_ns, "schedule": schedule,
                "actual_output_monotonic_ns": now_ns, "dry_run": True}
