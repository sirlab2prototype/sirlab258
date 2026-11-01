"""Headless desktop tests: fake ROS/clock, real Recorder/Collection save contract.

No Tk display, ROS initialization, camera, serial port, hardware or GPU is used.
"""
from copy import deepcopy
import json
import sys
import threading
import types

import numpy as np
import pytest

from ai.record import gui, gui_bridge as gb, collection as c
from ai.record.correction_marker import validate
from ai.record.test.test_indy7_act_record import recorder, frame, HistoryHarness, MONO, EPOCH
from ai.record.test.test_collection import managed, take


def attached(parts):
    module, ledger, session = parts
    bridge = gb.RecorderBridge()
    starter = types.SimpleNamespace(active=False)
    node = types.SimpleNamespace(source_timing={}, lock=threading.Lock())
    bridge.update(session, starter, node, force=True)
    return bridge, starter, node


def perform(bridge, starter, node, session, command, action):
    bridge.update(session, starter, node, force=True)
    assert bridge.submit(command)
    item = bridge.commands.get_nowait()
    with bridge.command_scope(item, session, starter, node) as accepted:
        assert accepted
        return action(item)


def test_initial_fields_use_ledger_and_unknown_fps(managed):
    _, ledger, session = managed
    bridge, _, _ = attached(managed)
    view, _ = bridge.read()
    fields = gui.display_fields(view)
    assert fields["progress"] == "정상 시연 0 / 108    교정 시연 0    남은 조건 108"
    assert (fields["order"], fields["condition"], fields["attempt"]) == ("001 / 108", "108", "1회차")
    assert (fields["position"], fields["yaw"], fields["depth"]) == (
        "오른쪽 (R)", "반시계방향 많이 (A6)", "자유 배치 (Random)")
    assert fields["fps"] == "미확정" and fields["marker"] == "없음"
    assert fields["pending"] == "Pending : 없음"
    assert fields[gb.CAMERAS[0]] == "미수신"
    assert set(view["allowed"]) >= {"s", "condition", "placement", "progress", "history"}
    assert "o" not in view["allowed"] and "d" not in view["allowed"]
    assert not ledger.state["attempts"] and session.path is None


def test_bounded_scalar_camera_status_without_images_or_history(managed):
    _, _, session = managed
    class MetadataOnly:
        lock = threading.Lock()
        source_timing = {gb.CAMERAS[0]: {"callback_monotonic_ns": 2_000_000_000},
                         gb.CAMERAS[1]: {"callback_monotonic_ns": 100_000_000}}
        @property
        def images(self):
            raise AssertionError("GUI must never access RGB")
        def history_snapshot(self):
            raise AssertionError("GUI must never snapshot RGB history")
    node = MetadataOnly()
    view = gb.snapshot(session, types.SimpleNamespace(active=False), node, now_ns=2_100_000_000)
    assert view["cameras"][gb.CAMERAS[0]] == {"age_ms": 100, "receiving": True}
    assert not view["cameras"][gb.CAMERAS[1]]["receiving"]
    assert "시작 검사" not in gui.display_fields(view)[gb.CAMERAS[0]]
    node.source_timing.clear()
    assert view["cameras"][gb.CAMERAS[0]]["age_ms"] == 100


def test_single_inflight_and_stale_command_cannot_start_attempt(managed):
    _, ledger, session = managed
    bridge, starter, node = attached(managed)
    assert bridge.submit("s") and not bridge.submit("s") and not bridge.submit("q")
    command = bridge.commands.get_nowait()
    ledger.select(37)  # State changed since the button click.
    with bridge.command_scope(command, session, starter, node) as accepted:
        assert not accepted
    view, messages = bridge.read()
    assert not view["busy"] and view["condition"]["condition_id"] == 37
    assert any("상태가 변경" in m for m in messages)
    assert not ledger.current and not ledger.state["attempts"] and session.path is None


