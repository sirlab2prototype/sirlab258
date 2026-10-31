"""Task 1 orchestration over the existing ExecutionController, not a robot driver."""
from dataclasses import asdict, dataclass
import threading
import time
import uuid

import numpy as np
from ai.execute.controller import UserApproval
from ai.execute.home import HomeReturn
from ai.execute.safety import SafetyError

TASK_STATES = ("IDLE", "PREPARING", "READY", "RUNNING_ACT", "STOPPING_ACT", "VERIFYING_STOP",
               "RETURNING_HOME", "COMPLETED", "ABORTED", "FAULT")
TERMINAL = ("COMPLETED", "ABORTED", "FAULT")


@dataclass(frozen=True)
class TaskApproval:
    execution: UserApproval
    task_id: str
    config_id: str
    allow_home: bool
    home_path_confirmed: bool
    path_evidence: str

    def require(self, task):
        self.execution.require(task.session_id, task.checkpoint_id, task.clock(), task.controller.is_mock)
        if self.task_id != task.config.task_id or self.config_id != task.config.identity:
            raise SafetyError("task_approval_contract_mismatch")
        if self.allow_home is not True or self.home_path_confirmed is not True or not self.path_evidence.strip():
            raise SafetyError("explicit_home_and_path_approval_required")


@dataclass(frozen=True)
class CompletionDecision:
    """Extension boundary for future validated completion detectors; user-only today."""
    session_id: str
    operator: str
    source: str = "user"
    note: str = ""


