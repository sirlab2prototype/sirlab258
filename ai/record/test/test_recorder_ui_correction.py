"""UI, receipt clock, additive v1 compatibility and correction crash recovery.

All Datasets are synthetic and isolated by tmp_path. ROS is stubbed, never initialized.
"""
from copy import deepcopy
import io
import json
import queue
import shutil
import types

import numpy as np
import pytest

from ai.record import collection as c, terminal_ui as ui
from ai.record.correction_marker import validate
from ai.record.test.test_collection import managed, take
from ai.record.test.test_indy7_act_record import recorder, frame, HistoryHarness, MONO, EPOCH
from ai.record.test.test_pending_start import request, poll


def test_initial_ui_exact_and_no_settings_or_guidance(managed):
    _, ledger, _ = managed
    assert ui.startup(ledger) == """========== INDY7 ACT RECORDER ==========

[수집 현황]
정상 시연 : 0 / 108
교정 시연 : 0
남은 조건 : 108

========== [현재 수집 조건] ==========

수집 순서 : 001 / 108
조건 ID   : 108
시도      : 1회차

바구니 위치 : 오른쪽 (R)
바구니 회전 : 반시계방향 많이 (A6)
바구니 깊이 : 자유 배치 (Random)

[현재 상태] 녹화 대기

바구니 배치 후 s
====================================="""
    for unwanted in ("ready", "help", "guide", "progress", "20 FPS", "수집 설정", "다음 작업", "회전 기준", "LeRobot", ".venv"):
        assert unwanted not in ui.startup(ledger)
    assert "시계방향(-): 바구니 왼쪽 끝이 벽 쪽, 오른쪽 끝이 입구 쪽" in ui.guide()
    assert "반시계방향(+): 바구니 오른쪽 끝이 벽 쪽, 왼쪽 끝이 입구 쪽" in ui.guide()
    assert "x : 녹화 중 교정 시연 전환" in ui.help_text() and "ready" not in ui.help_text()


def test_s_prepare_preserves_null_coordinates_and_failed_start(managed):
    module, ledger, session = managed
    assert ledger.command("ready") is None and module._normalize_command("ready") is None
    ledger.command("placement actual_x=12.5 actual_yaw=null")
    a = ledger.prepare()  # Main calls this from s before capturing t0.
    assert a["status"] == "PREPARED" and a["attempt_number"] == 1
    assert a["placement"]["actual_x"] == 12.5
    assert all(a["placement"][k] is None for k in c.POSE_FIELDS if k != "actual_x")
    h = HistoryHarness(module, ledger.directory / "harness")
    h.session = session
    for name in ("camera_317222074298", "camera_844212071939", "state"):
        h.history.items[name].clear()
    pending = request(h)
    message = poll(h, pending, 60)
    assert "통과" not in message and "no_initial_state" in message
    assert session.path is None and session.state == "READY" and not ledger.completed()
    assert ledger.prepare()["attempt_id"] == a["attempt_id"]
    assert len(ledger.state["attempts"]) == 1


def test_line_receipt_timestamp_and_command_aliases(recorder, monkeypatch):
    monkeypatch.setattr(recorder.sys, "stdin", io.StringIO("x\n"))
    before = recorder.time.monotonic_ns()
    command = recorder._commands().get(timeout=2)
    after = recorder.time.monotonic_ns()
    assert command == "x" and before <= command.received_monotonic_ns <= after
    assert type(command.received_system_time_ns) is int
    assert recorder._normalize_command(command) == "correction"
    assert recorder._normalize_command("failure") is None
    assert recorder._normalize_command("f") is None


def test_correction_boundary_uses_reference_not_receipt_margin_or_frame_count(recorder, tmp_path, monkeypatch):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(700)
    h.tick(50)  # reference=50ms, receipt/sample=110ms, index=0
    stopped_before = (h.session.started_ns, h.session.deadline.started_ns, h.session.frames)
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: MONO+180_000_000)
    text = h.session.mark_correction(recorder.CommandInput("x", MONO+125_000_000, EPOCH+125_000_000))
    assert "대응 대기" in text and h.session.state == "RECORDING"
    assert (h.session.started_ns, h.session.deadline.started_ns, h.session.frames) == stopped_before
    h.tick(100)  # second frame's sample is AFTER input, reference is BEFORE input.
    assert h.session.correction["start_frame_index"] is None
    h.tick(150)
    marker = deepcopy(h.session.correction)
    assert marker["start_frame_index"] == 2 and marker["start_dataset_timestamp_s"] == .1
    assert marker["start_reference_monotonic_ns"] == MONO+150_000_000
    assert marker["start_reference_system_time_ns"] == EPOCH+150_000_000
    assert marker["input_received_monotonic_ns"] == MONO+125_000_000
    assert marker["command_processed_monotonic_ns"] == MONO+180_000_000
    assert marker["boundary_source"] == "stdin_line_received"
    assert "유지" in h.session.mark_correction(recorder.CommandInput("x", MONO+175_000_000))
    assert h.session.correction == marker
    assert h.session.frames == 3 and h.session.integrity["valid"]


