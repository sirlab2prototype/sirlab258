"""ROS is entirely fake here; no DDS, SDK, service or actuator is used."""
import json
import sys
import time
import types
from types import SimpleNamespace as Message

import numpy as np
import pytest

from ai import dataset_contract as contract
from ai.observation_timing import IntegrityError, SyncOptions
from ai.serve.observations import RosSource
from ai.serve.test.test_inference import timing, stamp


@pytest.fixture
def fake_ros(monkeypatch):
    modules = {name: types.ModuleType(name) for name in (
        "rclpy", "rclpy.node", "rclpy.qos", "sensor_msgs", "sensor_msgs.msg", "std_msgs", "std_msgs.msg")}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    calls, current = [], [100]
    class Node:
        def __init__(self, name):
            self.topics = []
        def create_subscription(self, kind, topic, callback, qos):
            self.topics.append(topic)
        def get_clock(self):
            return Message(clock_type="ROS_TIME", now=lambda: Message(nanoseconds=time.time_ns()))
        def get_parameter(self, name):
            return Message(value=False)
        def destroy_node(self):
            calls.append("destroy")
        def __getattr__(self, name):
            raise AssertionError(f"Forbidden ROS call: {name}")
    modules["rclpy.node"].Node = Node
    modules["rclpy.qos"].qos_profile_sensor_data = object()
    modules["sensor_msgs.msg"].Image = modules["sensor_msgs.msg"].JointState = object
    modules["std_msgs.msg"].Float32 = object
    modules["rclpy"].init = lambda: calls.append("fake_init")
    modules["rclpy"].shutdown = lambda: calls.append("fake_shutdown")
    modules["rclpy"].spin_once = lambda *a, **kw: time.sleep(.001)
    monkeypatch.setattr(RosSource, "timing", lambda self: timing(current[0]))
    return current, calls


def observations(source, current, receipt=140, header=120):
    current[0] = receipt
    for name in contract.CAMERA_NAMES:
        source.append(name, np.zeros(contract.IMAGE_SHAPE, dtype=np.uint8), vars(stamp(header)))
    source.append("state", np.zeros(6, dtype=np.float32), vars(stamp(header)))


@pytest.mark.parametrize("value", [0, 1])
def test_assumed_initial_is_not_ros_receipt_or_success(fake_ros, value):
    current, calls = fake_ros
    source = RosSource(operator_initial_gripper=value)
    try:
        observations(source, current)
        anchor = source.begin()
        assert anchor["sources"]["gripper"]["origin"] == "operator_assumed"
        observations(source, current, receipt=250, header=180)
        obs, metadata = source.sample(timing(190)["sample_monotonic_ns"])
        assert obs["observation.state"].shape == (7,)
        assert obs["observation.state"][6] == value
        origin = metadata["sources"]["gripper"]
        assert origin["origin"] == "operator_assumed"
        assert origin["sensor_measurement"] is False and origin["command_success_evidence"] is False
        assert origin["header_stamp"] is None and origin["callback_monotonic_ns"] is None
        assert "receipt_age_ns" not in origin and origin["assumption_age_ns"] == 150_000_000
        assert origin["receipt_sequence"] == 0
        assert source.history.gripper_carry is None and not source.history.items.get("gripper")
        diag = source.diagnostics()
        assert diag["topics"]["gripper"]["callbacks"] == 0
        assert diag["gripper_initialization"]["latest_available_origin"] == "operator_assumed"
        json.dumps(metadata, allow_nan=False)
    finally:
        source.close()
    assert calls == ["fake_init", "destroy", "fake_shutdown"]


def test_omitted_argument_still_blocks(fake_ros):
    current, _ = fake_ros
    source = RosSource()
    try:
        observations(source, current)
        with pytest.raises(IntegrityError, match="unknown_initial_gripper"):
            source.begin()
        assert source.diagnostics()["gripper_initialization"]["latest_available_origin"] == "unknown"
    finally:
        source.close()


