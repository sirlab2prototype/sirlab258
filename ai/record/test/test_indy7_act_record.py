"""Offline recorder tests: ROS imports are stubbed, never initialized or spun."""

import importlib.util
import io
import json
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def recorder(monkeypatch):
    for name in ("rclpy", "rclpy.node", "rclpy.qos", "sensor_msgs", "sensor_msgs.msg", "std_msgs", "std_msgs.msg"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["rclpy.node"].Node = object
    sys.modules["rclpy.qos"].QoSProfile = types.SimpleNamespace
    sys.modules["rclpy.qos"].ReliabilityPolicy = types.SimpleNamespace(RELIABLE="reliable")
    sys.modules["rclpy.qos"].DurabilityPolicy = types.SimpleNamespace(TRANSIENT_LOCAL="transient_local")
    sys.modules["sensor_msgs.msg"].Image = object
    sys.modules["sensor_msgs.msg"].JointState = object
    sys.modules["std_msgs.msg"].Float32 = object
    spec = importlib.util.spec_from_file_location("tested_act_recorder", Path(__file__).parents[1] / "indy7_act_record.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "IMAGE_SHAPE", (32, 32, 3))
    return module


def frame(module, value=1):
    return {
        **{key: np.full(module.IMAGE_SHAPE, value, dtype=np.uint8)
           for key in module._features() if ".images." in key},
        "observation.state": np.full(7, value, dtype=np.float32),
        "action": np.full(7, value + 1, dtype=np.float32),
        "task": "synthetic basket demonstration",
    }


class FakeDataset:
    def __init__(self, saved, fail=False):
        self.saved = saved
        self.frames = []
        self.fail = fail
        self.meta = types.SimpleNamespace(total_episodes=len(saved))

    def add_frame(self, value):
        self.frames.append(value)

    def save_episode(self, **kwargs):
        if self.fail:
            self.frames.clear()  # Simulate a destructive partial save.
            raise OSError("injected save failure")
        self.saved.append(self.frames)
        self.meta.total_episodes += 1

    def finalize(self):
        pass


@pytest.fixture
def session(recorder, tmp_path):
    saved = {"success": [], "correction": []}
    opens = []

    def factory(outcome):
        opens.append(outcome)
        return FakeDataset(saved[outcome])

    instance = recorder.EpisodeSession(tmp_path / "pending", factory, 20, "basket")
    return instance, saved, opens


def take(module, instance, value=1, correction=False):
    instance.start(np.zeros(6, dtype=np.float32), 10.0)
    if correction:
        instance.mark_correction()
    instance.add_frame(frame(module, value))
    instance.add_frame(frame(module, value))
    instance.stop()


def test_keymap(recorder):
    assert [recorder._normalize_command(key) for key in "srox dq".replace(" ", "")] == [
        "start", "stop", "success", "correction", "discard", "quit",
    ]
    for obsolete in ("a", "e", "retry", "save"):
        assert recorder._normalize_command(obsolete) is None


def test_routes_success_correction_and_discard(recorder, session, tmp_path):
    instance, saved, opens = session
    take(recorder, instance, 11)
    assert opens == []  # No Dataset is even opened before classification.
    instance.save("success", tmp_path / "success")
    take(recorder, instance, 22, correction=True)
    instance.save("correction", tmp_path / "correction")
    take(recorder, instance, 33)
    instance.discard()
    assert instance.state == "READY"
    assert opens == ["success", "correction"]
    assert len(saved["success"]) == len(saved["correction"]) == 1
    assert saved["success"][0][0]["observation.state"][0] == 11
    assert saved["correction"][0][0]["observation.state"][0] == 22


@pytest.mark.parametrize("state", ["READY", "RECORDING", "REVIEW"])
def test_invalid_save_does_not_open_dataset(recorder, session, tmp_path, state):
    instance, _, opens = session
    if state != "READY":
        instance.start(np.zeros(6), 10)
    if state == "REVIEW":
        instance.add_frame(frame(recorder))  # One frame is too short.
        instance.stop()
    original = instance.path
    for outcome in ("success", "correction"):
        assert "거부" in instance.save(outcome, tmp_path / outcome)
    assert instance.path == original
    assert opens == []


@pytest.mark.parametrize("review", [False, True])
def test_repeat_start_and_review_updates_cannot_mix_frames(recorder, session, review):
    instance, _, _ = session
    instance.start(np.zeros(6), 10)
    incoming = frame(recorder, 5)
    instance.add_frame(incoming)
    path = instance.path
    original = (path / "00000000.npz").read_bytes()
    if review:
        instance.stop()
    assert "거부" in instance.start(np.ones(6), 20)
    incoming["action"][:] = 99
    if review:
        instance.add_frame(frame(recorder, 99))
    assert instance.frames == 1
    assert instance.path == path
    assert (path / "00000000.npz").read_bytes() == original


def test_action_boundary_and_hold(recorder, session):
    instance, _, _ = session
    q0 = np.arange(6, dtype=np.float32)
    instance.start(q0, 10)
    assert np.array_equal(instance.target_action((np.ones(6) * 99, 9)), q0)
    assert np.array_equal(instance.target_action(None), q0)
    target = np.ones(6, dtype=np.float32) * 3
    assert np.array_equal(instance.target_action((target, 11)), target)
    assert np.array_equal(instance.target_action(None), target)
    instance.discard()
    instance.start(q0, 20)
    assert np.array_equal(instance.target_action((target, 11)), q0)


def test_quit_never_discards_pending_and_input_survives_q(recorder, session, monkeypatch):
    instance, _, _ = session
    assert instance.request_quit()[0]
    take(recorder, instance)
    path = instance.path
    assert not instance.request_quit()[0]
    assert path.exists()
    instance.discard()
    assert instance.request_quit()[0]
    monkeypatch.setattr(recorder.sys, "stdin", io.StringIO("q\nd\nq\n"))
    commands = recorder._commands()
    assert [commands.get(timeout=2) for _ in range(4)] == ["q", "d", "q", "__eof__"]


def test_recording_quit_freezes_without_deleting(recorder, session):
    instance, _, _ = session
    instance.start(np.zeros(6), 10)
    instance.add_frame(frame(recorder))
    assert not instance.request_quit()[0]
    assert instance.state == "REVIEW"
    assert (instance.path / "00000000.npz").is_file()


def test_save_failure_preserves_raw_take_and_blocks_restart(recorder, session, tmp_path):
    instance, saved, _ = session
    take(recorder, instance)
    raw = (instance.path / "00000000.npz").read_bytes()
    instance.dataset_factory = lambda outcome: FakeDataset(saved[outcome], fail=True)
    target = tmp_path / "success"
    assert "SAVE FAILED" in instance.save("success", target)
    assert instance.state == "REVIEW"
    assert (instance.path / "00000000.npz").read_bytes() == raw
    assert "차단" in instance.save("correction", tmp_path / "correction").lower()
    assert "차단" in instance.discard().lower()
    assert instance.request_quit()[0]  # Explicitly reports retained recovery path.
    with pytest.raises(ValueError, match="partial save"):
        recorder._check_pending(instance.pending_root, {str(target)})


def test_pending_write_failure_freezes_take(recorder, session, monkeypatch):
    instance, _, _ = session
    instance.start(np.zeros(6), 10)
    instance.add_frame(frame(recorder))
    monkeypatch.setattr(recorder.np, "savez", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        instance.add_frame(frame(recorder))
    assert instance.state == "REVIEW"
    assert instance.frames == 1
    assert (instance.path / "00000000.npz").exists()


def test_buffer_does_not_accumulate_rgb_in_memory(recorder, session):
    instance, _, _ = session
    instance.start(np.zeros(6), 10)
    for _ in range(100):
        instance.add_frame(frame(recorder))
    assert instance.frames == 100
    assert len(list(instance.path.glob("*.npz"))) == 100
    assert not any(isinstance(value, list) for value in vars(instance).values())


def test_real_lerobot_public_replay_and_resume(recorder, tmp_path):
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    import av

    roots = {outcome: tmp_path / outcome for outcome in ("success", "correction")}

    def factory(outcome):
        root = roots[outcome]
        if root.exists():
            recorder._validate_destination(root, 20, True)
            return LeRobotDataset(repo_id=f"local/{outcome}", root=root, vcodec="h264")
        return LeRobotDataset.create(
            repo_id=f"local/{outcome}", root=root, robot_type="indy7", fps=20,
            features=recorder._features(), use_videos=True, image_writer_threads=0, vcodec="h264",
        )

    instance = recorder.EpisodeSession(tmp_path / "pending", factory, 20, "basket")
    take(recorder, instance, 11)
    assert "저장 완료" in instance.save("success", roots["success"])
    old_videos = {path: path.read_bytes() for path in roots["success"].rglob("*.mp4")}
    take(recorder, instance, 22, correction=True)
    assert "저장 완료" in instance.save("correction", roots["correction"])
    take(recorder, instance, 33)
    assert "저장 완료" in instance.save("success", roots["success"])
    take(recorder, instance, 44)
    instance.discard()
    assert all(path.read_bytes() == data for path, data in old_videos.items())
    for outcome, values in (("success", [11, 11, 33, 33]), ("correction", [22, 22])):
        dataset = LeRobotDataset(repo_id=f"local/{outcome}", root=roots[outcome])
        rows = dataset.hf_dataset.with_format("numpy")[:]
        assert rows["observation.state"][:, 0].tolist() == values
        assert dataset.meta.total_episodes == len(values) // 2
        for index in range(dataset.meta.total_episodes):
            sidecar = roots[outcome] / "meta" / "recorder_timing" / f"episode-{index:06d}"
            meta = json.loads((sidecar / "episode.json").read_text())
            timing_rows = [json.loads(line) for line in (sidecar / "frames.jsonl").read_text().splitlines()]
            assert meta["dataset_episode_index"] == index
            assert meta["dataset_root"] == str(roots[outcome])
            assert meta["dataset_repo_id"] == f"local/{outcome}"
            assert meta["outcome"] == outcome
            assert meta["timing_summary"]["frames"] == 2
            assert [row["frame_index"] for row in timing_rows] == [0, 1]
            assert [row["dataset_nominal_timestamp_s"] for row in timing_rows] == [0, .05]
        for video in roots[outcome].rglob("*.mp4"):
            with av.open(str(video)) as stream:
                decoded = list(stream.decode(video=0))
            assert len(decoded) == 2
        dataset.finalize()
        with pytest.raises(ValueError, match="FPS/robot"):
            recorder._validate_destination(roots[outcome], 30, True)
    assert json.loads((roots["success"] / "meta/info.json").read_text())["features"] == json.loads(
        (roots["correction"] / "meta/info.json").read_text()
    )["features"]
    assert list((tmp_path / "pending").iterdir()) == []


def test_real_partial_video_failure_keeps_pending_and_previous_videos(recorder, tmp_path, monkeypatch):
    import lerobot.datasets.lerobot_dataset as dataset_module

    root = tmp_path / "success"

    def factory(outcome):
        if root.exists():
            return dataset_module.LeRobotDataset(repo_id="local/success", root=root, vcodec="h264")
        return dataset_module.LeRobotDataset.create(
            repo_id="local/success", root=root, robot_type="indy7", fps=20,
            features=recorder._features(), use_videos=True, image_writer_threads=0, vcodec="h264",
        )

    instance = recorder.EpisodeSession(tmp_path / "pending", factory, 20, "basket")
    take(recorder, instance, 11)
    assert "저장 완료" in instance.save("success", root)
    old_videos = {path: path.read_bytes() for path in root.rglob("*.mp4")}
    encode = dataset_module._encode_video_worker

    def fail_second_camera(video_key, *args, **kwargs):
        if "844212071939" in video_key:
            raise OSError("injected second-camera encoding failure")
        return encode(video_key, *args, **kwargs)

    monkeypatch.setattr(dataset_module, "_encode_video_worker", fail_second_camera)
    take(recorder, instance, 55)
    assert "SAVE FAILED" in instance.save("success", root)
    assert instance.save_failed
    assert all(path.read_bytes() == data for path, data in old_videos.items())
    for index in range(2):
        with np.load(instance.path / f"{index:08d}.npz", allow_pickle=False) as pending:
            assert pending["observation.images.camera_317222074298"].mean() == 55
            assert pending["observation.images.camera_844212071939"].mean() == 55
    assert json.loads((root / "meta/info.json").read_text())["total_episodes"] == 1
    with pytest.raises(ValueError, match="partial save"):
        recorder._check_pending(instance.pending_root, {str(root)})


def fake_observations(module):
    node = module.Indy7Observations.__new__(module.Indy7Observations)
    node.lock = threading.Lock()
    node.images = {"camera_317222074298": None, "camera_844212071939": None}
    node.state = node.action = node.gripper = None
    node.source_timing = {}
    clock = types.SimpleNamespace(clock_type="ROS_TIME", now=lambda: types.SimpleNamespace(nanoseconds=123456789000))
    node.get_clock = lambda: clock
    node.get_parameter = lambda name: types.SimpleNamespace(value=False)
    return node


def fake_joint(stamp_sec, value=1):
    return types.SimpleNamespace(
        name=[f"joint{i}" for i in range(6)], position=[float(value)] * 6,
        header=types.SimpleNamespace(stamp=types.SimpleNamespace(sec=stamp_sec, nanosec=345)))


def fake_image(module, sec):
    h, w, _ = module.IMAGE_SHAPE
    return types.SimpleNamespace(
        encoding="rgb8", width=w, height=h, step=w * 3,
        data=np.zeros(module.IMAGE_SHAPE, dtype=np.uint8).tobytes(),
        header=types.SimpleNamespace(stamp=types.SimpleNamespace(sec=sec, nanosec=678)))


@pytest.mark.parametrize("fps", [20, 30])
def test_fixed_deadlines_no_cumulative_drift(recorder, fps):
    start = 987654321012345
    scheduler = recorder.FixedDeadline(fps, start)
    for slot in range(1, fps * 300 + 1):
        deadline = start + (slot * 1_000_000_000 + fps - 1) // fps
        assert scheduler.consume(deadline - 1) is None
        tick = scheduler.consume(deadline)
        assert tick["slot"] == slot
        assert tick["missed_deadlines"] == tick["lateness_ns"] == 0
        assert scheduler.consume(deadline) is None
    assert scheduler.missed == 0
    assert scheduler.next_ns == start + ((fps * 300 + 1) * 1_000_000_000 + fps - 1) // fps


def test_deadline_delay_skips_without_catchup_burst(recorder):
    scheduler = recorder.FixedDeadline(20, 0)
    assert scheduler.consume(50_000_000)["slot"] == 1
    delayed = scheduler.consume(240_000_000)
    assert delayed["slot"] == 4
    assert delayed["missed_deadlines"] == 2
    assert delayed["lateness_ns"] == 40_000_000
    assert scheduler.next_ns == 250_000_000
    assert scheduler.consume(240_000_000) is None
    assert scheduler.consume(249_999_999) is None
    assert scheduler.consume(250_000_000)["missed_deadlines"] == 0
    assert scheduler.consume(352_000_000)["missed_deadlines_total"] == 3


def test_source_headers_receipts_preserved_without_clock_conversion(recorder, monkeypatch):
    node = fake_observations(recorder)
    mono = [1_000_000_000]
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: mono[0])
    node._image("camera_317222074298", fake_image(recorder, 7))
    mono[0] += 1_000_000
    node._image("camera_844212071939", fake_image(recorder, 8000))
    mono[0] += 1_000_000
    node._state(fake_joint(0))
    mono[0] += 1_000_000
    node._action(fake_joint(-5))
    mono[0] += 1_000_000
    node._gripper(types.SimpleNamespace(data=1.0))
    mono[0] += 1_000_000
    _, _, _, _, timing = node.snapshot_with_timing()
    sources = timing["sources"]
    assert sources["camera_317222074298"]["header_stamp"] == {"sec": 7, "nanosec": 678}
    assert sources["camera_844212071939"]["header_stamp"] == {"sec": 8000, "nanosec": 678}
    assert sources["state"]["header_stamp"] == {"sec": 0, "nanosec": 345}
    assert sources["action"]["header_stamp"] == {"sec": -5, "nanosec": 345}
    assert sources["gripper"]["header_stamp"] is None
    assert sources["camera_317222074298"]["callback_monotonic_ns"] == 1_000_000_000
    assert timing["sample_monotonic_ns"] == 1_005_000_000
    assert sources["action"]["receiver_clock"]["ros_time_ns"] == 123456789000
    assert sources["action"]["header_clock_relation"] == "unverified"


def test_action_header_regression_is_diagnostic_not_rejection(recorder, session, monkeypatch):
    node = fake_observations(recorder)
    mono = [11_000_000_000]
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: mono[0])
    node._action(fake_joint(100, 1))
    mono[0] += 50_000_000
    node._action(fake_joint(99, 2))
    info = node.source_timing["action"]
    assert info["header_decreased_since_previous_callback"]
    assert info["header_decreases_total"] == 1
    instance, _, _ = session
    instance.start(np.zeros(6), 10)
    assert np.array_equal(instance.target_action(node.action, info), np.full(6, 2))
    instance.add_frame(frame(recorder), node.snapshot_with_timing()[-1])
    row = json.loads((instance.path / "00000000.timing.json").read_text())
    assert row["sources"]["action"]["header_decreases_total"] == 1
    assert row["recorded_action"]["source"]["header_stamp"]["sec"] == 99


def test_duplicate_camera_cache_and_same_header_are_distinguishable(recorder, session, monkeypatch):
    node = fake_observations(recorder)
    mono = [10_010_000_000]
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: mono[0])
    node._image("camera_317222074298", fake_image(recorder, 70))
    instance, _, _ = session
    instance.start(np.zeros(6), 10)
    mono[0] = 10_050_000_000
    instance.add_frame(frame(recorder), node.snapshot_with_timing()[-1])
    mono[0] = 10_100_000_000
    instance.add_frame(frame(recorder), node.snapshot_with_timing()[-1])
    duplicate = json.loads((instance.path / "00000001.timing.json").read_text())["sources"]["camera_317222074298"]
    assert duplicate["reused_callback_sample"] and duplicate["same_header_as_previous_frame"]
    assert duplicate["receipt_age_ns"] == 90_000_000
    mono[0] = 10_150_000_000
    node._image("camera_317222074298", fake_image(recorder, 70))
    instance.add_frame(frame(recorder), node.snapshot_with_timing()[-1])
    repeated_stamp = json.loads((instance.path / "00000002.timing.json").read_text())["sources"]["camera_317222074298"]
    assert not repeated_stamp["reused_callback_sample"]
    assert repeated_stamp["same_header_as_previous_frame"]


