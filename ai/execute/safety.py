"""Indy7-specific validation. All numerical limits come from a reviewed profile."""
from dataclasses import dataclass, fields
import json
from pathlib import Path
import numpy as np

PERIOD_NS = 50_000_000


class SafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SafetyConfig:
    profile: str
    reviewed_for_real: bool
    review_record: str
    joint_min_rad: list
    joint_max_rad: list
    max_target_error_rad: list
    max_step_rad: list
    max_velocity_rad_s: list
    max_acceleration_rad_s2: list
    tracking_error_rad: list
    tracking_timeout_ms: float
    joint_stale_ms: float
    status_stale_ms: float
    observation_stale_ms: float
    max_action_lateness_ms: float
    command_timeout_ms: float
    mode_timeout_ms: float
    stop_timeout_ms: float
    scheduler_watchdog_ms: float
    inference_watchdog_ms: float
    first_action_timeout_ms: float
    stopped_velocity_rad_s: float
    gripper_open_threshold: float
    gripper_grasp_threshold: float
    gripper_output_min: float
    gripper_output_max: float
    gripper_dwell_ms: float
    gripper_min_interval_ms: float
    gripper_timeout_ms: float

    def __post_init__(self):
        vectors = ("joint_min_rad", "joint_max_rad", "max_target_error_rad", "max_step_rad",
                   "max_velocity_rad_s", "max_acceleration_rad_s2", "tracking_error_rad")
        for name in vectors:
            array = np.asarray(getattr(self, name), dtype=float)
            if array.shape != (6,) or not np.isfinite(array).all(): raise ValueError(f"Invalid six-joint limit: {name}")
            if name not in ("joint_min_rad", "joint_max_rad") and np.any(array <= 0): raise ValueError(name)
        if np.any(np.asarray(self.joint_min_rad) >= self.joint_max_rad): raise ValueError("Joint range")
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name not in (*vectors, "profile", "reviewed_for_real", "review_record"):
                if not isinstance(value, (int, float)) or not np.isfinite(value): raise ValueError(field.name)
                if field.name.endswith("_ms") and value <= 0: raise ValueError(field.name)
        if not 0 < self.max_action_lateness_ms < 50 or not 0 < self.command_timeout_ms <= 50: raise ValueError("20Hz budget")
        if not self.gripper_output_min <= self.gripper_open_threshold < self.gripper_grasp_threshold <= self.gripper_output_max:
            raise ValueError("Gripper hysteresis")
        if self.stopped_velocity_rad_s <= 0: raise ValueError("Stop velocity")

    @classmethod
    def load(cls, path): return cls(**json.loads(Path(path).read_text()))


@dataclass
class RobotFeedback:
    joints: np.ndarray
    velocity: np.ndarray
    joint_received_ns: int
    status_received_ns: int
    connected: bool | None
    servo_ready: bool | None
    ready: bool | None
    mode: str | None
    fault: bool | None
    estop: bool | None
    in_motion: bool | None
    external_exclusive: bool | None
    qp_inhibited: bool | None
    evidence: str | None
    controller_op_state: int | None = None
    teleop_method: int | None = None

    def require(self, now_ns, config, mode=None):
        if any(np.asarray(value).shape != (6,) or not np.isfinite(value).all() for value in (self.joints, self.velocity)):
            raise SafetyError("invalid_joint_feedback")
        for received, limit, name in ((self.joint_received_ns, config.joint_stale_ms, "joint"),
                                       (self.status_received_ns, config.status_stale_ms, "controller_status")):
            if not 0 <= now_ns - received <= limit * 1e6: raise SafetyError(f"stale_{name}")
        if self.connected is not True or self.servo_ready is not True or self.ready is not True:
            raise SafetyError("controller_not_confirmed_ready")
        if self.fault is not False or self.estop is not False: raise SafetyError("controller_fault_or_estop")
        if self.external_exclusive is not True or self.qp_inhibited is not True or not self.evidence:
            raise SafetyError("command_authority_unknown_or_conflicting")
        if mode is not None and self.mode != mode: raise SafetyError("controller_mode_not_confirmed")
        if np.any(self.joints < config.joint_min_rad) or np.any(self.joints > config.joint_max_rad): raise SafetyError("measured_joint_limit")
        if np.any(np.abs(self.velocity) > config.max_velocity_rad_s): raise SafetyError("measured_joint_velocity")


@dataclass
class ObservationHealth:
    validated_ns: int
    valid: bool
    detail: dict

    @classmethod
    def from_timing(cls, timing, options):
        sources, alignment = timing["sources"], timing["alignment"]
        for name, key in (("camera_317222074298", "camera_max_age_ms"), ("camera_844212071939", "camera_max_age_ms"), ("state", "state_max_age_ms")):
            if not 0 <= sources[name]["reference_age_ns"] <= options.ns(key): raise SafetyError("invalid_observation_age")
        skews = [alignment["camera_skew_ns"], alignment["observation_skew_ns"]]
        if not np.isfinite(skews).all() or min(skews) < 0 or skews[0] > options.ns("camera_skew_ms") or skews[1] > options.ns("observation_skew_ms"):
            raise SafetyError("invalid_observation_skew")
        return cls(timing["sample_created_monotonic_ns"], True, {"reference_ns": timing["observation_monotonic_ns"],
                    "camera_skew_ns": alignment["camera_skew_ns"], "observation_skew_ns": alignment["observation_skew_ns"]})

    def require(self, now_ns, config):
        if self.valid is not True or not 0 <= now_ns - self.validated_ns <= config.observation_stale_ms * 1e6:
            raise SafetyError("stale_or_invalid_observation")