def test_delayed_queue_processing_can_match_already_recorded_reference(recorder, tmp_path, monkeypatch):
    h = HistoryHarness(recorder, tmp_path)
    h.begin()
    h.generate(500)
    h.tick(50)
    h.tick(100)
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: MONO+180_000_000)
    h.session.mark_correction(recorder.CommandInput("x", MONO+75_000_000, EPOCH+75_000_000))
    assert h.session.correction["start_frame_index"] == 1
    assert h.session.correction["start_dataset_timestamp_s"] == .05
    assert h.row(1)["dataset_nominal_timestamp_s"] == .05  # Existing timing grid is unchanged.


def test_correction_refuses_wrong_state_and_empty_segment(managed):
    module, ledger, session = managed
    assert "거부" in session.mark_correction()
    ledger.prepare()
    session.start(np.zeros(6), 10)
    session.add_frame(frame(module))
    session.add_frame(frame(module))
    session.mark_correction()  # No future recording reference after this input.
    session.stop()
    assert "거부" in session.mark_correction()
    pending = session.path
    assert "저장 거부" in session.save("correction", ledger.roots["correction"])
    assert "저장 거부" in session.save("success", ledger.roots["success"])
    assert pending.exists() and not any(root.exists() for root in ledger.roots.values())


def test_success_correction_ui_and_progress_distinct_from_episode_index(managed):
    _, ledger, session = managed
    take(managed)
    assert "유형    : 일반 시연" in ui.review(session)
    text = session.save("success", ledger.roots["success"])
    assert "Dataset : Success\nEpisode : 0" in text and "정상 시연 : 1 / 108" in text
    assert "========== [다음 수집 조건] ==========" in text and "조건 ID   : 037" in text
    assert "guide" not in text and "회전 기준" not in text and "ready" not in text
    take(managed, correction=True)
    assert "유형    : 교정 시연" in ui.review(session)
    text = session.save("correction", ledger.roots["correction"])
    assert "Dataset : Correction\nEpisode : 0" in text and "교정 시연 : 1" in text
    assert "========== [재수집 조건] ==========" in text and "시도      : 2회차" in text
    assert ledger.completed() == {108} and ledger.condition()["condition_id"] == 37
    assert ledger.state["attempts"][-1]["status"] == "CORRECTION"
    assert not ledger.roots["failure"].exists()
    assert "교정 저장" in ledger.command("history 037")
    assert '"start_frame_index": 0' in ledger.command("history 037 detail")
    assert "회전 기준" not in ledger.command("progress all")
    assert sum(" | " in line for line in ledger.command("progress all").splitlines()) == 109
    ledger.prepare()
    assert "폐기 완료" in session.discard()
    assert "시도      : 3회차" in ledger.display()
    assert "폐기 대상 없음" in session.discard()
    assert "폐기 시도 : 1" in ledger.progress()


def legacy_failure(parts):
    """Build historical v1 bytes in tmp_path; production never writes failure."""
    _, ledger, session = parts
    take(parts)
    session.save("success", ledger.roots["success"])
    old, new = ledger.roots["success"], ledger.roots["failure"]
    shutil.move(old, new)
    manifest = new / "meta/recorder_timing/episode-000000/episode.json"
    meta = json.loads(manifest.read_text())
    meta.pop("demonstration")
    meta.update(outcome="failure", dataset_root=str(new))
    manifest.write_text(json.dumps(meta))
    attempt = ledger.state["attempts"][0]
    attempt.pop("demonstration_type")
    attempt.pop("correction")
    attempt.update(status="FAILURE", dataset_kind="failure", final_success=False)
    ledger.state["selected_condition_id"] = attempt["condition_id"]
    ledger.selected = attempt["condition_id"]
    c.durable_json(ledger.receipt_path(attempt), ledger._evidence(attempt, c.dataset_inventory(new)))
    ledger._persist()