def test_snapshot_cannot_mutate_live_ledger_and_update_is_throttled(managed, monkeypatch):
    _, ledger, session = managed
    bridge, starter, node = attached(managed)
    monkeypatch.setattr(gb.time, "monotonic_ns", lambda: 10)
    bridge._next_update_ns = 100
    ledger.select(37)
    bridge.update(session, starter, node)
    view, _ = bridge.read()
    assert view["condition"]["condition_id"] == 108
    bridge.update(session, starter, node, force=True)
    view, _ = bridge.read()
    view["condition"]["condition_id"] = 9
    view["stats"]["success"] = 108
    assert ledger.condition_view()["condition_id"] == 37 and not ledger.completed()


@pytest.mark.parametrize("kind", ["normal", "correction"])
def test_review_target_and_no_false_saved_message(managed, kind):
    _, _, session = managed
    take(managed, correction=kind == "correction")
    bridge, _, _ = attached(managed)
    view, messages = bridge.read()
    assert view["state"] == "REVIEW" and "o" in view["allowed"]
    assert gui.display_fields(view)["target"] == ("Success Dataset" if kind == "normal" else "Correction Dataset")
    assert not messages and view["stats"]["success"] == 0 and view["stats"]["correction"] == 0
    assert view["last_saved"] is None


def test_invalid_and_save_failure_button_policy_and_preservation(managed, monkeypatch):
    module, ledger, session = managed
    ledger.prepare(); session.start(np.zeros(6), 10)
    session.add_frame(frame(module)); path = session.path
    session.invalidate("observation_too_old", details={"topic": "state", "age_ns": 112_000_000})
    bridge, starter, node = attached(managed)
    view, _ = bridge.read()
    assert view["state"] == "INVALID" and "o" not in view["allowed"] and "d" in view["allowed"]
    assert path.exists() and not bridge.submit("o")
    assert session.request_quit()[0] and path.exists()
    session.save_failed = True
    bridge.update(session, starter, node, force=True)
    view, _ = bridge.read()
    assert view["state"] == "ERROR" and set(view["allowed"]) == {"q"}
    assert not bridge.submit("d") and not bridge.submit("s")


def test_empty_correction_cannot_offer_save_and_duplicate_x_is_blocked(managed):
    _, _, session = managed
    take(managed)
    session.state = "RECORDING"
    session.mark_correction()  # No future reference is recorded.
    session.stop()
    bridge, _, _ = attached(managed)
    view, _ = bridge.read()
    assert view["correction"]["start_frame_index"] is None
    assert "o" not in view["allowed"] and not bridge.submit("x")


def test_optional_placement_uses_existing_parser_and_keeps_null(managed):
    _, ledger, session = managed
    bridge, starter, node = attached(managed)
    perform(bridge, starter, node, session, "placement actual_x=43.2 actual_y=null",
            lambda cmd: ledger.command(cmd))
    ledger.prepare()
    assert ledger.context()["placement"]["actual_x"] == 43.2
    assert ledger.context()["placement"]["actual_y"] is None
    assert ledger.context()["placement"]["actual_yaw"] is None
    bridge.update(session, starter, node, force=True)
    assert not bridge.submit("placement actual_x=9")


