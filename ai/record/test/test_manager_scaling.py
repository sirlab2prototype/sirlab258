"""Real Tk sizing with isolated JSON fixtures; never start Recorder/ROS."""
from copy import deepcopy
from tkinter import ttk
import pytest
from ai.record.gui_scaling import (INITIAL_SIZE, configure_initial_window, initial_window_size, window_scale)
from ai.record.test.test_dataset_manager import manager, tkroot, register
from ai.record.test.test_gui_design import settle, PreviewBridge, view_for


@pytest.mark.parametrize('screen,maximum', [((2560,1440),(2560,1400)),((1920,1080),(1920,1040)),
    ((1280,720),(1280,680)),((1024,768),(1024,728)),((800,600),(800,560)),((640,480),(640,440))])
def test_initial_size_fits_screen_and_window_manager(screen,maximum):
    width,height=initial_window_size(*screen,maximum=maximum)
    assert 0<width<=min(screen[0],maximum[0])-48
    assert 0<height<=min(screen[1],maximum[1])-80
    if screen[0]>=1920:assert (width,height)==(1300,820)


@pytest.mark.parametrize('kind',['selector','recorder'])
def test_both_windows_start_at_shared_target_and_scale_content(manager,tmp_path,tkroot,monkeypatch,kind):
    monkeypatch.setattr(tkroot,'winfo_screenwidth',lambda:2560)
    monkeypatch.setattr(tkroot,'winfo_screenheight',lambda:1440)
    monkeypatch.setattr(tkroot,'maxsize',lambda:(2560,1400))
    if kind=='selector':
        from ai.record.manager_gui import DatasetManagerWindow
        app=DatasetManagerWindow(tkroot,lambda path:None,manager)
    else:
        from ai.record.gui import RecorderWindow
        bridge=PreviewBridge();bridge.set_view(view_for())
        app=RecorderWindow(tkroot,bridge)
    assert tkroot.geometry().split('+')[0]=='1300x820' or app.ui_scale.factor==pytest.approx(window_scale(*INITIAL_SIZE))
    tkroot.deiconify();settle(tkroot)
    assert (tkroot.winfo_width(),tkroot.winfo_height())==INITIAL_SIZE
    assert app.ui_scale.factor==pytest.approx(window_scale(*INITIAL_SIZE))
    assert abs(app.body_font.cget('size'))>14


@pytest.fixture
def selector(manager,tmp_path,tkroot,monkeypatch):
    from ai.record import manager_gui
    monkeypatch.setattr(manager_gui,'PROJECT',tmp_path)
    register(manager,tmp_path)
    app=manager_gui.DatasetManagerWindow(tkroot,lambda path:pytest.fail('resize cannot open a dataset'),manager)
    tkroot.deiconify();settle(tkroot)
    return tkroot,app,manager


def selector_metrics(root,app):
    style=ttk.Style(root)
    return {'factor':app.ui_scale.factor,'font':abs(app.body_font.cget('size')),
            'title':abs(app.title_font.cget('size')),'heading':abs(app.heading_font.cget('size')),
            'row':int(style.lookup('Dataset.Treeview','rowheight')),
            'button':app.open_button.winfo_height(),'padding':str(style.lookup('Dataset.TButton','padding')),
            'details':app.details.winfo_height(),'column':int(app.tree.column('dataset','width'))}


def assert_inside(root,widget):
    x=widget.winfo_rootx()-root.winfo_rootx();y=widget.winfo_rooty()-root.winfo_rooty()
    assert x>=0 and y>=0
    assert x+widget.winfo_width()<=root.winfo_width()
    assert y+widget.winfo_height()<=root.winfo_height()


def test_selector_fonts_rows_buttons_spacing_columns_grow_and_shrink(selector):
    root,app,manager=selector
    rows=deepcopy(app.rows);widgets=[str(app.tree),str(app.open_button),str(app.details)]
    fonts={k:str(f) for k,f in app.ui_scale.fonts.items()}
    item=app.tree.get_children()[0];app.tree.selection_set(item);app.selection()
    selected=app.tree.selection()
    sizes=((720,420),(960,660),(1300,820),(1920,1080))
    metrics=[]
    for width,height in sizes:
        root.geometry(f'{width}x{height}');settle(root)
        metrics.append(selector_metrics(root,app))
        assert_inside(root,app.tree);assert_inside(root,app.open_button);assert_inside(root,app.refresh_button)
        assert app.tree.winfo_height()>100
        assert app.rows==rows and app.tree.selection()==selected
        assert app.details.cget('font')==str(app.body_font)
        assert app.ui_scale.factor==pytest.approx(window_scale(width,height))
    for a,b in zip(metrics,metrics[1:]):
        for key in ('font','title','heading','row','button','column','details'):assert a[key]<b[key],key
        assert a['padding']!=b['padding']
    for width,height in reversed(sizes):
        root.geometry(f'{width}x{height}');settle(root)
    assert selector_metrics(root,app)==metrics[0]
    assert widgets==[str(app.tree),str(app.open_button),str(app.details)]
    assert fonts=={k:str(f) for k,f in app.ui_scale.fonts.items()}
    assert not manager.datasets.exists()


def test_resize_bursts_refresh_and_destroy_cancel_pending_callbacks(selector):
    root,app,manager=selector
    calls=app.ui_scale.apply_count
    for i in range(30):
        root.geometry(f'{1000+i*5}x{680+i*3}');root.update_idletasks()
    settle(root)
    assert app.ui_scale.apply_count-calls<=5
    before=selector_metrics(root,app);app.refresh();settle(root)
    assert selector_metrics(root,app)==before
    root.geometry('1500x900');root.update_idletasks()
    app.frame.destroy();settle(root)
    assert app._resize_job is None
    assert not manager.datasets.exists()


@pytest.mark.parametrize('screen',[(800,600),(1024,768),(1280,720)])
@pytest.mark.parametrize('kind',['selector','recorder'])
def test_small_screen_initial_controls_fit(manager,tmp_path,tkroot,monkeypatch,screen,kind):
    monkeypatch.setattr(tkroot,'winfo_screenwidth',lambda:screen[0])
    monkeypatch.setattr(tkroot,'winfo_screenheight',lambda:screen[1])
    monkeypatch.setattr(tkroot,'maxsize',lambda:(screen[0],screen[1]-40))
    if kind=='selector':
        from ai.record.manager_gui import DatasetManagerWindow
        app=DatasetManagerWindow(tkroot,lambda path:None,manager)
        controls=(app.refresh_button,app.open_button)
    else:
        from ai.record.gui import RecorderWindow
        bridge=PreviewBridge();bridge.set_view(view_for('INVALID',correction=True))
        app=RecorderWindow(tkroot,bridge)
        controls=tuple(app.buttons.values())
    tkroot.deiconify();settle(tkroot)
    assert tkroot.winfo_width()<screen[0] and tkroot.winfo_height()<screen[1]
    for widget in controls:assert_inside(tkroot,widget)
    if kind=='recorder':assert app.content.winfo_reqheight()<=app.content.winfo_height()