def validate_action(record, session_id, checkpoint_id, now_ns, config):
    value = np.asarray(record.get("action"), dtype=float)
    if value.shape != (7,) or not np.isfinite(value).all(): raise SafetyError("action_must_be_finite_7d")
    if record.get("session_id") != session_id or record.get("checkpoint_id") != checkpoint_id: raise SafetyError("obsolete_session_or_checkpoint")
    if not isinstance(record.get("request_id"), str) or not record["request_id"]: raise SafetyError("missing_request_id")
    schedule = record.get("schedule", {})
    if type(record.get("action_index")) is not int or record["action_index"] < 0:
        raise SafetyError("invalid_action_index")
    if type(schedule.get("slot")) is not int or schedule["slot"] < 1: raise SafetyError("invalid_schedule_slot")
    deadline = schedule.get("deadline_monotonic_ns")
    if type(deadline) is not int or not 0 <= now_ns - deadline <= config.max_action_lateness_ms * 1e6:
        raise SafetyError("action_deadline_expired_or_not_due")
    if schedule.get("missed_deadlines") != 0 or schedule.get("missed_deadlines_total") != 0: raise SafetyError("scheduler_deadline_gap")
    if not config.gripper_output_min <= value[6] <= config.gripper_output_max: raise SafetyError("gripper_output_range")
    return value


class SafetyGuard:
    def __init__(self, config):
        self.config = config
        self.previous_target = self.previous_deadline = self.previous_slot = None
        self.previous_velocity = self.previous_feedback = None
        self.previous_send_ns = None
        self.tracking_bad_since = None

    def tracking(self, feedback, now_ns):
        cfg = self.config
        feedback.require(now_ns, cfg, "joint_absolute")
        if self.previous_target is not None and np.any(np.abs(self.previous_target - feedback.joints) > cfg.tracking_error_rad):
            if self.tracking_bad_since is None: self.tracking_bad_since = now_ns
            if now_ns - self.tracking_bad_since >= cfg.tracking_timeout_ms * 1e6: raise SafetyError("persistent_tracking_error")
        else: self.tracking_bad_since = None
        if self.previous_feedback is not None and feedback.joint_received_ns > self.previous_feedback.joint_received_ns:
            dt = (feedback.joint_received_ns - self.previous_feedback.joint_received_ns) / 1e9
            if np.any(np.abs((feedback.velocity - self.previous_feedback.velocity) / dt) > cfg.max_acceleration_rad_s2):
                raise SafetyError("measured_joint_acceleration")
        self.previous_feedback = feedback

    def check(self, record, value, feedback, health, now_ns):
        cfg, target = self.config, value[:6]
        health.require(now_ns, cfg)
        self.tracking(feedback, now_ns)
        if np.any(target < cfg.joint_min_rad) or np.any(target > cfg.joint_max_rad): raise SafetyError("target_joint_limit")
        if np.any(np.abs(target - feedback.joints) > cfg.max_target_error_rad): raise SafetyError("target_current_error")
        deadline, slot = record["schedule"]["deadline_monotonic_ns"], record["schedule"]["slot"]
        if self.previous_deadline is not None and (deadline - self.previous_deadline != PERIOD_NS or slot != self.previous_slot + 1):
            raise SafetyError("command_sequence_gap")
        previous = feedback.joints if self.previous_target is None else self.previous_target
        delta = target - previous
        if np.any(np.abs(delta) > cfg.max_step_rad): raise SafetyError("joint_step_limit")
        dt = .05 if self.previous_send_ns is None else min(.05, (now_ns - self.previous_send_ns) / 1e9)
        if dt <= 0: raise SafetyError("command_time_not_increasing")
        velocity = delta / dt
        if np.any(np.abs(velocity) > cfg.max_velocity_rad_s): raise SafetyError("command_velocity_limit")
        old_velocity = feedback.velocity if self.previous_velocity is None else self.previous_velocity
        if np.any(np.abs((velocity - old_velocity) / dt) > cfg.max_acceleration_rad_s2): raise SafetyError("command_acceleration_limit")
        return velocity

    def commit(self, record, value, velocity, sent_ns):
        self.previous_target = value[:6].copy()
        self.previous_velocity = velocity.copy()
        self.previous_deadline = record["schedule"]["deadline_monotonic_ns"]
        self.previous_slot = record["schedule"]["slot"]
        self.previous_send_ns = sent_ns
