"""No real ROS or robot: virtual-clock devices, fake ROS wiring, synthetic E2E."""
from concurrent.futures import Future
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace as M

import numpy as np
import pytest

from ai.execute.adapters import MockIndy7Adapter, MockMark7Transport
from ai.execute.authority import CommandLease
from ai.execute.controller import ExecutionController, UserApproval
from ai.execute.safety import SafetyConfig, ObservationHealth, SafetyError
from ai.execute.ros_adapters import RosIndy7Adapter, RosMark7Transport, RealActivation

ROOT = Path(__file__).resolve().parents[3]
MS = 1_000_000


class Clock:
    def __init__(self): self.now = 1_000_000_000
    def __call__(self): return self.now
    def advance(self, ms): self.now += round(ms * MS)


class Rig:
    def __init__(self, tmp_path, **overrides):
        self.clock, self.events = Clock(), []
        self.config = replace(SafetyConfig.load(ROOT/'ai/execute/config/mock.json'), **overrides)
        self.indy, self.mark = MockIndy7Adapter(self.clock), MockMark7Transport(self.clock)
        self.lease = CommandLease(tmp_path/'lease.json')
        self.controller = ExecutionController(self.indy, self.mark, self.lease, self.config,
             lambda event, **value: self.events.append({'event':event, **value}), clock=self.clock)
        self.session, self.checkpoint = 'session-a', 'checkpoint-a'
    def health(self): return ObservationHealth(self.clock(), True, {})
    def connect(self): self.controller.connect(self.session, self.checkpoint, 'test-checkpoint', self.health())
    def start(self):
        self.connect()
        self.controller.arm(UserApproval(self.session,self.checkpoint,'explicit-test-operator','mock',self.clock(),self.clock()+60_000*MS))
        self.controller.poll()
        assert self.controller.state == 'ARMED'
        self.controller.begin()
    def action(self, target=.01, grip=.5, slot=1):
        return {'session_id':self.session,'checkpoint_id':self.checkpoint,'request_id':f'request-{slot}',
                'action':[target]*6+[grip],'action_index':slot-1,
                'observation_monotonic_ns':self.clock()-100*MS,
                'schedule':{'slot':slot,'deadline_monotonic_ns':self.clock(),'missed_deadlines':0,'missed_deadlines_total':0}}
    def advance(self, ms=50):
        self.clock.advance(ms)
        self.controller.observe(self.health())
        self.controller.heartbeat(inference=True)
    def settle(self):
        for _ in range(30):
            self.controller.poll()
            if self.controller.state != 'STOPPING': break
            self.clock.advance(50)


@pytest.fixture
def rig(tmp_path):
    instance = Rig(tmp_path)
    yield instance
    instance.controller.close_watchdog()
    if instance.lease.stream: instance.lease.stream.close() # scoped mock test file only


def test_explicit_arm_and_absolute_rad_execution(rig):
    assert rig.controller.state == 'DISCONNECTED'
    rig.connect()
    assert rig.controller.state == 'READY' and rig.indy.mode_requests == 0
    with pytest.raises(SafetyError): rig.controller.begin()
    with pytest.raises(SafetyError): rig.controller.arm(UserApproval('wrong','checkpoint-a','op','mock',rig.clock(),rig.clock()+MS))
    assert rig.indy.mode_requests == 0
    rig.controller.arm(UserApproval(rig.session,rig.checkpoint,'operator','mock',rig.clock(),rig.clock()+10000*MS))
    rig.controller.poll(); rig.controller.begin()
    rig.controller.consume(rig.action(.01))
    assert rig.indy.commands == [[.01]*6] # no rad->degree conversion in this adapter
    assert rig.controller.acknowledged == 1
    rig.controller.complete_by_user(); rig.settle()
    result = rig.controller.snapshot()
    assert result['state']=='STOPPED' and result['stop_confirmed'] and result['policy_normal_end']
    assert result['executed_actions']==1 and not result['physical_execution_verified']


