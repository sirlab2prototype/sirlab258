"""Opt-in ROS log-only observation recovery. No robot or Action queue here."""
from collections import Counter, deque
from dataclasses import asdict, dataclass
import math
import time

from ai import dataset_contract as contract
from ai.observation_timing import FixedDeadline, IntegrityError, _check_clock


@dataclass(frozen=True)
class RecoveryOptions:
    stable_observations: int = 3
    no_valid_timeout: float = 1.0

    def __post_init__(self):
        if type(self.stable_observations) is not int or self.stable_observations < 1:
            raise ValueError("Stable observation count must be a positive integer")
        if not math.isfinite(self.no_valid_timeout) or self.no_valid_timeout <= 0:
            raise ValueError("No-valid-observation timeout must be finite and positive")


def add_recovery_arguments(parser):
    parser.add_argument("--ros-observation-recovery", action="store_true",
                        help="Opt-in ROS Dry Run only: stabilize and pause/reinfer on transient camera timing errors")
    parser.add_argument("--ros-stable-observations", type=int, default=None)
    parser.add_argument("--ros-no-valid-timeout", type=float, default=None, metavar="SECONDS")


def recovery_options(args, *, allowed):
    if not args.ros_observation_recovery:
        if args.ros_stable_observations is not None or args.ros_no_valid_timeout is not None:
            raise ValueError("Recovery tuning requires --ros-observation-recovery")
        return None
    if not allowed:
        raise ValueError("Observation recovery is restricted to ROS Dry Run without execution")
    return RecoveryOptions(3 if args.ros_stable_observations is None else args.ros_stable_observations,
                           1.0 if args.ros_no_valid_timeout is None else args.ros_no_valid_timeout)


def transient_camera_error(error):
    return isinstance(error, IntegrityError) and (
        error.reason == "camera_skew" or
        error.reason in ("observation_too_old", "missing_causal_observation") and
        error.details.get("topic") in contract.CAMERA_NAMES)


class RecoveryMonitor:
    consecutive_limit, window_limit, window_ns = 3, 6, 10_000_000_000

    def __init__(self, options):
        self.options = options
        self.startup_ns = None
        self.startup_started = None
        self.startup_ready = False
        self.startup_valid = self.startup_discarded = 0
        self.startup_errors = Counter()
        self.startup_waits = Counter()
        self.valid_count = self.discarded = self.consecutive = self.max_consecutive = 0
        self.errors, self.recent = Counter(), deque()
        self.fatal_errors = Counter()
        self.last_error_reference = self.last_valid_ns = None
        self.recovering = False
        self.successes = self.invalidated_actions = self.abandoned_requests = self.ignored_responses = 0
        self.failure = None

    def _prune(self, now):
        while self.recent and self.recent[0] < now-self.window_ns:
            self.recent.popleft()

    def valid(self, now):
        self.valid_count += 1
        self.consecutive = 0
        self.last_valid_ns = now

    def reject(self, error, reference, now):
        if reference == self.last_error_reference:
            return  # one decision per 20Hz reference, never per 1ms poll
        self.last_error_reference = reference
        self.discarded += 1
        self.errors[error.reason] += 1
        self.consecutive += 1
        self.max_consecutive = max(self.max_consecutive, self.consecutive)
        self._prune(now)
        self.recent.append(now)  # bounded by the six-error termination rule
        self.recovering = True
        if self.consecutive >= self.consecutive_limit:
            self.fail("consecutive_observation_error_limit")
        if len(self.recent) >= self.window_limit:
            self.fail("observation_error_window_limit")

    def watchdog(self, now):
        if self.last_valid_ns is not None and now-self.last_valid_ns >= round(self.options.no_valid_timeout*1e9):
            self.fail("no_valid_observation_timeout")

    def fail(self, reason):
        self.failure = reason
        raise RuntimeError(reason)

    def resumed(self):
        if self.recovering:
            self.successes += 1
            self.recovering = False

    def report(self, now):
        self._prune(now)
        return {"enabled": True, "scope": "ROS_DRY_RUN_TRIAL_ONLY", "settings": {**asdict(self.options), "consecutive_error_limit": 3,
                    "window_error_limit": 6, "window_seconds": 10},
                "initial_stabilization_ms": None if self.startup_ns is None else self.startup_ns/1e6,
                "initial_stabilization_succeeded": self.startup_ready,
                "startup_valid_observations": self.startup_valid,
                "startup_discarded_observations": self.startup_discarded,
                "startup_error_counts": dict(self.startup_errors),
                "startup_readiness_waits": dict(self.startup_waits),
                "valid_observations": self.valid_count, "discarded_observations": self.discarded,
                "total_valid_observations": self.startup_valid+self.valid_count,
                "total_discarded_observations": self.startup_discarded+self.discarded,
                "fatal_integrity_error_counts": dict(self.fatal_errors),
                "error_counts": dict(self.errors), "consecutive_errors": self.consecutive,
                "maximum_consecutive_errors": self.max_consecutive, "recent_10s_errors": len(self.recent),
                "recovery_successes": self.successes, "recovery_pending": self.recovering,
                "failure_reason": self.failure, "invalidated_actions": self.invalidated_actions,
                "abandoned_inference_requests": self.abandoned_requests,
                "ignored_obsolete_responses": self.ignored_responses,
                "had_recovery": self.discarded > 0,
                "quality": "recovery_failed" if self.failure else "recovered" if self.discarded else "no_active_errors"}