@pytest.mark.parametrize("outcome", ["success", "correction", "discard", "interrupt", "save_failure", "invalid", "start_failure"])
def test_actual_main_gui_queue_and_recovery_without_ros(recorder, tmp_path, monkeypatch, outcome):
    h = HistoryHarness(recorder, tmp_path/"harness")
    h.generate(1000, state_end=0 if outcome == "invalid" else 1000)
    if outcome == "start_failure":
        h.history.items["state"].clear()
        h.events = [e for e in h.events if e[1] != "state"]
    ms, submitted, messages, observations, cleanup = [0.], set(), [], [], []
    class Bridge(gb.RecorderBridge):
        def message(self, text):
            messages.append(str(text))
            super().message(text)
        def update(self, *args, **kwargs):
            super().update(*args, **kwargs)
            observations.append(self.read()[0])
    bridge = Bridge()
    node = types.SimpleNamespace(
        history_snapshot=lambda: (h.timing(ms[0]), h.history.snapshot(MONO+round(ms[0]*1e6))),
        source_timing=h.sources, destroy_node=lambda: cleanup.append("node"))
    events = [(5, "s")]
    if outcome == "correction": events.append((205, "x"))
    if outcome not in ("interrupt", "invalid", "start_failure"): events.append((400, "r"))
    if outcome in ("success", "correction", "save_failure"): events.append((425, "o"))
    if outcome == "discard": events.append((425, "d"))
    if outcome != "interrupt": events.append((455, "q"))
    def spin(*args, **kwargs):
        ms[0] += 5
        assert ms[0] < 1500, (submitted, bridge.read()[0])
        while h.events and h.events[0][0] <= ms[0]:
            receipt, name, header, value = h.events.pop(0); h.push(name, header, receipt, value)
        if outcome == "interrupt" and ms[0] >= 250:
            bridge.abort.set()
        for when, cmd in events:
            if when <= ms[0] and cmd not in submitted and bridge.submit(cmd):
                submitted.add(cmd)
                break
    monkeypatch.setitem(sys.modules, "rclpy.signals", types.SimpleNamespace(SignalHandlerOptions=types.SimpleNamespace(NO="NO")))
    def init(**kwargs):
        assert kwargs == {"args": [], "signal_handler_options": "NO"}
    for name, function in (("init", init), ("ok", lambda: True), ("spin_once", spin), ("shutdown", lambda: cleanup.append("ros_fake"))):
        monkeypatch.setattr(recorder.rclpy, name, function, raising=False)
    monkeypatch.setattr(recorder, "Indy7Observations", lambda *a: node)
    monkeypatch.setattr(recorder, "_commands", lambda: pytest.fail("GUI must not open stdin thread"))
    monkeypatch.setattr(recorder.time, "monotonic_ns", lambda: MONO+round(ms[0]*1e6))
    monkeypatch.setattr(recorder.time, "time_ns", lambda: EPOCH+round(ms[0]*1e6))
    monkeypatch.setattr(recorder.time, "sleep", lambda s: ms.__setitem__(0, ms[0]+s*1000))
    if outcome == "save_failure":
        monkeypatch.setattr(recorder.EpisodeSession, "_publish_timing", lambda *a: (_ for _ in ()).throw(OSError("injected sidecar disk failure")))
    directory = tmp_path/"formal"
    gb.run_engine(bridge, ["--collection-dir", str(directory)], runner=recorder.main)
    final = bridge.read()[0]
    assert cleanup == ["node", "ros_fake"] and not final["running"]
    assert any(v["state"] == "PRESTART" for v in observations)
    assert final["exit_code"] == (1 if outcome == "save_failure" else 0)
    assert not (directory/"failure").exists()
    if outcome in ("success", "correction", "save_failure"):
        saving = next(v for v in observations if v["state"] == "SAVING")
        assert saving["stats"]["success"] == saving["stats"]["correction"] == 0
        assert not saving["allowed"] and saving["busy"]
    if outcome == "save_failure":
        assert final["stats"]["success"] == 0
        assert any("SAVE FAILED" in m and "injected sidecar disk failure" in m and "reconcile" in m for m in messages)
        with pytest.raises(c.CollectionError, match="reconcile|Unresolved|unresolved"):
            c.Collection(directory)
        assert list((directory/"pending").glob("take-*/episode.json"))
        restarted = gb.RecorderBridge()
        gb.run_engine(restarted, ["--collection-dir", str(directory)], runner=recorder.main)
        state, diagnostics = restarted.read()
        assert state["state"] == "ERROR" and not state["allowed"]
        assert any("Collection Blocked" in m and "Pending" in m and "reconcile" in m for m in diagnostics)
        assert cleanup == ["node", "ros_fake"]  # Blocked before any second ROS init.
        return
    ledger = c.Collection(directory)
    try:
        stats = ledger.stats()
        assert stats["success"] == int(outcome == "success")
        assert stats["correction"] == int(outcome == "correction")
        assert stats["discard"] == int(outcome == "discard")
        view = ledger.condition_view()
        assert view["condition_id"] == (37 if outcome == "success" else 108)
        assert view["attempt_number"] == (1 if outcome == "success" else 2)
        fresh = recorder.EpisodeSession(ledger.pending_root, None, 20, "basket", collection=ledger)
        restored = gb.snapshot(fresh, types.SimpleNamespace(active=False), node)
        assert restored["stats"] == stats and restored["condition"] == view
        if outcome in ("success", "correction"):
            path = directory/outcome/"meta/recorder_timing/episode-000000"
            meta = json.loads((path/"episode.json").read_text())
            rows = [json.loads(line) for line in (path/"frames.jsonl").read_text().splitlines()]
            assert meta["integrity"]["valid"] and meta["start_monotonic_ns"] == MONO+5_000_000
            assert meta["collection"]["condition_id"] == 108 and meta["sync_options"]["receive_wait_ms"] == 60
            assert ledger.state["attempts"][0]["dataset_episode_index"] == 0
            assert list((directory/"receipts").glob("*.json"))
            ledger.audit()
            if outcome == "correction":
                marker = meta["demonstration"]["correction"]
                validate(marker, len(rows), 20, rows)
                assert marker["boundary_source"] == "gui_button_received"
                assert marker["input_received_monotonic_ns"] == MONO+205_000_000
                assert rows[marker["start_frame_index"]]["alignment"]["reference_monotonic_ns"] >= marker["input_received_monotonic_ns"]
        elif outcome in ("interrupt", "invalid"):
            pending = next((directory/"pending").glob("take-*/episode.json"))
            assert pending.exists() and not (directory/"success").exists()
            assert ledger.state["attempts"][0]["status"] == ("INVALID" if outcome == "invalid" else "INTERRUPTED")
        elif outcome == "start_failure":
            assert not (directory/"pending").exists()
            assert not any("[시작 검사] 통과" in m for m in messages)
            assert all(v["state"] != "RECORDING" for v in observations)
        else:
            assert not (directory/"success").exists() and not list((directory/"pending").glob("take-*"))
    finally:
        ledger.close()


