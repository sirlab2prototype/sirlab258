"""File-based discovery, Tk selector and isolated synthetic Recorder contracts."""
from copy import deepcopy
import json
from pathlib import Path
import tkinter as tk
import pytest
from ai.record.collection import Collection, CollectionError, tree_fingerprint, load_plan
from ai.record.dataset_manager import DatasetManager, read_state
from ai.record.plans import validate_plan, read_json, builtin_plan, validate_definition, BUILTIN_ACTION
from ai.record.test.test_indy7_act_record import recorder
from ai.record.test.test_collection import take


def plan(count=3, plan_id="custom3"):
    return {"version":1,"id":plan_id,"description":"사용자 작업 조건","kind":"custom",
            "conditions":[{"condition_id":1000+i,"collection_order":i+1,"description":f"물체 {i+1} 준비",
                           "fields":{"object":f"item{i}","height_cm":i}} for i in range(count)]}


def definition(count=3, dataset_id=None):
    return {"version":1,"dataset_id":dataset_id or f"act2_{count:03d}_001","robot":"indy7","policy":"act",
            "action":{"id":"act2","description":"새로운 작업 수행"},"plan":plan(count)}


@pytest.fixture
def manager(tmp_path):
    return DatasetManager(tmp_path/'outputs/preserved/datasets',tmp_path/'definitions',legacy=())


def register(manager,tmp_path,value=None):
    item=definition();item['plan']=value or plan()
    item['dataset_id']=f"act2_{len(item['plan']['conditions']):03d}_001"
    manager.plans.mkdir(parents=True,exist_ok=True)
    path=manager.plans/(item['dataset_id']+'.json');path.write_text(json.dumps(item))
    return path


def create(manager,action_id,plan_id,dataset_id):
    manager.plans.mkdir(parents=True,exist_ok=True)
    path=manager.plans/(dataset_id+'.json')
    if not path.exists():
        value=definition(dataset_id=dataset_id)
        value['action']=BUILTIN_ACTION if action_id=='act1' else value['action']
        value['plan']=builtin_plan() if plan_id=='core108' else plan()
        path.write_text(json.dumps(value))
    row=next(r for r in manager.list_datasets() if r['source']==path)
    return manager.open_dataset(row)


@pytest.mark.parametrize('damage', ['empty','duplicate_id','duplicate_order','missing_order','zero_id','bool_id','description','fields','kind','version','extra','nan'])
def test_invalid_plans(damage):
    value = plan()
    if damage == 'empty': value['conditions'] = []
    elif damage == 'duplicate_id': value['conditions'][1]['condition_id'] = 1000
    elif damage == 'duplicate_order': value['conditions'][1]['collection_order'] = 1
    elif damage == 'missing_order': value['conditions'][1]['collection_order'] = 7
    elif damage == 'zero_id': value['conditions'][0]['condition_id'] = 0
    elif damage == 'bool_id': value['conditions'][0]['condition_id'] = True
    elif damage == 'description': value['conditions'][0]['description'] = ' '
    elif damage == 'fields': value['conditions'][0]['fields'] = []
    elif damage == 'kind': value['kind'] = 'robot2'
    elif damage == 'version': value['version'] = True
    elif damage == 'extra': value['conditions'][0]['position_region'] = 'L'
    elif damage == 'nan': value['conditions'][0]['fields']['x'] = float('nan')
    with pytest.raises(ValueError): validate_plan(value)

def test_duplicate_json_and_nonfinite(tmp_path):
    path = tmp_path/'bad.json'
    for content in ('{"id":"a","id":"b"}', '{"x":NaN}', '{"x":Infinity}'):
        path.write_text(content)
        with pytest.raises(ValueError): read_json(path)

@pytest.mark.parametrize('damage', ['conditions','action','hash','name'])
def test_tampered_snapshot_blocks_resume(manager, tmp_path, damage):
    register(manager,tmp_path)
    directory = create(manager,'act2','custom3','act2_003_001')
    p = directory/'collection.json'; state = json.loads(p.read_text())
    if damage == 'conditions': state['conditions'][0]['description'] = '변경'
    elif damage == 'action': state['definition']['action']['description'] = '변경'
    elif damage == 'hash': state['definition_sha256'] = 'wrong'
    else: state['definition']['dataset_id'] = 'act2_003_002'
    p.write_text(json.dumps(state))
    with pytest.raises(ValueError): Collection(directory)
    assert manager.list_datasets()[0]['error']