@pytest.mark.parametrize('fault,reason', [('limit','target_joint_limit'),('target_error','target_current_error'),
    ('step','joint_step_limit'),('nan','action_must_be_finite_7d'),('inf','action_must_be_finite_7d'),
    ('shape','action_must_be_finite_7d'),('deadline','action_deadline'),('gap','scheduler_deadline_gap'),
    ('session','obsolete_session'),('gripper','gripper_output_range'),('index','invalid_action_index')])
def test_action_rejected_before_send(rig, fault, reason):
    rig.start(); record=rig.action()
    if fault=='limit': record['action'][0]=5
    elif fault=='target_error': record['action'][0]=.6
    elif fault=='step': record['action'][0]=.3
    elif fault=='nan': record['action'][0]=float('nan')
    elif fault=='inf': record['action'][6]=float('inf')
    elif fault=='shape': record['action']=[0]*6
    elif fault=='deadline': record['schedule']['deadline_monotonic_ns']-=30*MS
    elif fault=='gap': record['schedule']['missed_deadlines']=1
    elif fault=='session': record['session_id']='old-session'
    elif fault=='gripper': record['action'][6]=100
    else: record.pop('action_index')
    with pytest.raises(SafetyError): rig.controller.consume(record)
    assert not rig.indy.commands and reason in rig.controller.reason
    rig.settle()
    assert rig.controller.state=='FAULT'
    with pytest.raises(SafetyError): rig.controller.begin()
    with pytest.raises(SafetyError): rig.connect()


@pytest.mark.parametrize('kind', ['velocity','acceleration'])
def test_command_derivative_limits(tmp_path, kind):
    overrides={'max_velocity_rad_s':[.1]*6} if kind=='velocity' else {'max_acceleration_rad_s2':[1.]*6}
    rig=Rig(tmp_path,**overrides); rig.start()
    with pytest.raises(SafetyError): rig.controller.consume(rig.action(.01))
    assert f'command_{kind}_limit' in rig.controller.reason and not rig.indy.commands
    rig.settle()


@pytest.mark.parametrize('kind', ['joint','observation','authority','servo','mode','estop'])
def test_live_conditions_stop_commands(rig,kind):
    rig.start()
    if kind=='joint': rig.indy.stale=True; rig.advance(200)
    elif kind=='observation': rig.controller.health=ObservationHealth(rig.clock(),False,{})
    elif kind=='authority': rig.indy.exclusive=None
    elif kind=='servo': rig.indy.servo_ready=False
    elif kind=='mode': rig.indy.mode='joint_relative'
    else: rig.indy.estop=True
    with pytest.raises(SafetyError): rig.controller.consume(rig.action())
    assert rig.controller.fault_latched and not rig.indy.commands
    rig.settle()


def test_command_future_timeout_and_transmission_error(rig):
    rig.start(); rig.indy.command_delay_ns=100*MS
    rig.controller.consume(rig.action())
    rig.advance(60); rig.controller.poll()
    assert rig.controller.fault_latched and 'command_timeout' in rig.controller.reason
    rig.settle()
    assert rig.controller.state=='FAULT'


def test_send_exception_stops(rig):
    rig.start(); rig.indy.send_error=OSError('transport down')
    with pytest.raises(OSError): rig.controller.consume(rig.action())
    rig.settle()
    assert rig.controller.state=='FAULT' and rig.indy.stop_requests==1


def test_persistent_tracking_error(rig):
    rig.controller.config=replace(rig.config,tracking_error_rad=[.001]*6,tracking_timeout_ms=100)
    rig.start(); rig.indy.freeze=True
    rig.controller.consume(rig.action(.01))
    rig.advance(50); rig.controller.poll()
    rig.advance(60); rig.controller.poll()
    rig.advance(50); rig.controller.poll()
    assert rig.controller.fault_latched and 'tracking_error' in rig.controller.reason
    rig.settle()


