"""Read-only dataset selector. Creation uses a validated file on first open."""
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
from ai.record.collection import PROJECT
from ai.record.dataset_manager import DatasetManager, ERRORS
from ai.record.gui_scaling import UIScale, window_scale, configure_initial_window, RESIZE_DELAY_MS


class DatasetManagerWindow:
    def __init__(self, root, on_open, manager=None):
        self.root, self.on_open = root, on_open
        self.manager = manager or DatasetManager()
        root.title("Indy7 ACT · 데이터셋 선택")
        initial_size = configure_initial_window(root, minimum=(720, 420))
        family = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
        # Pixel sizes avoid multiplying the desktop's Tk DPI a second time.
        self.ui_scale = UIScale(root, family)
        self.body_font = self.ui_scale.font("ManagerBody", 14)
        self.heading_font = self.ui_scale.font("ManagerHeading", 14, "bold")
        self.title_font = self.ui_scale.font("ManagerTitle", 20, "bold")
        style = ttk.Style(root)
        style.configure("Dataset.Treeview", font=self.body_font, rowheight=30)
        style.configure("Dataset.Treeview.Heading", font=self.heading_font)
        style.configure("Dataset.TButton", font=self.body_font, padding=(12, 8))
        self.frame = ttk.Frame(root, padding=16); self.frame.pack(fill="both", expand=True)
        self.frame.columnconfigure(0, weight=1); self.frame.rowconfigure(2, weight=1)
        ttk.Label(self.frame, text="사용할 데이터셋을 선택하세요", font=self.title_font).grid(row=0, column=0, sticky="w")
        self.hint = ttk.Label(self.frame, font=self.body_font,
            text="정의 JSON은 파일에서 관리합니다. 녹화 화면 진입 전에는 ROS를 시작하지 않습니다.")
        self.hint.grid(row=1, column=0, sticky="ew", pady=(6, 12))
        table = ttk.Frame(self.frame); table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1); table.rowconfigure(0, weight=1)
        columns = ("dataset", "action", "total", "success", "correction", "status")
        self._column_bases = dict(zip(columns, (200, 300, 95, 85, 100, 95)))
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse", style="Dataset.Treeview")
        for key, title, width in zip(columns, ("데이터셋 ID", "행동 이름", "계획 조건", "Success", "Correction", "상태"), (200, 300, 95, 85, 100, 95)):
            self.tree.heading(key, text=title); self.tree.column(key, width=width, minwidth=width, stretch=False)
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns"); horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self.selection)
        self.notice = tk.StringVar(value="데이터셋을 선택하세요.")
        # Scrollable details retain full paths, long descriptions and parse errors.
        self.details = tk.Text(self.frame, height=5, wrap="word", font=self.body_font, state="disabled")
        detail_scroll = ttk.Scrollbar(self.frame, orient="vertical", command=self.details.yview)
        self.details.configure(yscrollcommand=detail_scroll.set)
        self.details.grid(row=3, column=0, sticky="ew", pady=10)
        detail_scroll.grid(row=3, column=1, sticky="ns", pady=10)
        toolbar = ttk.Frame(self.frame); toolbar.grid(row=4, column=0, sticky="ew")
        self.refresh_button = ttk.Button(toolbar, text="목록 새로고침", command=self.refresh, style="Dataset.TButton")
        self.refresh_button.pack(side="left")
        self.open_button = ttk.Button(toolbar, text="선택한 데이터셋 열기", command=self.open_selected, style="Dataset.TButton", state="disabled")
        self.open_button.pack(side="right")
        self._resize_job = None
        self._last_size = self._pending_size = initial_size
        self.ui_scale.capture(self.frame)
        self.frame.bind("<Configure>", self.resize)
        self.frame.bind("<Destroy>", self.destroyed)
        self.rows = {}; self.refresh()
        self._layout_size(*initial_size)

    def resize(self, event):
        if event.widget is not self.frame or event.width < 2 or event.height < 2:
            return
        self._pending_size = (event.width, event.height)
        if self._resize_job is None and self._pending_size != self._last_size:
            self._resize_job = self.root.after(RESIZE_DELAY_MS, self._apply_pending_size)

    def _apply_pending_size(self):
        self._resize_job = None
        self._layout_size(*self._pending_size)

    def destroyed(self, event):
        if event.widget is self.frame and self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
            self._resize_job = None

    def _layout_size(self, width, height):
        self._last_size = (width, height)
        self.ui_scale.apply(window_scale(width, height))
        self.hint.configure(wraplength=max(100, width - self.ui_scale.px(32)))
        self._layout_columns(width)

    def _layout_columns(self, width):
        px = self.ui_scale.px
        sizes = {}
        for column, base in self._column_bases.items():
            values = [self.tree.heading(column, "text")]
            values.extend(self.tree.set(item, column) for item in self.tree.get_children())
            sizes[column] = max(px(base), max(self.body_font.measure(str(v)) for v in values) + px(24))
        # Normal lists fill wide windows; long descriptions retain horizontal scrolling.
        sizes["action"] += max(0, width - px(60) - sum(sizes.values()))
        for column, size in sizes.items():
            self.tree.column(column, width=size, minwidth=px(self._column_bases[column]))

    def show(self, text):
        self.notice.set(text)
        self.details.configure(state="normal"); self.details.delete("1.0", "end")
        self.details.insert("end", text); self.details.configure(state="disabled")

    def error(self, exc):
        self.show(str(exc))
        messagebox.showerror("데이터셋 선택 오류", str(exc), parent=self.root)

    def refresh(self):
        selected = self.tree.selection()
        key = self.rows.get(selected[0], {}).get("key") if selected else None
        self.tree.delete(*self.tree.get_children()); self.rows = {}
        self.open_button.configure(state="disabled")
        try:
            for row in self.manager.list_datasets():
                item = self.tree.insert("", "end", values=(row["id"], row["action"], row["total"], row["success"], row["correction"], row["status"]))
                self.rows[item] = row
                if row["key"] == key: self.tree.selection_set(item)
            self.show(f"정의 폴더: {self.manager.plans}\n데이터셋을 선택하세요. JSON 오류는 해당 행을 선택하면 볼 수 있습니다.")
            self.selection()
            self._layout_columns(self._last_size[0])
        except ERRORS as exc: self.error(exc)

    def selection(self, event=None):
        selected = self.tree.selection()
        if selected and selected[0] in self.rows:
            row = self.rows[selected[0]]
            self.open_button.configure(state="disabled" if row["error"] else "normal")
            self.show(row["error"] or f"{row['id']} · {row['action']}\nSuccess {row['success']}/{row['total']} · Correction {row['correction']} · {row['status']}\n{row['path']}\n{row['detail'] or '처음 열면 JSON 스냅샷으로 Collection을 생성합니다.'}")

    def open_selected(self):
        selected = self.tree.selection()
        if not selected:
            self.show("데이터셋을 먼저 선택하세요."); return
        try:
            row = self.rows[selected[0]]
            if row["error"]: raise ValueError(row["error"])
            path = row["path"]
            if not path.is_relative_to(PROJECT) or not path.resolve().is_relative_to(PROJECT):
                raise ValueError("프로젝트 내부 Collection만 열 수 있습니다")
            path = self.manager.open_dataset(row)
            self.frame.destroy(); self.on_open(path)
        except ERRORS as exc: self.error(exc)