def stabilize(source, monitor, log, session_id, timeout, reception_report):
    """Validate consecutive real reference slots; no inference/actions during startup."""
    started = time.monotonic_ns()
    monitor.startup_started = started
    initial_clock = source.timing()
    anchor = deadline = latest = None
    consecutive, next_prepare = 0, started
    wait_ns = source.options.ns("receive_wait_ms")
    last_reason = None
    while True:
        source.update()
        reception_report()
        timing, history = source.snapshot()
        _check_clock(timing, initial_clock, source.options)
        # Header/clock faults must not be hidden by repeated prepare()/new anchors.
        if history["faults"] or history["fault_overflows"]:
            fault = history["faults"][-1] if history["faults"] else {"reason":"history_fault_overflow"}
            raise IntegrityError(fault["reason"], fault=fault)
        now = time.monotonic_ns()
        if now-started >= round(timeout*1e9):
            monitor.fail(f"observation_stabilization_timeout: {last_reason or 'insufficient_stable_observations'}")
        if anchor is None and now >= next_prepare:
            next_prepare = now+2_000_000
            try:
                anchor = source.begin_stabilization()
                deadline = FixedDeadline(contract.FPS, anchor["sample_monotonic_ns"]+wait_ns)
            except IntegrityError as error:
                if not (transient_camera_error(error) or error.reason in ("no_initial_state", "unknown_initial_gripper")):
                    raise
                monitor.startup_waits[error.reason] += 1
                if last_reason != error.reason:
                    log("observation_not_ready", session_id=session_id, reason=error.reason, phase="stabilizing")
                last_reason = error.reason
        if deadline is not None:
            source.health()
            schedule = deadline.consume(time.monotonic_ns())
            if schedule is not None:
                if schedule["missed_deadlines"]:
                    monitor.fail("stabilization_deadline_missed")
                reference = schedule["deadline_monotonic_ns"]-wait_ns
                try:
                    latest = source.sample(reference)
                except IntegrityError as error:
                    if not transient_camera_error(error):
                        raise
                    consecutive = 0
                    if error.reason == "missing_causal_observation":
                        monitor.startup_waits[error.reason] += 1
                        if last_reason != error.reason:
                            log("observation_not_ready", session_id=session_id, reason=error.reason, phase="stabilizing")
                    else:
                        monitor.startup_discarded += 1
                        monitor.startup_errors[error.reason] += 1
                        log("stabilization_observation_rejected", session_id=session_id,
                            reference_monotonic_ns=reference, reason=error.reason, details=error.details)
                    last_reason = error.reason
                else:
                    consecutive += 1
                    monitor.startup_valid += 1
                    log("stabilization_observation", session_id=session_id, timing=latest[1],
                        consecutive=consecutive, required=monitor.options.stable_observations)
                    if consecutive >= monitor.options.stable_observations:
                        monitor.startup_ns = time.monotonic_ns()-started
                        monitor.startup_ready = True
                        monitor.last_valid_ns = time.monotonic_ns()
                        return anchor, deadline, latest
        time.sleep(.001)
