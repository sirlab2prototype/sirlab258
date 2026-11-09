"""Only profile readiness gaps: virtual time and fake RPC, never devices."""
from types import SimpleNamespace as M
import pytest
from ai.execute.real_driver import DeadlineStub
from ai.execute.safety import SafetyError
from ai.execute.test.test_execution import Rig

@pytest.mark.parametrize('entry',['poll','watchdog_check','consume'])
def test_trial_deadline_blocks_further_actions_without_scheduler_progress(tmp_path,entry):
    rig=Rig(tmp_path);rig.controller.max_run_ns=5_000_000_000;chunks=[1,2]
    rig.controller.cancel_scheduler=lambda why:chunks.clear()
    try:
        rig.start();rig.clock.advance(5000)
        if entry=='consume':
            with pytest.raises(SafetyError):rig.controller.consume(rig.action())
        else:getattr(rig.controller,entry)()
        assert rig.controller.revoked.is_set() and not rig.indy.commands
        assert rig.indy.stop_requests==1 and not chunks
        with pytest.raises(SafetyError):rig.controller.consume(rig.action())
    finally:
        rig.controller.close_watchdog()
        if rig.lease.stream:rig.lease.stream.close()

@pytest.mark.parametrize('failed',[False,True])
def test_RPC_timing_deadline_and_failure_never_retried(failed):
    calls=[];events=[]
    def rpc(req,timeout):
        calls.append((req,timeout))
        if failed:raise TimeoutError('fake deadline')
        return 'ok'
    wrapper=DeadlineStub(M(GetControlData=rpc),.02,group='rtde',log=lambda event,**fields:events.append(fields))
    if failed:
        with pytest.raises(TimeoutError):wrapper.GetControlData('req')
    else:assert wrapper.GetControlData('req')=='ok'
    assert calls==[('req',.02)] and len(events)==1
    row=events[0];assert row['rpc']=='GetControlData' and row['elapsed_ns']>=0
    assert row['response_monotonic_ns']>=row['request_monotonic_ns']
    assert row['status']==('TimeoutError' if failed else 'OK')


def test_expanded_operating_limits_require_new_review_without_losing_estop_qualification():
    from ai.execute.preflight import check
    from pathlib import Path
    root=Path(__file__).resolve().parents[3]
    out=check(root/'ai/execute/config/indy7_real_reviewed.json',root/'ai/execute/config/real_hardware_reviewed.json')
    assert not out['profile_valid']
    assert any('reviewed_real_profiles_required' in error for error in out['errors'])
    assert out['estop_source']=='qualified_sdk'
    assert out['authority_mode']=='supervised_direct_link'
    assert out['hardware_connected'] is False and out['robot_commands_sent']==0
    old=check(root/'ai/execute/config/indy7_real_5sec_20261105_backup.json',root/'ai/execute/config/real_hardware_reviewed.json')
    assert old['profile_valid'] and not old['errors']