def test_worker_startup_exception_is_visible_and_stops_controls():
    bridge = gb.RecorderBridge()
    def broken(*args, **kwargs):
        raise OSError("injected collection lock failure")
    gb.run_engine(bridge, [], runner=broken)
    view, messages = bridge.read()
    assert not view["running"] and view["state"] == "ERROR" and not view["allowed"]
    assert "injected collection lock failure" in messages[-1]


def test_gui_help_does_not_construct_tk_or_load_engine(monkeypatch, capsys):
    monkeypatch.setattr(gui.tk, "Tk", lambda: pytest.fail("--help must not open a display"))
    with pytest.raises(SystemExit) as exc:
        gui.main(["--help"])
    assert exc.value.code == 0 and "--collection-dir" in capsys.readouterr().out


def test_gui_no_display_stops_before_engine(monkeypatch):
    def no_display():
        raise gui.tk.TclError("no display")
    monkeypatch.setattr(gui.tk, "Tk", no_display)
    monkeypatch.setattr(gui, "run_engine", lambda *a: pytest.fail("No ROS without window"))
    with pytest.raises(SystemExit) as exc:
        gui.main([])
    assert exc.value.code == 1


def test_save_confirmation_and_entry_shortcuts_do_not_send_commands(managed, monkeypatch):
    _, _, session = managed
    take(managed)
    bridge, _, _ = attached(managed)
    window = gui.RecorderWindow.__new__(gui.RecorderWindow)
    window.root, window.bridge = object(), bridge
    window.view = bridge.read()[0]
    window.notices = types.SimpleNamespace(set=lambda text: None)
    window.refresh = lambda **kw: setattr(window, "view", bridge.read()[0])
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **kw: False)
    window.action("o")
    assert bridge.commands.empty()
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **kw: True)
    window.action("o"); window.action("o")
    assert bridge.commands.qsize() == 1


