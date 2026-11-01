"""No real ROS/cameras: launch stubs, inherited-lock subprocesses and fake nodes."""
import importlib.util,json,os,signal,subprocess,sys,time,types
from pathlib import Path
import pytest
from test_commands import ROOT,checkout,run


def wait_file(path,process):
    for _ in range(200):
        if path.exists():return
        if process.poll() is not None:raise AssertionError(process.communicate())
        time.sleep(.01)
    raise AssertionError('fake launch did not start')


@pytest.mark.parametrize('name,selection,serials',[('camera','all',('317222074298','844212071939')),('cam1','cam1',('844212071939',)),('cam2','cam2',('317222074298',))])
def test_help_selection_and_symlink(checkout,tmp_path,name,selection,serials):
    root,env=checkout;home=tmp_path/'home';home.mkdir();env['HOME']=str(home)
    help_result=run(ROOT/'scripts/bin'/name,'--help')
    assert help_result.returncode==0
    assert all(serial in help_result.stdout for serial in serials)
    if len(serials)==1:
        other='317222074298' if name=='cam1' else '844212071939'
        assert other not in help_result.stdout
    link=tmp_path/name;link.symlink_to(root/'scripts/bin'/name)
    result=run(link,'show_camera:=false',env=env)
    assert result.returncode==0,result.stderr
    args=json.loads(result.stdout)['args']
    assert args[:2]==['launch',str(root/'ros2_ws/src/capstone_teleop/launch/cameras.launch.py')]
    assert f'camera_selection:={selection}' in args and args[-1]=='show_camera:=false'
    assert {p.stem for p in (home/'.local/bin/.sirlab258-camera-locks').glob('*.lock')}==set(serials)
    assert run(link,'camera_selection:=all',env=env).returncode==2
    assert run(root/'scripts/bin/indy','cam',env=env).returncode==2