def test_historical_v1_failure_receipts_stay_byte_identical_during_new_correction(managed):
    _, ledger, session = managed
    legacy_failure(managed)
    root = ledger.roots["failure"]
    before = c.tree_fingerprint(root)
    receipt = ledger.receipt_path(ledger.state["attempts"][0])
    receipt_before = receipt.read_bytes()
    ledger.close()
    reopened = c.Collection(ledger.directory)
    try:
        assert reopened.stats() == {"success": 0, "correction": 0, "discard": 0, "failure": 1}
        session.collection = reopened
        take((managed[0], reopened, session), correction=True)
        assert "저장 완료" in session.save("correction", reopened.roots["correction"])
        assert reopened.completed() == set()
        assert root.exists() and c.tree_fingerprint(root) == before and receipt.read_bytes() == receipt_before
        assert "과거 실패 저장" in reopened.history(108)
        assert reopened.stats()["correction"] == 1
        assert "저장 거부" in session.save("failure", root)
    finally:
        reopened.close()


def test_correction_commit_recovers_journal_failure_without_replay(managed, monkeypatch):
    _, ledger, session = managed
    take(managed, correction=True)
    writer = c.durable_json

    def fail_finish(path, value):
        if path == ledger.path and value["attempts"][-1]["status"] == "CORRECTION":
            raise OSError("injected correction ledger commit failure")
        writer(path, value)

    monkeypatch.setattr(c, "durable_json", fail_finish)
    assert "SAVE FAILED" in session.save("correction", ledger.roots["correction"])
    assert ledger.stats()["correction"] == 0 and ledger.current["status"] == "SAVING"
    root = ledger.roots["correction"]
    before = c.tree_fingerprint(root)
    ledger.close()
    monkeypatch.setattr(c, "durable_json", writer)
    with pytest.raises(c.CollectionError, match="Unresolved save"):
        c.Collection(ledger.directory)
    recovered = c.Collection(ledger.directory, recovery=True)
    try:
        recovered.reconcile()
        recovered.reconcile()
        assert recovered.stats()["correction"] == 1 and not recovered.completed()
        assert c.tree_fingerprint(root) == before and len(c.dataset_inventory(root)) == 1
        assert recovered.condition_view()["attempt_number"] == 2
        assert session.path.exists()
        managed[0]._check_pending(ledger.pending_root, {str(root)}, recovered)
    finally:
        recovered.close()


def test_correction_partial_video_save_stays_blocked_and_pending_survives(managed, monkeypatch):
    import lerobot.datasets.lerobot_dataset as upstream
    _, ledger, session = managed
    take(managed, correction=True)
    encode = upstream._encode_video_worker

    def fail_second(key, *args, **kwargs):
        if "844212071939" in key:
            raise OSError("injected partial correction video")
        return encode(key, *args, **kwargs)

    monkeypatch.setattr(upstream, "_encode_video_worker", fail_second)
    message = session.save("correction", ledger.roots["correction"])
    assert "SAVE FAILED" in message and "reconcile --collection-dir" in message and str(session.path) in message
    before = c.tree_fingerprint(ledger.roots["correction"])
    with pytest.raises((ValueError, OSError)):
        ledger.reconcile()
    assert session.path.exists() and ledger.stats()["correction"] == 0
    assert c.tree_fingerprint(ledger.roots["correction"]) == before
    assert "폐기 차단" in session.discard()


def test_invalid_shows_measured_limits_without_invented_values_and_blocks_both(managed):
    module, ledger, session = managed
    ledger.prepare()
    session.start(np.zeros(6), 10)
    session.mark_correction()
    session.add_frame(frame(module))
    session.invalidate("observation_too_old", details={"topic": "state", "age_ns": 112_000_000, "limit_ns": 75_000_000})
    text = ui.invalid(session)
    assert "대상      : robot_state" in text and "허용 지연 : 75ms" in text and "실제 지연 : 112ms" in text
    for unwanted in ("Success / Correction 저장이 차단", "원본 Pending 데이터는 보존"):
        assert unwanted not in text
    for kind in ("success", "correction"):
        assert "INVALID" in session.save(kind, ledger.roots[kind])
    pending = session.path
    assert session.request_quit()[0] and pending.exists()
    assert not any(root.exists() for root in ledger.roots.values())
    session.integrity["violations"][-1]["details"] = {}
    assert "허용 지연" not in ui.invalid(session) and "실제 지연" not in ui.invalid(session)


