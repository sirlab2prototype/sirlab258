"""Real-path tests with fake ROS/SDK, virtual devices and tiny Mock Actions only."""
from dataclasses import replace
from pathlib import Path
import importlib.util
import json
import sys
import threading
import time
from types import SimpleNamespace as M, ModuleType
import numpy as np
import pytest
from ai.execute.safety import SafetyConfig,SafetyError,ObservationHealth
from ai.execute.real_profile import HardwareProfile,ExternalInterlock
from ai.execute.real_driver import FeedbackCache,ack
from ai.execute.test.test_execution import Rig,ROOT,MS
from ai.execute.test.test_controller_safety import native_status,configuration


def profile(tmp_path):
    return HardwareProfile({'reviewed_for_real':True,'review_record':'FAKE ONLY','robot_ip':'192.0.2.1',
      'robot_serial':'FAKE','controller_version':'FAKE-FW','sdk_version':'FAKE-SDK','joint_unit':'deg',
      'initial_joints_rad':[0]*6,'initial_pose_is_project_home':True,'initial_tolerance_rad':.01,
      'sdk_limit_units':{k:'deg' for k in ('get_joint_limit_config','get_original_joint_limit_config','get_safety_limits')},
      'sdk_limit_units_review_record':'FAKE verified units',
      'sdk_vel_ratio':.8,'sdk_acc_ratio':7.,'rpc_timeout_ms':20,'stop_stable_ms':100,
      'stop_validation_record':'FAKE STOP','external_exclusion_record':'FAKE ACL',
      'interlock_file':str(tmp_path/'interlock.json'),'interlock_issuer':'FAKE-PLC','interlock_max_age_ms':100})


def test_real_profiles_fail_closed(tmp_path):
    cfg=SafetyConfig.load(ROOT/'ai/execute/config/mock.json');p=profile(tmp_path)
    with pytest.raises(SafetyError):p.require(cfg)
    cfg=replace(cfg,reviewed_for_real=True,review_record='FAKE')
    p.require(cfg)
    for key in ['stop_validation_record','external_exclusion_record','initial_joints_rad','rpc_timeout_ms','sdk_limit_units']:
        v=dict(p.values);v[key]=None
        with pytest.raises((SafetyError,TypeError)):HardwareProfile(v).require(cfg)
    with pytest.raises(ValueError):SafetyConfig.load(ROOT/'ai/execute/config/indy7_review_required.json')


def test_external_interlock_requires_fresh_session_bound_positive_evidence(tmp_path):
    p=profile(tmp_path);clock=lambda:10**9
    inter=ExternalInterlock(p,'nonce',clock)
    with pytest.raises(SafetyError):inter.read()
    row={'version':1,'run_nonce':'nonce','robot_serial':'FAKE','issuer':'FAKE-PLC',
         'observed_monotonic_ns':clock(),'estop_clear':True,'all_other_writers_blocked':True,'workspace_clear':True}
    path=Path(p.values['interlock_file']);path.write_text(json.dumps(row));assert inter.read()==row
    for key,value in [('run_nonce','old'),('observed_monotonic_ns',0),('estop_clear',False),('all_other_writers_blocked',None)]:
        path.write_text(json.dumps({**row,key:value}))
        with pytest.raises(SafetyError):inter.read()


def test_cached_SDK_feedback_not_assumed_ready(tmp_path):
    p=profile(tmp_path);proof=M(read=lambda:{'estop_clear':True,'all_other_writers_blocked':True})
    cache=FeedbackCache(p,proof)
    with pytest.raises(SafetyError):cache.get(100)
    data={'q':[10]*6,'qdot':[1]*6,'op_state':17,'is_robot_connected':True,'sim_mode':False}
    servo={'status_codes':['0x237']*6,'servo_actives':[True]*6,'brake_actives':[False]*6}
    cache.update(data,servo,{'is_in_motion':True,'is_stopping':False,'is_pausing':False,'motion_queue_size':0},{'method':10},100,110,native_status())
    fb=cache.get(110);assert fb.mode=='joint_absolute';assert fb.joints==pytest.approx(np.deg2rad([10]*6))
    assert fb.velocity==pytest.approx(np.deg2rad([1]*6));assert not fb.fault
    cache.update(data,{**servo,'status_codes':['0x238']*6},{'is_in_motion':True,'is_stopping':False,'is_pausing':False,'motion_queue_size':0},{'method':11},100,110,native_status())
    fb=cache.get(110);assert fb.mode=='unknown' and fb.fault
    cache.fail('RPC timeout')
    with pytest.raises(SafetyError,match='RPC timeout'):cache.get(110)


