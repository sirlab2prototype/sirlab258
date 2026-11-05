"""Offline inference integration. All artifacts belong to scoped project tmp."""
import ast
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as Message

import numpy as np
import pytest

from ai import dataset_contract as contract
from ai.observation_timing import SyncOptions, IntegrityError
from ai.serve.action_scheduler import DryRunScheduler
from ai.serve.observations import HistorySource, RosSource
from ai.serve.protocol import (PROTOCOL, encode_request, decode_request, decode_response, envelope,
                               local_endpoint, validate_observation)
from ai.serve.rpc import RPCClient
from ai.serve.zmq_act_server import InferenceService, serve
from ai.train.test.test_validate_dataset import real_base

MS = 1_000_000


def observation():
    return {**{key: np.zeros(contract.IMAGE_SHAPE, dtype=np.uint8) for key in contract.CAMERA_KEYS},
            "observation.state": np.zeros(7, dtype=np.float32)}


class FakeBackend:
    def __init__(self, delay=0):
        self.policy = Message(config=Message(chunk_size=12, n_action_steps=4))
        self.delay, self.resets, self.calls = delay, 0, 0
    def reset(self): self.resets += 1
    def predict_chunk(self, obs):
        validate_observation(obs)
        self.calls += 1
        if self.delay: time.sleep(self.delay)
        return np.zeros((12, 7), dtype=np.float32)


def begin(service, session="test-session", started=None):
    header = envelope("reset", session, "reset")
    header["session_started_monotonic_ns"] = time.monotonic_ns() if started is None else started
    return json.loads(service.handle(encode_request(header))[0])


def predict_header(session="test-session", request="p1", age_ms=1):
    return {**envelope("predict", session, request), "observation_monotonic_ns": time.monotonic_ns() - age_ms * MS,
            "observation_system_time_ns": time.time_ns(), "clock_domain": "same_host_monotonic"}


@pytest.mark.parametrize("fault", ["one_camera", "rgb_size", "rgb_dtype", "state_dim", "state_nan", "gripper"])
def test_bad_observation_rejected(fault):
    obs = observation()
    if fault == "one_camera": obs.pop(contract.CAMERA_KEYS[1])
    elif fault == "rgb_size": obs[contract.CAMERA_KEYS[0]] = np.zeros((32, 32, 3), dtype=np.uint8)
    elif fault == "rgb_dtype": obs[contract.CAMERA_KEYS[1]] = obs[contract.CAMERA_KEYS[1]].astype(np.float32)
    elif fault == "state_dim": obs["observation.state"] = np.zeros(6, dtype=np.float32)
    elif fault == "state_nan": obs["observation.state"][0] = np.nan
    else: obs["observation.state"][6] = .5
    with pytest.raises(ValueError): encode_request(predict_header(), obs)


def test_service_two_camera_preprocessing_boundary_and_reset():
    backend = FakeBackend()
    service = InferenceService(backend, "model-sha")
    assert begin(service)["ok"]
    response = service.handle(encode_request(predict_header(), observation()))
    header, chunk = decode_response(response, "test-session", "p1", 12)
    assert header["output_valid"] and chunk.shape == (12, 7) and np.isfinite(chunk).all()
    assert header["inference_ns"] > 0 and header["checkpoint_id"] == "model-sha"
    assert backend.calls == backend.resets == 1
    end = envelope("end", "test-session", "end")
    assert json.loads(service.handle(encode_request(end))[0])["ok"]
    assert not json.loads(service.handle(encode_request(predict_header(request="late"), observation()))[0])["ok"]


@pytest.mark.parametrize("fault", ["nan", "shape", "stale", "future", "session", "protocol", "buffers"])
def test_service_rejects_bad_output_and_requests(fault):
    backend = FakeBackend()
    service = InferenceService(backend, "model")
    begin(service)
    header = predict_header()
    if fault == "nan": backend.predict_chunk = lambda obs: np.full((12, 7), np.nan, dtype=np.float32)
    elif fault == "shape": backend.predict_chunk = lambda obs: np.zeros((3, 7), dtype=np.float32)
    elif fault == "stale": header["observation_monotonic_ns"] -= 1000 * MS
    elif fault == "future": header["observation_monotonic_ns"] += 1000 * MS
    elif fault == "session": header["session_id"] = "wrong"
    elif fault == "protocol": header["protocol"] = "wrong"
    parts = encode_request(header, observation())
    if fault == "buffers": parts.pop()
    result = json.loads(service.handle(parts)[0])
    assert not result["ok"] and not result["output_valid"]
    assert result["error"]["message"]


