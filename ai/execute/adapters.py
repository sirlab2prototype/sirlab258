"""Deterministic Mock devices. No ROS, serial, SDK, or hardware initialization."""
from types import SimpleNamespace
import numpy as np
from ai.execute.safety import RobotFeedback, SafetyError, PERIOD_NS


class MockFuture:
    def __init__(self, clock, delay_ns=0, result=None, effect=None, error=None):
        self.clock, self.due, self.value = clock, clock() + delay_ns, result
        self.effect, self.error, self.applied = effect, error, False
    def done(self): return self.clock() >= self.due
    def result(self):
        if not self.done(): raise RuntimeError("Future not complete")
        if self.error: raise self.error
        if not self.applied:
            if self.effect: self.effect()
            self.applied = True
        return self.value


class MockIndy7Adapter:
    is_mock = True
    def __init__(self, clock):
        self.clock = clock
        self.joints, self.velocity = np.zeros(6), np.zeros(6)
        self.mode, self.motion = "idle", False
        self.commands, self.mode_requests, self.stop_requests = [], 0, 0
        self.command_delay_ns = self.stop_delay_ns = 0
        self.send_error = self.stop_error = None
        self.freeze = self.stale = False
        self.exclusive = self.qp_inhibited = self.connected = self.servo_ready = True
        self.ready, self.fault, self.estop = True, False, False
        self.generation = 0
        self.last_feedback_ns = clock()
        self.permit = None
        self.home_commands = []
        self.home_delay_ns = 200_000_000
        self.home_error = None
        self.home_offset_rad = np.zeros(6)
        self.home_freeze = False

    def feedback(self, now_ns):
        if not self.stale and now_ns - self.last_feedback_ns >= PERIOD_NS:
            self.last_feedback_ns = now_ns
        return RobotFeedback(self.joints.copy(), self.velocity.copy(), self.last_feedback_ns,
                now_ns if self.connected else self.last_feedback_ns, self.connected, self.servo_ready, self.ready,
                self.mode, self.fault, self.estop, self.motion, self.exclusive, self.qp_inhibited, "mock_status_only",
                5 if self.mode == "idle" else 17, 10 if self.mode == "joint_absolute" else None)

    def activate(self, permit, config): self.permit = permit
    def request_absolute_mode(self):
        self.mode_requests += 1
        self.mode = "joint_absolute"
        return MockFuture(self.clock, result=SimpleNamespace(success=True))

    def send_absolute(self, joints_rad, *, expires_ns=None):
        if self.permit.is_set() or self.mode != "joint_absolute": raise SafetyError("adapter_not_authorized")
        if type(expires_ns) is not int or self.clock() > expires_ns: raise SafetyError("adapter_action_deadline_expired")
        if self.send_error: raise self.send_error
        target, generation = np.asarray(joints_rad).copy(), self.generation
        self.commands.append(target.tolist())
        def applied():
            if generation != self.generation: raise SafetyError("old_command_revoked")
            if not self.freeze:
                self.velocity = (target - self.joints) / .05
                self.joints = target
            self.motion = bool(np.any(np.abs(self.velocity) > 1e-9))
        return MockFuture(self.clock, self.command_delay_ns, SimpleNamespace(success=True, confirmation="mock_applied"), applied)

    def request_stop(self):
        self.stop_requests += 1
        self.generation += 1
        def stopped():
            self.mode, self.motion = "idle", False
            self.velocity[:] = 0
        return MockFuture(self.clock, self.stop_delay_ns, SimpleNamespace(success=True), stopped, self.stop_error)

    def request_project_home(self, target_rad, *, vel_ratio, acc_ratio, permit):
        """Mock movej-equivalent only; no physical dynamics or SDK connection."""
        if permit.is_set() or self.mode != "idle": raise SafetyError("mock_home_not_authorized_or_idle")
        if self.permit is None or not self.permit.is_set(): raise SafetyError("act_permission_not_revoked_before_home")
        target, generation = np.asarray(target_rad, dtype=float).copy(), self.generation
        self.home_commands.append({"target_rad": target.tolist(), "vel_ratio": vel_ratio, "acc_ratio": acc_ratio})
        self.mode, self.motion = "project_home", True
        def arrived():
            if permit.is_set() or generation != self.generation: raise SafetyError("old_home_command_revoked")
            if not self.home_freeze:
                self.joints, self.velocity = target + self.home_offset_rad, np.zeros(6)
                self.mode, self.motion = "idle", False
        return MockFuture(self.clock, self.home_delay_ns, SimpleNamespace(success=True), arrived, self.home_error)


class MockMark7Transport:
    is_mock = True
    def __init__(self, clock, initial_state=0):
        self.clock, self.state = clock, initial_state
        self.calls = []
        self.delay_ns, self.fail = 0, False
        self.on_confirm = lambda state: None
        self.permit = None
    def initial_state(self): return self.state
    def activate(self, permit, config): self.permit = permit
    def request(self, desired):
        if self.permit.is_set(): raise SafetyError("gripper_not_authorized")
        self.calls.append(desired)
        return MockFuture(self.clock, self.delay_ns, SimpleNamespace(success=not self.fail))
    def confirm_state(self, desired):
        self.state = desired
        self.on_confirm(desired)