def test_real_event_supersedes_assumption_only_at_causal_reference(fake_ros):
    current, _ = fake_ros
    source = RosSource(operator_initial_gripper=0)
    try:
        observations(source, current)
        source.begin()
        observations(source, current, receipt=250, header=180)
        source.ingest("gripper", Message(data=1.))  # Receipt=250, after reference=190.
        obs, meta = source.sample(timing(190)["sample_monotonic_ns"])
        assert obs["observation.state"][6] == 0 and meta["sources"]["gripper"]["origin"] == "operator_assumed"
        observations(source, current, receipt=350, header=280)
        obs, meta = source.sample(timing(290)["sample_monotonic_ns"])
        assert obs["observation.state"][6] == 1
        assert meta["sources"]["gripper"]["origin"] == "ros_message"
        assert meta["sources"]["gripper"]["callback_monotonic_ns"] == timing(250)["sample_monotonic_ns"]
        assert source.diagnostics()["topics"]["gripper"]["callbacks"] == 1
        # Retained real event outlives history pruning; fallback never returns.
        observations(source, current, receipt=1000, header=940)
        obs, meta = source.sample(timing(940)["sample_monotonic_ns"])
        assert obs["observation.state"][6] == 1 and meta["sources"]["gripper"]["origin"] == "ros_message"
    finally:
        source.close()


def test_real_event_before_begin_takes_precedence(fake_ros):
    current, _ = fake_ros
    source = RosSource(operator_initial_gripper=0)
    try:
        observations(source, current)
        source.ingest("gripper", Message(data=1.))
        source.begin()
        observations(source, current, receipt=250, header=180)
        obs, meta = source.sample(timing(190)["sample_monotonic_ns"])
        assert obs["observation.state"][6] == 1 and meta["sources"]["gripper"]["origin"] == "ros_message"
    finally:
        source.close()


@pytest.mark.parametrize("bad", [.5, float("nan"), float("inf")])
def test_bad_real_event_does_not_fall_back_to_assumption(fake_ros, bad):
    current, _ = fake_ros
    source = RosSource(operator_initial_gripper=0)
    try:
        observations(source, current)
        source.begin()
        source.ingest("gripper", Message(data=bad))
        with pytest.raises(IntegrityError, match="malformed_gripper"):
            source.sample(timing(140)["sample_monotonic_ns"])
    finally:
        source.close()


def test_no_sync_limits_relaxed(fake_ros):
    current, _ = fake_ros
    source = RosSource(operator_initial_gripper=0)
    try:
        assert source.options == SyncOptions()
        observations(source, current)
        source.begin()
        current[0] = 450
        with pytest.raises(IntegrityError, match="observation_too_old"):
            source.sample(timing(390)["sample_monotonic_ns"])
    finally:
        source.close()


@pytest.mark.parametrize("bad", [-1, 2, .5, True, "0"])
def test_invalid_assumption_rejected_before_any_ros_init(fake_ros, bad):
    _, calls = fake_ros
    with pytest.raises(ValueError):
        RosSource(operator_initial_gripper=bad)
    assert calls == []


@pytest.mark.parametrize("entry", ["launcher", "client"])
@pytest.mark.parametrize("execution", [False, True])
def test_flag_rejected_outside_ros_dry_run(tmp_path, entry, execution):
    if entry == "launcher":
        from ai.serve.infer import main
        args = ["--checkpoint", str(tmp_path)]
        kwargs = {"_mock_options": {"mock": True}} if execution else {}
    else:
        from ai.serve.dry_run import main
        args = ["--endpoint", "unused", "--metadata", "missing", "--output-dir", str(tmp_path/"unused")]
        kwargs = {"_mock_factory": lambda: None} if execution else {}
    with pytest.raises(SystemExit) as error:
        main([*args, "--source", "ros" if execution else "synthetic", "--dry-run",
              "--operator-initial-gripper", "0"], **kwargs)
    assert error.value.code == 2