def test_custom_success_correction_receipts_and_recovery(manager, tmp_path, recorder, monkeypatch):
    register(manager,tmp_path)
    directory = create(manager,'act2','custom3','act2_003_001')
    c = Collection(directory)
    def factory(outcome):
        root = c.roots[outcome]
        if root.exists(): return recorder.LeRobotDataset(repo_id=f'local/{outcome}',root=root,vcodec='h264')
        return recorder.LeRobotDataset.create(repo_id=f'local/{outcome}',root=root,robot_type='indy7',fps=20,
            features=recorder._features(),use_videos=True,image_writer_threads=0,vcodec='h264')
    session = recorder.EpisodeSession(c.pending_root,factory,20,c.state['definition']['action']['description'],collection=c)
    take((recorder,c,session),correction=True)
    assert '저장 완료' in session.save('correction',c.roots['correction'])
    take((recorder,c,session))
    assert '저장 완료' in session.save('success',c.roots['success'])
    assert c.completed() == {1000}
    before = tree_fingerprint(directory)
    c.close(); c = Collection(directory)
    try:
        c.reconcile()
        assert c.condition()['condition_id'] == 1001
        assert c.stats()['success'] == c.stats()['correction'] == 1
        assert tree_fingerprint(directory) == before
        row = manager.list_datasets()[0]
        assert (row['success'],row['correction'],row['total'],row['status']) == (1,1,3,'수집 중')
        for kind in ('success','correction'):
            meta = read_json(c.roots[kind]/'meta/recorder_timing/episode-000000/episode.json')
            assert meta['collection']['description'] == '물체 1 준비'
            assert meta['task'] == '새로운 작업 수행'
            assert list(c.roots[kind].rglob('*.mp4')) and list(c.roots[kind].rglob('*.parquet'))
        # Append after reopening, then reconcile a durable Dataset/receipt with
        # a deliberately failed ledger finish. Old sidecars/receipts stay intact.
        immutable = {p: p.read_bytes() for folder in ('success','correction')
                     for p in c.roots[folder].glob('meta/recorder_timing/episode-*/episode.json')}
        immutable.update({p:p.read_bytes() for p in (directory/'receipts').glob('*.json')})
        session = recorder.EpisodeSession(c.pending_root,factory,20,'새로운 작업 수행',collection=c)
        take((recorder,c,session))
        monkeypatch.setattr(c,'finish',lambda status: (_ for _ in ()).throw(OSError('injected ledger finish')))
        assert 'SAVE FAILED' in session.save('success',c.roots['success'])
        c.close(); c = Collection(directory,recovery=True)
        c.reconcile()
        assert c.stats()['success']==2 and c.stats()['correction']==1
        assert all(p.read_bytes()==content for p,content in immutable.items())
        c.audit()
    finally: c.close()

def test_new_core108_snapshot_and_legacy_unchanged(manager, tmp_path):
    directory = create(manager,'act1','core108','act1_108_001')
    c = Collection(directory)
    assert c.state['conditions'] == builtin_plan()['conditions']
    assert not c.custom and c.condition_view()['position'] == '오른쪽 (R)'
    c.close()
    legacy = tmp_path/'legacy'
    c = Collection(legacy);c.close()
    before=tree_fingerprint(legacy)
    c=Collection(legacy);c.audit();c.close()
    assert tree_fingerprint(legacy)==before
    assert 'definition' not in read_state(legacy)
    manager.legacy=(legacy,)
    assert len(manager.list_datasets())==2

def test_each_dataset_isolated(manager, tmp_path):
    register(manager,tmp_path)
    a=create(manager,'act2','custom3','act2_003_001')
    b=create(manager,'act2','custom3','act2_003_002')
    before=tree_fingerprint(b)
    c=Collection(a);c.select(1002);c.prepare();c.close()
    assert tree_fingerprint(b)==before
    c=Collection(b)
    try: assert c.condition()['condition_id']==1000 and c.state['attempts']==[]
    finally:c.close()

@pytest.fixture
def tkroot():
    root=tk.Tk();root.withdraw()
    yield root
    try: root.destroy()
    except tk.TclError: pass

