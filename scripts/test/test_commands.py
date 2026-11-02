"""Shell routing tests with fake ROS, Python and Docker; no hardware access."""
from pathlib import Path
import json,os,shutil,subprocess
import pytest
ROOT=Path(__file__).resolve().parents[2]


def run(path,*args,env=None,cwd='/tmp'):
    return subprocess.run([str(path),*args],env=env,cwd=cwd,text=True,capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    root=tmp_path/'clone with spaces';(root/'scripts/bin').mkdir(parents=True)
    for p in (ROOT/'scripts/bin').iterdir():
        if p.is_file():shutil.copy2(p,root/'scripts/bin'/p.name)
    if (ROOT/'scripts/lib').exists():shutil.copytree(ROOT/'scripts/lib',root/'scripts/lib')
    setup=root/'noop.bash';setup.write_text(':\n')
    for name in ('indy','act-record','../lib/camera-launch'):
        p=root/'scripts/bin'/name;p.write_text(p.read_text().replace('/opt/ros/jazzy/setup.bash',str(setup).replace(' ','\\ ')))
    for name in ('.venv/bin/activate','ros2_ws/install/setup.bash'):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(':\n')
    p=root/'scripts/activate_act.sh';p.write_text(':\n')
    fake=tmp_path/'fake';fake.mkdir()
    script='#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd(),"hf":os.environ.get("HF_HOME"),"py":os.environ.get("PYTHONPATH")}))\n'
    for name in ('python','ros2'):
        p=fake/name;p.write_text(script);p.chmod(0o755)
    p=root/'.venv-act/bin/python';p.parent.mkdir(parents=True);p.write_text(script);p.chmod(0o755)
    env=os.environ.copy();env.update(PATH=str(fake)+os.pathsep+env['PATH']);env.pop('HF_HOME',None)
    return root,env


@pytest.mark.parametrize('name',['indy','omy','act'])
def test_real_help_and_unknown_are_side_effect_free(name):
    path=ROOT/'scripts/bin'/name
    assert run(path,'--help').returncode==0
    assert run(path,'not-a-command').returncode==2


@pytest.mark.parametrize('args,launch',[((), 'setup.launch.py'),(('driver',),'indy.launch.py'),(('teleop',),'xbox.launch.py'),(('indy_ip:=test',),'setup.launch.py')])
def test_indy_routes_and_preserves_arguments(checkout,tmp_path,args,launch):
    root,env=checkout;link=tmp_path/'indy';link.symlink_to(root/'scripts/bin/indy')
    result=run(link,*args,'show_camera:=false',env=env)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['args']==['launch',str(root/'ros2_ws/src/capstone_teleop/launch'/launch),*([args[0]] if args and ':=' in args[0] else []),'show_camera:=false']


@pytest.mark.parametrize('command,module',[('train','ai.train.run_train'),('validate','ai.train.validate_dataset'),('infer','ai.serve.infer'),('execute','ai.execute.main'),('task','ai.execute.task_main'),('evaluate','ai.evaluate.main')])
def test_act_modules_and_symlink_root(checkout,tmp_path,command,module):
    root,env=checkout;link=tmp_path/'act';link.symlink_to(root/'scripts/bin/act')
    result=run(link,command,'--example','value with spaces',env=env)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value['args']==['-m',module,'--example','value with spaces']
    assert value['py'].split(':')[0]==str(root)
    if command=='train':assert value['hf']==str(root/'.hf_cache')


@pytest.mark.parametrize('args', [(),('--collection-dir','dataset path')])
def test_record_keeps_gui_vs_terminal(checkout,args):
    root,env=checkout;result=run(root/'scripts/bin/act','record',*args,env=env)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value['args']==(['-m','ai.record.gui'] if not args else [str(root/'ai/record/indy7_act_record.py'),*args])
    assert value['cwd']==(str(root) if not args else '/tmp')


def test_omy_only_readonly_operations(tmp_path):
    fake=tmp_path/'bin';fake.mkdir();log=tmp_path/'calls'
    for name in ('docker','python3','lsusb','ssh'):
        p=fake/name;p.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$0 $*" >> "$CALL_LOG"\ncase "$1" in inspect) echo true ;; top) echo "335122271033 /realsense2_camera_node" ;; esac\n');p.chmod(0o755)
    env=os.environ.copy();env.update(PATH=str(fake)+':'+env['PATH'],CALL_LOG=str(log))
    for args in ((),('status',),('doctor',),('logs',)):
        result=run(ROOT/'scripts/bin/omy',*args,env=env)
        assert result.returncode==(3 if not args else 0),result.stderr
    text=log.read_text()
    assert '7447' in text and 'inspect' in text and 'logs' in text
    assert not any(word in text for word in ('restart','docker start','docker stop','docker exec','pkill'))


def test_installer_collision_reinstall_and_legacy_links(checkout,tmp_path):
    root,env=checkout;shutil.copy2(ROOT/'scripts/install_commands.sh',root/'scripts/install_commands.sh')
    home=tmp_path/'home';home.mkdir();env['HOME']=str(home)
    for name in ('omy','caps','capi','capg','capx','act-record'):(root/'.venv/bin'/name).symlink_to('../../scripts/bin/'+name)
    result=run(root/'scripts/install_commands.sh',env=env);assert result.returncode==0,result.stderr
    assert not (root/'.venv/bin/omy').is_symlink()
    assert all((home/'.local/bin'/name).resolve()==root/'scripts/bin'/name for name in ('indy','omy','act','camera','cam1','cam2'))
    assert run(root/'scripts/install_commands.sh',env=env).returncode==0
    link=home/'.local/bin/act';link.unlink();link.write_text('user file')
    assert run(root/'scripts/install_commands.sh','--replace',env=env).returncode!=0
    assert link.read_text()=='user file'
    link.unlink();link.symlink_to('/missing/old/checkout/scripts/bin/act')
    assert run(root/'scripts/install_commands.sh','--replace',env=env).returncode==0