def test_launcher_forwards_assumption_to_only_observation_client(tmp_path, monkeypatch):
    from ai.serve import infer
    checkpoint, output = tmp_path/"checkpoint", tmp_path/"run"
    checkpoint.mkdir()
    commands = []
    class Process:
        def __init__(self, command, **kwargs):
            commands.append(command)
            self.server = "ai.serve.zmq_act_server" in command
            self.returncode = None if self.server else 0
            if self.server:
                (output/"server_ready.json").write_text(json.dumps({"device":"fake", "checkpoint_id":"fake-id"}))
            else:
                (output/"client").mkdir()
                (output/"client/summary.json").write_text(json.dumps({"status":"complete", "robot_commands_sent":0}))
        def poll(self): return self.returncode
        def terminate(self): self.returncode = 0
        def wait(self, **kwargs): return self.returncode
    monkeypatch.setattr(infer.subprocess, "Popen", Process)
    assert infer.main(["--checkpoint", str(checkpoint), "--source", "ros", "--dry-run",
        "--operator-initial-gripper", "1", "--output-dir", str(output)]) == 0
    assert "--operator-initial-gripper" not in commands[0]
    assert commands[1][:2] == ["bash", "-c"]
    assert "--operator-initial-gripper 1" in commands[1][2]
    report = json.loads((output/"result.json").read_text())
    assert report["operator_initial_gripper"]["origin"] == "operator_assumed"


def test_full_client_loop_fake_ros_assumption_then_real_event(fake_ros, monkeypatch, tmp_path):
    from ai.serve import dry_run
    from ai.serve.observations import HistorySource, SyntheticSource
    from ai.serve.test.test_inference import InferenceService, FakeBackend, server_thread
    # Use real local clocks but entirely fake rclpy/Node. Sensor inputs are
    # synthetic, the RPC service uses FakeBackend, and no publisher exists.
    monkeypatch.setattr(RosSource, "timing", HistorySource.timing)
    class StreamingRos(RosSource):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.sensors = SyntheticSource(self.options)
            self.copied = {}
            self.started = time.monotonic()
            self.sent_gripper_event = False
        def update(self):
            self.sensors.update()
            for name in (*contract.CAMERA_NAMES, "state"):
                items = self.sensors.history.items.get(name)
                if items:
                    value, source = items[-1]
                    if source["receipt_sequence"] != self.copied.get(name):
                        self.append(name, value, source["header_stamp"])
                        self.copied[name] = source["receipt_sequence"]
            if not self.sent_gripper_event and time.monotonic()-self.started > .25:
                self.ingest("gripper", Message(data=1.))
                self.sent_gripper_event = True
    monkeypatch.setattr(dry_run, "RosSource", StreamingRos)
    monkeypatch.setattr(dry_run.signal, "signal", lambda *args: None)
    metadata = tmp_path/"metadata.json"
    metadata.write_text(json.dumps({"protocol":"indy7_act_rgb2_v2", "fps":20,
        "checkpoint_id":"model", "checkpoint":"fake", "chunk_size":12, "n_action_steps":4}))
    output = tmp_path/"client"
    with server_thread(InferenceService(FakeBackend(), "model")) as endpoint:
        assert dry_run.main(["--source","ros","--dry-run", "--operator-initial-gripper","0",
            "--endpoint",endpoint,"--metadata",str(metadata),"--output-dir",str(output),
            "--duration",".65","--startup-timeout",".3"]) == 0
    report = json.loads((output/"summary.json").read_text())
    assert report["robot_commands_sent"] == 0 and report["actions"] > 0
    assert report["reception_diagnostics"]["topics"]["gripper"]["callbacks"] == 1
    frames = [row["timing"]["sources"]["gripper"] for row in
        map(json.loads, (output/"events.jsonl").read_text().splitlines()) if row["event"] == "observation"]
    assert {source["origin"] for source in frames} == {"operator_assumed", "ros_message"}
    assert all(source["callback_monotonic_ns"] is None for source in frames if source["origin"] == "operator_assumed")
