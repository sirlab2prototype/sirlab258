"""Core-108 tests: local synthetic Datasets and fault injection, no ROS/robot."""
from collections import Counter
import json
from pathlib import Path

import numpy as np
import pytest

from ai.record import collection as c
from ai.record.test.test_indy7_act_record import recorder, frame


@pytest.fixture
def managed(recorder, tmp_path):
    ledger = c.Collection(tmp_path / "formal")

    def factory(outcome):
        root = ledger.roots[outcome]
        if root.exists():
            return recorder.LeRobotDataset(repo_id=f"local/{outcome}", root=root, vcodec="h264")
        return recorder.LeRobotDataset.create(repo_id=f"local/{outcome}", root=root, robot_type="indy7",
            fps=20, features=recorder._features(), use_videos=True, image_writer_threads=0, vcodec="h264")

    session = recorder.EpisodeSession(ledger.pending_root, factory, 20, "synthetic basket", collection=ledger)
    yield recorder, ledger, session
    ledger.close()


def take(parts, value=1, correction=False):
    module, ledger, session = parts
    ledger.prepare()
    session.start(np.zeros(6), 10)
    if correction:
        session.mark_correction()
    session.add_frame(frame(module, value))
    session.add_frame(frame(module, value))
    session.stop()


def test_plan_is_fixed_unique_and_balanced(tmp_path):
    rows = c.load_plan()
    assert len(rows) == 108
    assert [r["condition_id"] for r in rows[:6]] == [108, 37, 19, 96, 76, 13]
    assert rows[-1]["condition_id"] == 58
    assert Counter(r["position_region"] for r in rows) == {"L": 36, "C": 36, "R": 36}
    assert set(Counter((r["position_region"], r["yaw_bin"]) for r in rows).values()) == {6}
    assert Counter(r["depth_coverage"] for r in rows) == {depth: 18 for depth in c.DEPTHS}
    text = c.PLAN.read_text()
    for damaged in (text.replace("| 037 | 002", "| 108 | 002"),
                    text.replace("| 037 | 002", "| 037 | 001"),
                    text.replace("| C | A1 | Front |", "| C | A1 | Back |")):
        path = tmp_path / "bad.md"
        path.write_text(damaged)
        with pytest.raises(c.CollectionError):
            c.load_plan(path)


def test_manual_selection_placement_and_no_fabrication(managed):
    _, ledger, _ = managed
    ledger.command("condition 037")
    ledger.command('placement target_x=42 actual_x=43.2 actual_y=null actual_yaw=-30 notes="Measured A" coordinate_frame="Shelf origin"')
    ledger.prepare()
    a = ledger.context()
    assert a["condition_id"] == 37 and a["collection_order"] == 2
    assert a["placement"]["target_x"] == 42 and a["placement"]["actual_x"] == 43.2
    assert a["placement"]["actual_y"] is None and a["placement"]["target_yaw"] is None
    assert a["placement"]["notes"] == "Measured A"
    with pytest.raises(c.CollectionError, match="frozen"):
        ledger.command("placement actual_x=50")
    with pytest.raises(c.CollectionError):
        ledger.command("condition 108")
    with pytest.raises(c.CollectionError):
        ledger.command("result grasp_success=true")


def test_correction_then_success_episode_links_resume_duplicate_protection(managed):
    module, ledger, session = managed
    take(managed, correction=True)
    ledger.command('result failure_reason="Handle missed" demo_quality="retry" grasp_success=false')
    assert "저장 완료" in session.save("correction", ledger.roots["correction"])
    assert not ledger.completed()
    assert ledger.condition()["condition_id"] == 108
    take(managed, 2)
    ledger.command('result demo_quality="good" grasp_success=true place_success=true')
    assert "저장 완료" in session.save("success", ledger.roots["success"])
    assert ledger.completed() == {108}
    assert [(a["condition_id"], a["attempt_number"], a["dataset_kind"], a["dataset_episode_index"])
            for a in ledger.state["attempts"]] == [(108, 1, "correction", 0), (108, 2, "success", 0)]
    with pytest.raises(c.CollectionError, match="completed"):
        ledger.select(108)
    original = c.load_plan()
    directory = ledger.directory
    ledger.close()
    reopened = c.Collection(directory)
    try:
        assert reopened.state["conditions"] == original
        assert reopened.condition()["condition_id"] == 37
        assert "정상 시연 : 1 / 108" in reopened.progress()
        assert "교정 시연 : 1" in reopened.progress()
        assert "Handle missed" in reopened.command("history 108")
    finally:
        reopened.close()


