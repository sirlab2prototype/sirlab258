"""Target update integration under explicit Fake limits, no device commands."""
import numpy as np
import pytest
from ai.execute.test.test_execution import Rig
from ai.execute.safety import SafetyError


def rig(tmp):
 r=Rig(tmp,max_step_rad=[.02]*6,max_velocity_rad_s=[.4]*6,max_acceleration_rad_s2=[1.]*6,tracking_error_rad=[.15]*6,max_target_error_rad=[.2]*6)
 r.controller.target_update_mode='bounded_rate';return r


def close(r):
 r.controller.close_watchdog()
 if r.lease.stream:r.lease.stream.close()


def test_continuous_bounded_commands_use_feedback_and_keep_original_goal(tmp_path):
 r=rig(tmp_path)
 try:
  r.start();r.controller.consume(r.action(0,slot=1))
  for i in range(2,10):
   r.advance();r.controller.consume(r.action(.08,slot=i))
  commands=np.array(r.indy.commands);v=np.diff(commands,axis=0)/.05;a=np.diff(v,axis=0)/.05
  assert np.max(abs(np.diff(commands,axis=0)))<=.02
  assert np.max(abs(v))<=.4 and np.max(abs(a))<=1.+1e-10
  logs=[x for x in r.events if x['event']=='execution_joint_command']
  assert logs[-1]['raw_policy_target_rad']==[.08]*6
  assert logs[-1]['target_update_mode']=='bounded_rate'
  assert len(r.indy.commands)==9 and len(set(tuple(x) for x in r.indy.commands))>1
 finally:close(r)


def test_first_raw_goal_is_checked_then_rate_limited_before_guard(tmp_path):
 r=rig(tmp_path)
 try:
  r.start()
  r.controller.consume(r.action(.015,slot=1))
  expected=1.*.05**2
  assert np.allclose(r.indy.commands[0],[expected]*6)
  event=next(x for x in r.events if x['event']=='execution_joint_command')
  assert event['raw_policy_target_rad']==[.015]*6
  assert np.allclose(event['target_absolute_rad'],[expected]*6)
  assert not r.controller.revoked.is_set()
 finally:close(r)


def test_first_raw_goal_outside_tracking_envelope_is_not_clipped_into_range(tmp_path):
 r=Rig(tmp_path,max_step_rad=[.2]*6,max_velocity_rad_s=[.4]*6,
       max_acceleration_rad_s2=[1.]*6,tracking_error_rad=[.01]*6,
       max_target_error_rad=[.2]*6)
 r.controller.target_update_mode='bounded_rate'
 try:
  r.start()
  with pytest.raises(SafetyError,match='raw_target_current_error'):
   r.controller.consume(r.action(.015,slot=1))
  assert r.indy.commands==[] and r.controller.revoked.is_set()
 finally:close(r)


def test_new_goal_replaces_first_goal_without_queued_replay(tmp_path):
 r=rig(tmp_path)
 try:
  r.start();r.controller.consume(r.action(.015,slot=1))
  first=r.indy.commands[-1][0]
  r.advance();r.controller.consume(r.action(-.01,slot=2))
  assert r.indy.commands[-1][0] <= first+1e-10
  events=[x for x in r.events if x['event']=='execution_joint_command']
  assert events[-1]['raw_policy_target_rad']==[-.01]*6
  assert len(r.indy.commands)==2
 finally:close(r)


@pytest.mark.parametrize('case',['first_jump','far','range','nan','stale'])
def test_bad_raw_goal_not_divided_into_safe_looking_commands(tmp_path,case):
 r=rig(tmp_path)
 try:
  r.start()
  if case!='first_jump':r.controller.consume(r.action(0));r.advance()
  target={'first_jump':.03,'far':.3,'range':5.,'nan':float('nan'),'stale':.08}[case]
  if case=='stale':
   r.indy.stale=True;r.advance(150)
  before=len(r.indy.commands);tails=[1];r.controller.cancel_scheduler=lambda why:tails.clear()
  with pytest.raises(SafetyError):r.controller.consume(r.action(target,slot=1 if case=='first_jump' else 2))
  assert len(r.indy.commands)==before and r.controller.revoked.is_set() and not tails
  assert r.indy.stop_requests==1
 finally:close(r)


@pytest.mark.parametrize('scenario',['normal','server_disconnect','operator_stop'])
def test_existing_chunk_scheduler_real_session_feedback_loop_with_bounded_mode(tmp_path,monkeypatch,scenario):
    from ai.execute.test import test_real_execution as existing
    original=existing.profile
    def bounded_profile(path):
        p=original(path);p.values['target_update_mode']='bounded_rate';return p
    monkeypatch.setattr(existing,'profile',bounded_profile)
    # Reuse existing Fake RPC, scheduler, two RGB history and RealSession checks.
    existing.test_Mock_ACT_chunk_scheduler_RealSession_controller_feedback_loop(tmp_path,monkeypatch,scenario)