def test_actual_nominal_time_difference_and_review_freeze(recorder, session):
    instance, _, _ = session
    instance.start(np.zeros(6), 0)
    for ns in (50_000_000, 120_000_000):
        tick = instance.deadline.consume(ns)
        instance.add_frame(frame(recorder), {"sample_monotonic_ns": ns, "sources": {}}, tick)
    path = instance.path
    rows = [json.loads((path / f"{i:08d}.timing.json").read_text()) for i in range(2)]
    assert rows[0]["episode_elapsed_ns"] == 50_000_000
    assert rows[0]["actual_interval_ns"] is None
    assert rows[1]["actual_interval_ns"] == 70_000_000
    assert rows[1]["dataset_nominal_timestamp_s"] == .05
    assert rows[1]["elapsed_minus_nominal_s"] == pytest.approx(.07)
    assert rows[1]["sample_lateness_ns"] == 20_000_000
    instance.stop(now_ns=150_000_000)
    original = {p.name: p.read_bytes() for p in path.iterdir()}
    instance.add_frame(frame(recorder), {"sample_monotonic_ns": 999_000_000})
    assert {p.name: p.read_bytes() for p in path.iterdir()} == original
    assert instance.timing_summary()["actual_sample_fps"] == pytest.approx(1 / .07)
    assert instance.timing_summary()["unprocessed_deadlines_at_stop"] == 1


