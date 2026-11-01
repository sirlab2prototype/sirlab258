"""Presentation tests plus real Tk rendering with scalar fake data only.

Never import/start the Recorder engine, ROS, a Dataset or any device. Render
tests skip explicitly if a display is unavailable; pure presentation tests run.
"""
from copy import deepcopy
import threading
import tkinter as tk
from tkinter import ttk

import pytest

from ai.record import gui, gui_bridge as gb
from ai.record.gui_presentation import operator_fields, message_notice, recording_clock


def view_for(state="READY", correction=False):
    allowed = {"READY": ["s", "condition", "placement", "progress", "history", "help", "guide", "q"],
               "PRESTART": ["r", "d", "q"], "RECORDING": ["r", "q"] + ([] if correction else ["x"]),
               "REVIEW": ["o", "d", "q", "result", "progress", "history", "help", "guide"],
               "INVALID": ["d", "q", "result", "progress", "history"], "ERROR": ["q"], "SAVING": []}
    pending = None if state == "READY" else "/fake/collection/pending/take-123"
    marker = {"start_frame_index": 140, "start_dataset_timestamp_s": 7.0} if correction else None
    return {"state": state, "running": True, "busy": state == "SAVING", "allowed": allowed[state],
            "token": (state, correction), "stats": {"success": 0, "correction": 0, "discard": 0, "failure": 0},
            "condition": {"collection_order": 1, "condition_id": 108, "attempt_number": 1,
                          "position": "오른쪽 (R)", "yaw": "반시계방향 많이 (A6)", "depth": "자유 배치 (Random)"},
            "frames": 820 if pending else 0, "elapsed_s": 41.0 if pending else 0,
            "actual_sample_fps": 20.0 if pending else None, "demonstration_type": "correction" if correction else "normal",
            "correction": marker, "pending": pending, "last_saved": None, "cameras": {},
            "integrity": {"valid": state != "INVALID", "violations": [] if state != "INVALID" else
                          [{"reason": "observation_too_old", "details": {"topic": gb.CAMERAS[0], "age_ns": 112_000_000}}]}}


class PreviewBridge(gb.RecorderBridge):
    """Real queue/duplicate guard, but no engine is attached to consume commands."""
    def set_view(self, view):
        with self._lock:
            self._view = deepcopy(view)
            self._busy = view["busy"]


@pytest.fixture
def window():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk render unavailable: {exc}")
    bridge = PreviewBridge()
    bridge.set_view(view_for())
    app = gui.RecorderWindow(root, bridge)
    root.geometry("960x660+100+100")
    root.update()
    try:
        yield root, bridge, app
    finally:
        root.destroy()


@pytest.mark.parametrize("seconds,text", [(0, "00:00"), (41.9, "00:41"), (65, "01:05"), (3600, "60:00")])
def test_operator_clock(seconds, text):
    assert recording_clock(seconds) == text


def test_progress_is_only_normal_success_and_camera_absence_is_not_an_alert():
    view = view_for()
    view["stats"].update(success=32, correction=8)
    fields = operator_fields(view)
    assert "32 / 108" in fields["progress"] and "30%" in fields["progress"]
    assert "교정 시연  8" in fields["progress"]
    assert fields["state"] == "녹화 대기" and not fields["alert"]
    assert view["cameras"] == {}
    view["stats"]["success"] = 108
    assert "완료" in operator_fields(view)["default_notice"]


@pytest.mark.parametrize("state,color", [("READY", "#64748b"), ("RECORDING", "#087443"),
                         ("REVIEW", "#9a5b00"), ("SAVING", "#2454cc"), ("ERROR", "#b4232c"), ("INVALID", "#b4232c")])
def test_state_has_text_and_color(state, color):
    fields = operator_fields(view_for(state))
    assert fields["state"] and fields["state_color"] == color


@pytest.mark.parametrize("camera,label", [(gb.CAMERAS[0], "Camera A"), (gb.CAMERAS[1], "Camera B")])
def test_actual_prestart_failure_gets_clear_camera_message(camera, label):
    raw = f"[시작 검사] 취소: missing_causal_observation: {{'topic': '{camera}'}} (Pending take 생성 없음)"
    text, error = message_notice(raw)
    assert error and label in text and "녹화를 시작할 수 없습니다" in text
    assert message_notice("[시작 검사] 취소: user_stop (Pending take 생성 없음)")[1] is False
    assert message_notice("========== [명령어 안내] ==========\nCamera A 미수신 / INVALID") is None


