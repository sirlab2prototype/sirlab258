"""Offline Task 1 tests: virtual clocks, Mock devices and a learned ACT checkpoint."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

from ai.execute.authority import CommandLease
from ai.execute.controller import UserApproval
from ai.execute.safety import SafetyError
from ai.execute.task_config import TaskConfig, TASK_NAME, ROOT
from ai.execute.task_executor import TaskExecutor, TaskApproval, CompletionDecision, TERMINAL
from ai.execute.test.test_execution import Rig, MS


class TaskRig(Rig):
    def __init__(self, tmp_path, **settings):
        super().__init__(tmp_path)
        self.task_config = replace(TaskConfig.load(ROOT/'ai/execute/config/task1.json'),
            home_stable_ms=100, home_timeout_seconds=.7, prepare_timeout_seconds=.7, max_act_seconds=2, **settings)
        self.indy.joints = self.task_config.home_rad.copy()
        self.scheduler_stopped = False
        self.scheduler_pending = True
        self.path_ok = True
        self.controller.cancel_scheduler = self.cancel
        self.task = self.new_task()

    def new_task(self):
        return TaskExecutor(self.controller, self.task_config,
            scheduler_empty=lambda: self.scheduler_stopped and not self.scheduler_pending,
            home_path_check=lambda: self.path_ok)

    def cancel(self, reason):
        self.scheduler_stopped, self.scheduler_pending = True, False

    def step(self, ms=50):
        self.clock.advance(ms)
        self.controller.observe(self.health())
        self.controller.heartbeat(inference=True)
        self.task.poll()

    def prepare_task(self):
        self.task.prepare(self.session, self.checkpoint, 'test-checkpoint', self.health())
        for _ in range(5):
            if self.task.state != 'PREPARING': break
            self.step()
        assert self.task.state == 'READY'

    def approval(self, **kwargs):
        base=TaskApproval(UserApproval(self.session,self.checkpoint,'explicit-task-op','mock',self.clock(),self.clock()+10_000*MS),
                          TASK_NAME,self.task_config.identity,True,True,'known software-only path')
        return replace(base,**kwargs)

    def start_task(self):
        self.prepare_task()
        self.task.start(self.approval())
        assert self.task.state == 'RUNNING_ACT'

    def send(self, slot=1, grip=0, offset=.01):
        record=self.action(slot=slot,grip=grip)
        record['action'][:6]=(self.task_config.home_rad+offset).tolist()
        self.task.consume(record)

    def complete_task(self):
        self.task.complete(CompletionDecision(self.session,'human operator'))

    def settle_task(self):
        for _ in range(80):
            if self.task.state in TERMINAL: break
            self.step()
        assert self.task.state in TERMINAL, self.task.snapshot()


@pytest.fixture
def rig(tmp_path):
    result=TaskRig(tmp_path)
    yield result
    if result.task.state not in ('IDLE',*TERMINAL):
        result.task.abort('test_cleanup',fault=True)
        result.settle_task()
    result.task.close()
    # Failed stop markers remain quarantined in test tmp; do not mark released.
    if result.lease.stream is not None:
        result.lease.stream.close();result.lease.stream=None


def test_team_home_configuration_is_reused():
    config=TaskConfig.load(ROOT/'ai/execute/config/task1.json')
    assert config.home_joint_deg==(0.005221,40.003174,-129.996640,90.000870,0.000435,90.507)
    assert config.home_vel_ratio==18 and config.home_acc_ratio==100
    assert config.home_tolerance_deg==.5 and not config.home_path_reviewed_for_real
    assert np.rad2deg(config.home_rad)==pytest.approx(config.home_joint_deg)


def test_whole_task_stop_home_arrival_and_authority(rig):
    rig.start_task()
    for slot in range(1,5):
        rig.send(slot);rig.step()
    rig.complete_task()
    assert rig.task.state=='VERIFYING_STOP' and rig.scheduler_stopped and not rig.scheduler_pending
    assert rig.controller.revoked.is_set() and not rig.indy.home_commands
    with pytest.raises(SafetyError):rig.send(5)
    rig.settle_task()
    result=rig.task.snapshot()
    assert result['state']=='COMPLETED' and result['success'] and result['act_policy_success_user_reported']
    assert result['act_stop_confirmed'] and result['home']['stop_confirmed']
    assert max(result['home']['final_error_deg'])==pytest.approx(0)
    assert len(rig.indy.home_commands)==1 and rig.lease.stream is None
    assert rig.indy.home_commands[0]['target_rad']==pytest.approx(rig.task_config.home_rad)
    assert rig.indy.home_commands[0]['vel_ratio']==18 and rig.indy.home_commands[0]['acc_ratio']==100
    states=[event['state'] for event in rig.events if event['event']=='task_state']
    assert states==['PREPARING','READY','RUNNING_ACT','STOPPING_ACT','VERIFYING_STOP','RETURNING_HOME','COMPLETED']
    authority=[(e['phase'],e['event_kind']) for e in rig.events if e['event']=='task_authority']
    assert authority==[('act','acquired'),('act','released_after_stop'),('home','acquired'),('home','released_after_arrival')]


@pytest.mark.parametrize('kind',['no_approval','no_home','no_path','wrong_config','old_session','expired'])
def test_explicit_bound_task_and_home_approval(rig,kind):
    rig.prepare_task();approval=rig.approval()
    if kind=='no_approval':approval=replace(approval,execution=replace(approval.execution,operator=''))
    elif kind=='no_home':approval=replace(approval,allow_home=False)
    elif kind=='no_path':approval=replace(approval,home_path_confirmed=False)
    elif kind=='wrong_config':approval=replace(approval,config_id='other')
    elif kind=='old_session':approval=replace(approval,execution=replace(approval.execution,session_id='old'))
    else:approval=replace(approval,execution=replace(approval.execution,expires_ns=rig.clock()))
    with pytest.raises(SafetyError):rig.task.start(approval)
    assert not rig.indy.commands and not rig.indy.home_commands and rig.indy.mode_requests==0


def test_not_at_home_preparation_never_arms(rig):
    rig.indy.joints[0]+=.1
    rig.task.prepare(rig.session,rig.checkpoint,'checkpoint',rig.health())
    rig.step(750);rig.settle_task()
    assert rig.task.state=='FAULT' and not rig.indy.mode_requests and not rig.indy.home_commands


def test_cached_home_state_is_not_a_stability_window(rig):
    rig.task.prepare(rig.session,rig.checkpoint,'checkpoint',rig.health())
    rig.indy.stale=True
    rig.step(80)
    assert rig.task.state=='PREPARING'
    rig.step(30);rig.settle_task()
    assert rig.task.state=='FAULT' and not rig.indy.home_commands


def test_leaving_home_after_ready_blocks_start(rig):
    rig.prepare_task();rig.indy.joints[0]+=.1
    with pytest.raises(SafetyError,match='project_home_for_start'):rig.task.start(rig.approval())
    assert rig.indy.mode_requests==0 and not rig.indy.commands


@pytest.mark.parametrize('kind',['timeout','user','emergency','safety','inference','disconnect'])
def test_abnormal_act_never_starts_home(rig,kind):
    rig.start_task();rig.send()
    if kind=='timeout':rig.step(2100)
    elif kind=='user':rig.task.abort('user_abort')
    elif kind=='emergency':rig.task.abort('emergency_stop',emergency=True)
    elif kind=='safety':
        bad=rig.action(slot=2);bad['action'][0]=float('nan')
        with pytest.raises(SafetyError):rig.task.consume(bad)
    elif kind=='inference':rig.controller.fault('inference_timeout');rig.task.poll()
    else:rig.indy.connected=False;rig.task.poll()
    rig.settle_task()
    assert rig.task.state in ('ABORTED','FAULT') and not rig.indy.home_commands
    assert not rig.task.snapshot()['success']
    if kind=='timeout':assert rig.task.reason=='act_timeout'
    with pytest.raises(SafetyError):rig.task.start(rig.approval())


def test_user_completion_is_not_inferred_from_open_or_stillness(rig):
    rig.start_task()
    for slot in range(1,6):rig.send(slot,offset=0);rig.step()
    assert rig.task.state=='RUNNING_ACT' and rig.mark.calls==[]
    with pytest.raises(SafetyError):rig.task.complete(CompletionDecision(rig.session,'detector',source='unverified_detector'))
    assert not rig.indy.home_commands


def test_completion_needs_actions_current_session_and_stale_health_is_fault(rig):
    rig.start_task()
    with pytest.raises(SafetyError):rig.complete_task()
    rig.send()
    with pytest.raises(SafetyError):rig.task.complete(CompletionDecision('other','op'))
    rig.clock.advance(200)
    with pytest.raises(SafetyError):rig.complete_task()
    rig.settle_task()
    assert rig.task.state=='FAULT' and not rig.indy.home_commands


def test_gripper_grasp_release_history_and_no_duplicate_calls(rig):
    rig.start_task()
    for slot in range(1,7):rig.send(slot,grip=1);rig.step()
    for slot in range(7,14):rig.send(slot,grip=0);rig.step()
    assert rig.mark.calls==[1,0]
    rig.complete_task();rig.settle_task()
    assert rig.task.state=='COMPLETED'
    assert {value['command_state'] for value in rig.task.snapshot()['gripper_history']}=={0,1}


def test_completion_while_still_grasping_does_not_home(rig):
    rig.start_task()
    for slot in range(1,5):rig.send(slot,grip=1);rig.step()
    rig.complete_task();rig.settle_task()
    assert rig.task.state=='ABORTED' and not rig.indy.home_commands
    assert 'release_command' in rig.task.reason


@pytest.mark.parametrize('kind',['stop_error','stop_timeout','not_empty','path_unknown','approval_expired','authority_unknown'])
def test_home_preconditions_are_rechecked_after_act(rig,kind):
    rig.start_task();rig.send();rig.step()
    if kind=='stop_error':rig.indy.stop_error=OSError('stop link lost')
    elif kind=='stop_timeout':rig.indy.stop_delay_ns=2000*MS
    elif kind=='not_empty':rig.controller.cancel_scheduler=lambda reason:None
    elif kind=='path_unknown':rig.path_ok=False
    elif kind=='approval_expired':rig.task.approval=replace(rig.task.approval,execution=replace(rig.task.approval.execution,expires_ns=rig.clock()))
    else:rig.indy.exclusive=None
    rig.complete_task();rig.settle_task()
    assert rig.task.state=='FAULT' and not rig.indy.home_commands


@pytest.mark.parametrize('kind',['command_failure','timeout','offset'])
def test_home_failure_preserves_act_success_but_task_fails(rig,kind):
    rig.start_task();rig.send();rig.step()
    if kind=='command_failure':rig.indy.home_error=OSError('home command failed')
    elif kind=='timeout':rig.indy.home_delay_ns=2000*MS
    else:rig.indy.home_offset_rad[0]=.1
    rig.complete_task();rig.settle_task()
    result=rig.task.snapshot()
    assert result['state']=='FAULT' and not result['success'] and result['failure_stage']=='RETURNING_HOME'
    assert result['act_policy_success_user_reported'] and len(rig.indy.home_commands)==1
    assert result['home']['stop_confirmed']


def test_home_ack_does_not_imply_arrival_or_stability(rig):
    rig.start_task();rig.send();rig.step();rig.complete_task()
    while rig.task.state!='RETURNING_HOME':rig.step()
    rig.step(200)
    assert rig.task.state=='RETURNING_HOME' and rig.task.home.future.done()
    rig.step(50)
    assert rig.task.state=='RETURNING_HOME'
    rig.settle_task();assert rig.task.state=='COMPLETED'


def test_home_stability_does_not_credit_prearrival_cached_time(rig):
    rig.start_task();rig.send();rig.step()
    rig.indy.home_delay_ns=10*MS
    rig.complete_task()
    while rig.task.state!='RETURNING_HOME':rig.step()
    rig.step(10)  # Arrival first observed between the 20Hz feedback ticks.
    first_observed=rig.clock()
    rig.step(90)
    assert rig.clock()-first_observed==90*MS
    assert rig.task.state=='RETURNING_HOME'  # Full configured 100ms has not elapsed.
    rig.settle_task()
    assert rig.task.state=='COMPLETED'


def test_fresh_post_stop_joint_state_required(rig):
    rig.start_task();rig.send();rig.step()
    rig.complete_task();rig.indy.stale=True
    rig.task.poll()
    assert not rig.indy.home_commands
    rig.step(150);rig.settle_task()
    assert rig.task.state=='FAULT' and not rig.indy.home_commands


def test_authority_reacquisition_race_does_not_stop_other_owner(rig,tmp_path):
    rig.start_task();rig.send();rig.step();rig.complete_task()
    competing=CommandLease(tmp_path/'lease.json')
    original=rig.task.scheduler_empty
    def raced():
        if competing.stream is None:competing.acquire('external-home-owner')
        return original()
    rig.task.scheduler_empty=raced
    stops=rig.indy.stop_requests
    rig.settle_task()
    assert rig.task.state=='FAULT' and not rig.indy.home_commands and rig.indy.stop_requests==stops
    assert rig.task.snapshot()['act_policy_success_user_reported']
    competing.release(confirmed_stopped=True)


def test_home_acquire_failure_on_shared_lease_never_stops_its_owner(rig):
    rig.prepare_task()
    rig.lease.acquire('another-controller')
    stops=rig.indy.stop_requests
    with pytest.raises(SafetyError,match='lease_already_held'):rig.task.home.start()
    rig.task.home.cancel('acquisition_failed')
    assert rig.indy.stop_requests==stops and rig.lease.owner=='another-controller'
    rig.lease.release(confirmed_stopped=True)


def test_controller_identity_change_before_home_is_rejected(rig):
    rig.start_task();rig.send();rig.step();rig.complete_task()
    rig.controller.poll()
    assert rig.controller.state=='STOPPED'
    rig.controller.session_id='another-session'
    rig.task.poll()
    assert rig.task.state=='FAULT' and not rig.indy.home_commands
    assert 'session_or_checkpoint_changed' in rig.task.reason


@pytest.mark.parametrize('kind',['user','emergency','path_lost','communication'])
def test_home_interrupts_stop_without_restarting_home(rig,kind):
    rig.start_task();rig.send();rig.step();rig.complete_task()
    while rig.task.state!='RETURNING_HOME':rig.step()
    if kind=='user':rig.task.abort('user_abort')
    elif kind=='emergency':rig.task.abort('emergency_stop',emergency=True)
    elif kind=='path_lost':rig.path_ok=False
    else:rig.indy.connected=False
    rig.settle_task()
    assert rig.task.state in ('ABORTED','FAULT') and len(rig.indy.home_commands)==1
    assert rig.task.home.permit.is_set()


def test_repeat_sessions_need_new_task_and_no_old_completion(rig):
    rig.start_task();rig.send();rig.step();rig.complete_task();rig.settle_task()
    old=rig.session
    with pytest.raises(SafetyError):rig.task.prepare(old,rig.checkpoint,'checkpoint',rig.health())
    rig.task=rig.new_task();rig.session='session-b';rig.scheduler_stopped=False;rig.scheduler_pending=True
    rig.start_task();rig.send();rig.step()
    with pytest.raises(SafetyError):rig.task.complete(CompletionDecision(old,'op'))
    rig.complete_task();rig.settle_task()
    assert rig.task.state=='COMPLETED' and rig.task.snapshot()['act_action_count']==1


def test_real_home_and_task_cli_are_locked(rig):
    rig.start_task();rig.send();rig.step();rig.complete_task()
    rig.indy.is_mock=False
    rig.settle_task()
    assert rig.task.state=='FAULT' and not rig.indy.home_commands
    assert 'real_project_home_locked' in rig.task.reason
    command=['./act','task','--task',TASK_NAME,'--mode','real','--checkpoint','unused']
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==2 and 'locked' in result.stderr
    command[command.index('real')]='mock'
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==2 and '--approve-mock' in result.stderr


def test_pending_home_fault_stays_latched_without_rearming(rig):
    rig.start_task();rig.send();rig.step();rig.complete_task()
    while rig.task.state!='RETURNING_HOME':rig.step()
    rig.indy.home_error=OSError('too late to change already dispatched future')
    rig.task.abort('collision_suspected',fault=True)
    rig.settle_task()
    requests=len(rig.indy.home_commands)
    for _ in range(5):rig.step()
    assert rig.task.state=='FAULT' and len(rig.indy.home_commands)==requests
    with pytest.raises(SafetyError):rig.task.start(rig.approval())


def test_obsolete_file_command_faults_without_home(rig,tmp_path):
    from ai.execute.task_session import MockTaskSession
    rig.start_task();rig.send()
    output=tmp_path/'task-client';output.mkdir()
    session=MockTaskSession(None,rig.controller,'op',rig.task_config,output,'mock path')
    session.task=rig.task;session.prepared=True
    with session.command_path.open('a') as stream:
        stream.write(json.dumps({'session_id':'previous-session','command':'complete','operator':'op'})+'\n')
    try:
        with pytest.raises(SafetyError,match='obsolete'):session.poll()
        rig.settle_task()
        assert rig.task.state=='FAULT' and not rig.indy.home_commands
    finally:session.commands.close()


def test_late_completion_is_timeout_even_before_next_poll(rig):
    rig.start_task();rig.send();rig.clock.advance(2100)
    rig.controller.observe(rig.health())
    with pytest.raises(SafetyError,match='completion_after_act_timeout'):rig.complete_task()
    rig.settle_task()
    assert rig.task.state=='ABORTED' and rig.task.reason=='act_timeout' and not rig.indy.home_commands


def test_task_log_failure_cannot_bypass_stop(rig):
    rig.start_task();rig.send()
    def broken(*args,**kwargs):raise OSError('Task log disk failure')
    rig.controller._log_sink=broken
    with pytest.raises(SafetyError):rig.task.abort('user_abort')
    assert rig.controller.revoked.is_set() and rig.indy.stop_requests==1
    rig.settle_task()
    assert rig.task.state=='FAULT' and not rig.indy.home_commands


def test_late_act_fault_during_home_cancels_home(rig):
    rig.start_task();rig.send();rig.step();rig.complete_task()
    while rig.task.state!='RETURNING_HOME':rig.step()
    rig.controller.fault('late_watchdog_fault')
    rig.settle_task()
    assert rig.task.state=='FAULT' and len(rig.indy.home_commands)==1 and rig.task.home.permit.is_set()


def test_emergency_file_command_is_processed_during_home_finish(rig,tmp_path,monkeypatch):
    from ai.execute.task_session import MockTaskSession
    rig.start_task();rig.send();rig.step();rig.complete_task()
    while rig.task.state!='RETURNING_HOME':rig.step()
    output=tmp_path/'home-client';output.mkdir()
    session=MockTaskSession(None,rig.controller,'op',rig.task_config,output,'mock path')
    session.task=rig.task;session.prepared=True
    with session.command_path.open('a') as stream:
        stream.write(json.dumps({'session_id':rig.session,'command':'emergency_stop'})+'\n')
    monkeypatch.setattr('ai.execute.task_session.time.sleep',lambda _:rig.step())
    session.finish('task_control_transition')
    assert session.task_result['state']=='FAULT' and session.task_result['emergency_requested']
    assert len(rig.indy.home_commands)==1 and (output/'task_result.json').exists()


from ai.train.test.test_validate_dataset import real_base


@pytest.fixture(scope='module')
def task_checkpoint(real_base,tmp_path_factory):
    """Real two-step ACT training with safe Home-relative hold/open labels.

    This tests wiring; it does not learn or verify the basket manipulation task.
    """
    import shutil
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from ai.dataset_contract import dataset_features,CAMERA_KEYS,IMAGE_SHAPE,TIMING_DIRECTORY
    from ai.train import run_train
    from ai.train.test.test_training_pipeline import arguments
    root=tmp_path_factory.mktemp('task-model')
    config=TaskConfig.load(ROOT/'ai/execute/config/task1.json')
    target=config.home_rad+.01
    dataset=root/'synthetic-home-hold-open'
    ds=LeRobotDataset.create(repo_id='local/indy7_act',root=dataset,robot_type='indy7',fps=20,
         features=dataset_features(),use_videos=True,image_writer_threads=0,vcodec='h264')
    for episode in range(2):
        for i in range(3):
            ds.add_frame({**{key:np.full(IMAGE_SHAPE,i*30+episode,np.uint8) for key in CAMERA_KEYS},
                'observation.state':np.array([*(config.home_rad+i*.005),i%2],np.float32),
                'action':np.array([*target,0],np.float32),'task':'synthetic Home-relative hold and open'})
        ds.save_episode(parallel_encoding=False)
    ds.finalize()
    shutil.copytree(real_base/TIMING_DIRECTORY,dataset/TIMING_DIRECTORY)
    manifest=run_train.main([*arguments(dataset,root/'run'),'--chunk-size','12','--n-action-steps','4'])
    return Path(manifest['checkpoint'])


def task_command(checkpoint,output):
    return ['./act','task','--task',TASK_NAME,'--mode','mock','--checkpoint',str(checkpoint),
            '--approve-mock','explicit-mock-task-operator','--approve-home-mock',
            '--confirm-home-path-mock','software fixture only','--device','cpu','--output-dir',str(output)]


def check_successful_e2e(output,source):
    result=json.loads((output/'result.json').read_text())
    assert result['status']=='complete' and result['robot_commands_sent']==0
    task=result['task']
    assert task['state']=='COMPLETED' and task['success'] and task['act_stop_confirmed']
    assert task['home']['state']=='COMPLETED' and max(task['home']['final_error_deg'])==pytest.approx(0)
    assert (task['home']['ended_monotonic_ns']-task['home']['started_monotonic_ns'])/1e9 >= .45
    assert task['completion_decision']['source']==source and not task['physical_task_success_verified']
    assert task['act_action_count']>=20 and result['client']['missed_action_deadlines']==0
    events=[json.loads(line) for line in (output/'client/events.jsonl').read_text().splitlines()]
    home_start=next(i for i,event in enumerate(events) if event['event']=='task_home_requested')
    assert not any(event['event']=='execution_joint_command' for event in events[home_start:])
    assert any(event['event']=='actions_cleared' for event in events)
    assert json.loads((output/'client/mock_command_lease.json').read_text())['status']=='released'
    assert (output/'client/task_result.json').exists() and 'COMPLETED' in (output/'client/task_summary.txt').read_text()
    print('TASK_E2E '+json.dumps(result['client']),flush=True)
    import os,shutil
    if os.environ.get('ACT_TASK_TEST_REPORT_DIR'):
        destination=Path(os.environ['ACT_TASK_TEST_REPORT_DIR'])
        label='simulated_user' if source=='mock_test_user' else 'user_command_file'
        shutil.copyfile(output/'client/events.jsonl',destination/f'{label}_events.jsonl')
        shutil.copyfile(output/'client/task_result.json',destination/f'{label}_task_result.json')
        shutil.copyfile(output/'client/task_summary.txt',destination/f'{label}_task_summary.txt')
    return result


def test_learned_checkpoint_to_mock_task_and_home(task_checkpoint,tmp_path):
    output=tmp_path/'learned-task'
    result=subprocess.run([*task_command(task_checkpoint,output),'--duration','4',
                           '--mock-complete-after-actions','25'],capture_output=True,text=True,timeout=90)
    if result.returncode:pytest.fail(result.stdout+result.stderr+(output/'client.log').read_text()[-5000:])
    report=check_successful_e2e(output,'mock_test_user')
    assert report['task']['act_action_count']==25
    assert 19.5 <= report['client']['actual_action_fps'] <=20.5


def test_user_completion_file_to_home(task_checkpoint,tmp_path):
    import time
    output=tmp_path/'user-command-task'
    with (tmp_path/'launcher.log').open('w') as stream:
        process=subprocess.Popen([*task_command(task_checkpoint,output),'--duration','8'],stdout=stream,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                if process.poll() is not None:pytest.fail((tmp_path/'launcher.log').read_text())
                events_path=output/'client/events.jsonl'
                try:
                    rows=[json.loads(line) for line in events_path.read_text().splitlines()]
                    actions=[row for row in rows if row['event']=='action']
                except (FileNotFoundError,json.JSONDecodeError):actions=[]
                if len(actions)>=20:
                    with (output/'client/task_commands.jsonl').open('a') as command:
                        command.write(json.dumps({'command':'complete','session_id':actions[-1]['session_id'],
                            'operator':'offline-test-user','note':'Synthetic test completion assertion only'})+'\n')
                    break
                time.sleep(.01)
            else:pytest.fail('No actions for completion command')
            assert process.wait(timeout=20)==0,(tmp_path/'launcher.log').read_text()
            check_successful_e2e(output,'user')
        finally:
            if process.poll() is None:
                process.terminate();process.wait(timeout=5)


def test_cli_duration_is_timeout_not_success(task_checkpoint,tmp_path):
    output=tmp_path/'task-timeout'
    result=subprocess.run([*task_command(task_checkpoint,output),'--duration','1.2'],capture_output=True,text=True,timeout=90)
    assert result.returncode==1
    report=json.loads((output/'result.json').read_text())
    assert report['task']['state']=='ABORTED' and report['task']['reason']=='act_timeout'
    assert not report['task']['success'] and report['task']['home']['state']=='IDLE'
    assert report['task']['completion_decision'] is None and report['robot_commands_sent']==0