def test_gripper_hysteresis_and_duplicate_suppression(rig):
    rig.start()
    for slot, value in enumerate([.8,.6,.8,.8,.8,.8,.8],1):
        rig.controller.consume(rig.action(0,value,slot)); rig.advance()
    assert rig.mark.calls==[1] and rig.controller.gripper.state==1
    for slot in range(8,15):
        rig.controller.consume(rig.action(0,.1,slot)); rig.advance()
    assert rig.mark.calls==[1,0] and rig.controller.gripper.state==0
    rig.controller.stop('test_end'); rig.settle()


@pytest.mark.parametrize('kind',['failure','timeout','busy'])
def test_gripper_async_errors_and_busy(rig,kind):
    rig.start()
    if kind=='failure': rig.mark.fail=True
    else: rig.mark.delay_ns=(1500 if kind=='timeout' else 200)*MS
    for slot in range(1,4):
        try: rig.controller.consume(rig.action(0,.9,slot))
        except SafetyError: break
        rig.advance()
    assert rig.mark.calls==[1]
    if kind=='busy':
        for slot in range(4,7):
            rig.controller.consume(rig.action(0,.1,slot)); rig.advance()
        assert rig.mark.calls==[1]
        rig.controller.stop('test'); rig.settle()
    else:
        if kind=='timeout': rig.advance(1100)
        rig.controller.poll(); rig.settle()
        assert rig.controller.fault_latched and rig.controller.gripper.uncertain
        assert rig.controller.gripper.state is None and rig.lease.quarantined


def test_unknown_initial_gripper_and_qp_conflict(rig):
    rig.mark.state=None
    with pytest.raises(SafetyError,match='unknown_initial'): rig.connect()
    assert rig.indy.mode_requests==0
    rig.mark.state=0; rig.indy.qp_inhibited=None
    with pytest.raises(SafetyError,match='authority'): rig.connect()
    assert rig.indy.mode_requests==0 and rig.indy.stop_requests==0


def test_local_lease_cannot_replace_active_or_unresolved_owner(tmp_path):
    a,b=CommandLease(tmp_path/'lease.json'),CommandLease(tmp_path/'lease.json')
    a.acquire('qp-owner')
    with pytest.raises((BlockingIOError,SafetyError)): b.acquire('act-owner')
    a.quarantine(); a.stream.close(); a.stream=None
    with pytest.raises(SafetyError,match='unresolved'): b.acquire('act-owner')


@pytest.mark.parametrize('kind',['user','emergency','inference','watchdog'])
def test_stop_paths_no_home_no_auto_rearm(rig,kind):
    rig.start(); rig.controller.consume(rig.action())
    if kind=='user': rig.controller.stop('user_abort')
    elif kind=='emergency': rig.controller.emergency_stop()
    elif kind=='inference': rig.controller.fault('inference_timeout')
    else: rig.clock.advance(150); rig.controller.watchdog_check()
    rig.settle(); snapshot=rig.controller.snapshot()
    assert snapshot['stop_confirmed'] and rig.indy.stop_requests==1
    assert snapshot['state']==('STOPPED' if kind=='user' else 'FAULT')
    assert not snapshot['policy_normal_end']
    assert snapshot['emergency_requested']==(kind=='emergency')
    assert snapshot['user_aborted']==(kind=='user')
    with pytest.raises(SafetyError): rig.controller.consume(rig.action())


def test_unconfirmed_stop_quarantines_authority(rig):
    rig.start(); rig.indy.stop_error=OSError('stop delivery unknown')
    rig.controller.stop('user_abort'); rig.settle()
    assert rig.controller.state=='FAULT' and not rig.controller.stop_confirmed
    assert rig.lease.quarantined


def test_sessions_do_not_share_actions_or_approval(rig):
    rig.start(); old=rig.action(); rig.controller.stop('session_end'); rig.settle()
    rig.session='session-b'; rig.start()
    assert rig.controller.guard.previous_target is None and rig.controller.submitted==0
    with pytest.raises(SafetyError): rig.controller.consume(old)
    assert len(rig.indy.commands)==0
    rig.settle()


