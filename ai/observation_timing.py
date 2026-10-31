"""Pure bounded timestamp history shared by Recorder and observation-only clients.

No ROS, Dataset, disk recording, robot control or GPU imports.
"""
from collections import deque
from dataclasses import asdict, dataclass
import numpy as np

NANOSECONDS = 1_000_000_000


class FixedDeadline:
    """Monotonic schedule. Consume at most one (latest due) slot per poll."""

    def __init__(self, fps: int, started_ns: int):
        if fps <= 0:
            raise ValueError("FPS must be positive")
        self.fps = fps
        self.started_ns = started_ns
        self.next_slot = 1
        self.missed = 0

    @property
    def next_ns(self):
        return self.started_ns + (self.next_slot * NANOSECONDS + self.fps - 1) // self.fps

    def consume(self, now_ns: int):
        if now_ns < self.next_ns:
            return None
        slot = (now_ns - self.started_ns) * self.fps // NANOSECONDS
        missed = slot - self.next_slot
        deadline_ns = self.started_ns + (slot * NANOSECONDS + self.fps - 1) // self.fps
        self.next_slot = slot + 1
        self.missed += missed
        return {"slot": slot, "deadline_monotonic_ns": deadline_ns,
                "poll_monotonic_ns": now_ns,
                "lateness_ns": now_ns - deadline_ns, "missed_deadlines": missed,
                "missed_deadlines_total": self.missed}


@dataclass(frozen=True)
class SyncOptions:
    """Initial quality limits, not measured worst-case synchronization guarantees.

    60ms wait covers the supplied 33.2/36.2ms median delivery with ~24ms slack;
    50/75ms ages are 1.5 periods at 30/20Hz, 40ms camera skew exceeds one RGB
    period. 10ms clock tolerance is a configurable local clock-change guard,
    never an offset applied to a source timestamp.
    """
    history_size: int = 32
    history_seconds: float = 0.5
    receive_wait_ms: float = 60.0
    camera_max_age_ms: float = 50.0
    state_max_age_ms: float = 75.0
    camera_skew_ms: float = 40.0
    observation_skew_ms: float = 75.0
    clock_tolerance_ms: float = 10.0

    def __post_init__(self):
        if not isinstance(self.history_size, int) or self.history_size < 1 or any(not np.isfinite(v) or v < 0 for v in asdict(self).values()):
            raise ValueError("History/synchronization limits must be finite and nonnegative")
        if self.clock_tolerance_ms <= 0 or self.history_seconds * 1000 < (
                self.receive_wait_ms + max(self.camera_max_age_ms, self.state_max_age_ms) + 50):
            raise ValueError("History duration must cover receive wait, age limit and a 50ms period")

    def ns(self, name):
        return round(getattr(self, name) * 1_000_000)


class IntegrityError(ValueError):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


def _header_ns(source):
    stamp = source.get("header_stamp")
    if stamp is None or stamp["sec"] < 0 or not 0 <= stamp["nanosec"] < NANOSECONDS:
        raise IntegrityError("missing_or_malformed_header", source=source)
    value = stamp["sec"] * NANOSECONDS + stamp["nanosec"]
    if value == 0:
        raise IntegrityError("zero_header", source=source)
    return value