@pytest.fixture
def live(checkout,tmp_path):
    root,env=checkout;home=tmp_path/'home';home.mkdir();env['HOME']=str(home)
    fake=Path(env['PATH'].split(':')[0]);counter=tmp_path/'counter'
    (fake/'ros2').write_text('''#!/usr/bin/env python3
import os,signal,time,sys
from pathlib import Path
Path(os.environ['READY']).write_text(' '.join(sys.argv))
signal.signal(signal.SIGINT,lambda *a:sys.exit(0))
signal.signal(signal.SIGTERM,lambda *a:sys.exit(0))
while True:time.sleep(.01)
''')
    processes=[]
    def start(name):
        ready=tmp_path/f'ready-{len(processes)}';e=env.copy();e['READY']=str(ready)
        p=subprocess.Popen([str(root/'scripts/bin'/name)],env=e,cwd='/tmp',stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        processes.append(p);wait_file(ready,p);return p
    yield root,env,start
    for p in processes:
        if p.poll() is None:p.send_signal(signal.SIGTERM)
        p.communicate(timeout=5)


@pytest.mark.parametrize('owner,blocked',[('cam1','cam1'),('camera','cam1'),('camera','cam2'),('cam1','camera'),('cam2','camera')])
def test_duplicate_is_rejected_without_touching_owner(live,owner,blocked):
    root,env,start=live;p=start(owner)
    result=run(root/'scripts/bin'/blocked,env=env)
    assert result.returncode==1 and 'already locked' in result.stderr
    assert p.poll() is None
    # A rejected combined request cannot leave a lock on the other camera.
    if owner in ('cam1','cam2'):
        other=start('cam2' if owner=='cam1' else 'cam1');assert other.poll() is None


def test_independent_stop_releases_only_own_lock_and_indy_is_separate(live):
    root,env,start=live;a=start('cam1');b=start('cam2');robot=start('indy')
    robot.send_signal(signal.SIGINT);assert robot.wait(timeout=5)==0
    assert a.poll() is None and b.poll() is None
    a.send_signal(signal.SIGINT);assert a.wait(timeout=5)==0
    new=start('cam1');assert new.poll() is None and b.poll() is None


@pytest.fixture
def launches(monkeypatch):
    class Object:
        def __init__(self,*args,**kwargs):self.args,self.kwargs=args,kwargs
    class Config(Object):
        def perform(self,context):return context[self.args[0]]
    for name in ['launch','launch.actions','launch.conditions','launch.launch_description_sources','launch.substitutions','launch_ros','launch_ros.actions','launch_ros.substitutions']:
        monkeypatch.setitem(sys.modules,name,types.ModuleType(name))
    sys.modules['launch'].LaunchDescription=lambda items:items
    for name in ('DeclareLaunchArgument','IncludeLaunchDescription','LogInfo','OpaqueFunction'):setattr(sys.modules['launch.actions'],name,type(name,(Object,),{}))
    sys.modules['launch.conditions'].IfCondition=Object
    sys.modules['launch.launch_description_sources'].PythonLaunchDescriptionSource=Object
    sys.modules['launch.substitutions'].LaunchConfiguration=Config
    sys.modules['launch.substitutions'].PathJoinSubstitution=Object
    sys.modules['launch_ros.actions'].Node=type('Node',(Object,),{})
    sys.modules['launch_ros.substitutions'].FindPackageShare=Object
    def load(name):
        path=ROOT/'ros2_ws/src/capstone_teleop/launch'/name
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    return load


@pytest.mark.parametrize('selection,serials',[('all',['317222074298','844212071939']),('cam1',['844212071939']),('cam2',['317222074298'])])
def test_launch_nodes_preserve_topics_contract(launches,selection,serials):
    module=launches('cameras.launch.py')
    context={'camera_registry':str(ROOT/'ros2_ws/src/capstone_teleop/config/rgb_cameras.yaml'),'camera_config':'existing.yaml','camera_selection':selection}
    items=module._camera_nodes(context)
    nodes=[x.kwargs for x in items if type(x).__name__=='Node']
    cameras=[x for x in nodes if x['package']=='realsense2_camera']
    from ai.dataset_contract import CAMERA_NAMES,IMAGE_SHAPE,FPS
    assert set(_namespace for _namespace in (f'camera_{serial}' for serial in serials)).issubset(CAMERA_NAMES)
    assert IMAGE_SHAPE==(480,640,3) and FPS==20
    assert [x['parameters'][-1]['serial_no'] for x in cameras]==serials
    for serial in serials:
        assert any(x.get('namespace')==f'camera_{serial}' and x['name']=='camera' for x in cameras)
        assert any(('image',f'/camera_{serial}/camera/color/image_raw') in x.get('remappings',[]) for x in nodes)
    assert '335122271033' not in str(nodes)


def test_setup_never_contains_cameras(launches):
    module=launches('setup.launch.py');items=module.generate_launch_description()
    includes=[x for x in items if type(x).__name__=='IncludeLaunchDescription']
    names=[x.args[0].args[0].args[0][-1] for x in includes]
    assert names==['indy.launch.py','grip.launch.py','xbox.launch.py']


def test_combined_stop_releases_both_serials(live):
    root,env,start=live;p=start('camera')
    p.send_signal(signal.SIGTERM);assert p.wait(timeout=5)==0
    assert start('cam1').poll() is None and start('cam2').poll() is None


def test_launch_failure_does_not_leave_locks(checkout,tmp_path):
    root,env=checkout;home=tmp_path/'home';home.mkdir();env['HOME']=str(home)
    fake=Path(env['PATH'].split(':')[0])/'ros2';original=fake.read_text()
    fake.write_text('#!/usr/bin/env bash\nexit 7\n')
    assert run(root/'scripts/bin/camera',env=env).returncode==7
    fake.write_text(original)
    assert run(root/'scripts/bin/camera',env=env).returncode==0
