"""Inactive-by-default wiring to the UNMODIFIED official driver and Mark7.

No node/SDK construction, Servo ON, recovery, or Home command exists here.
A caller must supply a ROS node and an independent cached RobotFeedback
provider. The current driver does NOT publish all required readiness evidence.
"""
from concurrent.futures import Future
from dataclasses import dataclass
import time
from types import SimpleNamespace
import numpy as np

from ai.dataset_contract import JOINT_NAMES
from ai.execute.safety import SafetyError

TRAJECTORY_TOPIC = "/joint_trajectory_controller/joint_trajectory"
INDY_SERVICE = "/indy_srv"
# Official indy_define.py: service=6 selects TELE_JOINT_ABSOLUTE (SDK method=10).
ABSOLUTE_MODE_REQUEST, STOP_REQUEST = 6, 8


@dataclass(frozen=True)
class RealActivation:
    enable_hardware: bool = False
    operator: str = ""
    validation_record: str = ""
    expires_ns: int = 0

    def require(self, config, now_ns):
        if self.enable_hardware is not True or not self.operator or not self.validation_record or now_ns >= self.expires_ns:
            raise SafetyError("real_execution_not_explicitly_activated")
        if config.reviewed_for_real is not True or not config.review_record:
            raise SafetyError("hardware_limits_not_reviewed")


class RosIndy7Adapter:
    is_mock = False
    def __init__(self, node, feedback_provider, activation=None, *, clock=time.monotonic_ns, service_name=INDY_SERVICE, command_token=""):
        self.service_name, self.command_token = service_name, command_token
        self.node, self.provider, self.clock = node, feedback_provider, clock
        self.activation = activation or RealActivation()
        self.publisher = self.service = None
        self.permit = self.config = None
        self.last_dispatch_timing = None

    def feedback(self, now_ns):
        if self.provider is None: raise SafetyError("independent_controller_readiness_provider_missing")
        feedback = self.provider(now_ns)  # cached, nonblocking, externally verified; no SDK writer
        if feedback.mode == "idle" and feedback.controller_op_state != 5:
            raise SafetyError("actual_controller_idle_not_confirmed")
        if feedback.mode == "joint_absolute" and (feedback.controller_op_state != 17 or feedback.teleop_method != 10):
            raise SafetyError("actual_controller_absolute_mode_not_confirmed")
        return feedback

    def _graph(self):
        if any("qp_teleop" in name for name in self.node.get_node_names()):
            raise SafetyError("qp_node_present_auto_rearm_not_excluded")
        if self.publisher is not None:
            infos = self.node.get_publishers_info_by_topic(TRAJECTORY_TOPIC)
            own = (self.node.get_name(), self.node.get_namespace())
            if len(infos) != 1 or (infos[0].node_name, infos[0].node_namespace) != own:
                raise SafetyError("trajectory_publisher_ownership_not_confirmed")
        # ROS graph does not reveal direct SDK connections or queued action goals.
        # feedback.external_exclusive MUST independently cover those channels.

    def activate(self, permit, config):
        self.activation.require(config, self.clock())
        if permit.is_set(): raise SafetyError("execution_revoked")
        self.feedback(self.clock()).require(self.clock(), config, "idle")
        self._graph()
        from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
        from indy_interfaces.srv import IndyService
        self.trajectory_type, self.point_type, self.service_type = JointTrajectory, JointTrajectoryPoint, IndyService
        self.publisher = self.node.create_publisher(JointTrajectory, TRAJECTORY_TOPIC, 1)
        self.service = self.node.create_client(IndyService, self.service_name)
        self.permit, self.config = permit, config

    def request_absolute_mode(self):
        if self.permit is None or self.permit.is_set(): raise SafetyError("execution_revoked")
        self._graph()
        if not self.service.service_is_ready(): raise SafetyError("indy_service_unavailable")
        request = self.service_type.Request()
        request.data = ABSOLUTE_MODE_REQUEST
        return self.service.call_async(request)

    def send_absolute(self, joints_rad, *, expires_ns=None):
        timing = {"entered_ns": self.clock(), "expires_ns": expires_ns}
        self.last_dispatch_timing = timing
        try:
            return self._send_absolute(joints_rad, expires_ns, timing)
        finally:
            timing["finished_ns"] = self.clock()

    def _send_absolute(self, joints_rad, expires_ns, timing):
        if self.permit is None or self.permit.is_set(): raise SafetyError("execution_revoked")
        if type(expires_ns) is not int or self.clock() > expires_ns: raise SafetyError("adapter_action_deadline_expired")
        self.activation.require(self.config,self.clock())
        try:
            self.feedback(self.clock()).require(self.clock(), self.config, "joint_absolute")
        finally:
            owner = getattr(self.provider, "__self__", None)
            diagnostics = getattr(owner, "feedback_diagnostics", None)
            if diagnostics is not None:
                timing["provider"] = dict(getattr(diagnostics, "last", {}))
        timing["feedback_completed_ns"] = self.clock()
        self._graph()
        timing["graph_completed_ns"] = self.clock()
        array = np.asarray(joints_rad, dtype=float)
        if array.shape != (6,) or not np.isfinite(array).all(): raise SafetyError("invalid_absolute_rad_target")
        message = self.trajectory_type()
        message.joint_names = list(JOINT_NAMES)
        message.header.stamp = self.node.get_clock().now().to_msg()
        if self.command_token: message.header.frame_id = f"{self.command_token}:{expires_ns}"
        point = self.point_type()
        point.positions = array.tolist()  # official driver converts rad -> degree exactly once
        point.time_from_start.sec = 0
        point.time_from_start.nanosec = 50_000_000  # driver currently ignores this field
        message.points = [point]
        timing["message_completed_ns"] = self.clock()
        if self.permit.is_set(): raise SafetyError("execution_revoked")
        if self.clock() > expires_ns: raise SafetyError("adapter_action_deadline_expired")
        self.publisher.publish(message)
        timing["published_ns"] = self.clock()
        result = Future()
        result.set_result(SimpleNamespace(success=True, confirmation="ros_publish_only_not_sdk_ack"))
        return result

    def request_stop(self):
        # Stop is allowed after revocation, but only through a previously activated transport.
        if self.service is None or not self.service.service_is_ready(): raise SafetyError("stop_transport_unavailable")
        request = self.service_type.Request()
        request.data = STOP_REQUEST
        return self.service.call_async(request)