def test_custom_gui_fields_and_progress(tkroot):
    from ai.record.gui import RecorderWindow,display_fields
    from ai.record.gui_presentation import operator_fields
    from ai.record.test.test_gui_design import PreviewBridge,view_for
    bridge=PreviewBridge();view=view_for()
    view.update(total=3,custom_plan=True,dataset_id='act2_003_001')
    view['condition'].update(position='작업 조건',yaw='물체=상자',depth='—',condition_id=1000)
    view['stats'].update(success=2,correction=1)
    bridge.set_view(view)
    app=RecorderWindow(tkroot,bridge);tkroot.update()
    assert '/ 3' in display_fields(view)['progress']
    assert '/ 3' in operator_fields(view)['progress']
    assert int(app.progressbar['maximum'])==3
    assert app.condition_titles[0]['text']=='조건 설명'
    assert '1000' in app.fields['identity'].get()
    view['stats']['success']=3;view['condition']=None
    bridge.set_view(view);app.refresh(schedule=False)
    assert '정상 3개' in app.fields['identity'].get()

def test_custom_status_history_and_reconcile_cli(manager,tmp_path,monkeypatch,capsys):
    import sys
    from ai.record.collection import main
    register(manager,tmp_path)
    directory=create(manager,'act2','custom3','act2_003_001')
    for command,extra in [('status',[]),('history',['1000']),('reconcile',[])]:
        monkeypatch.setattr(sys,'argv',['collection',command,*extra,'--collection-dir',str(directory)])
        assert main()==0
    monkeypatch.setattr(sys,'argv',['collection','history','108','--collection-dir',str(directory)])
    assert main()==1

def test_manager_main_cancel_does_not_start_engine(tkroot,monkeypatch):
    from ai.record import gui,manager_gui
    calls=[]
    monkeypatch.setattr(gui.tk,'Tk',lambda:tkroot)
    def manager(root,on_open):
        calls.append('manager')
        root.after(1,root.quit)
    monkeypatch.setattr(manager_gui,'DatasetManagerWindow',manager)
    monkeypatch.setattr(gui,'run_engine',lambda *a:pytest.fail('engine must not start'))
    assert gui.main([])==0
    assert calls==['manager']

def test_manager_to_recorder_main_uses_selected_path(tkroot,manager,tmp_path,monkeypatch):
    from ai.record import gui,manager_gui
    directory=create(manager,'act1','core108','act1_108_001')
    monkeypatch.setattr(gui.tk,'Tk',lambda:tkroot)
    def select(root,on_open):root.after(1,lambda:on_open(directory))
    monkeypatch.setattr(manager_gui,'DatasetManagerWindow',select)
    calls=[]
    def engine(bridge,argv):
        calls.append(argv);bridge.finished(0)
    monkeypatch.setattr(gui,'run_engine',engine)
    assert gui.main([])==0
    assert calls==[['--collection-dir',str(directory)]]

def test_explicit_gui_path_bypasses_manager(tkroot,manager,monkeypatch):
    from ai.record import gui,manager_gui
    directory=create(manager,'act1','core108','act1_108_001')
    monkeypatch.setattr(gui.tk,'Tk',lambda:tkroot)
    monkeypatch.setattr(manager_gui,'DatasetManagerWindow',lambda *a:pytest.fail('manager must be bypassed'))
    calls=[]
    def engine(bridge,argv):calls.append(argv);bridge.finished(0)
    monkeypatch.setattr(gui,'run_engine',engine)
    assert gui.main(['--collection-dir',str(directory)])==0
    assert calls==[['--collection-dir',str(directory)]]


def test_discovery_first_open_resume_and_source_conflict(manager,tmp_path):
    source=register(manager,tmp_path);row=manager.list_datasets()[0]
    assert (row['id'],row['total'],row['success'],row['correction'],row['status'])==('act2_003_001',3,0,0,'수집 전')
    assert not manager.datasets.exists()
    directory=manager.open_dataset(row);before=tree_fingerprint(directory)
    assert manager.open_dataset(manager.list_datasets()[0])==directory
    assert tree_fingerprint(directory)==before
    changed=read_json(source);changed['action']['description']='원본 변경';source.write_text(json.dumps(changed))
    row=manager.list_datasets()[0];assert row['status']=='오류' and '스냅샷' in row['error']
    with pytest.raises(ValueError):manager.open_dataset(row)
    assert tree_fingerprint(directory)==before
    c=Collection(directory)
    try:assert c.state['definition']['action']['description']=='새로운 작업 수행'
    finally:c.close()


