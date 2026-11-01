"""Desktop frontend. Run with python -m ai.record.gui; --help never starts ROS."""
import argparse
from collections import deque
from pathlib import Path
import signal
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, messagebox, scrolledtext, ttk

from ai.record.gui_bridge import CAMERAS, RecorderBridge, run_engine
from ai.record.gui_presentation import operator_fields, message_notice
from ai.record.gui_scaling import UIScale, BASE_SIZE, RESIZE_DELAY_MS, window_scale

STATE_LABELS = {"INITIALIZING": "시작·장부 확인 중", "READY": "녹화 대기",
                "PRESTART": "시작 검사 중", "RECORDING": "녹화 중", "REVIEW": "저장 검토",
                "SAVING": "저장 중", "INVALID": "오류 — INVALID", "ERROR": "오류 / 복구 필요",
                "CLOSED": "Recorder 종료"}


def display_fields(view, *, now_ns=None):
    """Render scalar metadata. Unknown values stay unknown; age cached receipts."""
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    snapshot_ns = view.get("snapshot_monotonic_ns", now_ns)
    update_age_ms = max(0, now_ns - snapshot_ns) / 1e6
    stats, condition = view.get("stats"), view.get("condition")
    fields = {"progress": (f"정상 시연 {stats['success']} / 108    교정 시연 {stats['correction']}    "
                           f"남은 조건 {108-stats['success']}" if stats is not None else "장부 확인 중"),
              "state": STATE_LABELS[view["state"]], "order": "—", "condition": "—", "attempt": "—",
              "position": "—", "yaw": "—", "depth": "—"}
    if condition:
        fields.update(order=f"{condition['collection_order']:03d} / 108",
                      condition=f"{condition['condition_id']:03d}", attempt=f"{condition['attempt_number']}회차",
                      position=condition["position"], yaw=condition["yaw"], depth=condition["depth"])
    elif stats and stats["success"] == 108:
        fields["condition"] = "108개 완료"
    fps = view.get("actual_sample_fps")
    fields.update(frames=str(view.get("frames", 0)), elapsed=f"{view.get('elapsed_s', 0):.2f}초",
                  fps="미확정" if fps is None else f"{fps:.2f}",
                  target=("Correction Dataset" if view.get("demonstration_type") == "correction" else "Success Dataset")
                  if view.get("pending") else "—", pending="Pending : " + (view.get("pending") or "없음"))
    marker = view.get("correction")
    fields["marker"] = ("없음" if not marker else "교정 표시됨 — 대응 프레임 대기" if marker["start_frame_index"] is None
                        else f"{marker['start_frame_index']}프레임 / {marker['start_dataset_timestamp_s']:.2f}초 (0부터)")
    for name in CAMERAS:
        camera = view.get("cameras", {}).get(name, {})
        age = camera.get("age_ms")
        age = None if age is None else age + update_age_ms
        receiving = age is not None and 0 <= age <= 1000
        text = "미수신" if age is None else f"{'수신 중' if receiving else '수신 지연'} / 최근 {age:.0f}ms 전"
        if view["state"] == "SAVING":
            text = "저장 중 — 콜백 갱신 대기 / " + text
        fields[name] = text if view["running"] else "Recorder 수신 종료"
    return fields