def test_window_close_queues_quit_without_destroying_pending_review(managed):
    _, _, session = managed
    take(managed)
    pending = session.path
    bridge, starter, node = attached(managed)
    calls = []
    window = gui.RecorderWindow.__new__(gui.RecorderWindow)
    window.bridge = bridge
    window.root = types.SimpleNamespace(destroy=lambda: calls.append("destroy"))
    window.notices = types.SimpleNamespace(set=lambda text: calls.append(text))
    window.append_messages = lambda messages: None
    window.close()
    item = bridge.commands.get_nowait()
    assert item == "q" and "destroy" not in calls
    with bridge.command_scope(item, session, starter, node) as accepted:
        assert accepted and not session.request_quit()[0]
    assert pending.exists() and bridge.read()[0]["state"] == "REVIEW"


def test_close_while_saving_does_not_queue_or_cancel_save(managed):
    _, _, session = managed
    take(managed)
    bridge, starter, node = attached(managed)
    bridge.submit("o")
    bridge.update(session, starter, node, phase="SAVING", force=True)
    window = gui.RecorderWindow.__new__(gui.RecorderWindow)
    window.bridge = bridge
    window.notices = types.SimpleNamespace(set=lambda text: None)
    window.root = types.SimpleNamespace(destroy=lambda: pytest.fail("cannot destroy during save"))
    window.append_messages = lambda messages: None
    window.close()
    assert window.close_after_busy and bridge.commands.qsize() == 1
    assert bridge.commands.get_nowait() == "o" and session.path.exists()


def test_duplicate_gui_x_keeps_first_input_and_receipt_contract(managed, monkeypatch):
    module, ledger, session = managed
    ledger.prepare(); session.start(np.zeros(6), 10)
    bridge, starter, node = attached(managed)
    monkeypatch.setattr(gb.time, "monotonic_ns", lambda: 10_050_000_000)
    perform(bridge, starter, node, session, "x", lambda command: session.mark_correction(command))
    marker = deepcopy(session.correction)
    assert marker["boundary_source"] == "gui_button_received"
    assert marker["input_received_monotonic_ns"] == 10_050_000_000
    assert not bridge.submit("x") and session.correction == marker
    session.add_frame(frame(module), {"sample_monotonic_ns": 10_100_000_000, "sources": {}})
    rows = [json.loads((session.path/"00000000.timing.json").read_text())]
    validate(session.correction, 1, 20, rows)
    for change in ({"input_received_monotonic_ns": None}, {"boundary_monotonic_ns": 10_060_000_000},
                   {"boundary_source": "made_up"}, {"start_frame_index": None}):
        with pytest.raises(ValueError):
            validate({**session.correction, **change}, 1, 20, rows)


def test_window_ignores_shortcuts_while_typing():
    window = gui.RecorderWindow.__new__(gui.RecorderWindow)
    calls = []
    window.action = calls.append
    # Skip widget initialization, so no Tcl interpreter or display is created.
    entry = object.__new__(gui.ttk.Entry)
    window.shortcut(types.SimpleNamespace(widget=entry), "s")
    assert not calls
    window.shortcut(types.SimpleNamespace(widget=object()), "s")
    assert calls == ["s"]


def test_slow_real_save_does_not_hold_ui_lock_or_accept_duplicate(managed):
    _, ledger, session = managed
    take(managed)
    bridge, starter, node = attached(managed)
    entered, release = threading.Event(), threading.Event()
    original_factory = session.dataset_factory
    def slow_factory(outcome):
        entered.set()
        assert release.wait(5), "test did not release fake disk delay"
        return original_factory(outcome)
    session.dataset_factory = slow_factory
    assert bridge.submit("o")
    def runner(argv, *, frontend):
        item = frontend.commands.get(timeout=1)
        with frontend.command_scope(item, session, starter, node) as accepted:
            assert accepted
            frontend.update(session, starter, node, phase="SAVING", force=True)
            frontend.message(session.save("success", ledger.roots["success"]))
        return 0
    worker = threading.Thread(target=gb.run_engine, args=(bridge, [], runner))
    worker.start()
    try:
        assert entered.wait(3)
        view, messages = bridge.read()  # Must return while the engine is blocked on disk.
        assert worker.is_alive() and view["state"] == "SAVING" and view["busy"]
        assert view["stats"]["success"] == 0 and not messages
        assert all(not bridge.submit(cmd) for cmd in ("o", "s", "d", "q"))
    finally:
        release.set()
        worker.join(10)
    view, messages = bridge.read()
    assert not worker.is_alive() and view["exit_code"] == 0
    assert view["stats"]["success"] == 1 and view["last_saved"]["dataset_episode_index"] == 0
    assert any("저장 완료" in m for m in messages)