@pytest.mark.parametrize('count',[1,3,7,55])
def test_arbitrary_counts_from_actual_lists(manager,count):
    manager.plans.mkdir();value=definition(count)
    (manager.plans/(value['dataset_id']+'.json')).write_text(json.dumps(value))
    row=manager.list_datasets()[0];assert row['total']==count
    directory=manager.open_dataset(row);c=Collection(directory)
    try:assert c.total==count and c.condition()['condition_id']==1000
    finally:c.close()


def test_duplicate_ids_block_all_rows_and_existing_snapshot(manager,tmp_path):
    source=register(manager,tmp_path);directory=manager.open_dataset(manager.list_datasets()[0]);before=tree_fingerprint(directory)
    (manager.plans/'copy.json').write_bytes(source.read_bytes())
    rows=manager.list_datasets();assert len(rows)==2
    assert all('중복 Dataset ID' in r['error'] for r in rows)
    for row in rows:
        with pytest.raises(ValueError):manager.open_dataset(row)
    assert tree_fingerprint(directory)==before


@pytest.mark.parametrize('damage',['invalid_json','duplicate_keys','count','filename','robot','policy','path','id','empty'])
def test_bad_definitions_are_visible_and_blocked(manager,damage):
    manager.plans.mkdir();value=definition();name='act2_003_001.json'
    if damage=='count':value['dataset_id']='act2_055_001'
    elif damage=='filename':name='wrong.json'
    elif damage=='robot':value['robot']='another_robot'
    elif damage=='policy':value['policy']='vla'
    elif damage=='path':value['dataset_id']='../escape'
    elif damage=='id':value['action']['id']='act0'
    elif damage=='empty':value['plan']['conditions']=[]
    content='{' if damage=='invalid_json' else '{"version":1,"version":2}' if damage=='duplicate_keys' else json.dumps(value)
    (manager.plans/name).write_text(content)
    row=manager.list_datasets()[0];assert row['error'] and row['status']=='오류'
    with pytest.raises(ValueError):manager.open_dataset(row)
    assert not manager.datasets.exists()


def test_source_changed_after_selection_and_path_collision(manager,tmp_path):
    source=register(manager,tmp_path);row=manager.list_datasets()[0]
    changed=read_json(source);changed['plan']['conditions'][0]['description']='변경';source.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='새로고침'):manager.open_dataset(row)
    assert not manager.datasets.exists()
    manager.datasets.mkdir(parents=True)
    directory=manager.datasets/'act2_003_001';directory.mkdir();(directory/'evidence.txt').write_text('keep')
    before=tree_fingerprint(directory);row=manager.list_datasets()[0];assert row['error']
    with pytest.raises(ValueError):manager.open_dataset(row)
    assert tree_fingerprint(directory)==before


def test_removed_source_uses_saved_snapshot_and_invalid_source_cannot_bypass(manager,tmp_path):
    source=register(manager,tmp_path);content=source.read_bytes()
    directory=manager.open_dataset(manager.list_datasets()[0]);source.unlink()
    row=manager.list_datasets()[0];assert row['source'] is None and not row['error']
    assert manager.open_dataset(row)==directory
    source.write_text('{');rows=manager.list_datasets();assert len(rows)==1 and rows[0]['error']
    with pytest.raises(ValueError):manager.open_dataset(row)
    source.write_bytes(content);assert not manager.list_datasets()[0]['error']


def test_symlink_roots_and_collection_blocked(tmp_path,manager):
    elsewhere=tmp_path/'elsewhere';elsewhere.mkdir();alias=tmp_path/'alias';alias.symlink_to(elsewhere,target_is_directory=True)
    for m in (DatasetManager(alias,tmp_path/'defs',legacy=()),DatasetManager(tmp_path/'data',alias,legacy=())):
        with pytest.raises(ValueError):m.list_datasets()
    register(manager,tmp_path);manager.datasets.mkdir(parents=True)
    (manager.datasets/'act2_003_001').symlink_to(elsewhere,target_is_directory=True)
    row=manager.list_datasets()[0];assert row['error']
    with pytest.raises(ValueError):manager.open_dataset(row)
    assert list(elsewhere.iterdir())==[]


def test_core108_file_matches_original_and_changed_order_blocked(manager):
    directory=create(manager,'act1','core108','act1_108_001')
    path=manager.plans/'act1_108_001.json';value=read_json(path);assert value['plan']['conditions']==load_plan()
    a,b=value['plan']['conditions'][:2];a['collection_order'],b['collection_order']=b['collection_order'],a['collection_order']
    path.write_text(json.dumps(value));assert manager.list_datasets()[0]['error'];before=tree_fingerprint(directory)
    with pytest.raises(ValueError):manager.open_dataset(manager.list_datasets()[0])
    assert tree_fingerprint(directory)==before