def test_real_adapters_inactive_by_default_and_real_cli_locked(rig):
    class NoOperations:
        def __getattr__(self,key): raise AssertionError('Unexpected ROS operation')
    adapter=RosIndy7Adapter(NoOperations(),None)
    with pytest.raises(SafetyError): adapter.activate(threading.Event(),rig.config)
    with pytest.raises(SafetyError): adapter.send_absolute(np.zeros(6))
    result=subprocess.run(['./scripts/bin/act','execute','--mode','real','--checkpoint','not-used'],capture_output=True,text=True)
    assert result.returncode==2 and 'locked' in result.stderr
    result=subprocess.run(['./scripts/bin/act','execute','--mode','mock','--checkpoint','not-used'],capture_output=True,text=True)
    assert result.returncode==2 and '--approve-mock' in result.stderr


def test_watchdog_revoke_during_blocked_dispatch(tmp_path):
    clock=time.monotonic_ns
    config=SafetyConfig.load(ROOT/'ai/execute/config/mock.json')
    indy,mark=MockIndy7Adapter(clock),MockMark7Transport(clock)
    c=ExecutionController(indy,mark,CommandLease(tmp_path/'lease'),config,lambda *args,**kwargs:None,clock=clock)
    c.connect('s','m','checkpoint',ObservationHealth(clock(),True,{}))
    c.arm(UserApproval('s','m','explicit-op','mock',clock(),clock()+10_000*MS));c.poll();c.begin()
    entered,release=threading.Event(),threading.Event()
    original=indy.send_absolute
    def blocked(target, **kwargs):
        entered.set();release.wait(1);return original(target, **kwargs)
    indy.send_absolute=blocked
    failures=[]
    now=clock()
    record={'session_id':'s','checkpoint_id':'m','request_id':'r','action':[0]*7,'action_index':0,
            'schedule':{'slot':1,'deadline_monotonic_ns':now,'missed_deadlines':0,'missed_deadlines_total':0}}
    def send():
        try:c.consume(record)
        except Exception as exc:failures.append(str(exc))
    c.start_watchdog();thread=threading.Thread(target=send,daemon=True);thread.start()
    try:
        assert entered.wait(.5)
        deadline=time.monotonic()+.5
        while (not c.revoked.is_set() or indy.stop_requests==0) and time.monotonic()<deadline:time.sleep(.002)
        assert c.revoked.is_set() and indy.stop_requests==1
    finally:
        release.set();thread.join(1);c.poll();c.close_watchdog()
    assert failures and c.fault_latched and c.state=='FAULT'


@pytest.mark.parametrize('kind',['velocity','acceleration'])
def test_measured_motion_limits(rig,kind):
    if kind=='acceleration':rig.controller.config=replace(rig.config,max_acceleration_rad_s2=[1]*6)
    rig.start();rig.controller.consume(rig.action(0))
    rig.indy.velocity[:]=5 if kind=='velocity' else .2
    rig.advance();rig.controller.poll()
    assert rig.controller.fault_latched and f'measured_joint_{kind}' in rig.controller.reason
    rig.settle()