class TaskExecutor:
    def __init__(self, controller, config, *, scheduler_empty, home_path_check=None):
        self.controller, self.config, self.clock = controller, config, controller.clock
        self.scheduler_empty = scheduler_empty
        self.home_path_check = home_path_check or (lambda: False)
        self.state, self.task_run_id = "IDLE", uuid.uuid4().hex
        self.session_id = self.checkpoint_id = self.checkpoint = None
        self.started_ns = self.ended_ns = self.act_ended_ns = None
        self.started_system_ns = self.ended_system_ns = None
        self.failure_stage = self.reason = None
        self.approval = self.completion = None
        self.completion_received_ns = None
        self.arming = False
        self.act_authority_seen = False
        self.act_authority_released = False
        self.intent = None
        self.user_aborted = self.emergency_requested = False
        self.home_stable_since = None
        self.gripper_history = []
        self.gripper_history_truncated = False
        self.lock = threading.RLock()
        self.watchdog_exit = threading.Event()
        self.watchdog_thread = None
        self.home = HomeReturn(controller, config, self._log)

    def _log(self, event, **value):
        self.controller.log(event, task_id=self.config.task_id, task_run_id=self.task_run_id,
                            session_id=self.session_id, checkpoint_id=self.checkpoint_id, **value)
        if self.controller.logging_error: raise SafetyError("task_logging_failed")

    def _state(self, state, reason):
        old, self.state = self.state, state
        self._log("task_state", old_state=old, state=state, reason=reason)

    def _finish(self, state, reason):
        self.reason, self.ended_ns, self.ended_system_ns = reason, self.clock(), time.time_ns()
        self._state(state, reason)

    def prepare(self, session_id, checkpoint_id, checkpoint, health):
        with self.lock:
            if self.state != "IDLE": raise SafetyError("task_cannot_restart_create_new_session")
            self.session_id, self.checkpoint_id, self.checkpoint = session_id, checkpoint_id, checkpoint
            self.started_ns, self.started_system_ns = self.clock(), time.time_ns()
            self._state("PREPARING", "checking_project_home_and_observations")
            try:
                if not self.controller.is_mock: raise SafetyError("real_task_locked_until_project_home_and_readiness_are_verified")
                self.controller.connect(session_id, checkpoint_id, checkpoint, health)
                self._log("task_config", config=asdict(self.config), config_id=self.config.identity)
                self.poll()
            except Exception as exc:
                self.abort(f"prepare_failed: {exc}", fault=True)
                raise

    def start(self, approval):
        with self.lock:
            if self.state != "READY" or self.arming: raise SafetyError("task_not_ready")
            approval.require(self)
            if not self.home_path_check(): raise SafetyError("home_path_unconfirmed")
            self._require_home_for_start("idle")
            self.approval = approval
            self._log("task_approved", operator=approval.execution.operator, scope=approval.execution.scope,
                      includes_home=True, path_evidence=approval.path_evidence,
                      expires_monotonic_ns=approval.execution.expires_ns)
            try:
                self.controller.arm(approval.execution)
                self.arming = True
                self.poll()
            except Exception as exc:
                self.abort(f"arm_failed: {exc}", fault=True)
                raise

    def consume(self, record):
        with self.lock:
            if self.state != "RUNNING_ACT": raise SafetyError("task_not_accepting_act_actions")
            if self.clock() - self.controller.started_ns >= self.config.max_act_seconds * 1e9:
                self.abort("act_timeout")
                raise SafetyError("act_timeout")
            try:
                self.controller.consume(record)
                self._gripper_history()
            except Exception as exc:
                self.abort(f"act_action_failed: {exc}", fault=True)
                raise

    def complete(self, decision):
        with self.lock:
            if self.state != "RUNNING_ACT": raise SafetyError("completion_only_during_act")
            if decision.session_id != self.session_id or not decision.operator.strip() or decision.source not in ("user", "mock_test_user"):
                raise SafetyError("invalid_or_unsupported_completion_decision")
            if decision.source == "mock_test_user" and not self.controller.is_mock: raise SafetyError("mock_completion_on_real")
            if self.controller.submitted == 0: raise SafetyError("completion_without_actions")
            if self.clock() - self.controller.started_ns >= self.config.max_act_seconds * 1e9:
                self.abort("act_timeout")
                raise SafetyError("completion_after_act_timeout")
            try:
                self.controller.health.require(self.clock(), self.controller.config)
                self.completion, self.completion_received_ns = decision, self.clock()
                self._log("task_completion_decision", decision=asdict(decision), physical_success_verified=False)
                self.intent = "COMPLETED"
                self._stop_act("user_completed")
            except Exception as exc:
                self.abort(f"completion_failed: {exc}", fault=True)
                raise

    def _stop_act(self, reason, *, fault=False, emergency=False):
        self.act_ended_ns = self.clock()
        self.stop_started_ns = self.clock()
        try:
            self._state("STOPPING_ACT", reason)
        finally:
            # Even a failed Task log must not bypass Controller revocation/stop.
            try:
                if self.intent == "COMPLETED": self.controller.complete_by_user()
                elif emergency: self.controller.emergency_stop(reason)
                else: self.controller.stop(reason, fault=fault)
            finally:
                self._state("VERIFYING_STOP", reason)

    def abort(self, reason="user_abort", *, fault=False, emergency=False):
        with self.lock:
            if self.state in TERMINAL: return
            self.failure_stage = self.failure_stage or self.state
            self.reason = reason
            self.user_aborted |= reason in ("user_abort", "user_interrupt")
            self.emergency_requested |= emergency
            self.intent = "FAULT" if fault or emergency else "ABORTED"
            if self.state == "RETURNING_HOME":
                self.home.cancel(reason)
                return
            if self.state in ("STOPPING_ACT", "VERIFYING_STOP"):
                if fault or emergency: self.controller.stop(reason, fault=True, emergency=emergency)
                return
            self._stop_act(reason, fault=fault, emergency=emergency)

    def _gripper_history(self):
        entry = {"command_state": self.controller.gripper.state, "requests": self.controller.gripper.sent}
        if not self.gripper_history or any(self.gripper_history[-1][key] != value for key, value in entry.items()):
            self._log("task_gripper", **entry)
            if len(self.gripper_history) < 256: self.gripper_history.append({"monotonic_ns": self.clock(), **entry})
            else: self.gripper_history_truncated = True

    def _require_home_for_start(self, mode):
        feedback = self.controller.indy.feedback(self.clock())
        feedback.require(self.clock(), self.controller.config, mode)
        if (max(np.rad2deg(np.abs(feedback.joints - self.config.home_rad))) > self.config.home_tolerance_deg
                or feedback.in_motion is not False
                or np.any(np.abs(feedback.velocity) > self.controller.config.stopped_velocity_rad_s)):
            raise SafetyError("not_stopped_at_project_home_for_start")

    def poll(self):
        with self.lock:
            if self.state in TERMINAL or self.state == "IDLE": return
            try:
                self._poll()
            except Exception as exc:
                # Logging and configuration failures must still revoke output.
                self.failure_stage = self.failure_stage or self.state
                self.reason, self.intent = str(exc), "FAULT"
                if self.state == "RETURNING_HOME":
                    self.home.cancel(str(exc))
                elif self.controller.state == "STOPPED" and self.controller.stop_confirmed:
                    # A Task/Home precondition failure after a verified ACT stop
                    # must not rewrite the already recorded ACT outcome.
                    self._finish("FAULT", str(exc))
                else:
                    self.controller.fault(str(exc))
                    self.state = "VERIFYING_STOP"
                    self.stop_started_ns = getattr(self, "stop_started_ns", self.clock())
                if self.controller.logging_error:
                    # The sink is disabled by ExecutionController. Continue its
                    # stop confirmation/timeout without requiring another Task log.
                    self.controller.poll()
                    if self.controller.state in ("STOPPED", "FAULT") and self.home.state not in ("MOVING", "STOPPING"):
                        self.state, self.ended_ns, self.ended_system_ns = "FAULT", self.clock(), time.time_ns()

    def _poll(self):
        if self.controller.session_id != self.session_id or self.controller.checkpoint_id != self.checkpoint_id:
            raise SafetyError("task_execution_session_or_checkpoint_changed")
        self._gripper_history()
        if self.state == "RETURNING_HOME":
            if self.home.state == "MOVING":
                if self.controller.fault_latched or self.controller.emergency_requested:
                    raise SafetyError("act_fault_reported_during_home_transition")
                self.approval.require(self)
                if not self.home_path_check(): raise SafetyError("home_path_became_unconfirmed")
            self.home.poll()
            if self.home.state == "COMPLETED":
                self._finish("COMPLETED", "user_completed_act_and_verified_mock_home")
            elif self.home.state == "FAILED":
                self.failure_stage = "RETURNING_HOME"
                self._finish("FAULT" if self.intent != "ABORTED" else "ABORTED", self.home.reason)
            return
        self.controller.poll()
        now = self.clock()
        if self.controller.lease.stream is not None and not self.act_authority_seen:
            self.act_authority_seen = True
            self._log("task_authority", phase="act", event_kind="acquired", owner=self.controller.lease.owner)
        if self.state == "PREPARING":
            if now - self.started_ns > self.config.prepare_timeout_seconds * 1e9: raise SafetyError("task_prepare_timeout_not_stably_home")
            self.controller.health.require(now, self.controller.config)
            feedback = self.controller.indy.feedback(now)
            feedback.require(now, self.controller.config, "idle")
            error = np.rad2deg(np.abs(feedback.joints - self.config.home_rad))
            if feedback.joint_received_ns < self.started_ns or feedback.in_motion is not False or np.any(np.abs(feedback.velocity) > self.controller.config.stopped_velocity_rad_s) or max(error) > self.config.home_tolerance_deg:
                self.home_stable_since = None
                return
            if self.home_stable_since is None: self.home_stable_since = now
            if feedback.joint_received_ns - self.home_stable_since >= self.config.home_stable_ms * 1e6:
                self._state("READY", "project_home_stably_verified_waiting_user_approval")
        elif self.state == "READY":
            if self.controller.state == "FAULT": raise SafetyError("execution_fault_while_ready")
            if self.arming and self.controller.state == "ARMED":
                self.approval.require(self)
                self._require_home_for_start("joint_absolute")
                self.controller.begin()
                self._state("RUNNING_ACT", "explicit_task_start")
        elif self.state == "RUNNING_ACT":
            if self.controller.state != "RUNNING": raise SafetyError(f"act_execution_stopped: {self.controller.reason}")
            if now - self.controller.started_ns >= self.config.max_act_seconds * 1e9:
                self.abort("act_timeout")
        elif self.state == "VERIFYING_STOP":
            if self.controller.state not in ("STOPPED", "FAULT"):
                if now - self.stop_started_ns > (self.controller.config.stop_timeout_ms + 100) * 1e6:
                    self.controller.fault("task_stop_verification_timeout")
                return
            snapshot = self.controller.snapshot()
            if self.controller.state == "FAULT" or snapshot["safety_fault"] or snapshot["emergency_requested"]:
                self.failure_stage = self.failure_stage or "VERIFYING_STOP"
                self._finish("FAULT", self.reason or snapshot["state_reason"] or snapshot["reason"] or "act_fault")
                return
            if self.intent != "COMPLETED":
                self._finish("ABORTED", self.reason or "act_aborted")
                return
            if not snapshot["policy_normal_end"] or not snapshot["stop_confirmed"] or not self.controller.revoked.is_set():
                raise SafetyError("normal_act_stop_not_verified")
            if snapshot["scheduler_cancel_error"] or not self.scheduler_empty(): raise SafetyError("scheduler_not_confirmed_empty")
            if snapshot["lease_quarantined"] or self.controller.lease.stream is not None: raise SafetyError("act_authority_not_released")
            if snapshot["gripper_command_state"] != 0 or not snapshot["gripper_service_settled"]:
                self.failure_stage = "VERIFYING_STOP"
                self._finish("ABORTED", "release_command_not_confirmed_no_auto_home")
                return
            feedback = self.controller.indy.feedback(self.clock())
            feedback.require(self.clock(), self.controller.config, "idle")
            if feedback.joint_received_ns <= self.controller.stop_started_ns:
                if now - self.stop_started_ns > (self.controller.config.stop_timeout_ms + 100) * 1e6:
                    raise SafetyError("no_post_stop_joint_feedback")
                return
            if feedback.in_motion is not False or np.any(np.abs(feedback.velocity) > self.controller.config.stopped_velocity_rad_s):
                raise SafetyError("robot_moving_after_act_stop")
            self.approval.require(self)
            if not self.home_path_check(): raise SafetyError("home_path_unconfirmed")
            if self.act_authority_seen and not self.act_authority_released:
                self.act_authority_released = True
                self._log("task_authority", phase="act", event_kind="released_after_stop")
            self._state("RETURNING_HOME", "stopped_act_authority_released_and_home_preconditions_verified")
            self.home.start()

    def start_watchdog(self):
        if self.watchdog_thread: return
        self.controller.start_watchdog()
        def watch():
            while not self.watchdog_exit.wait(.01):
                if self.state in TERMINAL: return
                self.poll()
        self.watchdog_thread = threading.Thread(target=watch, name="act-task-watchdog", daemon=True)
        self.watchdog_thread.start()

    def close(self):
        self.watchdog_exit.set()
        if self.watchdog_thread: self.watchdog_thread.join(1)
        self.controller.close_watchdog()

    def snapshot(self):
        with self.lock:
            execution = self.controller.snapshot()
            act_start = execution["started_monotonic_ns"]
            return {"task_id": self.config.task_id, "task_run_id": self.task_run_id, "session_id": self.session_id,
                    "checkpoint_id": self.checkpoint_id, "checkpoint": self.checkpoint, "state": self.state,
                    "started_monotonic_ns": self.started_ns, "ended_monotonic_ns": self.ended_ns,
                    "started_system_time_ns": self.started_system_ns, "ended_system_time_ns": self.ended_system_ns,
                    "act_elapsed_seconds": ((self.act_ended_ns or self.clock()) - act_start) / 1e9 if act_start is not None else None,
                    "act_action_count": execution["submitted_actions"], "gripper_history": list(self.gripper_history),
                    "gripper_history_truncated": self.gripper_history_truncated,
                    "completion_decision": asdict(self.completion) if self.completion else None,
                    "completion_received_monotonic_ns": self.completion_received_ns,
                    "act_stop_reason": execution["reason"], "act_stop_confirmed": execution["stop_confirmed"],
                    "act_policy_success_user_reported": bool(self.completion and execution["policy_normal_end"] and not execution["safety_fault"]),
                    "home": self.home.snapshot(), "success": self.state == "COMPLETED",
                    "failure_stage": self.failure_stage, "reason": self.reason,
                    "user_aborted": self.user_aborted, "emergency_requested": self.emergency_requested,
                    "execution": execution, "physical_task_success_verified": False}


def render_task_summary(result):
    return (f"Task: {result['task_id']} / {result['task_run_id']}\n"
            f"Session: {result['session_id']}\nCheckpoint: {result['checkpoint_id']}\n"
            f"State: {result['state']}\nTask success: {result['success']}\n"
            f"ACT user-reported success: {result['act_policy_success_user_reported']}\n"
            f"ACT actions: {result['act_action_count']}\nACT stop confirmed: {result['act_stop_confirmed']}\n"
            f"Home: {result['home']['state']}\nHome error (deg): {result['home']['final_error_deg']}\n"
            f"Failure stage: {result['failure_stage']}\nReason: {result['reason']}\n"
            "Mock validation only; physical task success is not verified.\n")