def test_obsolete_session_reset_is_rejected():
    service = InferenceService(FakeBackend(), "model")
    first = time.monotonic_ns()
    assert begin(service, "new", first)["ok"]
    assert not begin(service, "old", first - 1)["ok"]


@pytest.mark.parametrize("endpoint", ["tcp://0.0.0.0:5557", "tcp://192.168.0.1:5557", "ipc:///tmp/test", "tcp://localhost:5557"])
def test_no_external_endpoint(endpoint):
    with pytest.raises(ValueError): local_endpoint(endpoint)


def test_scheduler_20hz_sequential_and_prefetched_chunks():
    scheduler = DryRunScheduler(12, 3)
    scheduler.start("episode", 0)
    scheduler.requested("a", 0)
    values = np.repeat(np.arange(12, dtype=np.float32)[:, None], 7, axis=1)
    scheduler.response("episode", "a", values, 70 * MS)
    scheduler.requested("b", 100 * MS)
    scheduler.response("episode", "b", values + 2, 130 * MS)
    rows = [scheduler.tick(ms * MS) for ms in (100, 150, 200, 250, 300, 350)]
    assert [row["action"][0] for row in rows] == [2, 3, 4, 5, 6, 7]
    assert [row["action_index"] for row in rows] == [2, 3, 4, 3, 4, 5]
    assert all(row["schedule"]["lateness_ns"] == 0 for row in rows)
    scheduler.stop()
    assert scheduler.tick(400 * MS) is None


def test_scheduler_late_response_episode_isolation_and_expiry():
    scheduler = DryRunScheduler(4, 1)
    scheduler.start("old", 0)
    scheduler.requested("old-id", 0)
    scheduler.start("new", 0)
    assert not scheduler.response("old", "old-id", np.zeros((4, 7), np.float32), 0)
    scheduler.requested("new-id", 0)
    with pytest.raises(RuntimeError, match="expired"):
        scheduler.response("new", "new-id", np.zeros((4, 7), np.float32), 250 * MS)
    assert scheduler.tick(300 * MS) is None


@pytest.mark.parametrize("fault", ["starvation", "missed_deadline", "late_response"])
def test_scheduler_stops_instead_of_holding(fault):
    scheduler = DryRunScheduler(12, 1)
    scheduler.start("s", 0)
    scheduler.requested("a", 0)
    scheduler.response("s", "a", np.zeros((12, 7), np.float32), 0)
    assert scheduler.tick(50 * MS)
    with pytest.raises(RuntimeError):
        if fault == "starvation": scheduler.tick(100 * MS)
        elif fault == "missed_deadline": scheduler.tick(200 * MS)
        else:
            scheduler.requested("b", 50 * MS)
            scheduler.response("s", "b", np.zeros((12, 7), np.float32), 101 * MS)
    assert scheduler.state == "FAILED" and scheduler.tick(250 * MS) is None


def timing(ms):
    mono, system = 10_000_000_000 + round(ms * MS), 1_700_000_000_000_000_000 + round(ms * MS)
    return {"sample_monotonic_ns": mono, "sample_system_time_ns": system, "clock_read_span_ns": 0,
            "receiver_clock": {"ros_time_ns": system, "use_sim_time": False, "ros_clock_type": "ROS_TIME"}}


