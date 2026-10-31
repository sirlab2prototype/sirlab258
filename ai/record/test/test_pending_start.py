"""Fixed-reference start tests: fake ROS history only; never initialize ROS."""
import json
import queue
import types

import numpy as np
import pytest

from ai.record.test.test_indy7_act_record import recorder, HistoryHarness, MONO, EPOCH

MS = 1_000_000


def advance(h, ms):
    while h.events and h.events[0][0] <= ms:
        receipt, name, header, value = h.events.pop(0)
        h.push(name, header, receipt, value)
    timing = h.timing(ms)
    return timing, h.history.snapshot(timing["sample_monotonic_ns"])


def request(h, ms=0):
    pending = h.m.PendingStart(h.options)
    timing, history = advance(h, ms)
    pending.request(timing, history)
    return pending


def poll(h, pending, ms):
    timing, history = advance(h, ms)
    return pending.poll(h.session, history, timing)


def test_cached_inputs_start_at_release_without_reanchoring(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.generate(1000)
    pending = request(h)
    assert poll(h, pending, 59) is None
    assert pending.active and h.session.state == "READY" and h.session.path is None
    assert not (tmp_path/"pending").exists()
    assert "RECORDING" in poll(h, pending, 60)
    assert not pending.active and h.session.frames == 0
    assert h.session.started_ns == h.session.aligner.start_mono == MONO
    assert h.session.aligner.start_system == EPOCH
    assert h.session.deadline.next_slot == 1
    assert h.session.deadline.next_ns == MONO+110*MS
    assert not list(h.session.path.glob("*.npz"))


@pytest.mark.parametrize("delay", [30, 35, 40])
def test_delayed_camera_headers_fix_wall_now_start_rejection(recorder, tmp_path, delay):
    h = HistoryHarness(recorder, tmp_path)
    h.history = recorder.BoundedHistory(h.options)
    h.sequences, h.sources = {}, {}
    h.push("camera_317222074298", -70, -30)
    h.push("camera_844212071939", -70, -29)
    h.push("state", -5, -3)
    h.push("gripper", None, -1, 0.)
    timing, history = advance(h, 0)
    with pytest.raises(recorder.IntegrityError, match="observation_too_old"):
        recorder.HistoryAligner.prepare(h.options, history, timing)
    h.generate(500)
    h.events = [(header+delay if name.startswith("camera_") else receipt, name, header, value)
                for receipt, name, header, value in h.events]
    h.events.sort(key=lambda event: event[0])
    pending = request(h)
    assert poll(h, pending, delay) is None
    assert "RECORDING" in poll(h, pending, 60)
    assert h.session.started_ns == MONO


def test_future_sensor_data_cannot_validate_past_reference(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    for name in ("camera_317222074298", "camera_844212071939", "state"):
        h.history.items[name].clear()
    pending = request(h)
    # All available Headers are after t0, although their callbacks are before release.
    for index, name in enumerate(("camera_317222074298", "camera_844212071939", "state")):
        h.push(name, 20, 55+index)
    message = poll(h, pending, 60)
    assert "no_initial_state" in message
    assert not pending.active and h.session.state == "READY" and h.session.path is None


def test_future_camera_alone_cannot_replace_missing_past_frame(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    camera = "camera_317222074298"
    h.history.items[camera].clear()
    pending = request(h)
    h.push(camera, 20, 55)
    assert "missing_causal_observation" in poll(h, pending, 60)
    assert h.session.state == "READY" and h.session.path is None


def test_camera_skew_above_original_40ms_limit_still_rejected(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.history = recorder.BoundedHistory(h.options)
    h.sequences, h.sources = {}, {}
    h.push("camera_317222074298", -45.057, -9)
    h.push("state", -5, -3)
    h.push("gripper", None, -1, 0.)
    pending = request(h)
    h.events.append((36, "camera_844212071939", 0, None))
    assert "camera_skew" in poll(h, pending, 60)
    assert h.session.state == "READY" and h.session.path is None


def test_missing_observation_fails_once_without_searching_a_new_epoch(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.history.items["camera_317222074298"].clear()
    pending = request(h)
    assert "missing_causal_observation" in poll(h, pending, 60)
    h.push("camera_317222074298", 70, 90)
    assert poll(h, pending, 100) is None  # No automatic later-reference retry.
    assert h.session.state == "READY" and h.session.path is None
    assert not h.opens


def test_receive_processing_deadline_exceeded_returns_ready(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.generate(500)
    pending = request(h)
    assert "start_receive_deadline_exceeded" in poll(h, pending, 110)
    assert h.session.state == "READY" and not pending.active
    assert not (tmp_path/"pending").exists()


def test_poll_jitter_before_first_recording_release_does_not_shift_grid(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.generate(500)
    pending = request(h)
    assert "RECORDING" in poll(h, pending, 75)
    assert h.session.started_ns == MONO and h.session.deadline.next_ns == MONO+110*MS
    h.tick(50)
    assert h.row(0)["schedule"]["slot"] == 1 and h.session.deadline.missed == 0


def test_wait_does_not_retry_data_arriving_after_validation(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.history.items["camera_317222074298"].clear()
    pending = request(h)
    assert "missing_causal_observation" in poll(h, pending, 60)
    h.push("camera_317222074298", 0, 65)
    assert poll(h, pending, 70) is None and h.session.state == "READY"


def test_first_frame_and_all_grid_indices_match_original_contract(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.generate(1200)
    pending = request(h)
    poll(h, pending, 60)
    for index, reference in enumerate(range(50, 1001, 50)):
        h.tick(reference)
        row = h.row(index)
        assert row["frame_index"] == index and row["schedule"]["slot"] == index+1
        assert row["alignment"]["reference_monotonic_ns"] == MONO+reference*MS
        assert row["sample_monotonic_ns"] == MONO+(reference+60)*MS
        assert row["dataset_nominal_timestamp_s"] == index/20
        for name in ("camera_317222074298", "camera_844212071939", "state"):
            assert recorder._header_ns(row["sources"][name]) <= EPOCH+reference*MS
    assert h.session.frames == 20 and h.session.deadline.missed == 0
    assert h.session.stale_sample_skips == 0 and h.session.integrity["valid"]


def test_action_and_gripper_events_during_wait_keep_original_causality(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.push("action", -.5, -.25, np.full(6, 4, dtype=np.float32))  # Old cached target.
    h.generate(500)
    h.events += [(30, "action", 20, np.full(6, 2, dtype=np.float32)),
                 (40, "action", -5, np.full(6, 9, dtype=np.float32)),
                 (30.5, "gripper", None, 1.)]
    h.events.sort(key=lambda item: item[0])
    pending = request(h)
    assert "RECORDING" in poll(h, pending, 60)
    assert h.session.aligner.gripper[0] == 0  # Event at30.5ms cannot describe t0.
    h.tick(50)
    with np.load(h.session.path/"00000000.npz", allow_pickle=False) as data:
        assert data["action"][:6].tolist() == [2]*6
        assert data["action"][6] == data["observation.state"][6] == 1
    assert h.row(0)["recorded_action"]["kind"] == "qp_target"


def test_gripper_first_received_after_start_is_not_backdated(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path, grip=False)
    h.generate(500)
    h.events.append((20, "gripper", None, 0.))
    h.events.sort(key=lambda item: item[0])
    pending = request(h)
    assert "unknown_initial_gripper" in poll(h, pending, 60)
    assert h.session.path is None and h.session.state == "READY"


def test_recording_integrity_remains_fail_fast(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.generate(500, camera_end=0)
    pending = request(h)
    poll(h, pending, 60)
    h.tick(50)  # Header0 age50 is exactly the allowed limit.
    assert h.session.frames == 1
    path = h.session.path
    h.tick(100)
    assert h.session.state == "REVIEW" and not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == "observation_too_old"
    assert h.session.path == path and not (path/"00000001.npz").exists()
    for outcome in ("success", "correction"):
        assert "INVALID" in h.session.save(outcome, tmp_path/outcome)
    assert not h.opens


@pytest.mark.parametrize("fault", ["clock", "overflow"])
def test_wait_cannot_hide_clock_fault_or_history_overflow(recorder, tmp_path, fault):
    h = HistoryHarness(recorder, tmp_path)
    h.generate(500)
    pending = request(h)
    timing, history = advance(h, 60)
    if fault == "clock":
        timing["sample_system_time_ns"] += 100*MS
        timing["receiver_clock"]["ros_time_ns"] += 100*MS
    else:
        history["fault_overflows"] += 1
    message = pending.poll(h.session, history, timing)
    assert ("system_clock_jump_or_drift" if fault == "clock" else "history_fault_overflow") in message
    assert not pending.active and h.session.path is None


def test_repeat_start_and_cancel_preserve_original_reference(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    pending = request(h)
    timing, history = advance(h, 20)
    assert "유지" in pending.request(timing, history)
    assert pending.release_ns == MONO+60*MS
    assert "취소" in pending.cancel("user_stop")
    assert poll(h, pending, 60) is None and h.session.path is None


@pytest.mark.parametrize("command", ["r", "d", "q", "__eof__", "interrupt"])
def test_main_keeps_callbacks_running_and_start_can_be_cancelled(recorder, tmp_path, monkeypatch, command):
    h = HistoryHarness(recorder, tmp_path/"harness")
    h.generate(500)
    ms, calls = [0.], []
    commands = queue.Queue()
    node = types.SimpleNamespace(history_snapshot=lambda: advance(h, ms[0]), destroy_node=lambda: calls.append("destroy"))
    def spin(*args, **kwargs):
        ms[0] += 5
        calls.append("spin")
        advance(h, ms[0])
        assert ms[0] < 150
        if len([v for v in calls if v == "spin"]) == 1:
            commands.put("s")
        elif ms[0] >= 25:
            if command == "interrupt":
                raise KeyboardInterrupt
            commands.put(command)
            if command in ("r", "d"): commands.put("q")
    monkeypatch.setattr(recorder,"Indy7Observations",lambda *args:node)
    monkeypatch.setattr(recorder,"_commands",lambda:commands)
    monkeypatch.setattr(recorder.time,"monotonic_ns",lambda:MONO+round(ms[0]*MS))
    monkeypatch.setattr(recorder.time,"sleep",lambda seconds:ms.__setitem__(0,ms[0]+seconds*1000))
    monkeypatch.setattr(recorder.rclpy,"init",lambda:None,raising=False)
    monkeypatch.setattr(recorder.rclpy,"ok",lambda:True,raising=False)
    monkeypatch.setattr(recorder.rclpy,"spin_once",spin,raising=False)
    monkeypatch.setattr(recorder.rclpy,"shutdown",lambda:calls.append("shutdown"),raising=False)
    monkeypatch.setattr(recorder.sys,"argv",["act-record","--root",str(tmp_path/"success"),
        "--failure-root",str(tmp_path/"failure"),"--correction-root",str(tmp_path/"correction"),"--pending-root",str(tmp_path/"pending")])
    assert recorder.main() == 0
    assert calls.count("spin") >= 2 and "shutdown" in calls
    assert not any((tmp_path/name).exists() for name in ("success", "failure", "pending"))