@pytest.mark.parametrize("raw,expected", [
    ("========== [저장 완료] ==========\nDataset : Success", "정상 시연이 저장되었습니다."),
    ("========== [저장 완료] ==========\nDataset : Correction", "교정 시연이 저장되었습니다."),
    ("[폐기 완료]", "시연을 폐기했습니다."),
    ("========== [녹화 종료] ==========", "녹화를 종료했습니다. 시연 결과를 확인하세요."),
])
def test_one_line_operation_notices(raw, expected):
    notice, error = message_notice(raw)
    assert notice == expected and not error and "\n" not in notice


@pytest.mark.parametrize("state,correction", [("READY", False), ("PRESTART", False), ("RECORDING", False),
    ("RECORDING", True), ("REVIEW", False), ("REVIEW", True), ("SAVING", True), ("INVALID", False), ("ERROR", False)])
def test_real_buttons_follow_existing_allowed_commands(window, state, correction):
    root, bridge, app = window
    view = view_for(state, correction)
    bridge.set_view(view); app.refresh(schedule=False); root.update()
    for command, button in app.buttons.items():
        assert button.instate(["!disabled"]) == (command in view["allowed"] and not view["busy"])
    assert app.fields["elapsed"].get() == ("00:00" if state == "READY" else "00:41")
    if correction:
        assert "교정 시연" in app.fields["mode"].get()
        assert app.buttons["x"].instate(["disabled"])
    if state == "REVIEW":
        assert app.fields["target_note"].get() == "저장 대상 · " + ("Correction" if correction else "Success")


def test_real_layout_fits_small_and_large_windows_including_errors(window):
    root, bridge, app = window
    for width, height in ((960, 660), (1024, 700), (860, 600), (1600, 1000)):
        root.geometry(f"{width}x{height}+100+100")
        for state in ("READY", "REVIEW", "SAVING", "INVALID", "ERROR"):
            bridge.set_view(view_for(state)); app.refresh(schedule=False); root.update()
            assert app.content.winfo_width() <= 1000
            for widget in [*app.buttons.values(), app.status_label, app.condition_values[1]]:
                x, y = widget.winfo_rootx()-root.winfo_rootx(), widget.winfo_rooty()-root.winfo_rooty()
                assert x >= 0 and y >= 0
                assert x+widget.winfo_width() <= root.winfo_width()
                assert y+widget.winfo_height() <= root.winfo_height()
            # Main footer and auxiliary controls must also remain reachable.
            assert app.error_card.winfo_ismapped() == (state in ("INVALID", "ERROR"))
            assert app.content.winfo_reqheight() <= app.content.winfo_height(), (width, height, state, app.content.winfo_reqheight())


def test_technical_info_is_popup_only_and_does_not_submit_commands(window):
    root, bridge, app = window
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    basic_variables = {str(w.cget("textvariable")) for w in descendants(app.content) if isinstance(w, ttk.Label)}
    for key in (gb.CAMERAS[0], gb.CAMERAS[1], "fps", "frames", "pending", "marker"):
        assert str(app.fields[key]) not in basic_variables
    app.show_details(); root.update()
    assert app.dialogs["details"].winfo_exists() and bridge.commands.empty()
    assert str(app.fields["pending"]) in {str(w.cget("textvariable")) for w in descendants(app.dialogs["details"]) if isinstance(w, ttk.Label)}
    app.dialogs["details"].destroy(); app.refresh(schedule=False)


def test_real_clicks_keep_duplicate_guard_and_queries_do_not_replace_footer(window):
    root, bridge, app = window
    app.buttons["s"].invoke(); app.buttons["s"].invoke()
    assert bridge.commands.qsize() == 1 and bridge.commands.get_nowait() == "s"
    assert all(b.instate(["disabled"]) for b in app.buttons.values())
    bridge.set_view(view_for()); app.refresh(schedule=False)
    app.show_progress(); root.update()
    assert bridge.commands.get_nowait() == "progress"
    bridge.set_view(view_for()); bridge.message("========== [수집 현황] ==========\n정상 시연 : 0 / 108\n" + "진단 "*100)
    app.refresh(schedule=False)
    assert app.notices.get() == "바구니 배치 후 녹화를 시작하세요."
    assert "정상 시연" in app._progress_text.get("1.0", "end")