class BoundedHistory:
    """Arrival-ordered references to immutable RGB/vectors, bounded by count AND age.

    Gripper's latest known command is carried separately, without an age timeout.
    Clock/header diagnostics survive queue pruning so a transient fault cannot
    disappear before the next sampling decision.
    """
    def __init__(self, options):
        self.options = options
        self.items = {}
        self.faults = deque(maxlen=options.history_size)
        self.fault_overflows = 0
        self.last_header = {}
        self.last_clock = None
        self.gripper_carry = None

    def append(self, name, value, source):
        source["history_error"] = None
        mono = source["callback_monotonic_ns"]
        local = {"sample_monotonic_ns": mono, "sample_system_time_ns": source["callback_system_time_ns"],
                 "receiver_clock": source["receiver_clock"],
                 "clock_read_span_ns": source.get("clock_read_span_ns", 0)}
        try:
            _check_clock(local, self.last_clock, self.options)
            if name != "gripper":
                header = _header_ns(source)
                if header > source["receiver_clock"]["ros_time_ns"] + self.options.ns("clock_tolerance_ms"):
                    raise IntegrityError("header_in_future_of_receiver", topic=name, header_ns=header)
                previous = self.last_header.get(name)
                if previous is not None and header < previous:
                    raise IntegrityError("header_reversal", topic=name, header_ns=header, previous_header_ns=previous)
                self.last_header[name] = header
                source["header_clock_relation"] = "operator_verified_unix_epoch_runtime_sanity_checked"
            elif value not in (0.0, 1.0):
                raise IntegrityError("invalid_gripper_command", value=value)
        except IntegrityError as exc:
            source["history_error"] = exc.reason
            if len(self.faults) == self.options.history_size:
                self.fault_overflows += 1
            self.faults.append({"topic": name, "reason": exc.reason, "details": exc.details,
                                "callback_monotonic_ns": mono,
                                "header_stamp": source.get("header_stamp")})
        # Retain the paired local clock reading even after a fault; active takes
        # latch faults and cannot continue across a clock epoch transition.
        self.last_clock = local
        queue_ = self.items.setdefault(name, deque(maxlen=self.options.history_size))
        queue_.append((value, source))
        if name == "gripper":
            self.gripper_carry = (value, source)
        self.prune(mono)

    def prune(self, now_ns):
        cutoff = now_ns - round(self.options.history_seconds * NANOSECONDS)
        for queue_ in self.items.values():
            while queue_ and queue_[0][1]["callback_monotonic_ns"] < cutoff:
                queue_.popleft()

    def snapshot(self, now_ns):
        self.prune(now_ns)
        return {"items": {name: tuple(values) for name, values in self.items.items()},
                "faults": tuple(self.faults), "fault_overflows": self.fault_overflows,
                "gripper_carry": self.gripper_carry,
                "limits": asdict(self.options),
                "counts": {name: len(values) for name, values in self.items.items()}}


def _check_clock(timing, anchor, options):
    """Compare Unix/ROS with Unix/ROS; compare elapsed durations, never header-mono."""
    receiver = timing.get("receiver_clock") or {}
    system = timing.get("sample_system_time_ns")
    mono = timing["sample_monotonic_ns"]
    tolerance = options.ns("clock_tolerance_ms") + timing.get("clock_read_span_ns", 0)
    if receiver.get("use_sim_time") or system is None or receiver.get("ros_time_ns") is None:
        raise IntegrityError("unsupported_clock", receiver_clock=receiver)
    if abs(receiver["ros_time_ns"] - system) > tolerance:
        raise IntegrityError("ros_system_clock_mismatch", receiver_clock=receiver, system_time_ns=system)
    if anchor is not None:
        if receiver.get("ros_clock_type") != anchor.get("receiver_clock", {}).get("ros_clock_type"):
            raise IntegrityError("receiver_clock_type_changed")
        monotonic_elapsed = mono - anchor["sample_monotonic_ns"]
        system_elapsed = system - anchor["sample_system_time_ns"]
        if monotonic_elapsed < 0 or abs(system_elapsed - monotonic_elapsed) > tolerance:
            raise IntegrityError("system_clock_jump_or_drift", system_elapsed_ns=system_elapsed,
                                 monotonic_elapsed_ns=monotonic_elapsed)


