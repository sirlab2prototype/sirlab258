"""Tk-main-thread-only responsive sizing, independent of Recorder/ROS state.

Scale = clamp(min(client_width/960, client_height/660), .85, 3.0).
Fonts use negative pixel sizes, so desktop Tk DPI is not applied a second time.
Original widget spacing is captured once: resizing never compounds rounding or
recreates widgets. Configure bursts coalesce into one latest-size update/40ms.
"""
import tkinter as tk
from tkinter import font as tkfont, ttk

BASE_SIZE = (960, 660)
MIN_SCALE, MAX_SCALE = .85, 3.0
RESIZE_DELAY_MS = 40


def window_scale(width, height):
    return max(MIN_SCALE, min(MAX_SCALE, width / BASE_SIZE[0], height / BASE_SIZE[1]))


class UIScale:
    def __init__(self, root, family):
        self.root, self.family = root, family
        self.factor = 1.0
        self.fonts, self._font_bases = {}, {}
        self._widgets = {}
        self._defaults = [tkfont.nametofont(n, root=root) for n in
                          ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                           "TkCaptionFont", "TkSmallCaptionFont", "TkTooltipFont")]
        self.apply_count = 0

    def px(self, value):
        return max(1, round(value * self.factor)) if value else 0

    def font(self, name, size, weight="normal"):
        self._font_bases[name] = size
        self.fonts[name] = tkfont.Font(root=self.root, family=self.family, size=-self.px(size), weight=weight)
        return self.fonts[name]

    @staticmethod
    def _numbers(value):
        if isinstance(value, (tuple, list)):
            return tuple(int(float(str(v))) for v in value)
        return tuple(int(float(v)) for v in str(value).split())

    def capture(self, widget):
        """Capture coded 100% spacing exactly once, also for new popup children."""
        if widget not in self._widgets:
            geometry = {}
            manager = widget.winfo_manager()
            if manager in ("grid", "pack"):
                # ScrolledText delegates its geometry methods to its wrapper.
                # Use unbound Tk methods to capture the actual widget instead.
                info = tk.Grid.grid_info(widget) if manager == "grid" else tk.Pack.pack_info(widget)
                geometry = {k: self._numbers(info[k]) for k in ("padx", "pady", "ipadx", "ipady") if k in info}
            options = {}
            if isinstance(widget, ttk.Frame):
                padding = self._numbers(widget.cget("padding"))
                if padding:
                    options["padding"] = padding
                # Only the progress track has an explicitly coded pixel height.
                if int(widget.cget("height")):
                    options["height"] = (int(widget.cget("height")),)
            if isinstance(widget, ttk.Label) and str(widget.cget("wraplength")) not in ("", "0"):
                options["wraplength"] = (int(widget.cget("wraplength")),)
            if isinstance(widget, tk.Scrollbar):
                options["width"] = (int(str(widget.cget("width"))),)
            if isinstance(widget, tk.Text):
                for k in ("padx", "pady", "borderwidth", "insertwidth"):
                    options[k] = (int(str(widget.cget(k))),)
            self._widgets[widget] = (manager, geometry, options)
        for child in widget.winfo_children():
            # Popups are registered only after their forms have been built.
            if not isinstance(child, tk.Toplevel):
                self.capture(child)

    def apply(self, factor):
        changed = abs(factor - self.factor) > .0001
        self.factor = factor
        self.apply_count += 1
        if changed:
            for name, font in self.fonts.items():
                font.configure(size=-self.px(self._font_bases[name]))
        for font in self._defaults:
            font.configure(family=self.family, size=-self.px(14))
        style = ttk.Style(self.root)
        style.configure("Action.TButton", padding=(self.px(8), self.px(11)), focusthickness=self.px(2))
        style.configure("Small.TButton", padding=(self.px(10), self.px(7)))
        style.configure("Collection.Horizontal.TProgressbar", thickness=self.px(3))
        style.configure("TNotebook.Tab", padding=(self.px(12), self.px(7)))
        style.configure("Vertical.TScrollbar", arrowsize=self.px(14))
        surviving = {}
        for widget, (manager, geometry, options) in self._widgets.items():
            if not widget.winfo_exists():
                continue
            scaled = {k: tuple(self.px(v) for v in values) for k, values in geometry.items()}
            if scaled:
                if manager == "grid":
                    # Preserve grid_remove() of the hidden error card.
                    if widget.winfo_manager() == "grid":
                        tk.Grid.grid_configure(widget, **scaled)
                elif manager == "pack":
                    tk.Pack.pack_configure(widget, **scaled)
            if options:
                widget.configure(**{k: tuple(self.px(v) for v in vals) if k == "padding" else self.px(vals[0])
                                    for k, vals in options.items()})
            surviving[widget] = (manager, geometry, options)
        self._widgets = surviving
