"""Only the resumed native SDK integration: fake payloads, no ROS/SDK clients."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as M
import ast
from pathlib import Path
import numpy as np
import pytest
from ai.execute.safety import SafetyConfig,SafetyError
from ai.execute.controller_safety import (CONFIG_GETTERS,STATUS_GETTERS,read_configuration,
    read_status,validate_limits,status_health,servo_states,request_normal_stop,response_ok)
ROOT=Path(__file__).resolve().parents[3]


def test_flat_sdk_stop_response_is_checked_without_accepting_missing_or_failed_ack():
    response_ok({'code':'0','msg':''},required=True)
    response_ok({'response':{'code':'0'}},required=True)
    with pytest.raises(SafetyError):response_ok({},required=True)
    with pytest.raises(SafetyError):response_ok({'code':'1'},required=True)


def configuration():
    return {
      'get_joint_limit_config':{'robot_name':'Indy7','joint_pos_limit':{'min':[-170.]*6,'max':[170.]*6},'source_file':'FAKE current'},
      'get_original_joint_limit_config':{'robot_name':'Indy7','joint_pos_limit':{'min':[-175.]*5+[-215.],'max':[175.]*5+[215.]},'source_file':'FAKE model'},
      'get_safety_limits':{'joint_lower_limits':[-160.]*6,'joint_upper_limits':[160.]*6},
      'get_safety_stop_config':{k:2 for k in ('joint_position_limit_stop_cat','joint_speed_limit_stop_cat',
          'joint_torque_limit_stop_cat','tcp_speed_limit_stop_cat','tcp_force_limit_stop_cat','power_limit_stop_cat')},
      'get_coll_policy':{'policy':3,'sleep_time':0.,'gravity_time':.1}}


def native_status():
    return {'get_stop_state':{'category':-1},'get_violation_data':{'violation_code':'0'},
      'get_boot_status':{'control_on':True,'ethercat_connected':True,'robot_pw_supply_on':True,
                         'safety_used':True,'safety_connected':True},
      'get_safety_control_data':{'auto_mode':False,'reduced_mode':False,'enabler_pressed':False,
                                'safety_state':{'id':0,'state':0}}}


def settings():
    p=M(values={'sdk_limit_units':{k:'deg' for k in CONFIG_GETTERS[:3]},'sdk_limit_units_review_record':'FAKE UNITS'})
    s=replace(SafetyConfig.load(ROOT/'ai/execute/config/mock.json'),joint_min_rad=[-2.]*6,
              joint_max_rad=[2.]*6,max_velocity_rad_s=[1.]*6)
    return p,s


def test_exact_wire_limits_and_units_bound_operating_profile():
    p,s=settings();raw=configuration();out=validate_limits(raw,p,s)
    assert out['effective_max_rad']==pytest.approx(np.deg2rad([160]*6))
    assert out['actual_controller_acceleration_limit'] is None
    assert out['actual_controller_velocity_limit'] is None
    assert not out['teleop_safety_enforcement_verified']
    assert out['effective_command_step_rad']==[.05]*6
    raw['get_safety_limits']={'joint_lower_limits':np.deg2rad([-160]*6).tolist(),
                              'joint_upper_limits':np.deg2rad([160]*6).tolist()}
    p.values['sdk_limit_units']['get_safety_limits']='rad'
    assert validate_limits(raw,p,s)['effective_max_rad']==pytest.approx(out['effective_max_rad'])


@pytest.mark.parametrize('case',['unit','old_docstring','position','speed','collision_none','collision_resume','unknown_stop','missing','nonfinite','failed_response'])
def test_unknown_or_excessive_controller_limits_block(case):
    p,s=settings();raw=configuration()
    if case=='unit':p.values['sdk_limit_units']['get_joint_limit_config']=None
    elif case=='old_docstring':raw['get_joint_limit_config']={'joint_pos_limits':{'lower':[-170]*6,'upper':[170]*6}}
    elif case=='position':s=replace(s,joint_max_rad=[3.]*6)
    elif case=='speed':s=replace(s,max_velocity_rad_s=[3.]*6)
    elif case=='collision_none':raw['get_coll_policy']['policy']=0
    elif case=='collision_resume':raw['get_coll_policy']['policy']=2
    elif case=='unknown_stop':raw['get_safety_stop_config']['joint_speed_limit_stop_cat']=99
    elif case=='missing':raw.pop('get_safety_limits')
    elif case=='nonfinite':raw['get_joint_limit_config']['joint_pos_limit']['min'][0]=float('nan')
    else:raw['get_joint_limit_config']['response']={'code':'7'}
    with pytest.raises(SafetyError):validate_limits(raw,p,s)


def test_all_native_reads_are_getters_only():
    calls=[]
    class Fake:
        def __getattr__(self,name):
            assert name in (*CONFIG_GETTERS,*STATUS_GETTERS)
            def get():calls.append(name);return deepcopy((configuration()|native_status())[name])
            return get
    assert set(read_configuration(Fake()))==set(CONFIG_GETTERS)
    assert set(read_status(Fake()))==set(STATUS_GETTERS)
    assert len(calls)==len(CONFIG_GETTERS)+len(STATUS_GETTERS)
    raw=native_status();assert status_health(raw)['estop_clear'] is None
    raw['get_violation_data']['violation_code']='42'
    assert status_health(raw)['violation_code']==42
    raw=native_status();raw['get_boot_status']['safety_connected']=False
    assert not status_health(raw)['boot_ready']


@pytest.mark.parametrize('field',['get_stop_state','get_violation_data','get_boot_status','get_safety_control_data'])
def test_incomplete_native_status_blocks(field):
    raw=native_status();raw[field]={}
    with pytest.raises(SafetyError):status_health(raw)


def test_servo_decoder_matches_installed_SDK_function_without_importing_SDK():
    path=Path('/home/sirlab2/.local/lib/python3.12/site-packages/neuromeka/enums.py')
    cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='EtherCATStatus')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='status2string')
    method.decorator_list=[];scope={}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),scope)
    for value in [0,0x40,0x21,0x23,0x237,7,15,8]:
        assert servo_states({'status_codes':[hex(value)]*6})==[scope['status2string'](value)]*6
    with pytest.raises(SafetyError):servo_states({'status_codes':['0xffff']*6})


@pytest.mark.parametrize('error',['none','motion','teleop','negative_ack'])
def test_CAT2_stop_always_tries_both_APIs(error):
    calls=[]
    class Fake:
        def stop_motion(self,stop_category):
            calls.append(('stop_motion',stop_category))
            if error=='motion':raise TimeoutError('FAKE disconnect')
            return {'response':{'code':'1' if error=='negative_ack' else '0'}}
        def stop_teleop(self):
            calls.append(('stop_teleop',))
            if error=='teleop':raise TimeoutError('FAKE disconnect')
            return {'response':{'code':'0'}}
    if error=='none':request_normal_stop(Fake())
    else:
        with pytest.raises(SafetyError):request_normal_stop(Fake())
    assert calls==[('stop_motion',2),('stop_teleop',)]


def test_supervised_workspace_uses_existing_approval_but_never_overrides_negative_guard(tmp_path):
    import json
    from ai.execute.real_profile import ExternalInterlock
    p=M(values={'interlock_file':str(tmp_path/'proof'),'robot_serial':'FAKE','interlock_issuer':'FAKE',
                'interlock_max_age_ms':100})
    row={'version':1,'run_nonce':'run','robot_serial':'FAKE','issuer':'FAKE','observed_monotonic_ns':1000,
         'estop_clear':True,'all_other_writers_blocked':True}
    path=Path(p.values['interlock_file']);path.write_text(json.dumps(row))
    checked=ExternalInterlock(p,'run',lambda:1000,supervising_operator='onsite test operator').read()
    assert 'workspace_clear' not in checked # No synthesized sensor result.
    with pytest.raises(SafetyError,match='workspace'):
        ExternalInterlock(p,'run',lambda:1000).read()
    for field in ('workspace_clear','estop_clear','all_other_writers_blocked'):
        path.write_text(json.dumps({**row,field:False}))
        with pytest.raises(SafetyError):ExternalInterlock(p,'run',lambda:1000,supervising_operator='operator').read()
    path.unlink()
    with pytest.raises(SafetyError):ExternalInterlock(p,'run',lambda:1000,supervising_operator='operator').read()


@pytest.mark.parametrize('case',['fault','estop','violation','stop_category','power','stopping','queued','unknown_servo'])
def test_real_feedback_blocks_native_or_external_unsafe_state(tmp_path,case):
    from ai.execute.real_driver import FeedbackCache
    from ai.execute.test.test_real_execution import profile
    p=profile(tmp_path);_,s=settings()
    proof={'estop_clear':case!='estop','all_other_writers_blocked':True}
    cache=FeedbackCache(p,M(read=lambda:proof))
    data={'q':[0.]*6,'qdot':[0.]*6,'op_state':17,'is_robot_connected':True,'sim_mode':False}
    servo={'status_codes':['0x237']*6,'servo_actives':[True]*6,'brake_actives':[False]*6}
    motion={'is_in_motion':False,'is_stopping':False,'is_pausing':False,'motion_queue_size':0}
    native=native_status()
    if case=='fault':servo['status_codes'][0]='0x238'
    elif case=='violation':native['get_violation_data']['violation_code']='123'
    elif case=='stop_category':native['get_stop_state']['category']=0
    elif case=='power':native['get_boot_status']['control_on']=False
    elif case=='stopping':motion['is_stopping']=True
    elif case=='queued':motion['motion_queue_size']=1
    elif case=='unknown_servo':servo['status_codes'][0]='0xffff'
    with pytest.raises(SafetyError):
        cache.update(data,servo,motion,{'method':10},1000,1000,native)
        cache.get(1000).require(1000,s,'joint_absolute')


def test_sdk_CAT2_category_alone_is_not_a_stop_confirmation(tmp_path):
    from ai.execute.real_driver import FeedbackCache
    from ai.execute.test.test_real_execution import profile
    p=profile(tmp_path);_,s=settings()
    cache=FeedbackCache(p,M(read=lambda:{'estop_clear':True,'all_other_writers_blocked':True}))
    data={'q':[0.]*6,'qdot':[0.]*6,'op_state':5,'is_robot_connected':True,'sim_mode':False}
    servo={'status_codes':['0x237']*6,'servo_actives':[True]*6,'brake_actives':[False]*6}
    native=native_status();native['get_stop_state']['category']=2
    motion={'is_in_motion':False,'is_stopping':False,'is_pausing':False,'motion_queue_size':0}
    cache.update(data,servo,motion,{'method':10},1000,1000,native)
    cache.get(1000).require(1000,s,'idle')
    # The controller's existing post-request freshness/stable window still must pass.
    with pytest.raises(SafetyError):cache.get(1000).require(1000,s,'joint_absolute')


def test_measured_GA12_uses_supported_RPC_and_never_invents_boot():
    p,s=settings();p.values.update(sdk_version='3.4.2.2',controller_version='3.3.2-GA12 [2026.09.15]')
    calls=[]
    class Fake:
        def __getattr__(self,name):
            assert name not in ('get_joint_limit_config','get_original_joint_limit_config','get_boot_status')
            def get():calls.append(name);return deepcopy((configuration()|native_status())[name])
            return get
    raw=read_configuration(Fake(),p)
    out=validate_limits(raw,p,s)
    assert out['effective_max_rad']==pytest.approx(np.deg2rad([160]*6))
    assert len(out['unavailable_configuration_getters'])==2
    health=status_health(read_status(Fake(),p),p)
    assert health['boot_ready'] is None and health['estop_clear'] is None
    raw['get_safety_limits']['joint_upper_limits']=[0]*6
    with pytest.raises(SafetyError):validate_limits(raw,p,s)


@pytest.mark.parametrize('fw,flag,sim,expected',[
 ('3.3.2-GA12 [2026.09.15]',False,False,True),
 ('OTHER',False,False,False),('OTHER',True,False,True),
 ('3.3.2-GA12 [2026.09.15]',False,True,False)])
def test_ga12_false_connection_flag_is_not_servo_or_estop_clear(fw,flag,sim,expected):
    from ai.execute.controller_safety import feedback_transport_connected
    p=M(values={'sdk_version':'3.4.2.2','controller_version':fw})
    assert feedback_transport_connected({'is_robot_connected':flag,'sim_mode':sim},p) is expected
    with pytest.raises(SafetyError):feedback_transport_connected({'sim_mode':False},p)


@pytest.mark.parametrize('case',['normal','fault','servo_off','relative_mode','estop','stale'])
def test_ga12_live_feedback_false_flag_keeps_independent_gates(tmp_path,case):
    from ai.execute.real_driver import FeedbackCache,require_control_available
    from ai.execute.test.test_real_execution import profile
    p=profile(tmp_path);p.values.update(sdk_version='3.4.2.2',controller_version='3.3.2-GA12 [2026.09.15]')
    _,s=settings();native=native_status();native.pop('get_boot_status')
    cache=FeedbackCache(p,M(read=lambda:{'estop_clear':case!='estop','all_other_writers_blocked':True}))
    data={'q':[0.]*6,'qdot':[0.]*6,'op_state':17,'is_robot_connected':False,'sim_mode':False}
    servo={'status_codes':['0x237']*6,'servo_actives':[True]*6,'brake_actives':[False]*6}
    if case=='fault':servo['status_codes'][0]='0x238'
    if case=='servo_off':servo['servo_actives'][0]=False
    motion={'is_in_motion':False,'is_stopping':False,'is_pausing':False,'motion_queue_size':0}
    cache.update(data,servo,motion,{'method':11 if case=='relative_mode' else 10},1000,1000,native)
    if case=='normal':require_control_available(cache.get(1000),1000,s)
    else:
        with pytest.raises(SafetyError):require_control_available(cache.get(1000),10**9 if case=='stale' else 1000,s)