def test_close_during_initialization_is_remembered():
    bridge = gb.RecorderBridge()
    window = gui.RecorderWindow.__new__(gui.RecorderWindow)
    window.bridge = bridge
    window.notices = types.SimpleNamespace(set=lambda text: None)
    window.root = types.SimpleNamespace(destroy=lambda: pytest.fail("must wait for initialization"))
    window.append_messages = lambda messages: None
    window.close()
    assert window.close_after_busy and bridge.commands.empty()


def test_abort_before_engine_start_does_not_open_collection():
    bridge = gb.RecorderBridge()
    bridge.abort.set()
    gb.run_engine(bridge, [], runner=lambda *a, **kw: pytest.fail("aborted before startup"))
    assert bridge.read()[0]["exit_code"] == 0


def test_gui_signal_does_not_take_ui_lock_or_leave_worker_running(monkeypatch, tmp_path):
    handlers, instances = {}, []
    def register(number, handler):
        previous = handlers.get(number)
        handlers[number] = handler
        return previous
    monkeypatch.setattr(gui.signal, "signal", register)
    def window(root, bridge):
        instances.append(bridge)
    def engine(bridge, argv):
        assert threading.current_thread() is not threading.main_thread()
        assert bridge.abort.wait(3)
        bridge.finished(0)
    def mainloop():
        bridge = instances[0]
        with bridge._lock:
            # Model a signal arriving during the UI's snapshot read.
            handlers[gui.signal.SIGTERM](gui.signal.SIGTERM, None)
    monkeypatch.setattr(gui, "RecorderWindow", window)
    monkeypatch.setattr(gui.tk, "Tk", lambda: types.SimpleNamespace(mainloop=mainloop))
    monkeypatch.setattr(gui, "run_engine", engine)
    assert gui.main(["--collection-dir", str(tmp_path/"not_created")]) == 0
    assert not (tmp_path/"not_created").exists()
    assert not handlers[gui.signal.SIGINT] and not handlers[gui.signal.SIGTERM]


def test_closed_terminal_does_not_hide_committed_save_or_fail_session(managed, monkeypatch):
    _, ledger, session = managed
    take(managed)
    bridge, starter, node = attached(managed)
    def closed_stdout(*args, **kwargs):
        raise BrokenPipeError("SSH stdout closed")
    monkeypatch.setattr(gb, "print", closed_stdout, raising=False)
    perform(bridge, starter, node, session, "o", lambda cmd:
            bridge.message(session.save("success", ledger.roots["success"])))
    view, messages = bridge.read()
    assert view["state"] == "READY" and view["stats"]["success"] == 1 and not session.save_failed
    assert any("저장 완료" in m for m in messages)
    ledger.audit()


def test_camera_receipt_age_keeps_aging_during_save_without_ros_calls(managed):
    _, _, session = managed
    take(managed)
    starter = types.SimpleNamespace(active=False)
    node = types.SimpleNamespace(source_timing={gb.CAMERAS[0]: {"callback_monotonic_ns": 2_000_000_000}})
    view = gb.snapshot(session, starter, node, phase="SAVING", now_ns=2_100_000_000)
    fresh = gui.display_fields(view, now_ns=2_100_000_000)[gb.CAMERAS[0]]
    old = gui.display_fields(view, now_ns=4_100_000_000)[gb.CAMERAS[0]]
    assert "갱신 대기" in fresh and "수신 중" in fresh and "100ms" in fresh
    assert "갱신 대기" in old and "수신 지연" in old and "2100ms" in old
    assert view["cameras"][gb.CAMERAS[0]]["age_ms"] == 100  # Rendering never mutates the engine view.