def test_error_is_visible_and_full_pending_and_reconcile_stay_in_details(window):
    root, bridge, app = window
    bridge.set_view(view_for("ERROR"))
    raw = "========== [저장 오류 - SAVE FAILED] ==========\n오류 : injected disk full\nPending : /fake/take-123\n복구 : python -m ai.record.collection reconcile --collection-dir /fake"
    bridge.message(raw); app.refresh(schedule=False); root.update()
    assert app.error_card.winfo_ismapped() and "Pending" in app.error_text.get()
    assert app.buttons["o"].instate(["disabled"]) and app.buttons["d"].instate(["disabled"])
    app.show_details(); root.update()
    text = app._details_log.get("1.0", "end")
    assert "injected disk full" in text and "/fake/take-123" in text and "reconcile" in text
    assert app._detail_tabs.select() == str(app._diagnostic_tab)
    root.geometry("860x640+100+100")
    bridge.message("[수집 안내 오류] " + "긴 진단 "*150)
    app.refresh(schedule=False); root.update()
    assert app.error_text.get().count("\n") == 1 and "…" in app.error_text.get()
    assert "긴 진단 "*150 in app._details_log.get("1.0", "end")
    assert app.content.winfo_reqheight() <= app.content.winfo_height()


def test_placement_and_history_remain_available_in_popups(window):
    root, bridge, app = window
    app.show_placement(); app.show_history(); app.show_condition(); root.update()
    assert all(app.dialogs[k].winfo_exists() for k in ("placement", "history", "condition"))
    assert bridge.commands.empty()  # Opening a form never freezes placement or begins a take.
    app.metadata.set("actual_x=43.2 actual_y=null")
    app.action(f"placement {app.metadata.get()}")
    assert bridge.commands.get_nowait() == "placement actual_x=43.2 actual_y=null"
    for key in ("placement", "history", "condition"): app.dialogs[key].destroy()
    app.refresh(schedule=False)  # Destroyed popup buttons must not break periodic refresh.


def test_app_fonts_use_pixels_and_real_typographic_hierarchy(window):
    root, _, app = window
    style = ttk.Style(root)
    normal = root.tk.splitlist(style.lookup("Progress.TLabel", "font"))
    placement = root.tk.splitlist(style.lookup("Placement.TLabel", "font"))
    assert int(normal[1]) == -14 and int(placement[1]) == -20
    assert app.status_label.cget("font") == ""  # No global *Font resource overrides ttk styles.
    assert app.condition_values[1].winfo_height() >= 24


def test_all_depth_labels_fit_without_clipping_even_with_invalid(window):
    from ai.record.collection import DEPTH_LABELS
    root, bridge, app = window
    root.geometry("860x640+100+100")
    for code, text in DEPTH_LABELS.items():
        view = view_for("INVALID")
        view["condition"]["depth"] = f"{text} ({code})"
        bridge.set_view(view); app.refresh(schedule=False); root.update()
        for label in app.condition_values:
            assert label.winfo_height() >= label.winfo_reqheight()
        assert app.content.winfo_reqheight() <= app.content.winfo_height(), code


def test_startup_pending_warning_survives_transition_to_ready(window):
    root, bridge, app = window
    initial = view_for(); initial["state"] = "INITIALIZING"; initial["allowed"] = []
    bridge.set_view(initial)
    bridge.message("[미저장 시연] 자동으로 이어 녹화하지 않습니다. Pending : /fake/previous-take")
    app.refresh(schedule=False)
    bridge.set_view(view_for()); app.refresh(schedule=False); root.update()
    assert app.error_card.winfo_ismapped() and "Pending" in app.error_text.get()
    assert app.buttons["s"].instate(["!disabled"])  # Presentation does not add an engine block.
    app.show_details()
    assert "/fake/previous-take" in app._details_log.get("1.0", "end")


def test_committed_save_cleanup_warning_is_visible_without_false_save_failure(window):
    root, bridge, app = window
    ready = view_for(); ready["stats"]["success"] = 1
    bridge.set_view(ready)
    bridge.message("========== [저장 완료] ==========\nDataset : Success\n[정리 경고] 저장은 확인했으나 staging이 남아 있습니다: /fake/leftover")
    app.refresh(schedule=False); root.update()
    assert "저장됐지만" in app.error_text.get() and "Pending" in app.error_text.get()
    assert app.fields["state"].get() == "녹화 대기" and "1 / 108" in app.fields["progress"].get()
    assert app.buttons["s"].instate(["!disabled"])
