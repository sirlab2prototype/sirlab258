"""Fake ROS/SDK only. Do not construct QPTeleop or initialize ROS."""
import importlib
import json
import sys
import time
import types

import numpy as np
import pytest

from capstone_teleop.action_context import ActionContext


def test_unverified_home_stays_visible_after_rearm():
    c = ActionContext(100)
    assert c.envelope(101)["phase"] == "QP_TELEOP"
    c.home_requested(110)
    c.transition("HOME_MOVING", 120, "rpc_returned")
    c.transition("HOME_UNVERIFIED", 130, "legacy_arrival")
    c.transition("QP_TELEOP", 140, "fresh_joint_after_arm")
    value = c.envelope(150)
    assert value["home_generation"] == 1
    assert value["last_unrecordable_home_ns"] == 110
    assert value["arrival_verified"] is False and value["sample"] is None


@pytest.fixture
def qp(monkeypatch):
    for name in ("rclpy", "rclpy.node", "rclpy.qos", "sensor_msgs", "sensor_msgs.msg",
                 "indy_interfaces", "indy_interfaces.srv", "std_srvs", "std_srvs.srv",
                 "std_msgs", "std_msgs.msg", "neuromeka"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["rclpy.node"].Node = object
    sys.modules["rclpy.qos"].QoSProfile = types.SimpleNamespace
    sys.modules["rclpy.qos"].ReliabilityPolicy = types.SimpleNamespace(RELIABLE="reliable")
    sys.modules["rclpy.qos"].DurabilityPolicy = types.SimpleNamespace(TRANSIENT_LOCAL="retained")
    for name in ("Joy", "JointState"):
        setattr(sys.modules["sensor_msgs.msg"], name, types.SimpleNamespace)
    for name in ("Float32", "Float64", "String"):
        setattr(sys.modules["std_msgs.msg"], name, types.SimpleNamespace)
    sys.modules["indy_interfaces.srv"].IndyService = types.SimpleNamespace(Request=types.SimpleNamespace)
    sys.modules["std_srvs.srv"].Trigger = types.SimpleNamespace(Request=types.SimpleNamespace)
    def forbidden(*args, **kwargs):
        raise AssertionError("test must never create SDK or ROS objects")
    sys.modules["neuromeka"].IndyDCP3 = forbidden
    monkeypatch.delitem(sys.modules, "capstone_teleop.qp_teleop", raising=False)
    module = importlib.import_module("capstone_teleop.qp_teleop")
    monkeypatch.setitem(sys.modules, "capstone_teleop.qp_teleop", module)
    node = module.QPTeleop.__new__(module.QPTeleop)
    now = [1_700_000_000_000_000_000]
    node.get_clock = lambda: types.SimpleNamespace(now=lambda: types.SimpleNamespace(nanoseconds=now[0]))
    node.get_logger = lambda: types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)
    node.capture_context = ActionContext(now[0])
    published = []
    node.context_pub = types.SimpleNamespace(publish=lambda msg: published.append(json.loads(msg.data)))
    node.armed = node.arming = False
    node.command_future = None
    node.capstone_home_joint_deg = [0, 40, -130, 90, 0, 90.5]
    node.capstone_home_vel_ratio, node.capstone_home_acc_ratio = 18., 100.
    commands = []
    class FakeSDK:
        def get_motion_data(self):
            commands.append("get_motion_data")
            return {"is_in_motion": False}
        def movej(self, **kwargs):
            commands.append(("movej", kwargs))
            return {"code": 0}
    node.indy = FakeSDK()
    future = types.SimpleNamespace(done=lambda: True, result=lambda: types.SimpleNamespace(success=True),
                                   add_done_callback=lambda callback: None)
    requests = []
    node.indy_service_client = types.SimpleNamespace(service_is_ready=lambda: True,
        call_async=lambda req: requests.append(req.data) or future)
    return module, node, commands, requests, published, future


def test_home_movej_path_unchanged_and_no_qdes_queries(qp):
    module, node, calls, requests, published, future = qp
    node.request_capstone_home()
    assert requests == [8] and calls == []
    assert published[-1]["phase"] == "HOME_REQUESTED"
    node.capstone_home_after_stop(future)
    assert calls == ["get_motion_data", ("movej", {"jtarget": node.capstone_home_joint_deg,
        "vel_ratio": 18., "acc_ratio": 100.})]
    assert node.home_rearm_pending and published[-1]["phase"] == "HOME_MOVING"
    # Existing arrival/rearm is retained; telemetry must not claim full proof.
    node.latest_joy = object()
    node.last_joy_time = node.last_joint_time = time.monotonic()
    node.joint_deg = np.array(node.capstone_home_joint_deg)
    node.xbox_is_neutral = lambda: True
    armed = []
    node.arm = lambda: armed.append(True)
    node.check_auto_arm()
    assert armed == [True] and published[-1]["phase"] == "HOME_UNVERIFIED"
    assert not published[-1]["arrival_verified"]


def test_home_sdk_failure_is_telemetry_failure_not_new_motion(qp):
    _, node, calls, requests, published, future = qp
    node.request_capstone_home()
    def fail(**kwargs): raise RuntimeError("fake RPC failure")
    node.indy.movej = fail
    node.capstone_home_after_stop(future)
    assert published[-1]["phase"] == "HOME_FAILED"
    assert calls == ["get_motion_data"] and requests == [8]


def test_telemetry_failure_does_not_change_home_control(qp):
    _, node, calls, requests, _, future = qp
    def failed_publish(msg): raise OSError("fake publisher failure")
    node.context_pub.publish = failed_publish
    node.request_capstone_home()
    node.capstone_home_after_stop(future)
    assert requests == [8] and calls[-1][0] == "movej"
