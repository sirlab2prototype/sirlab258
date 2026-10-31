"""Separate deterministic Home phase, using the SAME robot adapter and lease.

Only Mock Home is implemented: the current official service cannot express the
team target and speed/acceleration settings. Never open a second SDK connection.
"""
import threading
import numpy as np
from ai.execute.safety import SafetyError


class HomeReturn:
    def __init__(self, controller, config, log):
        self.controller, self.config, self.log = controller, config, log
        self.robot, self.lease, self.clock = controller.indy, controller.lease, controller.clock
        self.state = "IDLE"
        self.permit = threading.Event()
        self.permit.set()
        self.started_ns = self.ended_ns = self.stable_since = None
        self.future = self.stop_future = None
        self.error_deg = None
        self.reason = None
        self.stop_confirmed = False
        self.owner = None
        self.acquired = False

    def start(self):
        if self.state != "IDLE": raise SafetyError("home_already_started")
        if not self.robot.is_mock:
            raise SafetyError("real_project_home_locked_missing_target_speed_and_independent_readiness_interface")
        now = self.clock()
        feedback = self.robot.feedback(now)
        feedback.require(now, self.controller.config, "idle")
        if feedback.in_motion is not False or np.any(np.abs(feedback.velocity) > self.controller.config.stopped_velocity_rad_s):
            raise SafetyError("home_start_robot_not_stopped")
        target = self.config.home_rad
        if np.any(target < self.controller.config.joint_min_rad) or np.any(target > self.controller.config.joint_max_rad):
            raise SafetyError("home_target_joint_limit")
        self.owner = self.controller.session_id + ":home"
        self.lease.acquire(self.owner)
        self.acquired = True
        self.started_ns, self.state = now, "MOVING"
        self.permit.clear()
        try:
            self.log("task_authority", phase="home", event_kind="acquired", owner=self.lease.owner)
            self.future = self.robot.request_project_home(target, vel_ratio=self.config.home_vel_ratio,
                         acc_ratio=self.config.home_acc_ratio, permit=self.permit)
            self.log("task_home_requested", target_rad=target.tolist(), target_deg=list(self.config.home_joint_deg),
                     vel_ratio=self.config.home_vel_ratio, acc_ratio=self.config.home_acc_ratio,
                     source=self.config.home_source, real=False)
        except Exception as exc:
            self.cancel(f"home_command_failed: {exc}")

    def cancel(self, reason):
        self.permit.set()
        if self.state in ("COMPLETED", "FAILED"): return
        self.reason = reason
        self.state, self.stop_started_ns = "STOPPING", self.clock()
        if not self.acquired or self.lease.stream is None or self.lease.owner != self.owner:
            self.state, self.ended_ns = "FAILED", self.clock()
            return  # Never stop somebody else's owner on an acquisition failure.
        try:
            self.stop_future = self.robot.request_stop()
            self.log("task_home_stop_requested", reason=reason)
        except Exception as exc:
            self.lease.quarantine()
            self.state, self.ended_ns = "FAILED", self.clock()
            self.reason += f"; home_stop_send_failed: {exc}"

    def poll(self):
        if self.state not in ("MOVING", "STOPPING"): return
        now = self.clock()
        if self.state == "STOPPING":
            try:
                if now - self.stop_started_ns > self.controller.config.stop_timeout_ms * 1e6:
                    raise SafetyError("home_stop_unconfirmed")
                if self.stop_future is None or not self.stop_future.done(): return
                if self.stop_future.result().success is not True: raise SafetyError("home_stop_failed")
                feedback = self.robot.feedback(now)
                feedback.require(now, self.controller.config, "idle")
                if feedback.joint_received_ns <= self.stop_started_ns or feedback.in_motion is not False or np.any(
                        np.abs(feedback.velocity) > self.controller.config.stopped_velocity_rad_s): return
                self.stop_confirmed = True
                self.lease.release(confirmed_stopped=True)
                self.log("task_authority", phase="home", event_kind="released_after_stop")
                self.state, self.ended_ns = "FAILED", now
            except SafetyError as exc:
                if now - self.stop_started_ns <= self.controller.config.stop_timeout_ms * 1e6 and str(exc).startswith(("stale_", "controller_mode")):
                    return
                self.lease.quarantine()
                self.reason += f"; {exc}"
                self.state, self.ended_ns = "FAILED", now
            except Exception as exc:
                self.lease.quarantine()
                self.reason += f"; home_stop_error: {exc}"
                self.state, self.ended_ns = "FAILED", now
            return
        try:
            feedback = self.robot.feedback(now)
            feedback.require(now, self.controller.config)
            if feedback.mode not in ("idle", "project_home"): raise SafetyError("home_controller_mode_conflict")
            if self.future.done():
                if self.future.result().success is not True: raise SafetyError("home_command_rejected")
                # Completion ACK may update the cached Mock plant. Re-read actual feedback.
                feedback = self.robot.feedback(self.clock())
                now = self.clock()
                feedback.require(now, self.controller.config)
            self.error_deg = np.rad2deg(np.abs(feedback.joints - self.config.home_rad)).tolist()
            if now - self.started_ns > self.config.home_timeout_seconds * 1e9:
                raise SafetyError("home_arrival_error_timeout" if max(self.error_deg) > self.config.home_tolerance_deg else "home_timeout_not_stably_stopped")
            arrived = (self.future.done() and feedback.mode == "idle" and feedback.in_motion is False
                       and feedback.joint_received_ns >= self.started_ns
                       and max(self.error_deg) <= self.config.home_tolerance_deg
                       and np.all(np.abs(feedback.velocity) <= self.controller.config.stopped_velocity_rad_s))
            if not arrived:
                self.stable_since = None
                return
            # Never credit cached feedback time preceding the first observed
            # arrival. Fresh feedback must span the full window after this check.
            if self.stable_since is None: self.stable_since = now
            if feedback.joint_received_ns - self.stable_since < self.config.home_stable_ms * 1e6: return
            self.permit.set()
            self.stop_confirmed = True
            self.lease.release(confirmed_stopped=True)
            self.log("task_authority", phase="home", event_kind="released_after_arrival")
            self.state, self.ended_ns = "COMPLETED", now
            self.log("task_home_arrived", error_deg=self.error_deg, stable_ms=self.config.home_stable_ms)
        except Exception as exc:
            self.cancel(f"home_failed: {exc}")

    def snapshot(self):
        return {"state": self.state, "started_monotonic_ns": self.started_ns, "ended_monotonic_ns": self.ended_ns,
                "target_deg": list(self.config.home_joint_deg), "final_error_deg": self.error_deg,
                "stop_confirmed": self.stop_confirmed, "reason": self.reason,
                "real_enabled": False, "physical_arrival_verified": False}
