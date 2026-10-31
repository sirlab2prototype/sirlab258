"""Offline callback messages and fake clocks only; never rclpy.init()."""
import json
from types import SimpleNamespace as Message

import numpy as np
import pytest

from ai import dataset_contract as contract
from ai.observation_timing import IntegrityError, SyncOptions
from ai.serve.observation_diagnostics import ReceptionDiagnostics
from ai.serve.observations import HistorySource
from ai.serve.test.test_inference import timing, stamp


def source_at(ms=100):
    source = HistorySource()
    current = [ms]
    source.timing = lambda: timing(current[0])
    return source, current


def fill(source, current, header_ms, *, gripper=True):
    for name in contract.CAMERA_NAMES:
        source.append(name, np.zeros(contract.IMAGE_SHAPE, dtype=np.uint8), vars(stamp(header_ms)))
    source.append("state", np.arange(6, dtype=np.float32), vars(stamp(header_ms)))
    if gripper:
        source.ingest("gripper", Message(data=0.))


def test_startup_barrier_excludes_pre_subscription_generation():
    source, current = source_at(100)
    source.subscription_started_system_ns = timing(90)["sample_system_time_ns"]
    fill(source, current, 80)
    assert not source.history.items.get("state")
    with pytest.raises(IntegrityError, match="no_initial_state"):
        source.begin()
    diag = source.diagnostics()["topics"]["state"]
    assert diag["rejections"] == {"pre_subscription_header_discarded": 1}
    assert diag["callback_system_minus_header_ms"]["p50"] == 20
    current[0] = 140
    fill(source, current, 120)
    source.begin()
    assert source.aligner.initial_state_source["header_stamp"] == vars(stamp(120))
    # Once active, delayed old packets must fault rather than disappear.
    current[0] = 150
    source.append("state", np.zeros(6), vars(stamp(80)))
    with pytest.raises(IntegrityError, match="header_reversal"):
        source.health()


@pytest.mark.parametrize("clock_fault", ["sim", "offset", "jump"])
def test_startup_barrier_cannot_hide_clock_faults(clock_fault):
    source, current = source_at(100)
    source.subscription_started_system_ns = timing(90)["sample_system_time_ns"]
    t = timing(100)
    if clock_fault == "sim":
        t["receiver_clock"]["use_sim_time"] = True
    elif clock_fault == "offset":
        t["receiver_clock"]["ros_time_ns"] += 1_000_000_000
    else:
        fill(source, current, 95)
        source.begin()
        t["sample_system_time_ns"] += 1_000_000_000
        t["receiver_clock"]["ros_time_ns"] += 1_000_000_000
    source.append("state", np.zeros(6), vars(stamp(80)), t)
    assert source.history.faults
    diag = source.diagnostics()["topics"]["state"]
    assert "pre_subscription_header_discarded" not in diag["rejections"]


def test_callback_rates_delay_duplicates_gaps_are_bounded():
    source, current = source_at(100)
    for i in range(2100):
        current[0] = 100+i*50
        source.append("state", np.zeros(6), vars(stamp(current[0]-2)))
    diag = source.diagnostics()["topics"]["state"]
    assert diag["callback_fps"] == pytest.approx(20)
    assert diag["callback_system_minus_header_ms"]["p50"] == 2
    assert diag["callback_intervals_ms"]["retained_samples"] == 2048
    assert len(source.history.items["state"]) <= SyncOptions().history_size
    current[0] += 50
    source.append("state", np.zeros(6), vars(stamp(current[0]-52)))
    current[0] += 100
    source.append("state", np.zeros(6), vars(stamp(current[0]-2)))
    diag = source.diagnostics()["topics"]["state"]
    assert diag["duplicate_headers"] == 1
    assert diag["header_gaps_over_1_5_period"] == 1
    assert all(not isinstance(value, np.ndarray) for value in source.reception.topics["state"].values())


def test_unknown_gripper_remains_unknown_without_command_or_fake_state():
    source, current = source_at()
    fill(source, current, 90, gripper=False)
    with pytest.raises(IntegrityError, match="unknown_initial_gripper"):
        source.begin()
    assert source.diagnostics()["topics"]["gripper"]["command_state"] is None
    source.ingest("gripper", Message(data=1.))
    source.begin()
    assert source.diagnostics()["topics"]["gripper"]["command_state"] == 1