class RosMark7Transport:
    is_mock = False
    def __init__(self, node, initial_state_provider, authority_provider, activation=None, *, clock=time.monotonic_ns, service_namespace="/gripper"):
        self.service_namespace=service_namespace.rstrip("/")
        self.node, self.initial_provider, self.authority_provider = node, initial_state_provider, authority_provider
        self.activation, self.clock = activation or RealActivation(), clock
        self.clients = None
        self.permit = self.config = None
        self.state_publisher = None

    def initial_state(self): return self.initial_provider() if self.initial_provider else None

    def activate(self, permit, config):
        self.activation.require(config, self.clock())
        if permit.is_set(): raise SafetyError("execution_revoked")
        if self.authority_provider is None: raise SafetyError("gripper_authority_unconfirmed")
        self.authority_provider(self.clock()).require(self.clock(), config)
        if self.initial_state() not in (0, 1): raise SafetyError("unknown_initial_gripper")
        from std_srvs.srv import Trigger
        from std_msgs.msg import Float32
        self.request_type, self.state_type = Trigger.Request, Float32
        self.clients = {0:self.node.create_client(Trigger, self.service_namespace+"/open"),
                        1:self.node.create_client(Trigger, self.service_namespace+"/grasp")}
        # QP is inhibited/absent; ACT publishes command-state only after service success.
        self.state_publisher = self.node.create_publisher(Float32, "/capstone/gripper_state", 1)
        self.permit, self.config = permit, config

    def request(self, desired):
        if self.permit is None or self.permit.is_set(): raise SafetyError("execution_revoked")
        self.activation.require(self.config,self.clock())
        self.authority_provider(self.clock()).require(self.clock(), self.config, "joint_absolute")
        if not self.clients[desired].service_is_ready(): raise SafetyError("mark7_service_unavailable")
        return self.clients[desired].call_async(self.request_type())

    def confirm_state(self, desired):
        message = self.state_type()
        message.data = float(desired)
        self.state_publisher.publish(message)  # logical command state, never physical grasp proof