def test_condition_id_is_not_dataset_index(managed):
    _, ledger, session = managed
    take(managed)
    session.save("success", ledger.roots["success"])
    take(managed, correction=True)
    session.save("correction", ledger.roots["correction"])
    take(managed)
    session.save("success", ledger.roots["success"])
    assert [(a["condition_id"], a["dataset_episode_index"], a["dataset_kind"])
            for a in ledger.state["attempts"]] == [(108, 0, "success"), (37, 0, "correction"), (37, 1, "success")]


def test_discard_invalid_preserve_policy(managed):
    module, ledger, session = managed
    take(managed)
    assert "폐기 완료" in session.discard()
    assert ledger.state["attempts"][0]["status"] == "DISCARD"
    take(managed)
    # Invalidate during recording, as live record_aligned does.
    session.state = "RECORDING"
    session.invalidate("camera_skew")
    pending = session.path
    assert ledger.state["attempts"][-1]["status"] == "INVALID"
    assert "INVALID" in session.save("success", ledger.roots["success"])
    assert "INVALID" in session.save("correction", ledger.roots["correction"])
    assert session.request_quit()[0] and pending.exists()
    assert not any(root.exists() for root in ledger.roots.values())
    assert not ledger.completed()
    session.discard()
    assert ledger.state["attempts"][-1]["status"] == "INVALID"
    assert ledger.state["attempts"][-1]["pending_discarded"]
    ledger.prepare()
    assert ledger.current["attempt_number"] == 3


def test_restart_preserves_interrupted_raw_and_advances_attempt(managed):
    _, ledger, session = managed
    take(managed)
    pending = session.path
    ledger.close()
    reopened = c.Collection(ledger.directory)
    try:
        assert pending.exists()
        assert reopened.state["attempts"][-1]["status"] == "INTERRUPTED"
        reopened.prepare()
        assert reopened.current["attempt_number"] == 2
    finally:
        reopened.close()


def test_completed_dataset_but_ledger_write_failed_reconciles_without_replay(managed, monkeypatch):
    _, ledger, session = managed
    take(managed)
    write = c.durable_json

    def fail_commit(path, value):
        if path == ledger.path and value["attempts"][-1]["status"] == "SUCCESS":
            raise OSError("injected ledger failure after Dataset commit")
        return write(path, value)

    monkeypatch.setattr(c, "durable_json", fail_commit)
    assert "SAVE FAILED" in session.save("success", ledger.roots["success"])
    assert session.save_failed and session.path.exists()
    root = ledger.roots["success"]
    before = c.tree_fingerprint(root)
    directory = ledger.directory
    ledger.close()
    monkeypatch.setattr(c, "durable_json", write)
    with pytest.raises(c.CollectionError, match="Unresolved save"):
        c.Collection(directory)
    recovered = c.Collection(directory, recovery=True)
    try:
        recovered.reconcile()
        assert recovered.completed() == {108}
        assert c.tree_fingerprint(root) == before
        assert len(c.dataset_inventory(root)) == 1
        recovered.reconcile()  # Idempotent, no duplicate Episode.
    finally:
        recovered.close()


def test_save_exception_before_any_dataset_write_can_be_released(managed):
    _, ledger, session = managed
    take(managed)

    def unavailable(outcome):
        raise OSError("factory unavailable")

    session.dataset_factory = unavailable
    assert "SAVE FAILED" in session.save("success", ledger.roots["success"])
    assert "차단" in session.discard().lower()
    ledger.reconcile()
    assert ledger.state["attempts"][-1]["status"] == "INTERRUPTED"
    assert session.path.exists() and not ledger.completed()


