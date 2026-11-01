"""Real Tk responsive scaling; scalar Fake bridge only, never start an engine."""
from copy import deepcopy
import tkinter as tk
from tkinter import font as tkfont, ttk

import pytest

from ai.record.gui_scaling import MIN_SCALE, MAX_SCALE, window_scale
from ai.record.test.test_gui_design import window, view_for, settle


@pytest.mark.parametrize("width,height,expected", [(960,660,1), (860,720,860/960),
    (1280,900,1280/960), (1920,1080,1080/660), (3000,720,720/660),
    (860,1600,860/960), (1,1,MIN_SCALE), (10000,10000,MAX_SCALE)])
def test_scale_uses_both_axes_and_is_bounded(width, height, expected):
    assert window_scale(width, height) == pytest.approx(expected)


def metrics(root, app):
    return {"scale": app.ui_scale.factor, "fonts": {k:int(f.cget("size")) for k,f in app.ui_scale.fonts.items()},
            "button": (app.buttons["s"].winfo_width(), app.buttons["s"].winfo_height()),
            "padding": tuple(str(v) for v in ttk.Style(root).lookup("Action.TButton", "padding")),
            "indicator": app.indicator.winfo_height(), "timer": app.fields["elapsed"].get()}


def test_font_button_indicator_padding_and_content_really_grow(window):
    root, bridge, app = window
    original_widgets = {k:str(v) for k,v in app.buttons.items()}
    original_fonts = {k:str(v) for k,v in app.ui_scale.fonts.items()}
    dpi = float(root.tk.call("tk", "scaling"))
    results = []
    for width,height in ((860,720), (1280,900), (1920,1080)):
        root.geometry(f"{width}x{height}+100+100"); settle(root)
        results.append(metrics(root, app))
        # Tk's native messagebox uses TkCaptionFont for confirmation text.
        assert tkfont.nametofont("TkCaptionFont", root=root).cget("size") == app.body_font.cget("size")
        assert app.content.winfo_width() > root.winfo_width()*.9
        assert app.content.winfo_reqheight() <= app.content.winfo_height()
    small, middle, large = results
    assert small["scale"] < middle["scale"] < large["scale"]
    for name in app.ui_scale.fonts:
        assert abs(small["fonts"][name]) < abs(middle["fonts"][name]) < abs(large["fonts"][name]), name
    assert large["button"][1] > small["button"][1]*1.6
    assert large["indicator"] > small["indicator"]*1.5
    assert large["padding"] != small["padding"]
    assert app.content.winfo_width() > 1800  # Old fixed 1000px canvas must fail this.
    assert {k:str(v) for k,v in app.buttons.items()} == original_widgets
    assert {k:str(v) for k,v in app.ui_scale.fonts.items()} == original_fonts
    assert float(root.tk.call("tk", "scaling")) == dpi
    assert bridge.commands.empty()


@pytest.mark.parametrize("width,height", [(860,720), (1280,900), (1920,1080), (3000,720), (860,1600)])
def test_long_depth_error_footer_and_all_buttons_fit_at_each_aspect(window, width, height):
    from ai.record.collection import DEPTH_LABELS
    root, bridge, app = window
    root.geometry(f"{width}x{height}+100+100"); settle(root)
    for code, text in DEPTH_LABELS.items():
        v = view_for("INVALID", correction=True)
        v["condition"]["depth"] = f"{text} ({code})"
        bridge.set_view(v); app.refresh(schedule=False); root.update()
        for label in app.condition_values:
            assert label.winfo_height() >= label.winfo_reqheight()
        assert app.content.winfo_reqheight() <= app.content.winfo_height(), code
        for button in app.buttons.values():
            assert button.winfo_rooty()+button.winfo_height() <= root.winfo_rooty()+root.winfo_height()
            assert button.winfo_rootx()+button.winfo_width() <= root.winfo_rootx()+root.winfo_width()
        assert app.error_card.winfo_ismapped()
    assert bridge.commands.empty()


def test_maximize_restore_and_repeated_sizes_do_not_accumulate_rounding(window):
    root, bridge, app = window
    settle(root)
    baseline = metrics(root, app)
    root.attributes("-zoomed", True); settle(root, 300)
    assert bool(root.attributes("-zoomed")), "Window manager did not maximize the real Tk window"
    assert app.ui_scale.factor > 1 and app.content.winfo_width() > 1000
    assert app.content.winfo_reqheight() <= app.content.winfo_height()
    root.attributes("-zoomed", False)
    for width,height in ((1920,1080),(860,720),(1280,900),(960,660)):
        root.geometry(f"{width}x{height}+100+100"); settle(root)
    assert metrics(root, app) == baseline
    assert bridge.commands.empty()


def test_resize_burst_coalesces_without_changing_recording_or_pending(window):
    root, bridge, app = window
    view = view_for("RECORDING", correction=True)
    bridge.set_view(view);app.refresh(schedule=False);settle(root)
    original = deepcopy(app.view)
    count = app.ui_scale.apply_count
    for i in range(35):
        root.geometry(f"{1000+i*10}x{700+i*5}+100+100")
        root.update_idletasks()
    settle(root)
    assert app.ui_scale.apply_count-count <= 5
    assert app.ui_scale.factor == pytest.approx(window_scale(root.winfo_width(),root.winfo_height()))
    assert app.view == original and bridge.commands.empty()
    assert app.buttons["r"].instate(["!disabled"]) and app.buttons["x"].instate(["disabled"])
    assert app.view["correction"] == view["correction"] and app.view["pending"] == view["pending"]


def test_open_details_fonts_spacing_and_window_scale_with_parent(window):
    root, bridge, app = window
    app.show_details(); settle(root)
    dialog = app.dialogs["details"]
    text = app._details_log
    small = (dialog.winfo_width(), dialog.winfo_height(), app.body_font.cget("size"))
    root.geometry("1920x1080+100+100"); settle(root)
    large = (dialog.winfo_width(), dialog.winfo_height(), app.body_font.cget("size"))
    assert large[0] > small[0]*1.5 and large[1] > small[1]*1.5
    assert abs(int(large[2])) > abs(int(small[2]))
    assert text.cget("font") == str(app.body_font)
    assert app._details_log is text  # An open diagnostic widget is never recreated.
    for method in (app.show_condition, app.show_history, app.show_placement): method()
    settle(root)
    assert all(w.winfo_exists() for w in app.dialogs.values())
    for w in app.dialogs.values(): w.destroy()
    root.geometry("960x660+100+100"); settle(root)
    assert bridge.commands.empty()
