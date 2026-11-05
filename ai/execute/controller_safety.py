"""Installed IndyDCP3 safety getters, without SDK construction or configuration writes.

Wire schema is from installed 3.4.2.2 protobufs, NOT the inaccurate limit docstring.
Native status never grants external writer/workspace authority or E-stop clearance.
"""
import json
from pathlib import Path
import numpy as np
from ai.execute.safety import SafetyError

NORMAL_STOP_CATEGORY = 2  # SDK StopCategory.CAT2 = SMOOTH_ONLY, not hardware E-stop.
CONFIG_GETTERS = ('get_joint_limit_config','get_original_joint_limit_config','get_safety_limits',
                  'get_safety_stop_config','get_coll_policy')
STATUS_GETTERS = ('get_stop_state','get_violation_data','get_boot_status','get_safety_control_data')
REFERENCE = Path(__file__).parent/'config/indy7_manufacturer_reference.json'


def response_ok(value, *, required=False):
    if not isinstance(value,dict):raise SafetyError('malformed_controller_response')
    # Installed IndyDCP3 StopTeleOp/StopMotion return the Response payload
    # itself ({code,msg}); other RPCs wrap it as {response:{code,msg}}.
    if 'code' in value:
        code=value['code']
        if type(code) not in (int,str) or str(code)!='0':raise SafetyError('SDK_response_failed')
        return
    if 'response' not in value:
        if required:raise SafetyError('SDK_command_not_acknowledged')
        return  # Valid gRPC/payload can omit application response; do not invent an ACK.
    if not isinstance(value['response'],dict):raise SafetyError('malformed_controller_response')
    code=value['response'].get('code')
    if type(code) not in (int,str) or str(code)!='0':raise SafetyError('SDK_response_failed')


def ga12(profile):
    return bool(profile and profile.values.get('sdk_version')=='3.4.2.2' and
                profile.values.get('controller_version')=='3.3.2-GA12 [2026.09.15]')


def feedback_transport_connected(data, profile):
    """Live RPC availability, not an E-stop/Servo clearance signal.

    GA12 repeatedly reports this optional bool false during verified real QP/
    Home operation. For this exact version use successful non-simulation payloads;
    independent servo/control/stop/fault/freshness gates still must all pass.
    """
    flag=data.get('is_robot_connected')
    if type(flag) is not bool or type(data.get('sim_mode')) is not bool:
        raise SafetyError('controller_connection_fields_unknown')
    return data['sim_mode'] is False and (flag is True or ga12(profile))


def read_configuration(client, profile=None):
    names=CONFIG_GETTERS[2:] if ga12(profile) else CONFIG_GETTERS
    result={name:getattr(client,name)() for name in names}
    for raw in result.values():response_ok(raw)
    return result


def _vector(value):
    if not isinstance(value,list) or any(type(v) not in (int,float) for v in value):raise SafetyError('malformed_controller_limit_vector')
    a=np.asarray(value,dtype=float)
    if a.shape!=(6,) or not np.isfinite(a).all():raise SafetyError('malformed_controller_limit_vector')
    return a


def validate_limits(raw, profile, safety):
    """Bound reviewed ACT operating limits, never populate them from hardware maxima."""
    units=profile.values.get('sdk_limit_units')
    if not isinstance(units,dict) or not profile.values.get('sdk_limit_units_review_record'):
        raise SafetyError('controller_limit_units_unverified')
    names=CONFIG_GETTERS[2:] if ga12(profile) else CONFIG_GETTERS
    if set(raw)!=set(names):raise SafetyError('controller_configuration_incomplete')
    for item in raw.values():response_ok(item)
    ranges={}
    for name in names:
        if name not in CONFIG_GETTERS[:3]:continue
        response_ok(raw[name]);unit=units.get(name)
        if unit not in ('deg','rad'):raise SafetyError('controller_limit_units_unverified:'+name)
        data=raw[name]
        if name=='get_safety_limits':low,high=data.get('joint_lower_limits'),data.get('joint_upper_limits')
        else:
            # Actual JointLimitConfig has NO joint_vel_limits / joint_acc_limits.
            values=data.get('joint_pos_limit',{})
            if not isinstance(values,dict):raise SafetyError('controller_joint_limit_schema_unknown')
            low,high=values.get('min'),values.get('max')
        low,high=_vector(low),_vector(high)
        if unit=='deg':low,high=np.deg2rad(low),np.deg2rad(high)
        if np.any(low>=high):raise SafetyError('empty_controller_joint_range')
        ranges[name]={'min_rad':low.tolist(),'max_rad':high.tolist()}
    reference=json.loads(REFERENCE.read_text())
    ref_min=np.deg2rad(reference['joint_min_deg']);ref_max=np.deg2rad(reference['joint_max_deg'])
    low=np.maximum.reduce([ref_min,*[r['min_rad'] for r in ranges.values()]])
    high=np.minimum.reduce([ref_max,*[r['max_rad'] for r in ranges.values()]])
    if np.any(low>=high) or np.any(np.asarray(safety.joint_min_rad)<low) or np.any(np.asarray(safety.joint_max_rad)>high):
        raise SafetyError('ACT_joint_range_exceeds_controller_or_model_limits')
    if np.any(np.asarray(safety.max_velocity_rad_s)>np.deg2rad(reference['max_joint_velocity_deg_s'])):
        raise SafetyError('ACT_velocity_exceeds_manufacturer_reference')
    policy=raw['get_coll_policy'].get('policy')
    if type(policy) is not int or policy!=3:  # Installed enum COLL_STOP=3; NONE/PAUSE/auto-resume are not stop.
        raise SafetyError('controller_collision_STOP_policy_not_confirmed')
    stop_config=raw['get_safety_stop_config']
    for key in ('joint_position_limit_stop_cat','joint_speed_limit_stop_cat','joint_torque_limit_stop_cat',
                'tcp_speed_limit_stop_cat','tcp_force_limit_stop_cat','power_limit_stop_cat'):
        if type(stop_config.get(key)) is not int or stop_config[key] not in (0,1,2):
            raise SafetyError('controller_safety_stop_policy_unknown:'+key)
    return {'position_ranges':ranges,'unavailable_configuration_getters':list(CONFIG_GETTERS[:2]) if ga12(profile) else [],'effective_min_rad':low.tolist(),'effective_max_rad':high.tolist(),
            'manufacturer_velocity_reference_deg_s':reference['max_joint_velocity_deg_s'],
            'actual_controller_velocity_limit':None,'actual_controller_acceleration_limit':None,
            'units_review_record':profile.values['sdk_limit_units_review_record'],
            'teleop_safety_enforcement_verified':False,
            'period_s':0.05,
            'effective_command_step_rad':np.minimum(safety.max_step_rad,np.asarray(safety.max_velocity_rad_s)*0.05).tolist(),
            'max_velocity_change_per_tick_rad_s':(np.asarray(safety.max_acceleration_rad_s2)*0.05).tolist(),
            'note':'Startup configuration bounds only; no claim that MoveTeleJ enforces every limit.'}