def test_timing_publish_failure_preserves_pending_and_blocks_retry(recorder, session, tmp_path, monkeypatch):
    instance, saved, _ = session
    take(recorder, instance)
    path = instance.path
    monkeypatch.setattr(instance, "_publish_timing", lambda *args: (_ for _ in ()).throw(OSError("timing disk full")))
    assert "SAVE FAILED" in instance.save("success", tmp_path / "success")
    assert len(saved["success"]) == 1  # Dataset commit happened, do not save it again.
    assert (path / "00000000.timing.json").exists()
    assert "차단" in instance.save("success", tmp_path / "success").lower()
    with pytest.raises(ValueError, match="partial save"):
        recorder._check_pending(instance.pending_root, {str(tmp_path / "success")})


def test_real_dataset_keeps_nominal_grid_and_sidecar_actual_time(recorder, tmp_path):
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    root = tmp_path / "success"

    def factory(outcome):
        return LeRobotDataset.create(
            repo_id="local/actual_time", root=root, robot_type="indy7", fps=20,
            features=recorder._features(), use_videos=True, image_writer_threads=0, vcodec="h264")

    instance = recorder.EpisodeSession(tmp_path / "pending", factory, 20, "basket")
    instance.start(np.zeros(6), 0)
    for ns in (50_000_000, 120_000_000):
        instance.add_frame(frame(recorder), {"sample_monotonic_ns": ns, "sources": {}}, instance.deadline.consume(ns))
    instance.stop(now_ns=200_000_000)
    assert "저장 완료" in instance.save("success", root)
    loaded = LeRobotDataset(repo_id="local/actual_time", root=root)
    assert loaded.hf_dataset.with_format("numpy")[:]["timestamp"].reshape(-1).tolist() == pytest.approx([0, .05])
    rows = [json.loads(line) for line in (root / "meta/recorder_timing/episode-000000/frames.jsonl").read_text().splitlines()]
    assert [row["episode_elapsed_ns"] for row in rows] == [50_000_000, 120_000_000]
    assert rows[1]["actual_interval_ns"] == 70_000_000
    assert rows[1]["schedule"]["missed_deadlines"] == 0
    loaded.finalize()


