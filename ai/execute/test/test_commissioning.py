"""Only commissioning/native E-stop/preflight changes; Fake SDK, no ROS/model."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as M
import pytest
from ai.execute.estop import SdkEstop, require_mapping
from ai.execute.real_driver import FeedbackCache
from ai.execute.real_profile import ExternalInterlock
from ai.execute.safety import SafetyConfig, SafetyError
from ai.execute.test.test_controller_safety import configuration, native_status
from ai.execute.test.test_real_execution import profile

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('readonly_commission',ROOT/'scripts/inspect_indy7_real_readonly.py')
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)


def captures(tmp_path):
    p=profile(tmp_path);files=[];records={}
    for phase,pair in [('clear_before',[10,11]),('pressed',[10,12]),('clear_after',[10,11])]:
        raw=native_status();raw['get_safety_control_data']['safety_state']=dict(zip(('id','state'),pair))
        capture={'phase':phase,'stationary_only':True,'identity':{k:p.values[k] for k in ('robot_serial','controller_version','sdk_version')},
                 'robot_ip':p.values['robot_ip'],'samples':[{'raw':raw},{'raw':deepcopy(raw)}]}
        path=tmp_path/(phase+'.json');path.write_text(json.dumps(capture));files.append(path)
        records[phase]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    p.values['sdk_estop']={'clear_pair':[10,11],'pressed_pair':[10,12],'captures':records,
                           'review_record':'FAKE TEST ONLY','definition_reference':'FAKE FW manual'}
    return p,files


def test_qualified_native_estop_latches_no_resume(tmp_path):
    p,files=captures(tmp_path);sensor=SdkEstop(p)
    raw=lambda i:json.loads(files[i].read_text())['samples'][0]['raw']
    assert sensor.update(raw(0))
    assert not sensor.update(raw(1))
    assert not sensor.update(raw(2))  # Releasing E-stop does not re-enable this session.


def test_unknown_sdk_estop_revokes_even_after_clear(tmp_path):
    p,files=captures(tmp_path);sensor=SdkEstop(p)
    raw=json.loads(files[0].read_text())['samples'][0]['raw']
    assert sensor.update(raw)
    with pytest.raises(SafetyError):sensor.update({})
    assert not sensor.update(raw)


@pytest.mark.parametrize('case',['hash','identity','no_definition','malformed','same_pair','mapping_mismatch','unstable'])
def test_unqualified_or_changed_mapping_blocked(tmp_path,case):
    p,files=captures(tmp_path);mapping=p.values['sdk_estop']
    if case=='hash':files[0].write_text('{}')
    elif case=='identity':p.values['controller_version']='different FW'
    elif case=='no_definition':mapping['definition_reference']=''
    elif case=='malformed':p.values['sdk_estop']=True
    elif case=='mapping_mismatch':mapping['clear_pair']=[99,99]
    else:
        content=json.loads(files[1].read_text())
        content['samples'][0]['raw']['get_safety_control_data']['safety_state']={'id':10,'state':11}
        if case=='same_pair':content['samples'][1]=deepcopy(content['samples'][0])
        files[1].write_text(json.dumps(content))
        mapping['captures']['pressed']['sha256']=hashlib.sha256(files[1].read_bytes()).hexdigest()
    with pytest.raises(SafetyError):require_mapping(p)


def test_native_clear_never_substitutes_writer_evidence(tmp_path):
    p,files=captures(tmp_path)
    now=10**9
    proof={'version':1,'run_nonce':'nonce','robot_serial':'FAKE','issuer':'FAKE-PLC',
           'observed_monotonic_ns':now,'all_other_writers_blocked':True}
    path=Path(p.values['interlock_file']);path.write_text(json.dumps(proof))
    gate=ExternalInterlock(p,'nonce',lambda:now,supervising_operator='onsite')
    assert 'estop_clear' not in gate.read()
    for patch in ({'all_other_writers_blocked':False},{'estop_clear':False},{'observed_monotonic_ns':0}):
        path.write_text(json.dumps({**proof,**patch}))
        with pytest.raises(SafetyError):gate.read()


def test_feedback_uses_native_estop_and_cannot_resume(tmp_path):
    p,files=captures(tmp_path)
    cache=FeedbackCache(p,M(read=lambda:{'all_other_writers_blocked':True}))
    data={'q':[0.]*6,'qdot':[0.]*6,'op_state':17,'is_robot_connected':True,'sim_mode':False}
    servo={'status_codes':['0x237']*6,'servo_actives':[True]*6,'brake_actives':[False]*6}
    motion={'is_in_motion':False,'is_stopping':False,'is_pausing':False,'motion_queue_size':0}
    for idx in (0,1,2):
        raw=json.loads(files[idx].read_text())['samples'][0]['raw']
        cache.update(data,servo,motion,{'method':10},100,100,raw)
        if idx==0:assert cache.get(100).estop is False
        else:
            with pytest.raises(SafetyError,match='revoked'):cache.get(100)


def test_stationary_capture_getters_and_timing_only():
    raw={**configuration(),**native_status(),'get_device_info':{'robot_serial':'FAKE'},
         'get_control_info':{'control_version':'FAKE'},'get_control_data':{'q':[0]*6,'qdot':[0]*6},
         'get_motion_data':{'is_in_motion':False},'get_servo_data':{},'get_teleop_state':{}}
    calls=[]
    class Fake:
        def __getattr__(self,name):
            assert name.startswith('get_') and name in tool.GETTERS
            def get():calls.append(name);return deepcopy(raw[name])
            return get
    ticks=iter(range(10000))
    result=tool.capture(Fake(),'pressed','FAKE-SDK',clock=lambda:next(ticks))
    assert calls==list(tool.GETTERS)*3
    assert result['controller_sensor_timestamp'] is None
    assert all(t['rpc_elapsed_ns']==1 for row in result['samples'] for t in row['timing'].values())
    raw['get_motion_data']['is_in_motion']=True
    with pytest.raises(RuntimeError,match='Stationary'):tool.capture(Fake(),'pressed','FAKE')


def test_default_diagnostic_never_constructs_sdk(monkeypatch,capsys):
    import builtins
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        assert not name.startswith(('neuromeka','rclpy'))
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    assert tool.main([])==0
    assert json.loads(capsys.readouterr().out)['connects'] is False


def test_prepare_mapping_offline_no_execution_approval(tmp_path,monkeypatch):
    p,files=captures(tmp_path)
    monkeypatch.setattr(tool,'ROOT',tmp_path)
    template=tmp_path/'hardware.json';template.write_text(json.dumps(p.values))
    output=tmp_path/'draft.json'
    assert tool.main(['--prepare-hardware',str(template),'--captures',*[str(f) for f in files],
                      '--definition-reference','FAKE manual','--review-record','FAKE observation','--output',str(output)])==0
    value=json.loads(output.read_text())
    assert value['reviewed_for_real'] is False
    assert require_mapping(M(values=value))
    assert tool.main(['--prepare-hardware',str(template),'--captures',*[str(f) for f in files],
                      '--definition-reference','FAKE','--review-record','FAKE','--output',str(output)])==2


def test_preflight_without_model_sdk_ros(tmp_path,monkeypatch,capsys):
    import ai.execute.main as cli
    monkeypatch.setattr(cli,'inference_main',lambda *a,**k:pytest.fail('preflight must not start inference'))
    template=ROOT/'ai/execute/config/real_hardware_review_required.json'
    args=['--mode','real','--preflight-only','--config',str(ROOT/'ai/execute/config/indy7_review_required.json'),
          '--hardware-profile',str(template)]
    assert cli.main(args)==2
    result=json.loads(capsys.readouterr().out)
    assert not result['hardware_connected'] and result['robot_commands_sent']==0
    assert any('max_acceleration' in e for e in result['errors'])
    p,_=captures(tmp_path)
    safety=replace(SafetyConfig.load(ROOT/'ai/execute/config/mock.json'),reviewed_for_real=True,review_record='FAKE')
    cfg=tmp_path/'safe.json';cfg.write_text(json.dumps(asdict(safety)))
    hp=tmp_path/'hardware.json';hp.write_text(json.dumps(p.values))
    assert cli.main(['--mode','real','--preflight-only','--config',str(cfg),'--hardware-profile',str(hp)])==0
    assert json.loads(capsys.readouterr().out)['estop_source']=='qualified_sdk'


def test_unimplemented_optional_keeps_capture_but_required_never_passes(capsys):
    class Unsupported(Exception):
        def code(self):return M(name='UNIMPLEMENTED')
    class Fake:
        def __init__(self,failed):self.failed=failed
        def __getattr__(self,name):
            def get():
                if name==self.failed:raise Unsupported('unsupported RPC')
                if name=='get_device_info':return {'robot_serial':'FAKE'}
                if name=='get_control_info':return {'control_version':'FAKE'}
                if name=='get_motion_data':return {'is_in_motion':False}
                return {}
            return get
    result=tool.capture(Fake('get_joint_limit_config'),'clear_before','FAKE')
    assert result['capture_valid']
    assert result['samples'][0]['calls']['get_joint_limit_config']['grpc_code']=='UNIMPLEMENTED'
    result=tool.capture(Fake('get_safety_control_data'),'clear_before','FAKE')
    assert not result['capture_valid']
    assert result['samples'][0]['calls']['get_safety_function_state']['ok']
    assert 'get_safety_control_data' not in result['samples'][0]['raw']


@pytest.mark.parametrize('condition',['ready','servo','fault','mode','stale'])
def test_monitor_control_eligibility_uses_existing_fail_closed_checks(tmp_path,condition):
    from ai.execute.real_driver import require_control_available
    from ai.execute.test.test_execution import Rig
    rig=Rig(tmp_path)
    fb=rig.indy.feedback(rig.clock())
    if condition=='servo':fb.servo_ready=False
    elif condition=='fault':fb.fault=True
    elif condition=='mode':fb.mode='unknown'
    elif condition=='stale':fb.status_received_ns-=10**9
    if condition=='ready':require_control_available(fb,rig.clock(),rig.config)
    else:
        with pytest.raises(SafetyError):require_control_available(fb,rig.clock(),rig.config)