def writer_module():
    path=ROOT/'ros2_ws/src/capstone_teleop/capstone_teleop/writer_lock.py'
    spec=importlib.util.spec_from_file_location('test_writer_lock',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_QP_ACT_process_exclusion_and_quarantine(tmp_path):
    cls=writer_module().WriterLock;p=tmp_path/'lock'
    qp=cls('QP',p);act=cls('ACT',p);qp.acquire()
    with pytest.raises(BlockingIOError):act.acquire()
    qp.close();act.acquire();act.close(confirmed=False)
    with pytest.raises(RuntimeError,match='Unresolved'):cls('new-QP',p).acquire()


def test_lease_loss_and_observation_expiry_watchdog_stop(tmp_path):
    for scenario in ('lease','health'):
        rig=Rig(tmp_path/scenario);rig.start()
        if scenario=='lease':rig.lease.path.write_text(json.dumps({'owner':'foreign','status':'held'}))
        else:rig.controller.health=ObservationHealth(rig.clock()-1000*MS,True,{})
        rig.controller.watchdog_check()
        assert rig.controller.revoked.is_set() and rig.indy.stop_requests==1
        assert not rig.indy.commands
        rig.settle();rig.controller.close_watchdog()


def test_real_stop_needs_post_request_fresh_stable_feedback(tmp_path):
    rig=Rig(tmp_path);rig.start();rig.controller.is_mock=False;rig.indy.stop_stable_ns=100*MS
    rig.controller.stop('test')
    rig.controller.poll();assert not rig.controller.stop_confirmed
    rig.advance(50);rig.controller.poll();assert not rig.controller.stop_confirmed
    rig.advance(100);rig.controller.poll();assert rig.controller.stop_confirmed


def test_real_CLI_blocks_before_inference_or_ROS(tmp_path,monkeypatch):
    from ai.execute import main
    monkeypatch.setattr(main,'inference_main',lambda *a,**k:pytest.fail('must not launch'))
    with pytest.raises(SystemExit):main.main(['--mode','real','--checkpoint','not-used'])
    with pytest.raises(SystemExit):main.main(['--mode','real','--checkpoint','not-used','--approve-real','op',
        '--hardware-profile',str(ROOT/'ai/execute/config/real_hardware_review_required.json')])


def test_real_CLI_wires_explicit_profiles_and_stop_without_hardware(tmp_path,monkeypatch):
    from ai.execute import main
    monkeypatch.setattr(main,'PROJECT',tmp_path)
    c=replace(SafetyConfig.load(ROOT/'ai/execute/config/mock.json'),reviewed_for_real=True,review_record='FAKE')
    from dataclasses import asdict
    cp=tmp_path/'config.json';cp.write_text(json.dumps(asdict(c)))
    hp=tmp_path/'hardware.json';hp.write_text(json.dumps(profile(tmp_path).values))
    called=[];monkeypatch.setattr(main,'inference_main',lambda args,**kw:called.append((args,kw)) or 0)
    assert main.main(['--mode','real','--checkpoint','test-model','--approve-real','FAKE','--config',str(cp),
                     '--hardware-profile',str(hp),'--duration','1'])==0
    assert called[0][1]['_real_options']['approval']=='FAKE'
    client=tmp_path/'run/client';client.mkdir(parents=True);(client/'real_activation.json').write_text('{}')
    assert main.main(['--stop-run',str(client.parent)])==0 and (client/'STOP').exists()


@pytest.mark.parametrize('sdk_reply_ok',[True,False])
def test_guarded_official_Driver_denies_foreign_command_and_uses_absolute_degrees(tmp_path,monkeypatch,sdk_reply_ok):
    import ai.execute.real_driver as rd
    calls=[]
    class FakeSDK:
        def __init__(self,ip):
            calls.append(('connect_fake',ip))
            for name in ('boot','control','device','config','rtde','cri'):
                setattr(self,name,M());setattr(self,name+'_channel',M(close=lambda:None))
        def get_device_info(self):return {'robot_serial':'FAKE'}
        def get_control_info(self):return {'control_version':'FAKE-FW','robot_model':'Indy7'}
        def get_joint_limit_config(self):return configuration()['get_joint_limit_config']
        def get_original_joint_limit_config(self):return configuration()['get_original_joint_limit_config']
        def get_safety_limits(self):return configuration()['get_safety_limits']
        def get_safety_stop_config(self):return configuration()['get_safety_stop_config']
        def get_coll_policy(self):return configuration()['get_coll_policy']
        def start_teleop(self,method):calls.append(('mode',method));return {'response':{'code':'0'}}
        def movetelej_abs(self,**kwargs):calls.append(('absolute',kwargs));return {'response':{'code':'0' if sdk_reply_ok else '1'}}
        def stop_motion(self,stop_category=2):calls.append(('stop_motion',stop_category));return {'response':{'code':'0'}}
        def stop_teleop(self):calls.append(('stop_teleop',));return {'response':{'code':'0'}}
    class Base:
        def __init__(self):self.timer=M(cancel=lambda:None);self.indy=None
        def create_service(self,*args):return M()
        def destroy_node(self):pass
    monkeypatch.setattr(rd,'_official_driver_module',lambda:M(IndyROSConnector=Base))
    monkeypatch.setattr(rd.importlib.metadata,'version',lambda name:'FAKE-SDK')
    modules={'neuromeka':M(IndyDCP3=FakeSDK),'capstone_teleop.writer_lock':M(WriterLock=lambda owner:writer_module().WriterLock(owner,tmp_path/'lock')),
             'indy_interfaces.srv':M(IndyService=M()),'sensor_msgs.msg':M(JointState=M),
             'rclpy.action':M(GoalResponse=M(REJECT='reject'),CancelResponse=M(REJECT='reject'))}
    for key,value in modules.items():monkeypatch.setitem(sys.modules,key,value)
    # No monitor/ROS threads in this unit; invoke the actual command callbacks below.
    monkeypatch.setattr(threading.Thread,'start',lambda self:None)
    monkeypatch.setattr(threading.Thread,'join',lambda *args:None)
    cfg=replace(SafetyConfig.load(ROOT/'ai/execute/config/mock.json'),reviewed_for_real=True,review_record='FAKE',
                joint_min_rad=[-2.]*6,joint_max_rad=[2.]*6,max_velocity_rad_s=[1.]*6)
    p=profile(tmp_path);g=rd.create_driver(p,cfg,M(read=lambda:{}),'nonce',tmp_path/'STOP',lambda *a,**k:None)
    fb=Rig(tmp_path/'fb').indy.feedback(time.monotonic_ns());fb.mode='joint_absolute';fb.controller_op_state=17;fb.teleop_method=10
    fb.joint_received_ns=fb.status_received_ns=time.monotonic_ns();g.cache.get=lambda now:fb
    msg=M(header=M(frame_id=f'nonce:{time.monotonic_ns()+20*10**6}'),joint_names=[f'joint{i}' for i in range(6)],points=[M(positions=[.01]*6)])
    g.joint_trajectory_callback(msg)
    item=next(v for name,*v in calls if name=='absolute')[0]
    assert item['jpos']==pytest.approx(np.rad2deg([.01]*6));assert item['vel_ratio']==.8
    assert g.command_counts()=={'sdk_command_attempts':1,'sdk_acknowledged_actions':int(sdk_reply_ok)}
    assert g.tripped.is_set() is (not sdk_reply_ok)
    before=sum(c[0]=='absolute' for c in calls)
    msg.header.frame_id='foreign:99999999999999999';g.joint_trajectory_callback(msg)
    assert g.tripped.is_set() and sum(c[0]=='absolute' for c in calls)==before
    assert g.command_counts()=={'sdk_command_attempts':1,'sdk_acknowledged_actions':int(sdk_reply_ok)}
    g.stop_sdk();assert [c[0] for c in calls[-2:]]==['stop_motion','stop_teleop']
    response=g.indy_srv_callback(M(data=2),M());assert response.success is False
    g.close_real(True)


@pytest.mark.parametrize("scenario",["normal","server_disconnect","operator_stop"])
def test_Mock_ACT_chunk_scheduler_RealSession_controller_feedback_loop(tmp_path,monkeypatch,scenario):
    # Exercise the new Real bridge with in-memory robot/ROS stand-ins only.
    # Deterministic 20Hz time: host scheduling stalls must not make this software test flaky.
    virtual=[1_000_000_000_000]
    monkeypatch.setattr(time,'monotonic_ns',lambda:virtual[0])
    monkeypatch.setattr(time,'monotonic',lambda:virtual[0]/1e9)
    monkeypatch.setattr(time,'time_ns',lambda:1_790_000_000_000_000_000+virtual[0])
    monkeypatch.setattr(time,'sleep',lambda seconds:virtual.__setitem__(0,virtual[0]+round(seconds*1e9)))
    from ai.execute.adapters import MockIndy7Adapter,MockMark7Transport
    from ai.execute.authority import CommandLease
    from ai.execute.controller import ExecutionController
    from ai.execute.real_session import RealSession
    from ai.serve.observations import RosSource,HistorySource,SyntheticSource
    from ai.serve import dry_run
    class FakeRealRobot(MockIndy7Adapter):
        is_mock=False
        stop_stable_ns=100*MS
    class FakeRealMark(MockMark7Transport):is_mock=False
    robot=FakeRealRobot(time.monotonic_ns);mark=FakeRealMark(time.monotonic_ns,0)
    class FakeROS(RosSource):
        def __init__(self):
            HistorySource.__init__(self);self.operator_gripper=None
            self.origin=self.timing();self.generated={'camera_317222074298':0,'camera_844212071939':0,'state':0}
            self.feedback_samples=set();self.max_joint_change_rad=0.
            self.append('gripper',0.,None)
        def append(self,name,value,stamp,timing=None,message_format=None):
            if name=='state':
                value=robot.feedback(time.monotonic_ns()).joints.astype(np.float32)
                self.feedback_samples.add(time.monotonic_ns());self.max_joint_change_rad=float(np.max(abs(value)))
            HistorySource.append(self,name,value,stamp,timing,message_format)
        update=SyntheticSource.update
        def close(self):pass
    source=FakeROS();rows=[]
    log=lambda event,**kw:rows.append({'event':event,**kw})
    cfg=replace(SafetyConfig.load(ROOT/'ai/execute/config/mock.json'),reviewed_for_real=True,review_record='FAKE ONLY')
    c=ExecutionController(robot,mark,CommandLease(tmp_path/'lease'),cfg,log,clock=time.monotonic_ns)
    # RealSession API uses an activation expiry; this fake never creates a physical transport.
    robot.activation=M(expires_ns=time.monotonic_ns()+10**10)
    session=RealSession(source,c,profile(tmp_path),'FAKE','nonce',tmp_path)
    model={'checkpoint_id':'fake-model','checkpoint':'in-memory Mock Action source','chunk_size':16,'n_action_steps':4,'fps':20,'device':'fake'}
    observations=[]
    class RPC:
        def __init__(self,*args):self.pending=None
        def control(self,request):return ({**model,'session_generated_chunks':3},None,{})
        def submit(self,request,observation,chunk_size):
            current=observation['observation.state'].copy();observations.append(current.copy())
            chunk=np.tile(current,(chunk_size,1));chunk[:,:6]+=.0005
            self.pending=({**model,'session_id':request['session_id'],'request_id':request['request_id'],
                'observation_monotonic_ns':request['observation_monotonic_ns'],'inference_ns':1000},chunk)
        def poll(self):
            if scenario=='server_disconnect' and len(robot.commands)>=2:raise ConnectionError('fake server disconnected')
            if scenario=='operator_stop' and len(robot.commands)>=2:(tmp_path/'STOP').touch()
            if self.pending is None:return None
            reply,chunk=self.pending;self.pending=None
            return reply,chunk,{'client_received_monotonic_ns':time.monotonic_ns()}
        def close(self):pass
    monkeypatch.setattr(dry_run,'RPCClient',RPC)
    result=dry_run.run_session(source,'in-memory',model,log,duration=.8,_real_execution=session)
    assert result['status']==('complete' if scenario=='normal' else 'failed' if scenario=='server_disconnect' else 'stopped'),json.dumps(result,default=str)
    assert result['execution_mode']=='real' and result['controller_submitted_actions']>=2
    # This fake Real bridge has no SDK ACK provider. Never label Mock adapter
    # submissions as confirmed real SDK commands.
    assert result['robot_commands_sent'] is None and result['sdk_acknowledged_actions'] is None
    assert result['execution']['stop_confirmed'] and not result['execution']['physical_execution_verified']
    assert len(observations)>=2
    if scenario=='normal':assert np.max(observations[-1][:6])>0
    assert not robot.home_commands


def test_optional_Mark7_handoff_reports_success_only(monkeypatch):
    import ast
    path=ROOT/'ros2_ws/src/capstone_mark7/capstone_mark7/mark7_node.py'
    tree=ast.parse(path.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Mark7Node')
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('publish_command_state','open_callback','grasp_callback')]
    namespace={};exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Fake',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])),str(path),'exec'),namespace)
    node=namespace['Fake']();sent=[]
    node.send_preset=lambda name:None
    node.get_logger=lambda:M(error=lambda *a:None)
    node.command_state_pub=M(publish=lambda msg:sent.append(msg.data));node.command_state_type=lambda:M()
    assert node.open_callback(None,M()).success and sent==[0.]
    assert node.grasp_callback(None,M()).success and sent==[0.,1.]
    def fail(name):raise OSError('fake serial failure')
    node.send_preset=fail
    assert node.open_callback(None,M()).success is False and sent==[0.,1.]


def test_quarantine_IO_failure_does_not_suppress_fault_state(tmp_path,monkeypatch):
    rig=Rig(tmp_path);rig.start();rig.indy.stop_error=OSError('fake transport loss')
    def full(status):raise OSError('fake disk full')
    monkeypatch.setattr(rig.lease,'_write',full)
    rig.controller.stop('user_abort');rig.settle()
    assert rig.controller.state=='FAULT' and rig.controller.revoked.is_set()
    assert rig.lease.quarantined and rig.lease.persistence_error and rig.lease.stream is not None
    rig.lease.stream.close()
