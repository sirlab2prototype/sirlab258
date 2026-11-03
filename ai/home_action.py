"""Home recording contracts. No SDK, ROS or motor commands here.

Live collection requires an evidence-bound device profile. This never enables
ACT motor execution or claims equivalent actuator replay.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import numpy as np

CONTEXT_TOPIC = "/capstone/action_context"
JOINTS = [f"joint{i}" for i in range(6)]
HOME_PHASES = {"HOME_REQUESTED", "HOME_MOVING", "HOME_SETTLING", "HOME_COMPLETE"}
PHASES = HOME_PHASES | {"QP_TELEOP", "HOME_FAILED", "HOME_CANCELLED", "HOME_UNVERIFIED"}


class HomeActionError(ValueError):
    pass


def vector(value):
    try:
        array = np.asarray(value, dtype=float)
    except (ValueError, TypeError) as exc:
        raise HomeActionError("home_joint_vector_must_be_finite_6d") from exc
    if array.shape != (6,) or not np.isfinite(array).all():
        raise HomeActionError("home_joint_vector_must_be_finite_6d")
    return array


def validate_context(value):
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("phase") not in PHASES:
        raise HomeActionError("invalid_action_context")
    if not isinstance(value.get("stream_id"), str) or not value["stream_id"]:
        raise HomeActionError("invalid_action_context_stream")
    for key in ("sequence", "home_generation", "stamp_ns", "transition_ns", "last_unrecordable_home_ns"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise HomeActionError("invalid_action_context_" + key)
    if not 0 < value["transition_ns"] <= value["stamp_ns"]:
        raise HomeActionError("invalid_action_context_transition")
    if value["last_unrecordable_home_ns"] > value["stamp_ns"]:
        raise HomeActionError("invalid_action_context_home_time")
    return value


@dataclass(frozen=True)
class SimulatedHomeContract:
    """Explicit test injection, never created by Recorder/ROS/CLI.

    The evidence ID binds all simulated samples to one reviewed test fixture.
    Live collection uses a separate evidence-bound robot/FW/SDK contract.
    A message claiming 'verified' alone cannot enable acceptance.
    """
    evidence_id: str
    max_age_ns: int = 75_000_000
    max_rpc_ns: int = 25_000_000
    max_state_skew_ns: int = 75_000_000
    expected_simulated = True

    def __post_init__(self):
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip() or any(type(v) is not int or v <= 0 for v in (
                self.max_age_ns, self.max_rpc_ns, self.max_state_skew_ns)):
            raise ValueError("invalid_simulated_home_contract")

    def target(self, context, reference_ns, state_ns):
        sample = context.get("sample")
        if not isinstance(sample, dict) or sample.get("simulated") is not self.expected_simulated or sample.get("evidence_id") != self.evidence_id:
            raise HomeActionError("unverified_home_action")
        if sample.get("kind") != "home_qdes" or sample.get("unit") != "rad" or sample.get("joint_names") != JOINTS:
            raise HomeActionError("home_action_semantics_mismatch")
        start, end = sample.get("request_system_ns"), sample.get("response_system_ns")
        if type(start) is not int or type(end) is not int or not 0 < start <= end <= context["stamp_ns"]:
            raise HomeActionError("invalid_home_rpc_times")
        if end - start > self.max_rpc_ns:
            raise HomeActionError("home_rpc_too_slow")
        mono_start, mono_end = sample.get("request_monotonic_ns"), sample.get("response_monotonic_ns")
        if (type(mono_start) is not int or type(mono_end) is not int or not 0 < mono_start <= mono_end
                or sample.get("rpc_elapsed_ns") != mono_end - mono_start
                or abs((end - start) - (mono_end - mono_start)) > 10_000_000):
            raise HomeActionError("invalid_home_rpc_clock_pair")
        if mono_end - mono_start > self.max_rpc_ns:
            raise HomeActionError("home_rpc_too_slow")
        # Completion time is an availability bound, NOT a sensor timestamp.
        if not 0 <= reference_ns - end <= self.max_age_ns:
            raise HomeActionError("stale_home_action")
        if abs(end - state_ns) > self.max_state_skew_ns:
            raise HomeActionError("home_action_state_skew")
        if sample.get("response_ok") is not True:
            raise HomeActionError("home_query_failed")
        return vector(sample.get("target_rad")).astype(np.float32)


DEFAULT_LIVE_PROFILE = Path(__file__).resolve().parent / "record/config/home_capture.json"
LIVE_CONTEXT_TOPIC = "/capstone/recording_context"


@dataclass(frozen=True)
class LiveHomeContract(SimulatedHomeContract):
    """Collection-only acceptance bound to measured device/FW/SDK and evidence.

    This does not activate an ACT actuator, declare task success, or authorize
    a move. The reader process can only query; Xbox remains the writer.
    """
    profile: dict | None = None
    expected_simulated = False

    def __post_init__(self):
        super().__post_init__()
        p = self.profile or {}
        if (p.get("version") != 1 or p.get("scope") != "collection_only" or
                p.get("evidence_id") != self.evidence_id or p.get("source_unit") not in ("deg", "rad") or
                not all(p.get(k) for k in ("robot_ip", "robot_serial", "controller_version", "sdk_version")) or
                p.get("qdes_intermediate_observed") is not True or
                len(p.get("evidence_sha256", "")) != 64):
            raise ValueError("invalid_live_home_profile")
        vector(p.get("home_target_rad"))

    @classmethod
    def load(cls, path, *, verify_evidence=True):
        p = json.loads(Path(path).read_text())
        if verify_evidence:
            actual = hashlib.sha256(Path(p["evidence_path"]).read_bytes()).hexdigest()
            if actual != p["evidence_sha256"]:
                raise ValueError("home_evidence_hash_mismatch")
        return cls(p["evidence_id"], profile=p)


class SimulatedQdesReader:
    """One non-concurrent FakeSDK read; not a live reader or ROS callback.

    Proves the conversion/interface using qdes rather than measured q. Real
    sampling execution/threading/SDK concurrency must be designed after evidence.
    """
    def __init__(self, sdk, contract, *, source_unit, mono, system):
        if getattr(sdk, "is_mock", False) is not True or type(contract) is not SimulatedHomeContract:
            raise ValueError("live_home_action_locked")
        if source_unit not in {"deg", "rad"}:
            raise ValueError("unverified_qdes_unit")
        self.sdk, self.contract, self.unit = sdk, contract, source_unit
        self.mono, self.system = mono, system

    def read(self):
        start_mono, start_system = self.mono(), self.system()
        value = {"kind": "home_qdes", "simulated": True, "evidence_id": self.contract.evidence_id,
                 "unit": "rad", "joint_names": JOINTS, "response_ok": False}
        try:
            raw = self.sdk.get_control_state()
            if raw.get("response", {}).get("code") != 0:
                raise HomeActionError("fake_sdk_query_rejected")
            qdes = vector(raw.get("qdes"))
            target = np.deg2rad(qdes) if self.unit == "deg" else qdes
            value.update(target_rad=target.tolist(), response_ok=True)
        except Exception as exc:
            value["error"] = f"{type(exc).__name__}: {exc}"
        end_mono, end_system = self.mono(), self.system()
        return {**value, "request_monotonic_ns": start_mono, "response_monotonic_ns": end_mono,
            "request_system_ns": start_system, "response_system_ns": end_system,
            "rpc_elapsed_ns": end_mono - start_mono}


class ActionContextGuard:
    """Consume one atomic lifecycle/sample envelope; latch gaps and failed Home.

    QP targets remain on their original topic. Home envelopes contain their own
    sample, avoiding an unsafe join between two independently delivered topics.
    """
    def __init__(self, start_ns, *, required=False, home_contract=None, require_home_complete=False):
        if home_contract is not None and type(home_contract) not in (SimulatedHomeContract, LiveHomeContract):
            raise ValueError("live_home_action_locked")
        self.start_ns, self.required, self.contract = start_ns, required or require_home_complete, home_contract
        self.require_home_complete = require_home_complete
        self.current = None
        self.source = None
        self.receipt_seen = None
        self.active_home = False
        self.home_seen = False
        self.complete = False
        self.home_start_ns = None

    def update(self, history, reference_ns, *, starting=False, finishing=False):
        for value, source in history["items"].get("action_context", ()):
            if source.get("history_error"):
                raise HomeActionError(source["history_error"])
            validate_context(value)
        events = [(v, s) for v, s in history["items"].get("action_context", ())
                  if s.get("history_error") is None and v["stamp_ns"] <= reference_ns]
        if self.current is None:
            candidates = [(v, s) for v, s in events if v["stamp_ns"] <= self.start_ns]
            if candidates:
                self.current, source = max(candidates, key=lambda x: x[1]["receipt_sequence"])
                self.source = source
                self.receipt_seen = source["receipt_sequence"]
                settled_start = (type(self.contract) is LiveHomeContract and
                    self.current["phase"] == "HOME_COMPLETE" and self.current.get("arrival_verified") is True)
                if self.current["phase"] != "QP_TELEOP" and not settled_start:
                    raise HomeActionError("recording_started_during_home")
                if settled_start:
                    self.complete = True
                    self.home_start_ns = self.current["transition_ns"]
        for value, source in events:
            seq = source["receipt_sequence"]
            if self.receipt_seen is not None and seq <= self.receipt_seen:
                continue
            if self.current is None:
                raise HomeActionError("missing_initial_action_context")
            if seq != self.receipt_seen + 1 or value["sequence"] != self.current["sequence"] + 1:
                raise HomeActionError("action_context_gap")
            if value["stream_id"] != self.current["stream_id"]:
                raise HomeActionError("action_context_publisher_changed")
            if value["stamp_ns"] < self.current["stamp_ns"]:
                raise HomeActionError("action_context_time_reversed")
            if (value["home_generation"] < self.current["home_generation"] or
                    value["home_generation"] > self.current["home_generation"] + 1):
                raise HomeActionError("home_generation_gap")
            changed = value["home_generation"] != self.current["home_generation"]
            if changed:
                if value["phase"] != "HOME_REQUESTED" or self.active_home:
                    raise HomeActionError("home_transition_missing")
                self.home_seen, self.active_home, self.complete = True, True, False
                self.home_start_ns = value["transition_ns"]
            elif value["phase"] in HOME_PHASES and not self.active_home and not self.complete:
                raise HomeActionError("home_transition_missing")
            if value["phase"] == "QP_TELEOP" and self.active_home:
                raise HomeActionError("home_rearm_before_verified_completion")
            if value["phase"] in {"HOME_FAILED", "HOME_CANCELLED", "HOME_UNVERIFIED"}:
                raise HomeActionError("home_failed_or_cancelled")
            if value["phase"] == "HOME_COMPLETE":
                if value.get("arrival_verified") is not True:
                    raise HomeActionError("home_arrival_unverified")
                self.active_home, self.complete = False, True
            if value["phase"] in HOME_PHASES:
                if self.contract is None:
                    raise HomeActionError("live_home_action_locked")
                # Validate EVERY received Home sample, including ones between
                # the 20Hz reference slots. A later good sample cannot hide a
                # failed/invalid lookup. Selection age is checked separately.
                self.contract.target(value, value["stamp_ns"], value["stamp_ns"])
            allowed = {"QP_TELEOP": {"QP_TELEOP", "HOME_REQUESTED"},
                "HOME_REQUESTED": {"HOME_REQUESTED", "HOME_MOVING", "HOME_SETTLING"},
                "HOME_MOVING": {"HOME_MOVING", "HOME_SETTLING"},
                "HOME_SETTLING": {"HOME_MOVING", "HOME_SETTLING", "HOME_COMPLETE"},
                "HOME_COMPLETE": {"HOME_COMPLETE", "QP_TELEOP", "HOME_REQUESTED"}}
            if value["phase"] not in {"HOME_FAILED", "HOME_CANCELLED", "HOME_UNVERIFIED"} and (
                    value["phase"] not in allowed.get(self.current["phase"], set())):
                raise HomeActionError("invalid_home_phase_transition")
            self.current, self.receipt_seen = value, seq
            self.source = source
        if self.current is None:
            if self.required or events:
                raise HomeActionError("missing_action_context")
            return None
        value = self.current
        if not 0 <= reference_ns - value["stamp_ns"] <= 75_000_000:
            raise HomeActionError("stale_action_context")
        if value["last_unrecordable_home_ns"] >= self.start_ns:
            raise HomeActionError("unverified_home_action")
        if value["phase"] in {"HOME_FAILED", "HOME_CANCELLED", "HOME_UNVERIFIED"}:
            raise HomeActionError("home_failed_or_cancelled")
        if value["phase"] in HOME_PHASES and self.contract is None:
            raise HomeActionError("live_home_action_locked")
        if finishing and self.home_seen and not self.complete:
            raise HomeActionError("recording_ended_before_home_complete")
        if finishing and self.require_home_complete and (
                not self.home_seen or not self.complete or value["phase"] != "HOME_COMPLETE"):
            raise HomeActionError("required_home_endpoint_missing")
        return value


class HomeArrivalVerifier:
    """Passive, command-free completion check with caller-supplied fresh feedback.

    Defaults are offline policy values, NOT certified hardware safety limits.
    Missing controller health/status/velocity never means stopped.
    """
    def __init__(self, target_rad, started_ns, *, timeout_ns=60_000_000_000,
                 stable_ns=250_000_000, max_age_ns=75_000_000,
                 tolerance_rad=np.deg2rad(.5), stopped_rad_s=.01):
        self.target = vector(target_rad)
        if (type(started_ns) is not int or started_ns < 0 or
                any(type(t) is not int or t <= 0 for t in (timeout_ns, stable_ns, max_age_ns)) or
                not np.isfinite([tolerance_rad, stopped_rad_s]).all() or
                min(tolerance_rad, stopped_rad_s) <= 0):
            raise ValueError("invalid_home_arrival_limits")
        self.started_ns, self.timeout_ns = started_ns, timeout_ns
        self.stable_ns, self.max_age_ns = stable_ns, max_age_ns
        self.tolerance_rad, self.stopped_rad_s = tolerance_rad, stopped_rad_s
        self.stable_since = self.last_sample = None
        self.completed_ns = None
        self.state, self.reason = "HOME_MOVING", None

    def update(self, *, now_ns, sample_ns, status_ns, q_rad, velocity_rad_s,
               in_motion, connected, healthy):
        if self.state in {"HOME_COMPLETE", "HOME_FAILED", "HOME_CANCELLED"}:
            return self.state
        try:
            if now_ns - self.started_ns > self.timeout_ns:
                raise HomeActionError("home_arrival_timeout")
            if any(type(t) is not int or not self.started_ns <= t <= now_ns or now_ns - t > self.max_age_ns
                   for t in (sample_ns, status_ns)):
                raise HomeActionError("stale_home_feedback")
            q, velocity = vector(q_rad), vector(velocity_rad_s)
            if connected is not True or healthy is not True or type(in_motion) is not bool:
                raise HomeActionError("home_controller_health_unknown")
            if self.last_sample is not None and sample_ns < self.last_sample:
                raise HomeActionError("home_feedback_reversed")
            if sample_ns == self.last_sample:
                return self.state
            if self.last_sample is not None and sample_ns - self.last_sample > self.max_age_ns:
                self.stable_since = None
            self.last_sample = sample_ns
            arrived = (in_motion is False and np.max(np.abs(q - self.target)) <= self.tolerance_rad
                       and np.max(np.abs(velocity)) <= self.stopped_rad_s)
            if not arrived:
                self.stable_since, self.state = None, "HOME_MOVING"
            else:
                self.state = "HOME_SETTLING"
                if self.stable_since is None:
                    self.stable_since = sample_ns
                if sample_ns - self.stable_since >= self.stable_ns:
                    self.state = "HOME_COMPLETE"
                    self.completed_ns = sample_ns
        except (HomeActionError, TypeError, ValueError) as exc:
            self.state, self.reason = "HOME_FAILED", str(exc)
        return self.state

    def cancel(self, reason="operator_cancelled"):
        if self.state != "HOME_COMPLETE":
            self.state, self.reason = "HOME_CANCELLED", reason

    def snapshot(self):
        return {"state": self.state, "reason": self.reason, "started_ns": self.started_ns,
                "completed_ns": self.completed_ns, "last_feedback_ns": self.last_sample,
                "target_rad": self.target.tolist(), "physical_task_success_verified": False}


def completion_status(*, task_running, task_failed, task_success_verified,
                      home_complete, stop_confirmed, authority_released):
    """Allocator boundary; Home/action exhaustion alone never implies success.

    The caller must use a post-task HomeArrivalVerifier, not initial Home pose.
    Physical task evidence belongs to an independent operator/perception system.
    No command, automatic stop, Home or policy chaining is performed here.
    """
    success = task_success_verified is True and task_failed is not True and task_running is False
    return {"TASK_RUNNING": task_running is True, "TASK_FAILED": task_failed is True,
            "TASK_SUCCESS": success, "HOME_COMPLETE": home_complete is True,
            "READY_FOR_NEXT_TASK": success and home_complete is True
                and stop_confirmed is True and authority_released is True}
