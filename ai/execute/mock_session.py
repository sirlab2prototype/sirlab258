"""Only Mock devices can be attached to the existing inference runner."""
from pathlib import Path
import time
import numpy as np

from ai.execute.adapters import MockIndy7Adapter, MockMark7Transport
from ai.execute.authority import CommandLease
from ai.execute.controller import ExecutionController, UserApproval
from ai.execute.safety import SafetyConfig, ObservationHealth, SafetyError
from ai.serve.observations import SyntheticSource


class MockPlantSource(SyntheticSource):
    def __init__(self, robot, gripper, options):
        self.robot = robot
        super().__init__(options)
        gripper.on_confirm = lambda value: self.append("gripper", float(value), None)

    def append(self, name, value, stamp, timing=None):
        if name == "state": value = self.robot.feedback(time.monotonic_ns()).joints.astype("float32")
        super().append(name, value, stamp, timing)


class MockSession:
    is_mock = True
    def __init__(self, source, controller, approval_text):
        self.source, self.controller, self.approval_text = source, controller, approval_text
        self.prepared = False

    def observe(self, metadata):
        self.controller.observe(ObservationHealth.from_timing(metadata, self.source.options))

    def prepare(self, session_id, model, cancel_scheduler):
        if self.prepared: return
        controller = self.controller
        controller.cancel_scheduler = cancel_scheduler
        controller.connect(session_id, model["checkpoint_id"], model["checkpoint"], controller.health)
        now = controller.clock()
        approval = UserApproval(session_id, model["checkpoint_id"], self.approval_text, "mock", now, now + 60_000_000_000)
        controller.arm(approval)
        controller.poll()
        controller.begin()
        controller.start_watchdog()
        self.prepared = True

    def poll(self, inference=False):
        if not self.prepared: return
        self.controller.heartbeat(inference=inference)
        self.controller.poll()
        if self.controller.state != "RUNNING": raise SafetyError(f"execution_stopped: {self.controller.reason}")

    def consume(self, record):
        self.controller.consume(record)

    def finish(self, reason, fault=False):
        self.controller.stop(reason, fault=fault)
        deadline = time.monotonic() + self.controller.config.stop_timeout_ms / 1000 + .1
        try:
            while self.controller.state == "STOPPING" and time.monotonic() < deadline:
                self.controller.poll()
                time.sleep(.001)
        finally:
            self.controller.close_watchdog()
        snapshot = self.controller.snapshot()
        self.controller.log("execution_end", **snapshot)
        return snapshot


def validate_mock_initial_joints(joints, config):
    value=np.asarray(joints,dtype=float)
    if value.shape!=(6,) or not np.isfinite(value).all(): raise SafetyError("mock_initial_joints_must_be_finite_6d_rad")
    if np.any(value<config.joint_min_rad) or np.any(value>config.joint_max_rad): raise SafetyError("mock_initial_joint_limit")
    return value.copy()


def create_mock_session(options, log, model, output, config_path, approval_text, mock_initial_joints=None):
    if not approval_text.strip(): raise SafetyError("explicit_mock_arm_approval_required")
    config = SafetyConfig.load(config_path)
    clock = time.monotonic_ns
    indy, mark = MockIndy7Adapter(clock), MockMark7Transport(clock, initial_state=0)
    if mock_initial_joints is not None:
        # Declared software initial condition, not a motion or hardware state setter.
        indy.joints=validate_mock_initial_joints(mock_initial_joints,config)
        log("mock_initial_condition", joints_rad=indy.joints.tolist(), physical_motion=False)
    source = MockPlantSource(indy, mark, options)
    controller = ExecutionController(indy, mark, CommandLease(Path(output) / "mock_command_lease.json"), config, log, clock=clock)
    return source, MockSession(source, controller, approval_text)