def test_ros_wire_adapters_with_fake_messages_only(rig,monkeypatch):
    import sys,types,ast,math
    modules={name:types.ModuleType(name) for name in ('trajectory_msgs','trajectory_msgs.msg','indy_interfaces','indy_interfaces.srv','std_srvs','std_srvs.srv','std_msgs','std_msgs.msg')}
    for name,module in modules.items():monkeypatch.setitem(sys.modules,name,module)
    class Trajectory:
        def __init__(self):self.header=M(stamp=None);self.points=[]
    class Point:
        def __init__(self):self.positions=[];self.time_from_start=M(sec=0,nanosec=0)
    class Request:
        def __init__(self):self.data=None
    modules['trajectory_msgs.msg'].JointTrajectory=Trajectory
    modules['trajectory_msgs.msg'].JointTrajectoryPoint=Point
    modules['indy_interfaces.srv'].IndyService=M(Request=Request)
    modules['std_srvs.srv'].Trigger=M(Request=Request)
    modules['std_msgs.msg'].Float32=lambda:M(data=None)
    calls=[]
    class Node:
        def get_node_names(self):return ['indy_driver','act_unit_test']
        def get_name(self):return 'act_unit_test'
        def get_namespace(self):return '/'
        def get_clock(self):return M(now=lambda:M(to_msg=lambda:None))
        def get_publishers_info_by_topic(self,topic):return [M(node_name='act_unit_test',node_namespace='/')]
        def create_publisher(self,kind,topic,depth):return M(publish=lambda msg:calls.append((topic,msg)))
        def create_client(self,kind,service):
            def request(msg):
                calls.append((service,msg.data))
                if service=='/indy_srv':rig.indy.mode='joint_absolute' if msg.data==6 else 'idle'
                future=Future();future.set_result(M(success=True));return future
            return M(service_is_ready=lambda:True,call_async=request)
    config=replace(rig.config,reviewed_for_real=True,profile='fake_ros_transport_test_only',review_record='UNIT TEST, NOT HARDWARE')
    activation=RealActivation(True,'test-operator','fake-node-only',rig.clock()+10000*MS)
    node=Node();adapter=RosIndy7Adapter(node,rig.indy.feedback,activation,clock=rig.clock)
    permit=threading.Event()
    adapter.activate(permit,config)
    assert adapter.request_absolute_mode().result().success
    adapter.send_absolute(np.full(6,.1), expires_ns=rig.clock()+20*MS)
    topic,message=next((topic,value) for topic,value in calls if topic.startswith('/joint_trajectory'))
    assert message.points[0].positions==[.1]*6 and message.joint_names==[f'joint{i}' for i in range(6)]
    # Run only the official pure unit-conversion function, never import the driver/node.
    driver=ROOT/'ros2_ws/src/external/indy-ros2/indy_driver/indy_driver/indy_driver.py'
    function=next(n for n in ast.parse(driver.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='rads2degs')
    namespace={'math':math}
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(driver),'exec'),namespace)
    assert namespace['rads2degs'](message.points[0].positions)==pytest.approx([math.degrees(.1)]*6)
    grip=RosMark7Transport(node,lambda:0,rig.indy.feedback,activation,clock=rig.clock)
    grip.activate(permit,config)
    assert grip.request(1).result().success
    grip.confirm_state(1)
    assert ('/gripper/grasp',None) in calls
    assert any(topic=='/capstone/gripper_state' and value.data==1 for topic,value in calls)
    original_graph = node.get_publishers_info_by_topic
    def slow_graph(topic):
        rig.clock.advance(21)
        return original_graph(topic)
    node.get_publishers_info_by_topic = slow_graph
    before = len(calls)
    with pytest.raises(SafetyError, match='adapter_action_deadline_expired'):
        adapter.send_absolute(np.full(6,.1), expires_ns=rig.clock()+20*MS)
    assert len(calls) == before  # Expired during preparation: never publish.
    node.get_publishers_info_by_topic = original_graph
    permit.set()
    with pytest.raises(SafetyError):adapter.send_absolute(np.zeros(6))
    assert adapter.request_stop().result().success and ('/indy_srv',8) in calls
    node.get_node_names=lambda:['qp_teleop','indy_driver']
    with pytest.raises(SafetyError,match='qp_node'):adapter._graph()


# Reuse the actual, isolated Synthetic ACT checkpoint fixture from inference tests.
from ai.serve.test.test_inference import checkpoint,real_base