@pytest.mark.parametrize("managed_mode", [False, True])
def test_main_s_x_r_o_routes_only_correction_and_preserves_fixed_start(recorder, tmp_path, monkeypatch, managed_mode, capsys):
    h = HistoryHarness(recorder, tmp_path / "harness")
    h.generate(900)
    virtual_ms = [0.]
    commands = queue.Queue()
    queued = set()
    node = types.SimpleNamespace(
        history_snapshot=lambda: (h.timing(virtual_ms[0]), h.history.snapshot(MONO+round(virtual_ms[0]*1e6))),
        destroy_node=lambda: None)

    def spin(*args, **kwargs):
        virtual_ms[0] += 5
        assert virtual_ms[0] < 1500
        while h.events and h.events[0][0] <= virtual_ms[0]:
            receipt, name, header, value = h.events.pop(0)
            h.push(name, header, receipt, value)
        for when, names in ((5, ("s",)), (205, ("x",)), (400, ("r", "o", "q"))):
            if virtual_ms[0] >= when and when not in queued:
                for name in names:
                    commands.put(recorder.CommandInput(name, MONO+when*1_000_000, EPOCH+when*1_000_000))
                queued.add(when)

    monkeypatch.setattr(recorder, "Indy7Observations", lambda *args: node)
    monkeypatch.setattr(recorder, "_commands", lambda: commands)
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: MONO+round(virtual_ms[0]*1e6))
    monkeypatch.setattr(recorder.time, "sleep", lambda secs: virtual_ms.__setitem__(0, virtual_ms[0]+secs*1000))
    for name, function in (("init", lambda: None), ("ok", lambda: True), ("spin_once", spin), ("shutdown", lambda: None)):
        monkeypatch.setattr(recorder.rclpy, name, function, raising=False)
    directory = tmp_path / "formal"
    args = ["act-record", "--collection-dir", str(directory)] if managed_mode else ["act-record",
            "--root", str(tmp_path/"success"), "--correction-root", str(tmp_path/"correction"),
            "--failure-root", str(tmp_path/"failure"), "--pending-root", str(tmp_path/"pending")]
    monkeypatch.setattr(recorder.sys, "argv", args)
    assert recorder.main() == 0
    root = directory / "correction" if managed_mode else tmp_path / "correction"
    meta = json.loads((root / "meta/recorder_timing/episode-000000/episode.json").read_text())
    assert meta["start_monotonic_ns"] == MONO+5_000_000 and meta["sync_options"]["receive_wait_ms"] == 60
    assert meta["outcome"] == "correction"
    assert meta["demonstration"]["correction"]["start_frame_index"] == 3
    assert meta["demonstration"]["correction"]["start_dataset_timestamp_s"] == .15
    assert not (root.parent / "failure").exists() and not (root.parent / "success").exists()
    text = capsys.readouterr().out
    assert text.index("[시작 검사] 관측 데이터 확인 중...") < text.index("[시작 검사] 통과") < text.index("[녹화 시작]")
    assert "[교정 시연 전환]" in text and "[녹화 종료]" in text and "Dataset : Correction" in text
    assert "ready" not in text and "20 FPS" not in text and "Bounded history" not in text
    if managed_mode:
        reopened = c.Collection(directory)
        try:
            assert reopened.stats()["success"] == 0 and reopened.stats()["correction"] == 1
        finally:
            reopened.close()


def test_discard_delete_error_does_not_claim_complete_or_increment_counter(managed, monkeypatch):
    _, ledger, session = managed
    take(managed)
    pending = session.path

    def denied(path):
        raise OSError("injected discard delete failure")

    monkeypatch.setattr(shutil, "rmtree", denied)
    with pytest.raises(OSError, match="discard delete failure"):
        session.discard()
    assert pending.exists() and ledger.stats()["discard"] == 0
    assert ledger.state["attempts"][-1]["discard_completed"] is False
    assert "폐기 미확인" in ledger.history(108)