EPOCH = 1_700_000_000_000_000_000
MONO = 10_000_000_000


class HistoryHarness:
    """No ROS, no real Dataset. Millisecond offsets represent one Unix epoch."""
    def __init__(self, module, root, options=None, grip=True, state_age=5):
        self.m = module
        self.options = options or module.SyncOptions()
        self.history = module.BoundedHistory(self.options)
        self.sequences = {}
        self.sources = {}
        self.events = []
        self.saved = {"success": [], "correction": []}
        self.opens = []

        def factory(outcome):
            self.opens.append(outcome)
            return FakeDataset(self.saved[outcome])

        self.session = module.EpisodeSession(root / "pending", factory, 20, "basket")
        self.root = root
        self.push("camera_317222074298", -40, -5)
        self.push("camera_844212071939", -40, -4)
        self.push("state", -state_age, -3)
        if grip:
            self.push("gripper", None, -1, 0.0)

    def timing(self, ms):
        ns = round(ms * 1_000_000)
        return {"sample_monotonic_ns": MONO + ns, "sample_system_time_ns": EPOCH + ns,
                "receiver_clock": {"ros_time_ns": EPOCH + ns, "use_sim_time": False, "ros_clock_type": "ROS_TIME"},
                "sources": self.sources.copy(), "history_fault_overflows": self.history.fault_overflows}

    def push(self, name, header_ms, receipt_ms, value=None):
        ns = round(receipt_ms * 1_000_000)
        sequence = self.sequences.get(name, 0) + 1
        self.sequences[name] = sequence
        header = None if header_ms is None else EPOCH + round(header_ms * 1_000_000)
        source = {"header_stamp": None if header is None else {"sec": header // 1_000_000_000, "nanosec": header % 1_000_000_000},
                  "callback_monotonic_ns": MONO + ns, "callback_system_time_ns": EPOCH + ns,
                  "receiver_clock": self.timing(receipt_ms)["receiver_clock"], "receipt_sequence": sequence}
        if value is None:
            value = np.zeros(self.m.IMAGE_SHAPE, dtype=np.uint8) if name.startswith("camera_") else np.zeros(6, dtype=np.float32)
        self.sources[name] = source
        self.history.append(name, value, source)

    def begin(self):
        timing = self.timing(0)
        aligner = self.m.HistoryAligner.prepare(self.options, self.history.snapshot(MONO), timing)
        self.session.start(aligner.target, MONO / 1e9, timing, aligner)

    def generate(self, end_ms=1000, camera_end=None, state_end=None):
        for camera, hz, delay in (("camera_317222074298", 29.98, 33.2), ("camera_844212071939", 29.76, 36.2)):
            for i in range(int(end_ms * hz / 1000) + 1):
                generated = i * 1000 / hz
                if camera_end is None or generated <= camera_end:
                    self.events.append((generated + delay, camera, generated, None))
        for generated in range(0, int(end_ms) + 1, 50):
            if state_end is None or generated <= state_end:
                self.events.append((generated + 1.5, "state", generated, None))
        self.events.sort(key=lambda event: event[0])

    def tick(self, reference_ms, actual_ms=None):
        actual_ms = reference_ms + self.options.receive_wait_ms if actual_ms is None else actual_ms
        while self.events and self.events[0][0] <= actual_ms:
            receipt, name, generated, value = self.events.pop(0)
            self.push(name, generated, receipt, value)
        timing = self.timing(actual_ms)
        schedule = self.session.deadline.consume(timing["sample_monotonic_ns"])
        assert schedule is not None
        self.session.record_aligned(self.history.snapshot(timing["sample_monotonic_ns"]), timing, schedule)

    def row(self, index):
        return json.loads((self.session.path / f"{index:08d}.timing.json").read_text())


def test_bounded_30fps_20hz_causal_alignment(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(1200)
    for reference in range(50, 1001, 50):
        h.tick(reference)
        assert h.session.state == "RECORDING", h.session.integrity
        row = h.row(h.session.frames - 1)
        assert row["alignment"]["reference_system_time_ns"] == EPOCH + reference * 1_000_000
        assert row["alignment"]["reference_monotonic_ns"] > MONO
        for name in ("camera_317222074298", "camera_844212071939", "state"):
            source = row["sources"][name]
            assert recorder._header_ns(source) <= row["alignment"]["reference_system_time_ns"]
            assert source["reference_age_ns"] >= 0
        assert row["alignment"]["camera_skew_ns"] <= 40_000_000
        assert row["schedule"]["slot"] == h.session.frames
    assert h.session.frames == 20
    assert h.session.deadline.missed == 0


def test_bounded_camera_skew_rejected(recorder, tmp_path):
    options = recorder.SyncOptions(camera_skew_ms=1)
    h = HistoryHarness(recorder, tmp_path, options)
    h.begin()
    h.generate(1000)
    for ref in range(50, 1001, 50):
        h.tick(ref)
        if not h.session.integrity["valid"]:
            break
    assert not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == "camera_skew"


@pytest.mark.parametrize("missing", ["camera", "state"])
def test_bounded_missing_data_invalidates_and_blocks_both_outcomes(recorder, tmp_path, missing):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500, camera_end=100 if missing == "camera" else None,
               state_end=100 if missing == "state" else None)
    for ref in range(50, 501, 50):
        h.tick(ref)
        if h.session.state == "REVIEW":
            break
    assert not h.session.integrity["valid"]
    original = {p.name: p.read_bytes() for p in h.session.path.iterdir()}
    for outcome in ("success", "correction"):
        assert "INVALID" in h.session.save(outcome, tmp_path / outcome)
    assert h.opens == []
    assert {p.name: p.read_bytes() for p in h.session.path.iterdir()} == original


def test_bounded_stale_subscription_initial_state_cannot_start(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path, state_age=1000)
    with pytest.raises(recorder.IntegrityError, match="observation_too_old"):
        h.begin()
    assert h.session.state == "READY" and h.session.path is None


def test_bounded_no_action_initial_hold_and_long_target_hold(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(2500)
    h.tick(50)
    assert h.row(0)["recorded_action"]["kind"] == "initial_hold"
    h.events.append((121.5, "action", 120, np.full(6, 3, dtype=np.float32)))
    h.events.sort(key=lambda x: x[0])
    for ref in range(100, 2001, 50):
        h.tick(ref)
        assert h.session.integrity["valid"], h.session.integrity
    with np.load(h.session.path / "00000039.npz", allow_pickle=False) as data:
        assert data["action"][:6].tolist() == [3] * 6
    assert h.row(39)["recorded_action"]["hold_start_system_ns"] == EPOCH + 120_000_000
    assert h.row(39)["recorded_action"]["held_from_previous_reference"]
    assert len(h.history.items["action"]) == 0  # Held target outlives queue TTL.


def test_bounded_delayed_pre_episode_action_and_future_action(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    h.events += [(80, "action", -20, np.full(6, 9, dtype=np.float32)),
                 (81.5, "action", 80, np.full(6, 4, dtype=np.float32))]
    h.events.sort(key=lambda x: x[0])
    h.tick(50)  # Both messages received by release=110, only future goal is new.
    assert h.row(0)["recorded_action"]["kind"] == "initial_hold"
    h.tick(100)
    assert h.row(1)["recorded_action"]["source"]["header_stamp"] == {"sec": 1_700_000_000, "nanosec": 80_000_000}
    with np.load(h.session.path / "00000001.npz", allow_pickle=False) as data:
        assert data["action"][:6].tolist() == [4] * 6


def test_bounded_late_old_action_does_not_erase_valid_target(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    h.events += [(21.5, "action", 20, np.full(6, 3, dtype=np.float32)),
                 (90, "action", -20, np.full(6, 9, dtype=np.float32))]
    h.events.sort(key=lambda x: x[0])
    h.tick(50)
    assert h.session.integrity["valid"], h.session.integrity
    with np.load(h.session.path / "00000000.npz", allow_pickle=False) as data:
        assert data["action"][:6].tolist() == [3] * 6


def test_bounded_headerless_gripper_is_causal_and_held(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(1600)
    h.events.append((70, "gripper", None, 1.0))
    h.events.sort(key=lambda x: x[0])
    for ref in range(50, 1501, 50):
        h.tick(ref)
        assert h.session.integrity["valid"]
        with np.load(h.session.path / f"{h.session.frames-1:08d}.npz", allow_pickle=False) as data:
            assert data["action"][6] == (0 if ref == 50 else 1)
            assert data["observation.state"][6] == data["action"][6]
    assert h.row(29)["sources"]["gripper"]["header_stamp"] is None
    assert h.row(29)["sources"]["gripper"]["callback_monotonic_ns"] == MONO + 70_000_000


def test_bounded_unknown_gripper_not_invented(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path, grip=False)
    with pytest.raises(recorder.IntegrityError, match="unknown_initial_gripper"):
        h.begin()


def test_bounded_deadline_gap_is_invalid_not_compressed(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    h.tick(50)
    h.tick(100, actual_ms=300)  # Release 160 and 210 were missed; no catchup.
    assert h.session.frames == 1 and h.session.state == "REVIEW"
    assert h.session.integrity["violations"][-1]["reason"] == "missed_deadline_or_grid_gap"
    assert h.session.integrity["violations"][-1]["schedule"]["missed_deadlines"] == 2
    assert not (h.session.path / "00000001.npz").exists()


@pytest.mark.parametrize("fault", ["system_jump", "sim_time", "ros_jump", "clock_type", "header_reverse", "future_header"])
def test_bounded_clock_and_timestamp_faults_stop_review(recorder, tmp_path, fault):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    if fault in ("header_reverse", "future_header"):
        headers = (70, 50) if fault == "header_reverse" else (70, 200)
        h.events += [(72, "action", headers[0], np.ones(6, dtype=np.float32)),
                     (90, "action", headers[1], np.ones(6, dtype=np.float32))]
        h.events.sort(key=lambda x: x[0])
        h.tick(50)
    else:
        h.tick(50)
        while h.events and h.events[0][0] <= 160:
            receipt, name, generated, value = h.events.pop(0)
            h.push(name, generated, receipt, value)
        timing = h.timing(160)
        if fault == "sim_time":
            timing["receiver_clock"]["use_sim_time"] = True
        elif fault == "clock_type":
            timing["receiver_clock"]["ros_clock_type"] = "SYSTEM_TIME"
        elif fault == "system_jump":
            timing["sample_system_time_ns"] += 100_000_000
            timing["receiver_clock"]["ros_time_ns"] += 100_000_000
        else:
            timing["receiver_clock"]["ros_time_ns"] -= 100_000_000
        tick = h.session.deadline.consume(timing["sample_monotonic_ns"])
        h.session.record_aligned(h.history.snapshot(timing["sample_monotonic_ns"]), timing, tick)
    assert h.session.state == "REVIEW" and not h.session.integrity["valid"]
    assert "INVALID" in h.session.save("success", tmp_path / "success")


def test_bounded_history_count_ttl_and_image_identity(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path, recorder.SyncOptions(history_size=3, history_seconds=.2))
    image = np.ones(recorder.IMAGE_SHAPE, dtype=np.uint8)
    for i in range(50):
        h.push("camera_317222074298", i, i + 35, image)
    assert len(h.history.items["camera_317222074298"]) == 3
    assert all(entry[0] is image for entry in h.history.items["camera_317222074298"])
    h.history.prune(MONO + 1_000_000_000)
    assert all(len(values) == 0 for values in h.history.items.values())
    assert h.history.gripper_carry[0] == 0


@pytest.mark.parametrize("outcome", ["success", "correction"])
def test_bounded_fake_save_timing_metadata_and_discard(recorder, tmp_path, outcome):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    h.tick(50)
    h.tick(100)
    path = h.session.path
    if outcome == "correction":
        h.session.mark_correction(recorder.CommandInput("x", MONO+75_000_000, EPOCH+75_000_000))
    h.session.stop(now_ns=MONO + 170_000_000)
    assert "저장 완료" in h.session.save(outcome, tmp_path / outcome)
    assert h.opens == [outcome] and not path.exists()
    sidecar = tmp_path / outcome / "meta/recorder_timing/episode-000000"
    meta = json.loads((sidecar / "episode.json").read_text())
    rows = [json.loads(line) for line in (sidecar / "frames.jsonl").read_text().splitlines()]
    assert meta["dataset_episode_index"] == 0 and meta["integrity"]["valid"]
    assert meta["alignment_mode"] == "bounded_history"
    assert rows[0]["alignment"]["reference_system_time_ns"] == EPOCH + 50_000_000
    assert rows[1]["actual_interval_ns"] == 50_000_000
    assert rows[1]["dataset_nominal_timestamp_s"] == .05
    assert len(h.saved[outcome]) == 1 and not h.saved["correction" if outcome == "success" else "success"]
    h.session.start(np.zeros(6), 20)
    h.session.add_frame(frame(recorder))
    h.session.discard()
    assert len(h.opens) == 1


@pytest.mark.parametrize("topic", ["action", "gripper"])
def test_bounded_future_event_evicted_before_use_is_invalid(recorder, tmp_path, topic):
    from collections import deque
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    h.history.items[topic] = deque(h.history.items.get(topic, ()), maxlen=1)
    h.events += [(81.5, topic, 80 if topic == "action" else None,
                  np.ones(6, dtype=np.float32) if topic == "action" else 1.0),
                 (131.5, topic, 130 if topic == "action" else None,
                  np.full(6, 2, dtype=np.float32) if topic == "action" else 0.0)]
    h.events.sort(key=lambda x: x[0])
    h.tick(50)
    assert h.session.integrity["valid"]
    h.tick(100)
    assert not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == topic + "_history_overflow"


def test_bounded_camera_reuse_is_logged_and_age_limited(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500, camera_end=34)
    h.events += [(85, "camera_317222074298", 50, None),
                 (86, "camera_844212071939", 50, None)]
    h.events.sort(key=lambda x: x[0])
    h.tick(50)
    h.tick(100)
    assert h.session.integrity["valid"]
    assert h.row(1)["sources"]["camera_317222074298"]["reused_callback_sample"]
    assert h.row(1)["sources"]["camera_317222074298"]["reference_age_ns"] == 50_000_000
    h.tick(150)
    assert not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == "observation_too_old"


def test_bounded_bad_clock_not_hidden_by_later_pre_episode_action(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    h.events += [(21.5, "action", 20, np.ones(6, dtype=np.float32)),
                 (90, "action", -20, np.ones(6, dtype=np.float32))]
    h.events.sort(key=lambda x: x[0])
    # A transient fault is latched independently of subsequent header reversals.
    h.history.faults.append({"topic": "action", "reason": "system_clock_jump_or_drift",
                             "callback_monotonic_ns": MONO + 80_000_000, "header_stamp": None})
    h.tick(50)
    assert not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == "system_clock_jump_or_drift"


def test_bounded_invalid_quit_preserves_pending(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500, state_end=0)
    h.tick(50)
    h.tick(100)
    assert not h.session.integrity["valid"]
    path = h.session.path
    quit_ok, message = h.session.request_quit()
    assert quit_ok and "Pending" in message
    assert path.exists() and (path / "episode.json").exists()


def test_bounded_main_with_fake_ros_clock_and_dataset(recorder, tmp_path, monkeypatch):
    import queue
    h = HistoryHarness(recorder, tmp_path / "harness")
    h.generate(600)
    virtual_ms = [0.0]
    commands = queue.Queue()
    started = [False]
    finished = [False]
    cleanup = []
    node = types.SimpleNamespace(
        history_snapshot=lambda: (h.timing(virtual_ms[0]), h.history.snapshot(MONO + round(virtual_ms[0] * 1e6))),
        destroy_node=lambda: cleanup.append("node"))

    def spin(*args, **kwargs):
        virtual_ms[0] += 5
        assert virtual_ms[0] < 1000
        while h.events and h.events[0][0] <= virtual_ms[0]:
            receipt, name, generated, value = h.events.pop(0)
            h.push(name, generated, receipt, value)
        if not started[0]:
            commands.put("s")
            started[0] = True
        elif virtual_ms[0] >= 400 and not finished[0]:
            for command in ("r", "o", "q"):
                commands.put(command)
            finished[0] = True

    saved = []
    monkeypatch.setattr(recorder, "LeRobotDataset", types.SimpleNamespace(create=lambda **kwargs: FakeDataset(saved)))
    monkeypatch.setattr(recorder, "Indy7Observations", lambda *args: node)
    monkeypatch.setattr(recorder, "_commands", lambda: commands)
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: MONO + round(virtual_ms[0] * 1e6))
    monkeypatch.setattr(recorder.time, "sleep", lambda seconds: virtual_ms.__setitem__(0, virtual_ms[0] + seconds * 1000))
    monkeypatch.setattr(recorder.rclpy, "init", lambda: None, raising=False)
    monkeypatch.setattr(recorder.rclpy, "ok", lambda: True, raising=False)
    monkeypatch.setattr(recorder.rclpy, "spin_once", spin, raising=False)
    monkeypatch.setattr(recorder.rclpy, "shutdown", lambda: cleanup.append("ros_stub"), raising=False)
    monkeypatch.setattr(recorder.sys, "argv", ["act-record", "--root", str(tmp_path / "success"),
                        "--correction-root", str(tmp_path / "correction"), "--failure-root", str(tmp_path / "legacy_failure"), "--pending-root", str(tmp_path / "pending")])
    assert recorder.main() == 0
    assert len(saved) == 1 and len(saved[0]) >= 5
    assert cleanup == ["node", "ros_stub"]
    assert not (tmp_path / "correction").exists()
    meta = json.loads((tmp_path / "success/meta/recorder_timing/episode-000000/episode.json").read_text())
    assert meta["integrity"]["valid"] and meta["alignment_mode"] == "bounded_history"
    assert meta["start_monotonic_ns"] == MONO + 5_000_000  # Main thread handles s at5ms.
    first = json.loads((tmp_path / "success/meta/recorder_timing/episode-000000/frames.jsonl").read_text().splitlines()[0])
    assert first["alignment"]["reference_monotonic_ns"] == MONO + 55_000_000
    assert first["schedule"]["deadline_monotonic_ns"] == MONO + 115_000_000


@pytest.mark.parametrize("fault", ["clock", "deadline"])
def test_bounded_finish_checks_integrity_before_review(recorder, tmp_path, fault):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    h.tick(50)
    timing = h.timing(120 if fault == "clock" else 350)
    if fault == "clock":
        timing["receiver_clock"]["use_sim_time"] = True
    path = h.session.path
    assert "INVALID" in h.session.finish_aligned(h.history.snapshot(timing["sample_monotonic_ns"]), timing)
    assert h.session.frames == 1 and path.exists()
    assert "INVALID" in h.session.save("correction", tmp_path / "correction")
    assert h.opens == []


def test_bounded_options_reject_unsafe_history_configuration(recorder):
    for kwargs in ({"history_size": 0}, {"history_size": 1.5}, {"history_seconds": .05},
                   {"receive_wait_ms": float("nan")}, {"clock_tolerance_ms": 0}):
        with pytest.raises(ValueError):
            recorder.SyncOptions(**kwargs)
