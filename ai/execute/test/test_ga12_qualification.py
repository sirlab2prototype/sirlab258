"""Replay already captured evidence only; no SDK construction or ROS."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as M
import pytest
from ai.execute.estop import SdkEstop,ga12_control_available,require_mapping
from ai.execute.safety import SafetyError
from ai.execute.test.test_execution import Rig
ROOT=Path(__file__).resolve().parents[3]

def evidence():
    p=M(values=json.loads((ROOT/'ai/execute/config/real_hardware_reviewed.json').read_text()))
    rows=[json.loads(Path(p.values['sdk_estop']['captures'][phase]['path']).read_text())['samples'][0]['raw']
          for phase in ('clear_before','pressed','clear_after')]
    return p,rows

def test_actual_three_captures_qualify_and_session_never_recovers():
    p,rows=evidence();assert require_mapping(p)
    sensor=SdkEstop(p)
    assert sensor.update(rows[0]);assert not sensor.update(rows[1]);assert not sensor.update(rows[2])
    assert SdkEstop(p).update(rows[2]) # Only a new explicitly authorized session can qualify again.

@pytest.mark.parametrize('case',['servo','brake','fault','stop','op','missing','simulation'])
def test_independent_signals_fail_closed(case):
    _,rows=evidence();raw=deepcopy(rows[0])
    if case=='servo':raw['get_servo_data']['servo_actives'][0]=False
    elif case=='brake':raw['get_servo_data']['brake_actives'][0]=True
    elif case=='fault':raw['get_violation_data']['violation_code']='1'
    elif case=='stop':raw['get_stop_state']['category']=0
    elif case=='op':raw['get_control_data']['op_state']=2
    elif case=='simulation':raw['get_control_data']['sim_mode']=True
    else:raw.pop('get_stop_state')
    try:assert not ga12_control_available(raw)
    except SafetyError:pass

def test_opaque_safety_state_is_not_used_as_clearance():
    _,rows=evidence();raw=deepcopy(rows[0]);raw['get_safety_control_data']['safety_state']={'id':123,'state':123}
    assert ga12_control_available(raw)
    raw=deepcopy(rows[1]);raw['get_safety_control_data']['safety_state']={'id':0,'state':255}
    assert not ga12_control_available(raw)

def test_estop_cancels_actions_and_watchdog_session_no_resume(tmp_path):
    p,rows=evidence();sensor=SdkEstop(p);active=[rows[0]];rig=Rig(tmp_path);original=rig.indy.feedback
    chunks=['pending chunk']
    def feedback(now):
        if not sensor.update(active[0]):raise SafetyError('qualified_control_unavailable')
        return original(now)
    rig.indy.feedback=feedback
    rig.controller.cancel_scheduler=lambda reason:chunks.clear()
    try:
        rig.start();rig.controller.consume(rig.action());before=len(rig.indy.commands)
        active[0]=rows[1];rig.controller.watchdog_check()
        assert rig.controller.revoked.is_set() and not chunks and rig.indy.stop_requests==1
        active[0]=rows[2]
        with pytest.raises(SafetyError):rig.controller.consume(rig.action(slot=2))
        assert len(rig.indy.commands)==before and sensor.revoked
    finally:
        rig.controller.close_watchdog()
        if rig.lease.stream:rig.lease.stream.close()