def test_invalid_discard_then_quit_is_normal_and_history_keeps_invalid(managed):
    _, ledger, session = managed
    take(managed)
    session.state = "RECORDING"
    session.invalidate("camera_skew")
    assert "폐기 완료" in session.discard()
    assert ledger.state["attempts"][-1]["status"] == "INVALID"
    assert ledger.stats()["discard"] == 1
    ok, message = session.request_quit()
    assert ok and "수집 기록 보존 완료" in message and "녹화 오류" not in message


def test_normal_exit_never_claims_preserved_if_journal_flush_fails(managed, monkeypatch):
    _, ledger, session = managed

    def unavailable():
        raise OSError("journal flush failed")

    monkeypatch.setattr(ledger, "_persist", unavailable)
    with pytest.raises(OSError, match="journal flush failed"):
        session.request_quit()


def test_correction_interruption_retains_marker_and_raw_without_completing_condition(managed):
    module, ledger, session = managed
    ledger.prepare()
    session.start(np.zeros(6), 10)
    session.mark_correction()
    session.add_frame(frame(module))
    session.add_frame(frame(module))
    expected = deepcopy(session.correction)
    assert expected["start_frame_index"] == 0
    pending = session.path
    ledger.close()
    recovered = c.Collection(ledger.directory)
    try:
        a = recovered.state["attempts"][-1]
        assert a["status"] == "INTERRUPTED" and a["demonstration_type"] == "correction"
        assert a["correction"] == expected and pending.exists()
        assert recovered.stats()["correction"] == 0 and not recovered.completed()
        recovered.prepare()
        assert recovered.current["attempt_number"] == 2 and recovered.current["demonstration_type"] == "normal"
    finally:
        recovered.close()


def test_marker_cannot_be_shifted_to_later_frame_before_save(managed):
    _, ledger, session = managed
    take(managed, correction=True)
    rows = [json.loads((session.path / f"{i:08d}.timing.json").read_text()) for i in range(session.frames)]
    marker = session.correction
    from ai.record.correction_marker import reference
    marker.update(start_frame_index=1, start_dataset_timestamp_s=.05,
                  start_reference_monotonic_ns=reference(rows[1])[0], start_reference_system_time_ns=reference(rows[1])[1])
    text = session.save("correction", ledger.roots["correction"])
    assert "저장 거부" in text and "first_causal_frame" in text
    assert not ledger.roots["correction"].exists()


def test_old_failure_pending_commit_can_reconcile_without_new_episode(managed):
    _, ledger, _ = managed
    legacy_failure(managed)
    a = ledger.state["attempts"][0]
    a.update(status="SAVING", baseline=None)
    ledger._persist()
    before = c.tree_fingerprint(ledger.roots["failure"])
    ledger.reconcile()
    assert ledger.stats()["failure"] == 1 and not ledger.completed()
    assert c.tree_fingerprint(ledger.roots["failure"]) == before
    assert len(c.dataset_inventory(ledger.roots["failure"])) == 1


def test_every_condition_uses_original_labels_and_progress_all_does_not_repeat_guides(managed):
    _, ledger, _ = managed
    snapshot = ledger.path.read_bytes()
    for row in c.load_plan():
        ledger.selected = row["condition_id"]
        view = ledger.condition_view()
        text = ledger.display()
        assert f"조건 ID   : {row['condition_id']:03d}" in text
        assert view["position"] == f"{c.REGION_LABELS[row['position_region']]} ({row['position_region']})"
        assert view["yaw"] == f"{c.YAW_LABELS[row['yaw_bin']]} ({row['yaw_bin']})"
        assert view["depth"] == f"{c.DEPTH_LABELS[row['depth_coverage']]} ({row['depth_coverage']})"
    assert ledger.path.read_bytes() == snapshot
    all_rows = ledger.command("progress all")
    assert "회전 기준" not in all_rows and "현재 수집 조건" not in all_rows


def test_runtime_codec_info_hidden_but_warning_kept(recorder, capfd):
    import av
    recorder.configure_terminal_logging()
    av.logging.restore_default_callback()  # The normal upstream encoding completion path.
    av.logging.log(av.logging.INFO, "ui-test", "normal-codec-detail\n")
    av.logging.log(av.logging.WARNING, "ui-test", "important-codec-warning\n")
    output = capfd.readouterr()
    assert "normal-codec-detail" not in output.err
    assert "important-codec-warning" in output.err