def test_real_tk_selector_refresh_and_open(manager,tmp_path,tkroot,monkeypatch):
    from ai.record import manager_gui as ui
    monkeypatch.setattr(ui,'PROJECT',tmp_path)
    errors=[];monkeypatch.setattr(ui.messagebox,'showerror',lambda *a,**kw:errors.append(a))
    opened=[];app=ui.DatasetManagerWindow(tkroot,opened.append,manager)
    assert not app.tree.get_children()
    register(manager,tmp_path);app.refresh();assert len(app.tree.get_children())==1 and not manager.datasets.exists()
    for name in ('action_box','plan_box','dataset_id','register_action','import_plan','create_dataset'):assert not hasattr(app,name)
    item=app.tree.get_children()[0];app.tree.selection_set(item);app.selection()
    assert str(app.open_button['state'])=='normal'
    app.open_selected();assert opened==[manager.datasets/'act2_003_001'] and not errors


@pytest.mark.parametrize('scaling',[1.0,2.0,3.0])
def test_selector_dpi_resize_and_long_text(manager,tmp_path,tkroot,monkeypatch,scaling):
    from ai.record import manager_gui as ui
    monkeypatch.setattr(ui,'PROJECT',tmp_path)
    source=register(manager,tmp_path);value=read_json(source);value['action']['description']='매우 긴 행동 이름과 설명 '*80;source.write_text(json.dumps(value))
    tkroot.tk.call('tk','scaling',scaling);app=ui.DatasetManagerWindow(tkroot,lambda path:None,manager);tkroot.deiconify()
    from ai.record.test.test_gui_design import settle
    from ai.record.gui_scaling import window_scale
    for size in ('720x420','1000x580','1600x1000'):
        tkroot.geometry(size);settle(tkroot)
        for button in (app.refresh_button,app.open_button):
            x=button.winfo_rootx()-tkroot.winfo_rootx();y=button.winfo_rooty()-tkroot.winfo_rooty()
            assert x>=0 and y>=0 and x+button.winfo_width()<=tkroot.winfo_width() and y+button.winfo_height()<=tkroot.winfo_height()
        item=app.tree.get_children()[0];app.tree.selection_set(item);app.selection();tkroot.update()
        assert value['action']['description'] in app.details.get('1.0','end')
    assert app.body_font.cget('size')==-round(14*window_scale(tkroot.winfo_width(),tkroot.winfo_height()))


def test_shipped_core108_definition_and_refresh_new_file(manager,tmp_path):
    from ai.record.dataset_manager import PLANS
    defaults=DatasetManager(manager.datasets,PLANS,legacy=())
    rows=defaults.list_datasets();core=next(r for r in rows if r['id']=='act1_108_001')
    assert not core['error'] and core['definition']['plan']['conditions']==load_plan()
    assert core['total']==108 and not manager.datasets.exists()
    register(manager,tmp_path);assert len(manager.list_datasets())==1
    value=definition(2,'act3_002_001');value['action']['id']='act3'
    (manager.plans/'act3_002_001.json').write_text(json.dumps(value))
    rows=manager.list_datasets();assert len(rows)==2
    assert {r['id'] for r in rows}=={'act2_003_001','act3_002_001'}
    assert not manager.datasets.exists()


def test_duplicate_id_in_invalid_definition_still_blocks(manager,tmp_path):
    source=register(manager,tmp_path);value=read_json(source);value['policy']='wrong'
    (manager.plans/'invalid_copy.json').write_text(json.dumps(value))
    rows=manager.list_datasets();assert len(rows)==2
    assert all('중복 Dataset ID' in row['error'] for row in rows)
    assert not manager.datasets.exists()


def test_malformed_collection_is_reported_without_hiding_valid_files(manager,tmp_path):
    register(manager,tmp_path)
    manager.datasets.mkdir(parents=True);bad=manager.datasets/'broken';bad.mkdir()
    (bad/'collection.json').write_text('[]')
    rows=manager.list_datasets();assert len(rows)==2
    assert any(row['error'] and 'JSON 객체' in row['error'] for row in rows)
    assert any(not row['error'] for row in rows)