def test_partial_video_save_blocks_recovery_without_changing_old_data(managed, monkeypatch):
    import lerobot.datasets.lerobot_dataset as upstream
    _, ledger, session = managed
    take(managed)
    assert "저장 완료" in session.save("success", ledger.roots["success"])
    old = {p: p.read_bytes() for p in ledger.roots["success"].rglob("*.mp4")}
    original = upstream._encode_video_worker

    def fail_camera(key, *args, **kwargs):
        if "844212071939" in key:
            raise OSError("second video write failed")
        return original(key, *args, **kwargs)

    monkeypatch.setattr(upstream, "_encode_video_worker", fail_camera)
    take(managed)
    assert "SAVE FAILED" in session.save("success", ledger.roots["success"])
    with pytest.raises((ValueError, OSError)):
        ledger.reconcile()
    assert session.path.exists() and ledger.completed() == {108}
    assert all(p.read_bytes() == content for p, content in old.items())


def test_committed_without_timing_proof_stays_blocked(managed, monkeypatch):
    _, ledger, session = managed
    take(managed)

    def fail_sidecar(*args):
        raise OSError("sidecar failure")

    monkeypatch.setattr(session, "_publish_timing", fail_sidecar)
    assert "SAVE FAILED" in session.save("success", ledger.roots["success"])
    with pytest.raises(OSError):
        ledger.reconcile()
    assert not ledger.completed() and session.path.exists()
    assert len(c.dataset_inventory(ledger.roots["success"])) == 1


def test_no_pilot_adoption_or_symlink_or_concurrent_writer(tmp_path):
    with pytest.raises(c.CollectionError, match="Pilot"):
        c.Collection(c.PROJECT / "outputs/act_recorder_pilot_20261031/formal")
    root = tmp_path / "formal"
    root.mkdir()
    (root / "success").mkdir()
    with pytest.raises(c.CollectionError, match="without collection ledger"):
        c.Collection(root)
    clean = c.Collection(tmp_path / "clean")
    try:
        with pytest.raises(c.CollectionError, match="another writer"):
            c.Collection(clean.directory)
    finally:
        clean.close()
    separate = tmp_path / "symlink"
    separate.mkdir()
    (separate / "success").symlink_to(root / "success")
    with pytest.raises(c.CollectionError, match="symlinks"):
        c.Collection(separate)


def test_tampered_dataset_or_lost_ledger_not_adopted(managed):
    _, ledger, session = managed
    take(managed)
    session.save("success", ledger.roots["success"])
    sidecar = next(ledger.roots["success"].glob("meta/recorder_timing/*/frames.jsonl"))
    sidecar.write_text(sidecar.read_text() + "\n")
    with pytest.raises(c.CollectionError, match="changed"):
        ledger.audit()


def test_invalid_coordinates_and_unknown_fields_leave_no_partial_edits(managed):
    _, ledger, _ = managed
    for command in ("placement actual_x=nan", "placement actual_y=inf", "placement recommended_x=0",
                    "placement actual_x=1 unknown=2"):
        with pytest.raises(c.CollectionError):
            ledger.command(command)
    assert ledger.placement["actual_x"] is None


def test_all_108_real_synthetic_saves_complete_once_in_original_order(managed):
    _, ledger, session = managed
    for index, condition in enumerate(c.load_plan()):
        assert ledger.condition() == condition
        take(managed, index)
        assert "저장 완료" in session.save("success", ledger.roots["success"])
    assert len(ledger.completed()) == 108 and ledger.condition() is None
    assert [a["dataset_episode_index"] for a in ledger.state["attempts"]] == list(range(108))
    assert [a["condition_id"] for a in ledger.state["attempts"]] == [r["condition_id"] for r in c.load_plan()]
    assert all(a["attempt_number"] == 1 for a in ledger.state["attempts"])
    assert len(c.dataset_inventory(ledger.roots["success"])) == 108
    assert not ledger.roots["correction"].exists()
    with pytest.raises(c.CollectionError, match="complete"):
        ledger.prepare()
    ledger.audit()


