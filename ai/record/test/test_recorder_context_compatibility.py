"""Real Recorder callbacks/aligner/Collection with an in-process fake ROS bus.

No rclpy.init/spin, SDK import, graph access, or physical devices. Only new
pytest directories hold synthetic Dataset/Receipt files.
"""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as M

import numpy as np
import pyarrow.parquet as pq
import pytest

from ai.record.test.test_indy7_act_record import recorder, EPOCH, MONO
from ai.record import gui_bridge, gui_presentation
from ai.record.collection import Collection

MS = 1_000_000
PROJECT = Path(__file__).parents[3]


def emitter():
    spec = importlib.util.spec_from_file_location("tested_context_emitter", PROJECT /
        "ros2_ws/src/capstone_teleop/capstone_teleop/action_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ActionContext(EPOCH-10*MS)


class CallbackRig:
    def __init__(self, module, root, monkeypatch, *, modern=True, fault=None):
        self.module, self.ms, self.events, self.messages = module, -10, [], []
        self.emitter = emitter()
        self.callbacks, self.qos = {}, {}
        rig = self
        class FakeNode:
            def __init__(self, name): self.name = name
            def create_subscription(self, kind, topic, callback, qos):
                rig.callbacks[topic], rig.qos[topic] = callback, qos
                return M()
            def get_clock(self):
                return M(now=lambda: M(nanoseconds=EPOCH+round(rig.ms*MS)), clock_type="ROS_TIME")
            def get_parameter(self, name): return M(value=False)
            def get_logger(self): return M(error=lambda *args: None)
        monkeypatch.setattr(sys.modules["rclpy.node"], "Node", FakeNode)
        # Re-load the engine against the fake Node before construction. This
        # exercises its real subscription setup and clock/receipt callbacks.
        image_shape = module.IMAGE_SHAPE
        module.__spec__.loader.exec_module(module)
        module.IMAGE_SHAPE = image_shape
        self.node = module.Indy7Observations("/a", "/b", "/joint_states", "/capstone/teleop_action", "/capstone/gripper_state")
        self.node.require_action_context = modern
        self.root, self.modern, self.fault = root, modern, fault
        self.ledger = Collection(root / "formal")
        self.ledger.prepare()
        def factory(outcome):
            return module.LeRobotDataset.create(repo_id=f"local/{outcome}", root=self.ledger.roots[outcome],
                robot_type="indy7", fps=20, features=module._features(), use_videos=True,
                image_writer_threads=0, vcodec="h264")
        self.session = module.EpisodeSession(self.ledger.pending_root, factory, 20, "synthetic Core-108 QP", collection=self.ledger)
        self.starter = module.PendingStart(module.SyncOptions())
        self.send(-5, "/joint_states", self.joint(-5, np.zeros(6)))
        for topic in ("/a", "/b"):
            self.send(-4, topic, self.image(-20))
        self.send(-3, "/capstone/gripper_state", M(data=0.))
        if modern and fault != "missing":
            self.context(-1, -.5)
        self.ms = 0
        with self.clock(monkeypatch):
            timing, history = self.node.history_snapshot()
            self.starter.request(timing, history)
        for topic, delay in (("/a", 30), ("/b", 35)):
            for i in range(22):
                generated = i*1000/30
                self.events.append((generated+delay, topic, self.image(generated)))
        for generated in range(0, 751, 50):
            self.events.append((generated+1, "/joint_states", self.joint(generated, np.full(6, generated/10000))))
            if modern and fault != "missing" and (fault != "silence" or generated < 200):
                value = self.emitter.envelope(EPOCH+generated*MS)
                if fault == "gap" and generated >= 200: value["sequence"] += 1
                if fault == "publisher" and generated >= 200: value["stream_id"] = "different-producer"
                if fault == "version": value["version"] = 2
                self.events.append((generated+2, "/capstone/action_context", M(data=json.dumps(value))))
        for generated, target in ((30, .02), (150, .04)):
            self.events.append((generated+1, "/capstone/teleop_action", self.joint(generated, np.full(6, target))))
        self.events.append((251, "/capstone/gripper_state", M(data=1.)))
        self.events.sort(key=lambda x: x[0])

    def joint(self, generated, values):
        stamp = EPOCH+round(generated*MS)
        return M(header=M(stamp=M(sec=stamp//10**9, nanosec=stamp%10**9)),
                 name=[f"joint{i}" for i in range(6)], position=list(values))

    def image(self, generated):
        msg = self.joint(generated, [])
        height,width,_ = self.module.IMAGE_SHAPE
        return M(header=msg.header, height=height, width=width, step=width*3, encoding="rgb8",
                 data=np.zeros((height,width,3), np.uint8).tobytes())

    @contextmanager
    def clock(self, monkeypatch):
        with monkeypatch.context() as patch:
            patch.setattr(self.module.time, "monotonic_ns", lambda: MONO+round(self.ms*MS))
            patch.setattr(self.module.time, "time_ns", lambda: EPOCH+round(self.ms*MS))
            yield

    def send(self, receipt, topic, message):
        self.ms = receipt
        # Initial callbacks also use real receipt logic with a scoped clock.
        with pytest.MonkeyPatch.context() as patch:
            with self.clock(patch): self.callbacks[topic](message)

    def context(self, generated, receipt):
        self.send(receipt, "/capstone/action_context", M(data=json.dumps(self.emitter.envelope(EPOCH+generated*MS))))

    def run(self, monkeypatch, correction=False):
        marked = False
        for tick in range(5, 671, 5):
            while self.events and self.events[0][0] <= tick:
                receipt, topic, message = self.events.pop(0)
                self.send(receipt, topic, message)
            self.ms = tick
            with self.clock(monkeypatch):
                timing, history = self.node.history_snapshot()
                if self.starter.active:
                    message = self.starter.poll(self.session, history, timing)
                    if message: self.messages.append(message)
                if correction and tick >= 200 and not marked and self.session.state == "RECORDING":
                    self.session.mark_correction(M(received_monotonic_ns=MONO+190*MS,
                        received_system_time_ns=EPOCH+190*MS, boundary_source="gui_button_received"))
                    marked = True
                if self.session.state == "RECORDING":
                    schedule = self.session.deadline.consume(timing["sample_monotonic_ns"])
                    if schedule: self.session.record_aligned(history, timing, schedule)

    def close(self): self.ledger.close()


@pytest.mark.parametrize("correction", [False, True])
def test_existing_core108_numbers_and_save_match_legacy_with_real_callbacks(recorder, tmp_path, monkeypatch, correction):
    arrays = []
    for modern in (False, True):
        rig = CallbackRig(recorder, tmp_path/str(modern), monkeypatch, modern=modern)
        try:
            if modern:
                qos = rig.qos["/capstone/action_context"]
                assert qos.depth == 32 and qos.durability == "transient_local"
                assert rig.node.require_home_complete is False
            rig.run(monkeypatch, correction)
            assert rig.session.frames == 12 and rig.session.integrity["valid"]
            with rig.clock(monkeypatch):
                timing, history = rig.node.history_snapshot()
                rig.session.finish_aligned(history, timing)
            kind = "correction" if correction else "success"
            message = rig.session.save(kind, rig.ledger.roots[kind])
            assert rig.session.path is None and not rig.session.save_failed, message
            files = list((rig.ledger.roots[kind]/"data").rglob("*.parquet"))
            values = [row for p in files for row in pq.read_table(p).to_pylist()]
            arrays.append(np.array([row["action"] for row in values]))
            assert arrays[-1].shape == (12,7)
            np.testing.assert_allclose(arrays[-1][0,:6], .02)
            np.testing.assert_allclose(arrays[-1][-1,:6], .04)
            assert arrays[-1][0,6] == 0 and arrays[-1][-1,6] == 1
            assert rig.ledger.completed() == (set() if correction else {108})
            receipt = next((rig.ledger.directory/"receipts").glob("*.json"))
            before = receipt.read_bytes()
            rig.ledger.close()
            resumed = Collection(rig.ledger.directory)
            resumed.close()
            assert receipt.read_bytes() == before
        finally: rig.close()
    np.testing.assert_array_equal(*arrays)


@pytest.mark.parametrize("fault,reason", [("missing","missing_action_context"),
    ("silence","stale_action_context"), ("gap","action_context_gap"),
    ("publisher","action_context_publisher_changed"), ("version","action_context_protocol_mismatch")])
def test_context_is_required_and_errors_reach_gui(recorder, tmp_path, monkeypatch, fault, reason):
    rig = CallbackRig(recorder, tmp_path, monkeypatch, fault=fault)
    try:
        rig.run(monkeypatch)
        if fault in ("missing", "version"):
            assert rig.session.path is None and rig.session.state == "READY"
            # Start failure is communicated to the existing frontend as a notice.
            assert reason in rig.messages[-1]
            notice, error = gui_presentation.message_notice(rig.messages[-1])
            assert error and "QP" in notice and "버전" in notice
        else:
            assert not rig.session.integrity["valid"]
            assert rig.session.integrity["violations"][-1]["reason"] == reason
            view = gui_bridge.snapshot(rig.session, rig.starter, rig.node)
            assert view["state"] == "INVALID" and "o" not in view["allowed"]
            assert "QP" in gui_presentation.operator_fields(view)["alert"]
    finally: rig.close()


def test_static_install_path_check_never_imports_ros_or_sdk(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("compatibility_check", PROJECT/"scripts/check_recorder_compatibility.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path/"clone"
    source = root/"ros2_ws/src/capstone_teleop/capstone_teleop"
    installed = root/"ros2_ws/install/capstone_teleop/lib/python3.12/site-packages/capstone_teleop"
    source.mkdir(parents=True); installed.mkdir(parents=True)
    for name in ("qp_teleop.py", "action_context.py"):
        (source/name).write_text("must not execute")
        (installed/name).write_text("must not execute")
    report = module.inspect(root)
    assert report["installed_source_match"] and "unknown" in report["running_qp_version"]
    (installed/"action_context.py").write_text("stale")
    assert module.inspect(root)["installed_source_match"] is False


def test_qp_neutral_hold_does_not_require_new_actions_or_qdes(recorder, tmp_path, monkeypatch):
    rig = CallbackRig(recorder, tmp_path, monkeypatch)
    rig.events = [e for e in rig.events if e[1] != "/capstone/teleop_action"]
    try:
        rig.run(monkeypatch)
        assert rig.session.integrity["valid"] and rig.session.frames == 12
        assert rig.session.aligner.context_guard.contract is None
        for p in rig.session.path.glob("*.npz"):
            with np.load(p) as frame:
                np.testing.assert_array_equal(frame["action"][:6], np.zeros(6))
        row = json.loads((rig.session.path/"00000011.timing.json").read_text())
        assert row["recorded_action"]["kind"] == "initial_hold"
        assert row["recorded_action"]["action_context"]["sample"] is None
    finally: rig.close()


@pytest.mark.parametrize("reason", ["unverified_home_action", "live_home_action_locked",
    "recording_started_during_home", "recording_ended_before_home_complete", "required_home_endpoint_missing"])
def test_home_errors_are_explained_without_generic_clock_error(reason):
    text = gui_presentation.fault_text(reason)
    assert "Home" in text and "관측·시간 검사" not in text