@pytest.fixture(scope='module')
def execution_checkpoint(real_base,tmp_path_factory):
    """Constant safe labels for wiring tests; not a trained basket-task policy."""
    import shutil
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from ai.dataset_contract import dataset_features,CAMERA_KEYS,IMAGE_SHAPE,TIMING_DIRECTORY
    from ai.train import run_train
    from ai.train.test.test_training_pipeline import arguments
    root=tmp_path_factory.mktemp('execution-model')
    dataset=root/'synthetic-hold'
    ds=LeRobotDataset.create(repo_id='local/indy7_act',root=dataset,robot_type='indy7',fps=20,
         features=dataset_features(),use_videos=True,image_writer_threads=0,vcodec='h264')
    for episode in range(2):
        for i in range(3):
            ds.add_frame({**{key:np.full(IMAGE_SHAPE,i*30+episode,np.uint8) for key in CAMERA_KEYS},
                 'observation.state':np.array([i*.005]*6+[i%2],np.float32),
                 'action':np.array([.01]*6+[1],np.float32),'task':'synthetic hold and grasp'})
        ds.save_episode(parallel_encoding=False)
    ds.finalize()
    shutil.copytree(real_base/TIMING_DIRECTORY,dataset/TIMING_DIRECTORY)
    manifest=run_train.main([*arguments(dataset,root/'run'),'--chunk-size','12','--n-action-steps','4'])
    return Path(manifest['checkpoint'])


def test_synthetic_checkpoint_to_mock_devices(execution_checkpoint,tmp_path):
    checkpoint=execution_checkpoint
    from ai.serve.lerobot_act_backend import LeRobotActBackend
    diagnostic=LeRobotActBackend(checkpoint,device='cpu')
    step=diagnostic.postprocessor.steps[0]
    import torch
    assert diagnostic.processor_verification['normalization_state_verified']
    assert step.state_dict()['action.q10'].tolist()==pytest.approx([.01]*6+[1])
    known=diagnostic.postprocessor(torch.zeros(1,1,7))[0,0]
    assert known.tolist()==pytest.approx([.01]*6+[1],abs=1e-6)
    profile=json.loads((ROOT/'ai/execute/config/mock.json').read_text())
    config=tmp_path/'test-config.json';config.write_text(json.dumps(profile))
    output=tmp_path/'mock-run'
    result=subprocess.run(['./scripts/bin/act','execute','--mode','mock','--checkpoint',str(checkpoint),'--config',str(config),
            '--approve-mock','offline-test-explicit-arm','--device','cpu','--duration','2.5','--output-dir',str(output)],
            capture_output=True,text=True,timeout=90)
    report=json.loads((output/'result.json').read_text())
    if result.returncode:
        rows=[json.loads(line) for line in (output/'client/events.jsonl').read_text().splitlines()]
        rejected=[row for row in rows if row['event']=='execution_action_rejected']
        pytest.fail(json.dumps(rejected)+result.stdout+result.stderr+(output/'client.log').read_text()[-3000:])
    execution=report['client']['execution']
    assert execution['state']=='STOPPED' and execution['stop_confirmed'] and not execution['safety_fault']
    assert execution['submitted_actions']>=20 and execution['executed_actions']==execution['submitted_actions']
    assert execution['gripper_requests']>=1 and execution['gripper_command_state']==1
    assert not execution['policy_normal_end'] and report['robot_commands_sent']==0
    events=[json.loads(line) for line in (output/'client/events.jsonl').read_text().splitlines()]
    states=[row['state'] for row in events if row['event']=='execution_state']
    assert states==['READY','ARMED','RUNNING','STOPPING','STOPPED']
    assert any(row['event']=='gripper_command_confirmed' for row in events)
    assert any(row['event']=='execution_joint_command' for row in events)
    assert (output/'client/mock_command_lease.json').is_file()
    assert json.loads((output/'client/mock_command_lease.json').read_text())['status']=='released'
    print('MOCK_E2E '+json.dumps(report['client']),flush=True)
    import os,shutil
    if os.environ.get('ACT_TEST_REPORT_DIR'):
        destination=Path(os.environ['ACT_TEST_REPORT_DIR'])
        shutil.copyfile(output/'client/events.jsonl',destination/'mock_execution_events.jsonl')
        (destination/'mock_execution_result.json').write_text(json.dumps(report,indent=2))