def test_recording_main_keeps_fixed_reference_and_existing_keys(recorder, tmp_path, monkeypatch):
    import queue
    import types
    from ai.record.test.test_indy7_act_record import HistoryHarness, MONO
    h = HistoryHarness(recorder, tmp_path / "harness")
    h.generate(700)
    virtual_ms = [0.]
    commands = queue.Queue()
    started, finished = [False], [False]
    node = types.SimpleNamespace(
        history_snapshot=lambda: (h.timing(virtual_ms[0]), h.history.snapshot(MONO + round(virtual_ms[0]*1e6))),
        destroy_node=lambda: None)

    def spin(*args, **kwargs):
        virtual_ms[0] += 5
        assert virtual_ms[0] < 1500
        while h.events and h.events[0][0] <= virtual_ms[0]:
            receipt, name, header, value = h.events.pop(0)
            h.push(name, header, receipt, value)
        if not started[0]:
            for command in ('condition 037', 'placement actual_x=42 notes="Measured A"', 's'):
                commands.put(command)
            started[0] = True
        elif virtual_ms[0] >= 400 and not finished[0]:
            for command in ('r', 'result demo_quality="Good" place_success=true', 'o', 'q'):
                commands.put(command)
            finished[0] = True

    monkeypatch.setattr(recorder, "Indy7Observations", lambda *args: node)
    monkeypatch.setattr(recorder, "_commands", lambda: commands)
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: MONO + round(virtual_ms[0]*1e6))
    monkeypatch.setattr(recorder.time, "sleep", lambda seconds: virtual_ms.__setitem__(0, virtual_ms[0]+seconds*1000))
    for name, function in (("init", lambda: None), ("ok", lambda: True), ("spin_once", spin), ("shutdown", lambda: None)):
        monkeypatch.setattr(recorder.rclpy, name, function, raising=False)
    directory = tmp_path / "formal"
    monkeypatch.setattr(recorder.sys, "argv", ["act-record", "--collection-dir", str(directory)])
    assert recorder.main() == 0
    ledger = c.Collection(directory)
    try:
        assert ledger.completed() == {37}
        a = ledger.state["attempts"][0]
        assert a["placement"]["notes"] == "Measured A"
        assert a["placement"]["demo_quality"] == "Good"
        sidecar = directory / "success/meta/recorder_timing/episode-000000"
        meta = json.loads((sidecar / "episode.json").read_text())
        assert meta["start_monotonic_ns"] == MONO + 5_000_000
        assert meta["sync_options"]["receive_wait_ms"] == 60
        first = json.loads((sidecar / "frames.jsonl").read_text().splitlines()[0])
        assert first["schedule"]["deadline_monotonic_ns"] == MONO + 115_000_000
        assert first["alignment"]["reference_monotonic_ns"] == MONO + 55_000_000
    finally:
        ledger.close()


def test_atomic_ledger_failure_blocks_further_collection(managed, monkeypatch):
    _, ledger, _ = managed
    before = ledger.path.read_bytes()

    def fail_replace(*args):
        raise OSError("replace unavailable")

    monkeypatch.setattr(c.os, "replace", fail_replace)
    with pytest.raises(OSError):
        ledger.prepare()
    assert ledger.path.read_bytes() == before
    with pytest.raises(c.CollectionError, match="차단"):
        ledger.prepare()


def test_status_and_history_snapshot_while_writer_open(managed, monkeypatch, capsys):
    _, ledger, _ = managed
    ledger.command("condition 037")
    ledger.prepare()
    import sys
    monkeypatch.setattr(sys, "argv", ["collection", "status", "--collection-dir", str(ledger.directory)])
    assert c.main() == 0
    text = capsys.readouterr().out
    assert "조건 ID   : 037" in text and "시도      : 1회차" in text and "시작 검사 대기" in text
    monkeypatch.setattr(sys, "argv", ["collection", "history", "037", "--detail", "--collection-dir", str(ledger.directory)])
    assert c.main() == 0
    assert '"condition_id": 37' in capsys.readouterr().out