def stamp(ms):
    epoch = timing(ms)["sample_system_time_ns"]
    return Message(sec=epoch // 1_000_000_000, nanosec=epoch % 1_000_000_000)


def test_ros_formatted_messages_alignment_unknown_gripper_and_stale():
    source = HistorySource()
    current = [35.]
    source.timing = lambda: timing(current[0])
    for name in contract.CAMERA_NAMES:
        image = np.full(contract.IMAGE_SHAPE, [1, 2, 3], dtype=np.uint8)
        source.ingest(name, Message(header=Message(stamp=stamp(33)), width=640, height=480, step=1920, encoding="rgb8", data=image.tobytes()))
    current[0] = 52
    source.ingest("state", Message(header=Message(stamp=stamp(50)), name=list(reversed(contract.JOINT_NAMES)), position=[5,4,3,2,1,0]))
    current[0] = 70
    with pytest.raises(IntegrityError, match="unknown_initial_gripper"): source.begin()
    source.ingest("gripper", Message(data=1.))
    source.begin()
    current[0] = 102
    source.ingest("state", Message(header=Message(stamp=stamp(100)), name=list(contract.JOINT_NAMES), position=[0,1,2,3,4,5]))
    for name in contract.CAMERA_NAMES:
        current[0] = 135
        source.ingest(name, Message(header=Message(stamp=stamp(100)), width=640, height=480, step=1920, encoding="bgr8", data=image.tobytes()))
    current[0] = 180
    obs, metadata = source.sample(timing(120)["sample_monotonic_ns"])
    assert obs["observation.state"].tolist() == [0,1,2,3,4,5,1]
    assert obs[contract.CAMERA_KEYS[0]][0, 0].tolist() == [3, 2, 1]
    assert metadata["alignment"]["camera_skew_ns"] == 0
    assert "recorded_action" not in metadata["alignment"]
    assert metadata["sources"]["state"]["reference_age_ns"] == 20 * MS
    assert metadata["sources"]["gripper"]["header_stamp"] is None
    current[0] = 560
    with pytest.raises(IntegrityError): source.sample(timing(500)["sample_monotonic_ns"])


def test_act_camera_pair_uses_fresh_buffered_frames_without_changing_recorder_selection():
    from ai.observation_timing import HistoryAligner
    source = HistorySource()
    current = [100]
    source.timing = lambda: timing(current[0])
    def add(name, header_ms, receipt_ms):
        source.append(name, np.zeros((1,), dtype=np.uint8),
                      {"sec": stamp(header_ms).sec, "nanosec": stamp(header_ms).nanosec},
                      timing(receipt_ms))
    source.append("gripper", 0., None, timing(90))
    for name in (*contract.CAMERA_NAMES, "state"):
        add(name, 95, 97)
    source.begin()
    _, initial = source.snapshot()
    recorder_aligner = HistoryAligner.prepare(source.options, initial, timing(100))
    add(contract.CAMERA_NAMES[0], 150, 155)
    add(contract.CAMERA_NAMES[1], 160, 165)
    add("state", 185, 186)
    add(contract.CAMERA_NAMES[1], 193, 194)
    current[0] = 198
    _, history = source.snapshot()
    with pytest.raises(IntegrityError, match="camera_skew"):
        recorder_aligner.select(history, timing(198), timing(198)["sample_monotonic_ns"],
                                timing(198)["sample_system_time_ns"])
    _, metadata = source.sample(timing(198)["sample_monotonic_ns"])
    assert metadata["alignment"]["camera_skew_ns"] == 10 * MS
    assert [metadata["sources"][name]["reference_age_ns"] for name in contract.CAMERA_NAMES] == [48*MS, 38*MS]
    assert all(metadata["sources"][name]["selection"] == "nearest_fresh_causal_camera_pair"
               for name in contract.CAMERA_NAMES)
    no_near_pair = {**history, "items": {**history["items"],
                    contract.CAMERA_NAMES[1]: history["items"][contract.CAMERA_NAMES[1]][-1:]}}
    with pytest.raises(IntegrityError, match="camera_skew"):
        source.aligner.select(no_near_pair, timing(198), timing(198)["sample_monotonic_ns"],
                              timing(198)["sample_system_time_ns"])
    current[0] = 220
    with pytest.raises(IntegrityError, match="observation_too_old"):
        source.sample(timing(220)["sample_monotonic_ns"])


@contextmanager
def server_thread(service, stop_after=None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"tcp://127.0.0.1:{sock.getsockname()[1]}"
    stop, ready = threading.Event(), threading.Event()
    def event(response):
        if stop_after is not None and service.backend.calls >= stop_after: stop.set()
    thread = threading.Thread(target=serve, args=(service, endpoint), kwargs={"stop": stop.is_set, "ready": ready.set, "event": event}, daemon=True)
    thread.start()
    assert ready.wait(2)
    try: yield endpoint
    finally:
        stop.set()
        thread.join(2)
        assert not thread.is_alive()


def test_rpc_async_and_timeout_discards_response():
    service = InferenceService(FakeBackend(delay=.1), "model")
    with server_thread(service) as endpoint:
        rpc = RPCClient(endpoint, timeout_ms=40)
        reset = envelope("reset", "rpc-session", "r")
        reset["session_started_monotonic_ns"] = time.monotonic_ns()
        rpc.control(reset)
        request = predict_header(session="rpc-session")
        started = time.monotonic()
        rpc.submit(request, observation(), 12)
        assert time.monotonic() - started < .03
        with pytest.raises(TimeoutError): rpc.future.result(timeout=1)
        rpc.close()
        time.sleep(.12)  # old response is sent to a closed socket, never the new client
        second = RPCClient(endpoint, timeout_ms=200)
        reset["session_started_monotonic_ns"] = time.monotonic_ns()
        second.control(reset)
        second.close()


def test_no_actuator_calls_in_new_runtime():
    forbidden = {"create_publisher", "create_client", "create_service", "movetelej_abs", "movetelej_rel", "movej", "call_async"}
    for filename in ("observations.py", "dry_run.py", "infer.py", "action_scheduler.py", "rpc.py", "zmq_act_server.py"):
        tree = ast.parse((Path(__file__).parents[1] / filename).read_text())
        assert not [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden]


@pytest.fixture(scope="module")
def checkpoint(real_base, tmp_path_factory):
    from ai.train import run_train
    from ai.train.test.test_training_pipeline import arguments
    output = tmp_path_factory.mktemp("inference-checkpoint") / "run"
    manifest = run_train.main([*arguments(real_base, output), "--chunk-size", "12", "--n-action-steps", "4"])
    assert manifest["status"] == "complete"
    return Path(manifest["checkpoint"])


def run_cli(checkpoint, output, device):
    cmd = ["./scripts/bin/act", "infer", "--checkpoint", str(checkpoint), "--source", "synthetic", "--dry-run", "--device", device,
           "--duration", "2.5", "--output-dir", str(output)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    report = json.loads((output / "result.json").read_text())
    if result.returncode:
        logs = '\n'.join(path.read_text()[-4000:] for path in (output / "server.log", output / "client.log"))
        pytest.fail(result.stdout + result.stderr + logs)
    assert report["status"] == "complete", report
    assert report["client"]["robot_commands_sent"] == 0 and report["client"]["actions"] >= 20
    assert report["client"]["chunks"] >= 3 and report["client"]["missed_action_deadlines"] == 0
    assert 19.5 <= report["client"]["actual_action_fps"] <= 20.5
    events = [json.loads(line) for line in (output / "client/events.jsonl").read_text().splitlines()]
    assert any(row["event"] == "reset" for row in events)
    assert any(row["event"] == "reset_end" for row in events)
    assert all(len(row["action"]) == 7 and np.isfinite(row["action"]).all() for row in events if row["event"] == "action")
    summary = report["client"]
    print(f"MEASURED {device}: {json.dumps(summary)}", flush=True)
    return report


def test_actual_checkpoint_server_client_scheduler_cpu(checkpoint, tmp_path):
    first = run_cli(checkpoint, tmp_path / "cpu", "cpu")
    second = run_cli(checkpoint, tmp_path / "cpu-second", "cpu")
    assert first["client"]["session_id"] != second["client"]["session_id"]
    assert first["server"]["checkpoint_id"] == second["server"]["checkpoint_id"]


def test_actual_checkpoint_cuda(checkpoint, tmp_path):
    import torch
    if not torch.cuda.is_available(): pytest.skip("CUDA unavailable")
    result = run_cli(checkpoint, tmp_path / "cuda", "cuda")
    assert result["server"]["device"].startswith("cuda")


def test_invalid_prediction_clears_all_actions():
    scheduler = DryRunScheduler(12, 4)
    scheduler.start("episode", 0)
    scheduler.requested("a", 0)
    with pytest.raises(RuntimeError, match="invalid_prediction"):
        scheduler.response("episode", "a", np.full((12, 7), np.nan, np.float32), 0)
    assert scheduler.tick(50 * MS) is None and scheduler.active is None


def test_wrong_response_id_is_not_an_action():
    header = {"protocol": PROTOCOL, "ok": True, "session_id": "old", "request_id": "old", "shape": [12,7], "output_valid": True}
    with pytest.raises(ValueError, match="mismatch"):
        decode_response([json.dumps(header).encode(), np.zeros((12,7), np.float32).tobytes()], "new", "new", 12)


@pytest.mark.parametrize("fault", ["disconnect", "timeout", "state_stale", "missing", "model_identity"])
def test_runtime_failures_stop_actions(tmp_path, fault):
    from ai.serve.dry_run import run_session, EventLog
    from ai.serve.observations import SyntheticSource
    source = HistorySource() if fault == "missing" else SyntheticSource()
    if fault == "state_stale":
        original = source.update
        def update():
            original()
            if source.generated["state"] >= 3: source.generated["state"] = 1000000
        source.update = update
    service = InferenceService(FakeBackend(delay=.12 if fault == "timeout" else 0), "model")
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path, "failure-test")
    try:
        with server_thread(service, stop_after=2 if fault == "disconnect" else None) as endpoint:
            result = run_session(source, endpoint, {"chunk_size":12, "n_action_steps":4, "checkpoint_id":"wrong" if fault == "model_identity" else "model", "checkpoint":"fake"},
                                 log, duration=.8, rpc_timeout_ms=50, startup_timeout=.15)
    finally: log.close()
    assert result["status"] == "failed" and result["robot_commands_sent"] == 0
    expected = {"disconnect": "timeout", "timeout": "timeout", "state_stale": "observation_too_old",
                "missing": "observation_startup_timeout", "model_identity": "server_model_or_config_changed"}
    assert expected[fault] in result["reason"], result
    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    error = next(i for i, row in enumerate(rows) if row["event"] == "error")
    assert all(row["event"] != "action" for row in rows[error + 1:])
    assert rows[-1]["event"] == "session_end"


def test_ros_client_has_only_subscriptions_with_fake_ros(monkeypatch):
    import types
    modules = {name: types.ModuleType(name) for name in ("rclpy", "rclpy.node", "rclpy.qos", "sensor_msgs", "sensor_msgs.msg", "std_msgs", "std_msgs.msg")}
    for name, module in modules.items(): monkeypatch.setitem(sys.modules, name, module)
    calls = []
    class Node:
        def __init__(self, name): self.topics = []
        def create_subscription(self, kind, topic, callback, qos): self.topics.append(topic)
        def get_clock(self): return Message(clock_type="ROS_TIME", now=lambda: Message(nanoseconds=time.time_ns()))
        def get_parameter(self, name): return Message(value=False)
        def destroy_node(self): calls.append("destroy")
        def __getattr__(self, name): raise AssertionError(f"Forbidden unexpected ROS operation: {name}")
    modules["rclpy.node"].Node = Node
    modules["rclpy.qos"].qos_profile_sensor_data = object()
    modules["sensor_msgs.msg"].Image = modules["sensor_msgs.msg"].JointState = object
    modules["std_msgs.msg"].Float32 = object
    modules["rclpy"].init = lambda: calls.append("fake_init")
    modules["rclpy"].shutdown = lambda: calls.append("fake_shutdown")
    modules["rclpy"].spin_once = lambda *args, **kwargs: time.sleep(.001)
    source = RosSource()
    assert set(source.node.topics) == {"/joint_states", "/capstone/gripper_state", *{f"/{name}/camera/color/image_raw" for name in contract.CAMERA_NAMES}}
    source.close()
    assert calls == ["fake_init", "destroy", "fake_shutdown"]


def test_lightweight_client_imports_without_torch_or_ros():
    result = subprocess.run([str(Path('.venv/bin/python').resolve()), '-c',
        'import ai.serve.dry_run; import sys; assert "torch" not in sys.modules; assert "lerobot" not in sys.modules; assert "rclpy" not in sys.modules'],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