def test_ros_joint_order_rgb_padding_and_malformed_diagnostics():
    source, current = source_at()
    source.ingest("state", Message(header=Message(stamp=stamp(90)),
        name=list(reversed(contract.JOINT_NAMES)), position=[5,4,3,2,1,0]))
    assert source.history.items["state"][-1][0].tolist() == [0,1,2,3,4,5]
    image = np.zeros((480, 1924), dtype=np.uint8)
    image[:, :1920] = np.tile([1,2,3], 640)
    message = Message(header=Message(stamp=stamp(90)), height=480, width=640,
                      step=1924, encoding="bgr8", data=image.tobytes())
    source.ingest(contract.CAMERA_NAMES[0], message)
    assert source.history.items[contract.CAMERA_NAMES[0]][-1][0][0,0].tolist() == [3,2,1]
    message.width = 320
    source.ingest(contract.CAMERA_NAMES[0], message)
    assert source.diagnostics()["topics"][contract.CAMERA_NAMES[0]]["rejections"]["malformed_"+contract.CAMERA_NAMES[0]] == 1
    with pytest.raises(IntegrityError, match="malformed"):
        source.begin()
    json.dumps(source.diagnostics(), allow_nan=False)


def test_foreign_header_clock_is_not_reported_as_latency():
    source, current = source_at()
    t = timing(100)
    t["receiver_clock"]["ros_time_ns"] += 1_000_000_000
    source.append("state", np.zeros(6), vars(stamp(90)), t)
    diag = source.diagnostics()["topics"]["state"]
    assert diag["callback_system_minus_header_ms"]["retained_samples"] == 0
    assert diag["rejections"] == {"ros_system_clock_mismatch": 1}


def test_relative_device_header_is_not_presented_as_transport_latency():
    source, current = source_at()
    source.append("state", np.zeros(6), {"sec": 123, "nanosec": 0})
    diag = source.diagnostics()["topics"]["state"]
    assert diag["callback_system_minus_header_ms"]["retained_samples"] == 0
    assert diag["header_delay_unavailable"] == 1
    with pytest.raises(IntegrityError):
        source.begin()


def test_receipt_silence_reports_age_using_monotonic_only(monkeypatch):
    source, current = source_at()
    fill(source, current, 90)
    source.begin()
    monkeypatch.setattr("ai.serve.observations.time.monotonic_ns", lambda: timing(800)["sample_monotonic_ns"])
    assert source.diagnostics()["topics"]["state"]["last_receive_age_ms"] == 700
    current[0] = 800
    with pytest.raises(IntegrityError):
        source.sample(timing(740)["sample_monotonic_ns"])


@pytest.mark.parametrize("missing", [False, True])
def test_dry_run_logs_reception_and_distinguishes_unchecked_safety(tmp_path, missing):
    from ai.serve.dry_run import run_session, EventLog
    from ai.serve.observations import SyntheticSource
    from ai.serve.test.test_inference import server_thread, InferenceService, FakeBackend
    source = HistorySource() if missing else SyntheticSource()
    service = InferenceService(FakeBackend(), "model")
    log = EventLog(tmp_path/"events.jsonl", "readiness-test")
    try:
        with server_thread(service) as endpoint:
            result = run_session(source, endpoint, {"chunk_size":12, "n_action_steps":4,
                "checkpoint_id":"model", "checkpoint":"fake"}, log,
                duration=.4, startup_timeout=.15)
    finally:
        log.close()
    assert result["robot_commands_sent"] == 0
    assert result["safety_validation"]["evaluated"] is False
    assert result["safety_validation"]["violations"] is None
    if missing:
        assert result["status"] == "failed" and result["actions"] == 0
        assert result["reception_diagnostics"]["topics"]["gripper"]["command_state"] is None
        assert result["observation_validation"]["startup_attempt_errors"]["no_initial_state"] > 0
    else:
        assert result["status"] == "complete" and result["actions"] > 0
        assert result["observation_validation"]["valid_samples"] > 0
        assert result["reception_diagnostics"]["topics"]["state"]["callback_fps"] > 0
    rows = [json.loads(line) for line in (tmp_path/"events.jsonl").read_text().splitlines()]
    assert any(row["event"] == "reception_diagnostics" for row in rows)
