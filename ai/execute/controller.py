"""Indy7/Mark7 state machine. One pending send, no duplicated action queue."""
from dataclasses import dataclass, asdict
import threading
import time

import numpy as np
from ai.execute.safety import SafetyGuard, SafetyError, validate_action
from ai.execute.gripper import GripperAdapter

STATES = ("DISCONNECTED", "READY", "ARMED", "RUNNING", "STOPPING", "STOPPED", "FAULT")


@dataclass(frozen=True)
class UserApproval:
    session_id: str
    checkpoint_id: str
    operator: str
    scope: str
    approved_ns: int
    expires_ns: int

    def require(self, session, checkpoint, now_ns, is_mock):
        if self.session_id != session or self.checkpoint_id != checkpoint or not self.operator.strip(): raise SafetyError("approval_identity_mismatch")
        if self.scope != ("mock" if is_mock else "real") or not self.approved_ns <= now_ns < self.expires_ns:
            raise SafetyError("explicit_current_user_approval_required")


class ExecutionController:
    def __init__(self, indy, mark7, lease, config, log, *, clock=time.monotonic_ns):
        self.indy, self.lease, self.config, self.clock = indy, lease, config, clock
        self._log_sink, self.logging_error = log, None
        self.log = self._safe_log
        self.is_mock = indy.is_mock and mark7.is_mock
        if indy.is_mock != mark7.is_mock: raise SafetyError("mixed_mock_real_adapters")
        self.gripper = GripperAdapter(mark7, config, lambda event, **value: self.log(event, session_id=self.session_id, **value))
        self.state = "DISCONNECTED"
        self.state_reason = "not_connected"
        self.lock = threading.RLock()
        self.revoked = threading.Event()
        self.revoked.set()
        self.session_id = self.checkpoint_id = self.checkpoint = None
        self.pending = self.mode_future = self.stop_future = None
        self.health = self.last_feedback = None
        self.stop_confirmed = self.fault_latched = False
        self.normal_end = self.user_aborted = self.emergency_requested = False
        self.reason = None
        self.cancel_error = None
        self.submitted = self.acknowledged = 0
        self.started_ns = self.last_action_ns = None
        self.scheduler_heartbeat_ns = self.inference_heartbeat_ns = None
        self.cancel_scheduler = lambda reason: None
        self.watchdog_stop = threading.Event()
        self.watchdog_thread = None
        self.dispatching = False

    def _safe_log(self, event, **value):
        try:
            self._log_sink(event, **value)
        except Exception as exc:
            self.logging_error = str(exc)
            self._log_sink = lambda *args, **kwargs: None
            self.fault("execution_logging_failed")

    def _state(self, state, reason):
        old, self.state = self.state, state
        self.state_reason = reason
        self.log("execution_state", old_state=old, state=state, reason=reason, session_id=self.session_id)

    def connect(self, session_id, checkpoint_id, checkpoint, health):
        with self.lock:
            if self.state not in ("DISCONNECTED", "STOPPED") or self.fault_latched: raise SafetyError("new_session_not_allowed")
            if not isinstance(session_id, str) or not session_id or not isinstance(checkpoint_id, str) or not checkpoint_id:
                raise SafetyError("session_and_checkpoint_required")
            now = self.clock()
            feedback = self.indy.feedback(now)
            feedback.require(now, self.config, "idle")
            health.require(now, self.config)
            if feedback.in_motion is not False or np.any(np.abs(feedback.velocity) > self.config.stopped_velocity_rad_s):
                raise SafetyError("robot_not_stopped")
            self.gripper.initialize(self.gripper.transport.initial_state())
            self.guard = SafetyGuard(self.config)
            self.session_id, self.checkpoint_id, self.checkpoint = session_id, checkpoint_id, checkpoint
            self.health, self.last_feedback = health, feedback
            self.submitted = self.acknowledged = 0
            self.pending = self.mode_future = self.stop_future = None
            self.started_ns = self.last_action_ns = None
            self.normal_end = self.user_aborted = self.stop_confirmed = False
            self._state("READY", "readiness_verified_no_motion")
            self.log("execution_readiness", session_id=self.session_id, safety_config=asdict(self.config),
                     mode=feedback.mode, controller_op_state=feedback.controller_op_state,
                     teleop_method=feedback.teleop_method, evidence=feedback.evidence,
                     gripper_state=self.gripper.state, mock=self.is_mock)

    def arm(self, approval):
        with self.lock:
            if self.state != "READY" or self.mode_future is not None: raise SafetyError("not_ready_to_arm")
            now = self.clock()
            approval.require(self.session_id, self.checkpoint_id, now, self.is_mock)
            if not self.is_mock and (self.config.reviewed_for_real is not True or not self.config.review_record):
                raise SafetyError("hardware_profile_not_reviewed")
            feedback = self.indy.feedback(now)
            feedback.require(now, self.config, "idle")
            if feedback.in_motion is not False or np.any(np.abs(feedback.velocity) > self.config.stopped_velocity_rad_s):
                raise SafetyError("robot_not_stopped")
            self.health.require(now, self.config)
            self.lease.acquire(self.session_id)
            self.revoked.clear()
            self.approval = approval
        try:
            self.indy.activate(self.revoked, self.config)
            self.gripper.transport.activate(self.revoked, self.config)
            future = self.indy.request_absolute_mode()
            with self.lock:
                self.mode_future, self.mode_started_ns = future, self.clock()
                if self.revoked.is_set() or self.state != "READY":
                    self.mode_future = None
                    raise SafetyError("arm_revoked")
                self.log("execution_mode_requested", session_id=self.session_id, requested_mode="joint_absolute")
        except Exception as exc:
            self.fault(f"arm_failed: {exc}")
            raise

    def begin(self):
        with self.lock:
            if self.state != "ARMED" or self.revoked.is_set(): raise SafetyError("not_explicitly_armed")
            now = self.clock()
            self.approval.require(self.session_id, self.checkpoint_id, now, self.is_mock)
            self.health.require(now, self.config)
            self.indy.feedback(now).require(now, self.config, "joint_absolute")
            self.started_ns = self.scheduler_heartbeat_ns = self.inference_heartbeat_ns = now
            self._state("RUNNING", "explicit_session_start")

    def observe(self, health):
        with self.lock: self.health = health

    def heartbeat(self, *, inference=False):
        with self.lock:
            self.scheduler_heartbeat_ns = self.clock()
            if inference: self.inference_heartbeat_ns = self.clock()

    def consume(self, record):
        with self.lock:
            if self.state != "RUNNING" or self.revoked.is_set(): raise SafetyError("execution_not_running")
            try:
                self.poll()
                if self.state != "RUNNING": raise SafetyError("execution_stopped_during_poll")
                if self.pending is not None: raise SafetyError("previous_command_still_pending")
                # poll() may refresh feedback. Validate against a time captured
                # afterwards, rather than making that feedback appear future-dated.
                now = self.clock()
                feedback = self.indy.feedback(now)
                value = validate_action(record, self.session_id, self.checkpoint_id, now, self.config)
                velocity = self.guard.check(record, value, feedback, self.health, now)
                self.last_feedback = feedback
                # Mark dispatch pending BEFORE invoking a possibly slow transport.
                self.dispatch_started_ns = now
                self.dispatching = True
            except Exception as exc:
                self.fault(str(exc))
                self.log("execution_action_rejected", reason=str(exc), session_id=self.session_id,
                         action_repr=repr(record.get("action")), measured_joints_rad=feedback.joints.tolist() if 'feedback' in locals() else None)
                raise
        try:
            if self.revoked.is_set(): raise SafetyError("command_revoked_before_send")
            expires_ns = record["schedule"]["deadline_monotonic_ns"] + round(self.config.max_action_lateness_ms * 1e6)
            future = self.indy.send_absolute(value[:6], expires_ns=expires_ns)  # absolute radians
            completed = self.clock()
            with self.lock:
                self.submitted += 1
                if completed - now > self.config.command_timeout_ms * 1e6: raise SafetyError("command_dispatch_over_budget")
                if self.revoked.is_set() or self.state != "RUNNING": raise SafetyError("command_revoked_during_dispatch")
                self.pending = (future, now)
                self.guard.commit(record, value, velocity, completed)
                self.last_action_ns = completed
                self.log("execution_joint_command", session_id=self.session_id, checkpoint_id=self.checkpoint_id,
                         target_absolute_rad=value[:6].tolist(), request_id=record["request_id"],
                         action_index=record["action_index"], dispatch_duration_ns=completed-now,
                         confirmation="pending", mock=self.is_mock)
            if not self.revoked.is_set(): self.gripper.request(value[6], self.clock())
            self.poll()
        except Exception as exc:
            self.fault(str(exc))
            raise
        finally:
            self.dispatching = False

    def poll(self):
        with self.lock:
            now = self.clock()
            try:
                self.gripper.poll(now)
                if self.state == "STOPPING":
                    self._poll_stop(now)
                    return
                if self.mode_future is not None:
                    if now - self.mode_started_ns > self.config.mode_timeout_ms * 1e6: raise SafetyError("mode_confirmation_timeout")
                    if self.mode_future.done():
                        if self.mode_future.result().success is not True: raise SafetyError("mode_request_rejected")
                        feedback = self.indy.feedback(now)
                        feedback.require(now, self.config)
                        if feedback.mode in (None, "idle"): return
                        if feedback.mode != "joint_absolute": raise SafetyError("controller_mode_conflict")
                        self.mode_future = None
                        self._state("ARMED", "absolute_mode_and_external_readiness_confirmed")
                if self.pending is not None:
                    future, started = self.pending
                    if now - started > self.config.command_timeout_ms * 1e6: raise SafetyError("controller_command_timeout")
                    if future.done():
                        response = future.result()
                        if response.success is not True: raise SafetyError("command_rejected")
                        self.acknowledged += 1
                        self.pending = None
                        self.log("execution_command_ack", session_id=self.session_id, confirmation=response.confirmation)
                if self.state == "RUNNING":
                    self.health.require(now, self.config)
                    feedback = self.indy.feedback(now)
                    self.guard.tracking(feedback, now)
                    self.last_feedback = feedback
            except Exception as exc:
                if self.state != "STOPPING":
                    self.fault(str(exc))
                else:
                    self.fault_latched, self.normal_end = True, False
                    self.reason = str(exc)
            if self.state == "STOPPING": self._poll_stop(now)

    def _poll_stop(self, now):
        if now - self.stop_started_ns > self.config.stop_timeout_ms * 1e6:
            self.lease.quarantine()
            self.fault_latched = True
            self.normal_end = False
            self._state("FAULT", "stop_unconfirmed_lease_quarantined")
            return
        if self.stop_future is None or not self.stop_future.done(): return
        try:
            if self.stop_future.result().success is not True: raise SafetyError("stop_request_failed")
            feedback = self.indy.feedback(now)
            try:
                feedback.require(now, self.config, "idle")
            except SafetyError:
                return  # Keep STOPPING; a cached status is not stop confirmation.
            if feedback.in_motion is not False or np.any(np.abs(feedback.velocity) > self.config.stopped_velocity_rad_s): return
            if not self.gripper.settled: return
            self.stop_confirmed = True
            self.last_feedback = feedback
            self.pending = self.mode_future = None
            self.lease.release(confirmed_stopped=True)
            self._state("FAULT" if self.fault_latched else "STOPPED", "stop_confirmed")
        except Exception as exc:
            self.lease.quarantine()
            self.fault_latched = True
            self.normal_end = False
            self._state("FAULT", f"stop_confirmation_failed: {exc}")

    def stop(self, reason="user_abort", *, user_completed=False, fault=False, emergency=False):
        # Revocation does not wait for the main-loop lock or a blocked send.
        self.revoked.set()
        try:
            self.cancel_scheduler(reason)
        except Exception as exc:
            self.cancel_error = str(exc)  # failure to clear a caller must not suppress the stop request
        with self.lock:
            if self.state in ("STOPPED", "FAULT", "STOPPING"):
                self.fault_latched |= fault
                if fault:
                    self.normal_end = False
                    if self.state == "STOPPED": self._state("FAULT", reason)
                return
            self.reason = reason
            self.fault_latched |= fault
            self.emergency_requested |= emergency
            self.user_aborted |= reason in ("user_abort", "user_interrupt")
            self.normal_end = bool(user_completed and not self.fault_latched)
            self.gripper.stop()
            self.mode_future = None
            self.stop_started_ns = self.clock()
            self._state("STOPPING", reason)
            owned = self.lease.owner == self.session_id and self.lease.stream is not None
            if not owned:
                self._state("FAULT" if self.fault_latched else "STOPPED", "no_owned_motion_lease")
                return
        try:
            future = self.indy.request_stop()
            with self.lock:
                self.stop_future = future
                self.log("execution_stop_requested", session_id=self.session_id, reason=reason,
                         emergency_requested=self.emergency_requested, transport_confirmation_pending=True)
        except Exception as exc:
            with self.lock:
                self.lease.quarantine()
                self.fault_latched = True
                self.normal_end = False
                self._state("FAULT", f"stop_send_failed: {exc}")

    def fault(self, reason): self.stop(reason, fault=True)
    def emergency_stop(self, reason="emergency_stop_requested"): self.stop(reason, fault=True, emergency=True)
    def complete_by_user(self): self.stop("user_completed", user_completed=True)

    def watchdog_check(self):
        with self.lock:
            if self.state == "STOPPING":
                # Main-loop stalls must not prevent confirmation/timeout of the
                # independently requested stop. Feedback providers are cached.
                self.poll()
                return
            if self.state != "RUNNING": return
            now = self.clock()
            reason = None
            if self.pending is not None and now - self.pending[1] > self.config.command_timeout_ms * 1e6: reason = "controller_command_watchdog_timeout"
            elif self.dispatching and now - self.dispatch_started_ns > self.config.command_timeout_ms * 1e6: reason = "command_dispatch_watchdog_timeout"
            elif now - self.scheduler_heartbeat_ns > self.config.scheduler_watchdog_ms * 1e6: reason = "scheduler_watchdog_timeout"
            elif now - self.inference_heartbeat_ns > self.config.inference_watchdog_ms * 1e6: reason = "inference_watchdog_timeout"
            elif self.last_action_ns is None and now - self.started_ns > self.config.first_action_timeout_ms * 1e6: reason = "first_action_watchdog_timeout"
            elif self.last_action_ns is not None and now - self.last_action_ns > self.config.scheduler_watchdog_ms * 1e6: reason = "action_stream_watchdog_timeout"
        if reason: self.fault(reason)

    def start_watchdog(self):
        if self.watchdog_thread: return
        def loop():
            while not self.watchdog_stop.wait(.01): self.watchdog_check()
        self.watchdog_thread = threading.Thread(target=loop, name="act-execution-watchdog", daemon=True)
        self.watchdog_thread.start()

    def close_watchdog(self):
        self.watchdog_stop.set()
        if self.watchdog_thread: self.watchdog_thread.join(1)

    def snapshot(self):
        with self.lock:
            return {"state": self.state, "state_reason": self.state_reason, "session_id": self.session_id, "checkpoint_id": self.checkpoint_id,
                    "checkpoint": self.checkpoint, "started_monotonic_ns": self.started_ns,
                    "submitted_actions": self.submitted, "acknowledged_actions": self.acknowledged,
                    "executed_actions": self.acknowledged if self.is_mock else None,
                    "last_joints_rad": self.last_feedback.joints.tolist() if self.last_feedback else None,
                    "gripper_command_state": self.gripper.state, "gripper_requests": self.gripper.sent,
                    "policy_normal_end": self.normal_end, "user_aborted": self.user_aborted,
                    "safety_fault": self.fault_latched, "stop_confirmed": self.stop_confirmed,
                    "indy_stop_confirmed": self.stop_confirmed, "gripper_service_settled": self.gripper.settled,
                    "mark7_physical_stop_confirmed": None,
                    "emergency_requested": self.emergency_requested, "reason": self.reason,
                    "lease_quarantined": self.lease.quarantined, "mock": self.is_mock,
                    "logging_error": self.logging_error, "scheduler_cancel_error": self.cancel_error,
                    "physical_grasp_verified": False, "physical_execution_verified": False}