def read_status(client, profile=None):
    names=tuple(n for n in STATUS_GETTERS if not (ga12(profile) and n=='get_boot_status'))
    raw={name:getattr(client,name)() for name in names}
    for value in raw.values():response_ok(value)
    return raw


def status_health(raw, profile=None):
    for name in STATUS_GETTERS:
        if ga12(profile) and name=='get_boot_status':continue
        if name not in raw:raise SafetyError('controller_safety_status_missing:'+name)
        response_ok(raw[name])
    category=raw['get_stop_state'].get('category')
    if type(category) is not int or category not in (-1,0,1,2):raise SafetyError('controller_stop_state_unknown')
    code=raw['get_violation_data'].get('violation_code')
    if type(code) is int and code>=0:pass
    elif type(code) is str and code.isascii() and code.isdecimal():code=int(code)
    else:raise SafetyError('controller_violation_state_unknown')
    ready=None
    if not ga12(profile):
        boot=raw['get_boot_status']
        for name in ('control_on','ethercat_connected','robot_pw_supply_on','safety_used','safety_connected'):
            if type(boot.get(name)) is not bool:raise SafetyError('controller_boot_status_unknown:'+name)
        ready=all(boot[k] for k in ('control_on','ethercat_connected','robot_pw_supply_on'))
        if boot['safety_used'] and not boot['safety_connected']:ready=False
    safety=raw['get_safety_control_data']
    if any(type(safety.get(k)) is not bool for k in ('auto_mode','reduced_mode','enabler_pressed')):
        raise SafetyError('controller_safety_control_unknown')
    state=safety.get('safety_state',{})
    if not isinstance(state,dict):raise SafetyError('controller_safety_state_unknown')
    if any(type(state.get(k)) is not int or state[k]<0 for k in ('id','state')):
        raise SafetyError('controller_safety_state_unknown')
    response_ok(state)
    return {'stop_category':category,'violation_code':code,'boot_ready':ready,
            'controller_safety_state':{'id':state['id'],'state':state['state']},
            'estop_clear':None}  # These opaque uint32 fields have no verified E-stop mapping.


def servo_states(servo):
    codes=servo.get('status_codes')
    if not isinstance(codes,list) or len(codes)!=6:raise SafetyError('servo_status_missing')
    result=[]
    # Exact masks/states from installed neuromeka.enums.EtherCATStatus.status2string.
    table=((0x004f,0x0000,'NOT_READY'),(0x004f,0x0040,'SWITCH_DISABLED'),
           (0x006f,0x0021,'READY_SWITCH'),(0x006f,0x0023,'SWITCHED_ON'),
           (0x006f,0x0027,'OPERATION_ENABLED'),(0x006f,0x0007,'QUICK_STOP'),
           (0x004f,0x000f,'FAULT_REACTION'),(0x004f,0x0008,'FAULT'))
    for code in codes:
        try:
            if not isinstance(code,str):raise ValueError()
            value=int(code,16)
            if not 0<=value<=0xffff:raise ValueError()
        except (ValueError,TypeError):raise SafetyError('servo_state_unknown') from None
        state=next((name for mask,pattern,name in table if value&mask==pattern),None)
        if state is None:raise SafetyError('servo_state_unknown')
        result.append(state)
    return result


def request_normal_stop(client):
    """Request CAT2 and end Teleop; try BOTH on error. Still requires fresh stop feedback."""
    errors=[]
    for method in (lambda:client.stop_motion(stop_category=NORMAL_STOP_CATEGORY),client.stop_teleop):
        try:response_ok(method(),required=True)
        except Exception as exc:errors.append(str(exc))
    if errors:raise SafetyError('SDK_stop_request_failed:'+repr(errors))