class RecorderWindow:
    """Compact operator screen; technical data and entry forms are modeless dialogs."""
    def __init__(self, root, bridge):
        self.root, self.bridge = root, bridge
        self.close_after_busy = False
        self.view = {"state": "INITIALIZING", "running": True, "busy": False, "allowed": []}
        self.fields = {}
        self.dialogs, self._command_widgets = {}, []
        self._logs = deque(maxlen=200)
        self._notice_override, self._error_summary = None, ""
        self._start_failure = ""
        self._pending_notice = ""
        self._last_state = None
        self._reply_widget = self._details_log = None
        self._resize_job = None
        self._pending_size = self._last_size = BASE_SIZE
        self._popup_bases = {}
        root.title("ACT Recorder · Core-108")
        root.geometry("960x660")
        root.minsize(860, 640)
        root.configure(background="#f3f5f8")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._configure_styles()
        self.condition_id = tk.StringVar()
        self.metadata = tk.StringVar()
        self.notices = tk.StringVar(value="수집 장부를 확인하고 있습니다.")
        self.error_text = tk.StringVar()
        self.shell = ttk.Frame(root, style="Page.TFrame")
        self.shell.pack(fill="both", expand=True)
        self.content = ttk.Frame(self.shell, style="Page.TFrame")
        self.content.columnconfigure(0, weight=1)
        # Menus follow the recording area; spare height stays below the UI.
        self.content.rowconfigure(9, weight=0)

        header = ttk.Frame(self.content, style="Page.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="ACT RECORDER", style="Brand.TLabel").pack(side="left")
        self.label(header, "progress", style="Progress.TLabel").pack(side="right")
        self.progress_value = tk.DoubleVar(value=0)
        track = ttk.Frame(self.content, height=3, style="Page.TFrame")
        track.grid(row=1, column=0, sticky="ew", pady=(10, 18))
        track.pack_propagate(False)
        ttk.Progressbar(track, maximum=108, variable=self.progress_value,
                        style="Collection.Horizontal.TProgressbar").pack(fill="both", expand=True)

        condition = ttk.Frame(self.content, style="Card.TFrame", padding=(22, 14))
        condition.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        heading = ttk.Frame(condition, style="Card.TFrame")
        heading.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        ttk.Label(heading, text="현재 수집 조건", style="Heading.TLabel").pack(side="left")
        self.label(heading, "identity", style="Muted.TLabel").pack(side="right")
        self.condition_values = []
        for col, (key, title) in enumerate((("position", "바구니 위치"), ("yaw", "바구니 회전"), ("depth", "바구니 깊이"))):
            condition.columnconfigure(col, weight=1, uniform="placement")
            ttk.Label(condition, text=title, style="Muted.TLabel").grid(row=1, column=col, sticky="w", padx=(0, 12))
            label = self.label(condition, key, style="Placement.TLabel", wraplength=250)
            label.grid(row=2, column=col, sticky="nw", pady=(5, 0), padx=(0, 12))
            self.condition_values.append(label)

        record = ttk.Frame(self.content, style="Card.TFrame", padding=(22, 14))
        record.grid(row=3, column=0, sticky="ew")
        record.columnconfigure(0, weight=1)
        record.columnconfigure(1, weight=1)
        status = ttk.Frame(record, style="Card.TFrame")
        status.grid(row=0, column=0, sticky="w")
        self.indicator = ttk.Label(status, text="●", style="Indicator.TLabel")
        self.indicator.pack(side="left", padx=(0, 9))
        self.status_label = self.label(status, "state", style="State.TLabel")
        self.status_label.pack(side="left")
        self.label(record, "elapsed", style="Clock.TLabel").grid(row=0, column=1, sticky="e")
        self.mode_label = self.label(record, "mode", style="Muted.TLabel")
        self.mode_label.grid(row=1, column=0, sticky="w", pady=(7, 14))
        self.label(record, "target_note", style="Target.TLabel").grid(row=1, column=1, sticky="e", pady=(7, 14))
        self.controls = ttk.Frame(record, style="Card.TFrame")
        self.controls.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.buttons = {}
        for col, (command, text) in enumerate((("s", "녹화 시작  S"), ("r", "녹화 중지  R"), ("x", "교정 표시  X"),
                                               ("o", "저장  O"), ("d", "폐기  D"))):
            self.controls.columnconfigure(col, weight=1, uniform="actions")
            button = ttk.Button(self.controls, text=text, style="Action.TButton", width=1,
                                command=lambda c=command: self.action(c))
            button.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 7, 0))
            self.buttons[command] = button
        ttk.Label(record, text="실제 작업을 완료한 시연만 저장하세요.", style="Hint.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.error_card = ttk.Frame(self.content, style="Error.TFrame", padding=(14, 10))
        self.error_card.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self.error_card.columnconfigure(0, weight=1)
        self.error_label = ttk.Label(self.error_card, textvariable=self.error_text, style="Error.TLabel", wraplength=650)
        self.error_label.grid(row=0, column=0, sticky="w")
        ttk.Button(self.error_card, text="오류 상세", style="Small.TButton", command=self.show_details).grid(row=0, column=1, padx=(12, 0))
        self.ui_scale.capture(self.error_card)
        self.error_card.grid_remove()

        self.content.rowconfigure(6, weight=1)
        tools = ttk.Frame(self.content, style="Page.TFrame")
        tools.grid(row=7, column=0, sticky="ew", pady=(10, 10))
        for verb, text, callback in (("condition", "조건 선택", self.show_condition), ("progress", "수집 현황", self.show_progress),
                                     ("history", "이력 확인", self.show_history), (None, "상세 정보", self.show_details),
                                     ("placement", "배치·메모", self.show_placement)):
            button = ttk.Button(tools, text=text, style="Small.TButton", command=callback)
            button.pack(side="left", padx=(0, 6))
            if verb:
                # The dialog can still show frozen placement/result annotations in REVIEW.
                self._command_widgets.append((button, ("placement", "result") if verb == "placement" else (verb,)))
        ttk.Button(tools, text="종료", style="Small.TButton", command=self.close).pack(side="right")
        ttk.Separator(self.content).grid(row=8, column=0, sticky="ew")
        ttk.Label(self.content, textvariable=self.notices, style="Footer.TLabel", width=1,
                  anchor="w").grid(row=9, column=0, sticky="ew", pady=(10, 0))
        for command in "sxrod":
            root.bind(f"<KeyPress-{command}>", lambda event, c=command: self.shortcut(event, c))
        self.ui_scale.capture(self.shell)
        self._layout_size(*BASE_SIZE)
        root.bind("<Configure>", self._on_configure, add="+")
        root.bind("<Destroy>", self._on_destroy, add="+")
        self.refresh()

    def _configure_styles(self):
        families = set(tkfont.families(self.root))
        self.font_family = next((f for f in ("Noto Sans CJK KR", "맑은 고딕", "NanumGothic", "Malgun Gothic") if f in families),
                                tkfont.nametofont("TkDefaultFont").actual("family"))
        # Negative sizes are physical pixels: do not multiply 13pt by a desktop's 2x Tk scaling.
        self.ui_scale = UIScale(self.root, self.font_family)
        self.body_font = self.ui_scale.font("Body", 14)
        # An option-database '*Font' would override every ttk style, including
        # placement/status headings. Configure app-local named defaults instead.
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family=self.font_family, size=-14)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=self.body_font, foreground="#17223b")
        style.configure("Page.TFrame", background="#f3f5f8")
        style.configure("Card.TFrame", background="#ffffff", borderwidth=0)
        style.configure("TLabel", background="#ffffff", foreground="#17223b")
        for name, size, color, weight in (("Brand", 13, "#526179", "bold"), ("Progress", 14, "#17223b", "normal"),
                ("Heading", 14, "#17223b", "bold"), ("Muted", 12, "#526179", "normal"),
                ("Placement", 20, "#17223b", "bold"), ("State", 22, "#64748b", "bold"),
                ("Indicator", 18, "#64748b", "normal"), ("Clock", 30, "#17223b", "normal"),
                ("Target", 13, "#2454cc", "bold"), ("Hint", 12, "#526179", "normal"),
                ("Footer", 13, "#526179", "normal")):
            style.configure(name+".TLabel", font=self.ui_scale.font(name, size, weight), foreground=color,
                            background="#f3f5f8" if name in ("Brand", "Progress", "Footer") else "#ffffff")
        style.configure("Action.TButton", font=self.ui_scale.font("Action", 15, "bold"), padding=(8, 11),
                        background="#f0f3f8", foreground="#17223b", borderwidth=0, relief="flat", focusthickness=2)
        style.map("Action.TButton", background=[("disabled", "#eef1f5"), ("active", "#e0e6ef")],
                  foreground=[("disabled", "#7a8799")])
        style.configure("Primary.Action.TButton", background="#2454cc", foreground="#ffffff")
        style.map("Primary.Action.TButton", background=[("disabled", "#eef1f5"), ("active", "#1d43a5")],
                  foreground=[("disabled", "#7a8799"), ("!disabled", "#ffffff")])
        style.configure("Small.TButton", padding=(10, 7), font=self.ui_scale.font("Small", 13),
                        background="#e7ecf3", borderwidth=0, relief="flat")
        style.map("Small.TButton", background=[("active", "#dbe3ee")], foreground=[("disabled", "#7a8799")])
        style.configure("Error.TFrame", background="#fef0ef")
        style.configure("Error.TLabel", background="#fef0ef", foreground="#b4232c", font=self.ui_scale.font("Error", 13))
        style.configure("Collection.Horizontal.TProgressbar", troughcolor="#dfe5ee", background="#2454cc",
                        borderwidth=0, thickness=3)

    def _on_configure(self, event):
        if event.widget is not self.root or event.width < 2 or event.height < 2:
            return
        self._pending_size = (event.width, event.height)
        if self._resize_job is None and self._pending_size != self._last_size:
            # Coalesced debounce: latest size at most every 40ms, including during dragging.
            self._resize_job = self.root.after(RESIZE_DELAY_MS, self._apply_pending_size)

    def _apply_pending_size(self):
        self._resize_job = None
        self._layout_size(*self._pending_size)

    def _on_destroy(self, event):
        if event.widget is self.root and self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
            self._resize_job = None

    def _layout_size(self, window_width, window_height):
        self._last_size = (window_width, window_height)
        self.ui_scale.apply(window_scale(window_width, window_height))
        px = self.ui_scale.px
        margin = px(20)
        width = max(1, window_width-2*margin)
        self.content.place(x=margin, y=margin, width=width, height=max(1, window_height-2*margin))
        for label in self.condition_values:
            label.configure(wraplength=max(px(140), (width-px(44))//3-px(15)))
        self.error_label.configure(wraplength=max(px(250), width-px(170)))
        for window, base in list(self._popup_bases.items()):
            if not window.winfo_exists():
                del self._popup_bases[window]
                continue
            window.minsize(px(550), px(350))
            window.geometry(f"{min(px(base[0]), max(px(550), window_width-2*margin))}x"
                            f"{min(px(base[1]), max(px(350), window_height-2*margin))}")

    def _scale_new_dialog(self, window):
        if window.winfo_exists():
            self.ui_scale.capture(window)
            self._layout_size(*self._last_size)

    def label(self, parent, key, **kwargs):
        if key not in self.fields:
            self.fields[key] = tk.StringVar(value="—")
        return ttk.Label(parent, textvariable=self.fields[key], **kwargs)

    def _dialog(self, key, title, size="700x470"):
        window = self.dialogs.get(key)
        if window and window.winfo_exists():
            window.deiconify(); window.lift()
            return window, False
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry(size)
        window.minsize(550, 350)
        window.transient(self.root)
        window.configure(background="#ffffff")
        self.dialogs[key] = window
        self._popup_bases[window] = tuple(int(n) for n in size.split("x"))
        self.root.after_idle(lambda: self._scale_new_dialog(window))
        return window, True

    def _text(self, parent, **kwargs):
        widget = scrolledtext.ScrolledText(parent, wrap="word", font=self.body_font, relief="flat",
                                           background="#f7f9fc", foreground="#17223b", state="disabled", **kwargs)
        return widget

    @staticmethod
    def _write_text(widget, value):
        if widget is None or not widget.winfo_exists():
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def _command_button(self, parent, text, verb, callback):
        button = ttk.Button(parent, text=text, style="Small.TButton", command=callback)
        self._command_widgets.append((button, (verb,)))
        return button

    def show_condition(self):
        window, fresh = self._dialog("condition", "조건 선택", "550x350")
        if fresh:
            pane = ttk.Frame(window, padding=22, style="Card.TFrame"); pane.pack(fill="both", expand=True)
            ttk.Label(pane, text="미완료 조건 선택", style="Heading.TLabel").pack(anchor="w", pady=(0, 12))
            ttk.Label(pane, text="기존 수집 순서는 유지됩니다. 완료한 조건은 다시 선택할 수 없습니다.", wraplength=480).pack(anchor="w")
            ttk.Entry(pane, textvariable=self.condition_id, width=12).pack(anchor="w", pady=14)
            self._command_button(pane, "조건 선택", "condition", lambda: self.action(f"condition {self.condition_id.get()}" )).pack(anchor="w")
            ttk.Label(pane, textvariable=self.notices, wraplength=480, style="Muted.TLabel").pack(anchor="w", pady=14)
        self.refresh(schedule=False)

    def show_progress(self):
        window, fresh = self._dialog("progress", "수집 현황")
        if fresh:
            pane = ttk.Frame(window, padding=16, style="Card.TFrame"); pane.pack(fill="both", expand=True)
            toolbar = ttk.Frame(pane, style="Card.TFrame"); toolbar.pack(fill="x", pady=(0, 10))
            self._progress_text = self._text(pane, height=12); self._progress_text.pack(fill="both", expand=True)
            for command, title in (("progress", "현황 새로고침"), ("progress all", "전체 108개 조건")):
                self._command_button(toolbar, title, "progress", lambda c=command: self._query(c, self._progress_text)).pack(side="left", padx=(0, 8))
        self._query("progress", self._progress_text)

    def show_history(self):
        window, fresh = self._dialog("history", "조건별 시도 이력")
        if fresh:
            pane = ttk.Frame(window, padding=16, style="Card.TFrame"); pane.pack(fill="both", expand=True)
            toolbar = ttk.Frame(pane, style="Card.TFrame"); toolbar.pack(fill="x", pady=(0, 10))
            ttk.Label(toolbar, text="조건 ID").pack(side="left", padx=(0, 8))
            ttk.Entry(toolbar, textvariable=self.condition_id, width=8).pack(side="left", padx=(0, 8))
            self._history_text = self._text(pane, height=12); self._history_text.pack(fill="both", expand=True)
            for detail, title in (("", "이력 확인"), (" detail", "세부 장부")):
                self._command_button(toolbar, title, "history", lambda d=detail: self._query(f"history {self.condition_id.get()}{d}", self._history_text)).pack(side="left", padx=(0, 8))
        self.refresh(schedule=False)

    def show_placement(self):
        window, fresh = self._dialog("placement", "선택적 배치·검토 메모", "700x380")
        if fresh:
            pane = ttk.Frame(window, padding=22, style="Card.TFrame"); pane.pack(fill="both", expand=True)
            ttk.Label(pane, text="좌표 입력은 선택 사항입니다.", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(pane, text='미입력=null · 좌표 cm / 회전 degree\n예: actual_x=43.2 actual_y=null actual_yaw=-30 notes="측정 메모"', wraplength=640).pack(anchor="w", pady=12)
            ttk.Entry(pane, textvariable=self.metadata).pack(fill="x", pady=8)
            buttons = ttk.Frame(pane, style="Card.TFrame"); buttons.pack(anchor="w", pady=10)
            for verb, title in (("placement", "시작 전 배치 반영"), ("result", "검토 메모 반영")):
                self._command_button(buttons, title, verb, lambda v=verb: self.action(f"{v} {self.metadata.get()}" )).pack(side="left", padx=(0, 8))
            ttk.Label(pane, textvariable=self.notices, wraplength=640, style="Muted.TLabel").pack(anchor="w", pady=10)
        self.refresh(schedule=False)

    def show_details(self):
        window, fresh = self._dialog("details", "상세 정보 · 진단", "800x570")
        if fresh:
            pane = ttk.Frame(window, padding=16, style="Card.TFrame"); pane.pack(fill="both", expand=True)
            tabs = ttk.Notebook(pane); tabs.pack(fill="both", expand=True)
            technical = ttk.Frame(tabs, padding=16, style="Card.TFrame")
            diagnostic = ttk.Frame(tabs, padding=8, style="Card.TFrame")
            tabs.add(technical, text="수신·기록"); tabs.add(diagnostic, text="진단·로그")
            for row, (key, title) in enumerate(((CAMERAS[0], "Camera A"), (CAMERAS[1], "Camera B"),
                    ("frames", "기록 프레임"), ("fps", "측정 FPS"), ("marker", "교정 마커"), ("pending", "Pending"))):
                ttk.Label(technical, text=title, style="Muted.TLabel").grid(row=row, column=0, sticky="nw", pady=8, padx=(0, 15))
                self.label(technical, key, wraplength=560).grid(row=row, column=1, sticky="w", pady=8)
            ttk.Label(technical, text="수신 표시는 시작 검사 통과를 뜻하지 않습니다. FPS는 기록 기준시각 간 측정값입니다.",
                      wraplength=700, style="Muted.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=15)
            self._details_log = self._text(diagnostic); self._details_log.pack(fill="both", expand=True)
            toolbar = ttk.Frame(pane, style="Card.TFrame"); toolbar.pack(fill="x", pady=(10, 0))
            for verb, title in (("guide", "배치 기준"), ("help", "명령어 안내")):
                self._command_button(toolbar, title, verb, lambda v=verb: self._query(v, self._details_log)).pack(side="left", padx=(0, 8))
            self._detail_tabs, self._diagnostic_tab = tabs, diagnostic
        self._write_text(self._details_log, self._diagnostics())
        if self._error_summary or self.view["state"] in ("INVALID", "ERROR"):
            self._detail_tabs.select(self._diagnostic_tab)
        self.refresh(schedule=False)

    def _query(self, command, target):
        self._reply_widget = target
        self.action(command)

    def _diagnostics(self):
        import json
        snapshot = {key: self.view.get(key) for key in ("state", "pending", "integrity", "correction", "last_saved")}
        return (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n\n" +
                "\n\n".join(self._logs))[-100000:]

    def shortcut(self, event, command):
        if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text)):
            return
        self.action(command)

    def action(self, command):
        verb = command.split(maxsplit=1)[0]
        if self.view.get("busy") or verb not in self.view["allowed"]:
            return
        if verb == "o" and not messagebox.askyesno("시연 저장 확인",
                f"저장 대상: {display_fields(self.view)['target']}\n실제 작업을 성공적으로 완료했습니까?\n"
                "작업 실패 시 저장하지 말고 폐기하세요.", parent=self.root):
            return
        if verb == "d" and not messagebox.askyesno("시연 폐기", "현재 미저장 시도를 폐기합니까?", parent=self.root):
            return
        submitted = self.bridge.submit(command)
        if not submitted:
            self.notices.set("상태가 바뀌었거나 명령 처리 중입니다. 현재 상태를 확인하세요.")
        elif verb == "s":
            self._start_failure = ""
            self._error_summary = ""
            self._notice_override = None
        # Disable immediately; do not wait for the next Tk poll/double click.
        self.refresh(schedule=False)

    def close(self):
        view, messages = self.bridge.read()
        self.view = view
        self.append_messages(messages)
        if not view["running"]:
            self.root.destroy()
        elif view["busy"] or view["state"] == "INITIALIZING":
            self.close_after_busy = True
            self.notices.set("명령·저장 처리 완료를 기다린 뒤 종료를 요청합니다. 창을 강제로 종료하지 마세요.")
        else:
            self.close_after_busy = False
            self.bridge.submit("q")
            self.notices.set("종료 요청: 미저장 REVIEW는 저장 또는 폐기해야 합니다.")

    def append_messages(self, messages):
        for message in messages:
            self._logs.append(str(message))
            notice = message_notice(str(message))
            if notice:
                self._notice_override, error = notice
                self._error_summary = self._notice_override if error else ""
                if error and str(message).startswith(("[시작 검사] 취소:", "[시작 검사] 실패:")):
                    self._start_failure = self._notice_override
                if str(message).startswith(("[미저장 시연]", "[복구 기록]")) or "[정리 경고]" in str(message):
                    self._pending_notice = self._notice_override
        if messages:
            self._write_text(self._details_log, self._diagnostics())
            self._write_text(self._reply_widget, "\n\n".join(messages))
            self._reply_widget = None

    def refresh(self, *, schedule=True):
        self.view, messages = self.bridge.read()
        if self.view["state"] != self._last_state:
            self._notice_override, self._error_summary = None, self._pending_notice
            self._last_state = self.view["state"]
        self.append_messages(messages)
        values = display_fields(self.view)
        operator = operator_fields(self.view)
        values.update({key: operator[key] for key in ("progress", "state", "elapsed", "mode", "target_note")})
        condition = self.view.get("condition")
        values["identity"] = (f"순서 {condition['collection_order']:03d} / 108   ·   조건 {condition['condition_id']:03d}   ·   {condition['attempt_number']}회차"
                              if condition else "정상 108개 수집 완료" if (self.view.get("stats") or {}).get("success") == 108 else "조건 확인 중")
        start_failed = bool(self._start_failure) and self.view["state"] == "READY"
        if start_failed:
            values["state"] = "녹화 시작 실패 · 재시도 가능"
        for key, value in values.items():
            if key not in self.fields:
                self.fields[key] = tk.StringVar()
            self.fields[key].set(value)
        self.progress_value.set((self.view.get("stats") or {}).get("success", 0))
        status_color = "#b4232c" if start_failed else operator["state_color"]
        self.indicator.configure(foreground=status_color)
        self.status_label.configure(foreground=status_color)
        self.mode_label.configure(foreground="#6442a3" if operator["mode"].startswith("교정") else "#526179")
        self.notices.set(self._start_failure if start_failed else self._notice_override or operator["default_notice"])
        alert = operator["alert"] if self.view["state"] == "INVALID" else self._error_summary or operator["alert"] or self._pending_notice
        if start_failed:
            alert = self._start_failure
        if alert:
            pending_note = "Pending 보존 · 오류 상세에서 경로를 확인하세요." if self.view.get("pending") else "오류 상세에서 원문과 복구 안내를 확인하세요."
            # Keep the main alert to two lines even for a long parser exception.
            # Full error text/paths remain untouched in the diagnostics dialog.
            summary = alert if len(alert) <= 44 else alert[:43] + "…"
            self.error_text.set(summary + "\n" + pending_note)
            if not self.error_card.winfo_manager():
                self.error_card.grid()
                self._layout_size(*self._last_size)
        else:
            self.error_card.grid_remove()
        primary = {"READY": "s", "RECORDING": "r", "PRESTART": "r", "REVIEW": "o"}.get(self.view["state"])
        for command, button in self.buttons.items():
            allowed = command in self.view["allowed"] and not self.view["busy"]
            button.configure(state="normal" if allowed else "disabled",
                             style="Primary.Action.TButton" if allowed and command == primary else "Action.TButton")
        surviving = []
        for button, verbs in self._command_widgets:
            if button.winfo_exists():
                button.configure(state="normal" if not self.view["busy"] and any(v in self.view["allowed"] for v in verbs) else "disabled")
                surviving.append((button, verbs))
        self._command_widgets = surviving
        if self.close_after_busy and not self.view["busy"] and self.view["state"] != "INITIALIZING":
            self.close_after_busy = False
            if self.view["running"]:
                self.bridge.submit("q")
            else:
                self.root.destroy()
                return
        if not self.view["running"] and (self.view.get("exit_code") == 0 or self.bridge.abort.is_set()):
            self.root.destroy()
            return
        if schedule:
            self.root.after(100, self.refresh)


def main(argv=None):
    parser = argparse.ArgumentParser(description="기존 Indy7 Recorder의 Core-108 데스크톱 UI (로봇 제어 없음)")
    parser.add_argument("--collection-dir", type=Path, default=Path("datasets/indy7_act_core108"))
    args = parser.parse_args(argv)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        parser.exit(1, f"GUI 디스플레이에 연결할 수 없습니다: {exc}\n로컬 데스크톱 또는 승인된 X 전달 환경이 필요합니다.\n")
    bridge = RecorderBridge()
    app = RecorderWindow(root, bridge)
    worker = threading.Thread(target=run_engine, args=(bridge, ["--collection-dir", str(args.collection_dir)]),
                              name="act-recorder-engine", daemon=False)
    previous = {}
    def interrupted(signum, frame):
        # A signal can interrupt a Tk read while the bridge lock is held.
        # Do not log or acquire that lock here; the engine handles EOF preservation.
        bridge.abort.set()
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, interrupted)
    worker.start()
    try:
        root.mainloop()
    finally:
        # No thread kill or forced shutdown halfway through Dataset commit.
        bridge.abort.set()
        worker.join()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return bridge.read()[0].get("exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