def test_missing_stats_checkpoint_is_rejected(execution_checkpoint,tmp_path):
    import shutil
    from ai.train.checkpoint import verify_checkpoint
    root=tmp_path/'without-stats';shutil.copytree(execution_checkpoint,root)
    path=root/'policy_postprocessor.json'
    config=json.loads(path.read_text())
    for step in config['steps']:step.pop('state_file',None)
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='normalization statistics'):verify_checkpoint(root)


def test_logging_and_cancel_failure_do_not_suppress_stop(rig):
    rig.start()
    def broken(*args,**kwargs):raise OSError('disk failure')
    rig.controller._log_sink=broken
    rig.controller.cancel_scheduler=broken
    rig.controller.consume(rig.action(0))
    rig.settle()
    assert rig.controller.fault_latched and rig.indy.stop_requests==1
    assert rig.controller.logging_error and rig.controller.cancel_error


def test_inference_watchdog_and_expired_arm_approval(rig):
    rig.start();rig.clock.advance(800)
    rig.controller.heartbeat(inference=False)
    rig.controller.watchdog_check();rig.settle()
    assert rig.controller.state=='FAULT' and 'inference_watchdog' in rig.controller.reason


def test_late_then_on_time_commands_use_actual_interval(tmp_path):
    rig=Rig(tmp_path,max_velocity_rad_s=[.31]*6)
    rig.start();first=rig.action(.01)
    rig.clock.advance(15);rig.controller.consume(first)
    rig.advance(35)
    with pytest.raises(SafetyError):rig.controller.consume(rig.action(.025,slot=2))
    assert 'command_velocity' in rig.controller.reason and len(rig.indy.commands)==1
    rig.settle()


def test_pending_send_is_independently_watched(rig):
    rig.start();rig.indy.command_delay_ns=100*MS
    rig.controller.consume(rig.action())
    rig.clock.advance(60);rig.controller.heartbeat(inference=True)
    rig.controller.watchdog_check();rig.settle()
    assert rig.controller.fault_latched and 'command_watchdog' in rig.controller.reason


def test_explicit_mock_initial_pose_is_no_motion_and_cannot_escape_limits(tmp_path):
    from ai.execute.mock_session import create_mock_session,validate_mock_initial_joints
    from ai.observation_timing import SyncOptions
    config=SafetyConfig.load(ROOT/'ai/execute/config/mock.json')
    source,session=create_mock_session(SyncOptions(),lambda *a,**k:None,{},tmp_path,
                        ROOT/'ai/execute/config/mock.json','operator',mock_initial_joints=[.1]*6)
    assert session.controller.indy.joints.tolist()==[.1]*6
    assert session.controller.indy.commands==[] and session.controller.state=='DISCONNECTED'
    for value in ([float('nan')]*6,[10]*6,[0]*5):
        with pytest.raises(SafetyError):validate_mock_initial_joints(value,config)
    source.close()


def test_action_validation_clock_is_after_feedback_poll(rig):
    rig.start()
    rig.advance(50)
    original_poll = rig.controller.poll
    def poll_with_elapsed_processing():
        rig.clock.advance(1)
        original_poll()
    rig.controller.poll = poll_with_elapsed_processing
    rig.controller.consume(rig.action())
    assert rig.controller.state == 'RUNNING' and len(rig.indy.commands) == 1
    rig.controller.stop('test_complete')
    rig.settle()
    assert rig.controller.stop_confirmed


def test_watchdog_settles_stop_without_main_loop_poll(rig):
    rig.start()
    rig.controller.stop('user_abort')
    rig.controller.watchdog_check()
    assert rig.controller.state == 'STOPPED' and rig.controller.stop_confirmed


def test_watchdog_quarantines_unconfirmed_stop_without_main_loop_poll(rig):
    rig.start()
    rig.indy.stop_delay_ns = 2000*MS
    rig.controller.stop('user_abort')
    rig.clock.advance(1100)
    rig.controller.watchdog_check()
    assert rig.controller.state == 'FAULT' and rig.lease.quarantined