class HistoryAligner:
    """Causal selection on the operator-verified Unix epoch, with monotonic scheduling.

    The paired start clocks anchor the reference grid. Every decision validates
    that their elapsed times still agree. QP timestamps mean post-SDK-call target
    publication, NOT controller execution time. Headerless gripper events use
    the recorder's monotonic receipt boundary, not invented publisher stamps.
    """
    def __init__(self, options, start_timing, initial_state, gripper):
        self.options = options
        _check_clock(start_timing, None, options)
        self.anchor = start_timing
        self.start_mono = start_timing["sample_monotonic_ns"]
        self.start_system = start_timing["sample_system_time_ns"]
        self.target = np.asarray(initial_state, dtype=np.float32).copy()
        self.initial_state_source = start_timing.get("sources", {}).get("state")
        self.action_source = None
        self.action_seen = start_timing.get("sources", {}).get("action", {}).get("receipt_sequence", 0)
        self.action_high_water = None
        self.ignored_pre_episode_actions = 0
        self.last_ignored_action_sequence = 0
        self.last_ignored_action_source = None
        self.fault_overflow_baseline = start_timing.get("history_fault_overflows", 0)
        self.gripper = gripper
        if gripper is None or gripper[0] not in (0.0, 1.0) or gripper[1]["callback_monotonic_ns"] > self.start_mono:
            raise IntegrityError("unknown_initial_gripper")

    @classmethod
    def prepare(cls, options, history, timing):
        system = timing["sample_system_time_ns"]
        states = [(value, source) for value, source in history["items"].get("state", ())
                  if source.get("history_error") is None and _header_ns(source) <= system]
        if not states:
            raise IntegrityError("no_initial_state")
        value, _ = max(states, key=lambda item: _header_ns(item[1]))
        instance = cls(options, timing, value, history["gripper_carry"])
        instance.fault_overflow_baseline = history["fault_overflows"]
        selected, _, _ = instance.select(history, timing, timing["sample_monotonic_ns"], system, allow_start=True)
        instance.initial_state_source = selected["state"][1]
        return instance

    def check_integrity(self, history, timing):
        _check_clock(timing, self.anchor, self.options)
        if history["fault_overflows"] != self.fault_overflow_baseline:
            raise IntegrityError("history_fault_overflow")
        for fault in history["faults"]:
            name = fault["topic"]
            if fault["callback_monotonic_ns"] < self.start_mono:
                continue
            # A delayed pre-episode action is excluded, not a new command or a
            # new clock epoch. Other active timestamp faults invalidate the take.
            stamp = fault.get("header_stamp")
            if name == "action" and fault["reason"] == "header_reversal" and stamp is not None and (
                    stamp["sec"] * NANOSECONDS + stamp["nanosec"]) < self.start_system:
                continue
            raise IntegrityError(fault["reason"], topic=name, fault=fault)

    def select(self, history, timing, reference_mono_ns, reference_system_ns, allow_start=False):
        self.check_integrity(history, timing)
        if reference_mono_ns < self.start_mono or reference_system_ns < self.start_system or (
                not allow_start and reference_mono_ns == self.start_mono):
            raise IntegrityError("reference_before_episode_start")
        selected = {}
        for name in ("camera_317222074298", "camera_844212071939", "state"):
            candidates = [(value, source) for value, source in history["items"].get(name, ())
                          if source.get("history_error") is None and _header_ns(source) <= reference_system_ns]
            if not candidates:
                raise IntegrityError("missing_causal_observation", topic=name)
            value, source = max(candidates, key=lambda item: (_header_ns(item[1]), item[1]["receipt_sequence"]))
            age = reference_system_ns - _header_ns(source)
            limit = self.options.ns("state_max_age_ms" if name == "state" else "camera_max_age_ms")
            if age > limit:
                raise IntegrityError("observation_too_old", topic=name, age_ns=age, limit_ns=limit)
            selected[name] = (value, {**source, "reference_age_ns": age,
                                     "reference_age_clock": "unix_epoch_header",
                                     "header_semantics": "before_SDK_get_control_data" if name == "state" else "D435_global_time_header",
                                     "selection": "latest_header_at_or_before_reference"})
        stamps = [_header_ns(selected[name][1]) for name in ("camera_317222074298", "camera_844212071939", "state")]
        camera_skew = abs(stamps[0] - stamps[1])
        if camera_skew > self.options.ns("camera_skew_ms"):
            raise IntegrityError("camera_skew", skew_ns=camera_skew)
        if max(stamps) - min(stamps) > self.options.ns("observation_skew_ms"):
            raise IntegrityError("observation_skew", skew_ns=max(stamps) - min(stamps))
        actions = history["items"].get("action", ())
        expected = self.action_seen + 1
        future_pending = False
        for value, source in actions:
            stamp = _header_ns(source)
            sequence = source["receipt_sequence"]
            if sequence <= self.action_seen:
                continue
            if sequence != expected:
                raise IntegrityError("action_history_overflow", expected_sequence=expected, sequence=sequence)
            expected += 1
            if stamp < self.start_system or source["callback_monotonic_ns"] < self.start_mono:
                if sequence > self.last_ignored_action_sequence:
                    self.ignored_pre_episode_actions += 1
                    self.last_ignored_action_sequence = sequence
                    self.last_ignored_action_source = source
                if not future_pending:
                    self.action_seen = sequence
                continue
            if stamp > reference_system_ns:
                future_pending = True
                continue
            self.action_seen = sequence
            if self.action_high_water is not None and stamp < self.action_high_water:
                raise IntegrityError("action_timestamp_reversal", header_ns=stamp)
            self.action_high_water = stamp
        candidates = [(value, source) for value, source in actions if
                      self.start_system <= _header_ns(source) <= reference_system_ns and
                      source["callback_monotonic_ns"] >= self.start_mono]
        held = self.action_source
        if candidates:
            value, source = max(candidates, key=lambda item: (_header_ns(item[1]), item[1]["receipt_sequence"]))
            if held is None or _header_ns(source) >= _header_ns(held):
                self.target, self.action_source = value, source
        gripper_candidates = [item for item in history["items"].get("gripper", ())
                              if item[1]["callback_monotonic_ns"] <= reference_mono_ns]
        carry = history["gripper_carry"]
        expected = self.gripper[1]["receipt_sequence"] + 1
        for _, source in history["items"].get("gripper", ()):
            sequence = source["receipt_sequence"]
            if sequence < expected:
                continue
            if sequence != expected:
                raise IntegrityError("gripper_history_overflow", expected_sequence=expected, sequence=sequence)
            expected += 1
        if carry is not None and carry[1]["receipt_sequence"] >= expected:
            raise IntegrityError("gripper_history_overflow", expected_sequence=expected, sequence=carry[1]["receipt_sequence"])
        if carry is not None and carry[1]["callback_monotonic_ns"] <= reference_mono_ns:
            gripper_candidates.append(carry)
        if gripper_candidates:
            self.gripper = max([self.gripper, *gripper_candidates], key=lambda item: item[1]["callback_monotonic_ns"])
        if self.gripper[1]["callback_monotonic_ns"] > reference_mono_ns:
            raise IntegrityError("no_causal_gripper")
        selected["gripper"] = (self.gripper[0], {**self.gripper[1], "selection": "last_receipt_at_or_before_reference",
                                                "reference_age_clock": "recorder_monotonic",
                                                "reference_age_ns": reference_mono_ns - self.gripper[1]["callback_monotonic_ns"]})
        if self.action_source is not None:
            selected["action"] = (self.target, {**self.action_source,
                                                "reference_age_ns": reference_system_ns - _header_ns(self.action_source)})
        origin = {"kind": "initial_hold" if self.action_source is None else "qp_target",
                  "source": self.action_source, "held_from_previous_reference": held is self.action_source,
                  "hold_start_system_ns": self.start_system if self.action_source is None else _header_ns(self.action_source),
                  "definition": "absolute_joint_target_rad; QP header is after SDK return, not execution time"}
        return selected, {"reference_system_time_ns": reference_system_ns,
                          "reference_monotonic_ns": reference_mono_ns,
                          "camera_skew_ns": camera_skew, "observation_skew_ns": max(stamps) - min(stamps),
                          "history_counts": history["counts"], "recorded_action": origin,
                          "ignored_pre_episode_actions": self.ignored_pre_episode_actions,
                          "last_ignored_action_source": self.last_ignored_action_source}, self.target
