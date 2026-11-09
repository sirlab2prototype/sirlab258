"""Inherited numerical ceilings and pending capstone profile; Fake robot only."""
import json
import math
from pathlib import Path
import pytest
from ai.execute.test.test_execution import Rig
from ai.execute.safety import RobotFeedback, SafetyConfig, SafetyError
ROOT=Path(__file__).resolve().parents[3]

def profiles():
    folder=ROOT/'ai/execute/config'
    return [json.loads((folder/name).read_text()) for name in ('indy7_real_reviewed.json','real_hardware_reviewed.json')]

def test_inherited_values_preserve_units_and_stricter_ACT_tick():
    s,h=profiles()
    assert s['max_velocity_rad_s']==pytest.approx([math.radians(45)]*6)
    assert s['max_step_rad']==pytest.approx([math.radians(2.25)]*6)
    assert max(s['max_step_rad'])/.05==pytest.approx(math.radians(45))
    assert s['joint_min_rad']==pytest.approx([math.radians(-174)]*5+[math.radians(-214)])
    assert s['joint_max_rad']==pytest.approx([math.radians(174)]*5+[math.radians(214)])
    assert h['initial_tolerance_rad']==pytest.approx(math.radians(.5))
    assert h['stop_stable_ms']==250 and h['rpc_timeout_ms']==25
    assert h['rpc_timeout_ms']<=s['command_timeout_ms']
    assert s['reviewed_for_real'] is False and h['reviewed_for_real'] is True
    assert s['review_record'] and h['review_record']
    assert 'not approved for real execution' in s['review_record']
    old=json.loads((ROOT/'ai/execute/config/indy7_real_5sec_20261105_backup.json').read_text())
    assert old['reviewed_for_real'] is True
    assert old['max_step_rad']==pytest.approx([math.radians(1.5)]*6)
    assert old['joint_min_rad'][1]==pytest.approx(math.radians(38))
    assert old['joint_max_rad'][1]==pytest.approx(math.radians(42))
    assert s['max_target_error_rad']==old['max_target_error_rad']
    assert s['max_velocity_rad_s']==old['max_velocity_rad_s']
    assert s['max_acceleration_rad_s2']==old['max_acceleration_rad_s2']
    assert s['tracking_error_rad']==old['tracking_error_rad']
    assert s['max_acceleration_rad_s2']==[4,3,4,5,3,5]
    assert len(s['joint_max_rad'])==6 and all(math.isfinite(v) for v in s['joint_max_rad'])
    assert s['tracking_error_rad']==[.05,.05,.05,.08,.05,.08]

@pytest.mark.parametrize('axis',range(6))
@pytest.mark.parametrize('side',['min','max'])
def test_capstone_joint_range_includes_boundary_and_rejects_excess(axis,side):
    import numpy as np
    cfg=SafetyConfig.load(ROOT/'ai/execute/config/indy7_real_reviewed.json')
    target=np.array(cfg.joint_min_rad if side=='min' else cfg.joint_max_rad,dtype=float)
    home=np.array(profiles()[1]['initial_joints_rad'],dtype=float)
    home[axis]=target[axis]
    def feedback(joints):
        return RobotFeedback(joints,np.zeros(6),1_000_000_000,1_000_000_000,
                             True,True,True,'idle',False,False,False,True,True,'FAKE')
    feedback(home).require(1_000_000_000,cfg)
    home[axis]+=(-1 if side=='min' else 1)*1e-8
    with pytest.raises(SafetyError,match='measured_joint_limit'):
        feedback(home).require(1_000_000_000,cfg)

@pytest.mark.parametrize('excess',[False,True])
def test_actual_adopted_step_is_enforced_by_existing_controller(tmp_path,excess):
    s,_=profiles()
    # Acceleration100 is an isolated Fake fixture, never saved as a Real limit.
    rig=Rig(tmp_path,max_step_rad=s['max_step_rad'],max_velocity_rad_s=s['max_velocity_rad_s'],max_acceleration_rad_s2=[100]*6)
    try:
        rig.start();target=s['max_step_rad'][0]+(1e-5 if excess else 0)
        if excess:
            with pytest.raises(SafetyError,match='joint_step'):rig.controller.consume(rig.action(target))
            assert not rig.indy.commands and rig.indy.stop_requests==1
        else:
            rig.controller.consume(rig.action(target));assert len(rig.indy.commands)==1
    finally:
        rig.controller.close_watchdog()
        if rig.lease.stream:rig.lease.stream.close()

def test_inherited_initial_pose_is_exact_project_home():
    import yaml
    _,h=profiles()
    params=yaml.safe_load((ROOT/'ros2_ws/src/capstone_teleop/config/indy7.yaml').read_text())['qp_teleop']['ros__parameters']
    assert h['initial_joints_rad']==pytest.approx([math.radians(v) for v in params['capstone_home_joint_deg']])
    assert h['initial_pose_is_project_home'] is True