def forced_exit_worker(directory, kind="success"):
    """Spawned interpreter: stub ROS before loading the real Recorder/Dataset."""
    import os
    with pytest.MonkeyPatch.context() as patch:
        module = recorder.__wrapped__(patch)
        ledger = c.Collection(directory)

        def factory(kind):
            return module.LeRobotDataset.create(repo_id="local/forced_exit", root=ledger.roots[kind],
                robot_type="indy7", fps=20, features=module._features(), use_videos=True,
                image_writer_threads=0, vcodec="h264")

        session = module.EpisodeSession(ledger.pending_root, factory, 20, "synthetic", collection=ledger)
        take((module, ledger, session), correction=kind == "correction")
        ledger.commit = lambda: os._exit(23)  # No Python cleanup after finalized data+sidecar.
        session.save(kind, ledger.roots[kind])
        os._exit(24)


@pytest.mark.parametrize("kind", ["success", "correction"])
def test_forced_process_exit_after_dataset_commit_recovers_without_duplicate(recorder, tmp_path, kind):
    import multiprocessing
    directory = tmp_path / "crashed-formal"
    process = multiprocessing.get_context("spawn").Process(target=forced_exit_worker, args=(directory, kind))
    process.start()
    process.join(timeout=30)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("Synthetic save child timed out")
    assert process.exitcode == 23
    disk = json.loads((directory / "collection.json").read_text())
    assert disk["attempts"][0]["status"] == "SAVING"
    before = c.tree_fingerprint(directory / kind)
    recovery = c.Collection(directory, recovery=True)
    try:
        recovery.reconcile()
        assert recovery.completed() == ({108} if kind == "success" else set())
        assert recovery.stats()[kind] == 1
        assert c.tree_fingerprint(directory / kind) == before
        assert len(c.dataset_inventory(directory / kind)) == 1
        recovery.reconcile()
        assert list((directory / "pending").glob("take-*/episode.json"))
    finally:
        recovery.close()


def test_recovery_cannot_attach_a_different_attempt(managed):
    _, ledger, session = managed
    take(managed)
    original_commit = ledger.commit
    ledger.commit = lambda: (_ for _ in ()).throw(OSError("interrupted before ledger commit"))
    assert "SAVE FAILED" in session.save("success", ledger.roots["success"])
    ledger.commit = original_commit
    path = next(ledger.roots["success"].glob("meta/recorder_timing/*/episode.json"))
    meta = json.loads(path.read_text())
    meta["collection"]["condition_id"] = 37
    path.write_text(json.dumps(meta))
    with pytest.raises(c.CollectionError, match="exact condition"):
        ledger.reconcile()
    assert not ledger.completed()


def test_crash_between_invalid_manifest_and_ledger_update_keeps_invalid(managed, monkeypatch):
    module, ledger, session = managed
    ledger.prepare()
    session.start(np.zeros(6), 10)
    session.add_frame(frame(module))

    def interrupted(*args):
        raise OSError("interrupted before invalid ledger update")

    monkeypatch.setattr(ledger, "review", interrupted)
    with pytest.raises(OSError):
        session.invalidate("camera_skew")
    pending = session.path
    ledger.close()
    reopened = c.Collection(ledger.directory)
    try:
        assert reopened.state["attempts"][-1]["status"] == "INVALID"
        assert reopened.state["attempts"][-1]["integrity"]["violations"][-1]["reason"] == "camera_skew"
        assert pending.exists() and not reopened.completed()
    finally:
        reopened.close()


def test_explicit_retry_selection_survives_restart(managed):
    _, ledger, _ = managed
    ledger.command("condition 037")
    ledger.prepare()
    ledger.close()
    reopened = c.Collection(ledger.directory)
    try:
        assert reopened.condition()["condition_id"] == 37
        reopened.prepare()
        assert reopened.current["attempt_number"] == 2
        assert reopened.current["collection_order"] == 2
        assert reopened.state["conditions"][0]["condition_id"] == 108
    finally:
        reopened.close()


def test_dataset_contract_change_blocks_even_if_episode_counts_match(managed):
    _, ledger, session = managed
    take(managed)
    assert "저장 완료" in session.save("success", ledger.roots["success"])
    path = ledger.roots["success"] / "meta/info.json"
    info = json.loads(path.read_text())
    info["fps"] = 30
    path.write_text(json.dumps(info))
    with pytest.raises(c.CollectionError, match="contract mismatch"):
        ledger.audit()
