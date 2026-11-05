"""Explicit Real bridge to the existing observation/RPC/scheduler/controller loop."""
import json
from pathlib import Path
import threading
import time
import uuid
import numpy as np
from ai.execute.authority import CommandLease
from ai.execute.controller import ExecutionController, UserApproval
from ai.execute.ros_adapters import RosIndy7Adapter, RosMark7Transport, RealActivation
from ai.execute.real_profile import HardwareProfile, ExternalInterlock, make_interlock, precheck_interlock
from ai.execute.safety import SafetyConfig, SafetyError, ObservationHealth
from ai.serve.observations import RosSource


class RealSource(RosSource):
    def __init__(self,options,profile,safety,nonce,output,log,supervising_operator):
        self.driver=None;self.gateway_thread=None;self.gateway_executor=None;self.gateway_stop=threading.Event()
        self.controller=None;self.initial_feedback=None;self.feedback_samples=set();self.max_joint_change_rad=0.
        self.feedback_diagnostics=threading.local()
        super().__init__(options)  # Existing two RGB / 7D / bounded causal history. No assumed gripper.
        try:
            from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy
            from std_msgs.msg import Float32
            self.node.create_subscription(Float32,'/capstone/gripper_state',lambda msg:self.ingest('gripper',msg),
                QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))
            # Discovery is a negative screen; explicit authority mode is separately checked.
            time.sleep(.3)
            if any(n=='indy_driver' or 'qp_teleop' in n for n in self.node.get_node_names()):
                raise SafetyError('stop_legacy_Driver_QP_by_approved_operator_procedure_before_Real')
            from ai.execute.real_driver import create_driver
            interlock=make_interlock(profile,nonce,supervising_operator=supervising_operator)
            precheck_interlock(interlock)  # Before SDK construction; local lock is acquired by Driver.
            self.driver=create_driver(profile,safety,interlock,nonce,output/'STOP',log)
            from rclpy.executors import SingleThreadedExecutor
            self.gateway_executor=SingleThreadedExecutor()
            self.gateway_executor.add_node(self.driver)
            def spin():
                try:
                    while not self.gateway_stop.is_set():self.gateway_executor.spin_once(timeout_sec=.005)
                except Exception as exc:self.driver.trip('ROS_gateway_spin_failed:'+str(exc))
            self.gateway_thread=threading.Thread(target=spin,name='act-guarded-driver-ROS',daemon=True)
            self.gateway_thread.start()
        except BaseException:
            self.close();raise

    def feedback(self,now):
        timing={'entered_ns':time.monotonic_ns()}
        diagnostics=getattr(self,'feedback_diagnostics',None)
        if diagnostics is not None:diagnostics.last=timing
        state=self.driver.cache.get(now)
        timing['authority_and_cache_completed_ns']=time.monotonic_ns()
        names=self.node.get_node_names()
        if any('qp_teleop' in n for n in names) or names.count('indy_driver')!=1:raise SafetyError('Real_writer_graph_conflict')
        infos=self.node.get_publishers_info_by_topic('/joint_states')
        if len(infos)!=1 or infos[0].node_name!='indy_driver':raise SafetyError('foreign_joint_feedback_publisher')
        grippers=self.node.get_publishers_info_by_topic('/capstone/gripper_state')
        allowed={'mark7_node',self.node.get_name()}
        if not grippers or any(i.node_name not in allowed for i in grippers):raise SafetyError('gripper_publisher_not_confirmed')
        timing['publishers_completed_ns']=time.monotonic_ns()
        services=dict(self.node.get_service_names_and_types())
        timing['services_completed_ns']=time.monotonic_ns()
        for service in ('/capstone/real_gripper/open','/capstone/real_gripper/grasp'):
            # Public Mark7 endpoints must be remapped away before Real use.
            if service not in services:raise SafetyError('private_Mark7_service_missing')
        if any(n in services for n in ('/gripper/open','/gripper/grasp')):
            raise SafetyError('public_Mark7_services_must_be_remapped')
        if self.initial_feedback is None:self.initial_feedback=state.joints.copy()
        if len(self.feedback_samples)<100000:self.feedback_samples.add(state.joint_received_ns)
        self.max_joint_change_rad=max(self.max_joint_change_rad,float(np.max(abs(state.joints-self.initial_feedback))))
        return state

    def command_counts(self):
        return self.driver.command_counts()

    def confirmed_gripper(self):
        with self.lock:
            item=self.history.gripper_carry
            return None if item is None else item[0]

    def close(self):
        self.stopping.set();self.thread.join(1)
        self.gateway_stop.set()
        if self.gateway_thread:self.gateway_thread.join(.5)
        if self.gateway_executor:self.gateway_executor.shutdown(timeout_sec=.5)
        if self.driver:
            self.driver.close_real(bool(self.controller and self.controller.stop_confirmed))
            self.driver=None
        self.node.destroy_node();self.rclpy.try_shutdown()


class RealSession:
    is_mock=False
    def __init__(self,source,controller,profile,approval,nonce,output):
        self.source,self.controller,self.profile,self.approval=source,controller,profile,approval
        self.output,self.nonce=Path(output),nonce
        if profile.values.get('authority_mode')=='supervised_direct_link':controller.max_run_ns=5_000_000_000
        controller.target_update_mode=profile.values.get('target_update_mode','strict')
        self.prepared=False;self.last_status_ns=0
    @property
    def ready_for_actions(self):return self.controller.state=='RUNNING'
    def _command_counts(self):
        return (self.source.command_counts() if hasattr(self.source,'command_counts') else
                {'sdk_command_attempts':None,'sdk_acknowledged_actions':None})
    def observe(self,metadata):
        self.controller.observe(ObservationHealth.from_timing(metadata,self.source.options))
        state=self.controller.indy.feedback(time.monotonic_ns())
        self.controller.log('real_feedback',measured_joints_rad=state.joints.tolist(),
            measured_velocity_rad_s=state.velocity.tolist(),joint_available_monotonic_ns=state.joint_received_ns,
            status_available_monotonic_ns=state.status_received_ns,mode=state.mode,
            controller_op_state=state.controller_op_state,teleop_method=state.teleop_method,
            observation_reference_monotonic_ns=metadata['observation_monotonic_ns'])
    def prepare(self,session_id,model,cancel_scheduler):
        if self.prepared:return
        c=self.controller; c.cancel_scheduler=cancel_scheduler
        state=c.indy.feedback(time.monotonic_ns());v=self.profile.values
        if np.max(abs(state.joints-np.asarray(v['initial_joints_rad'])))>v['initial_tolerance_rad']:
            raise SafetyError('initial_pose_mismatch_no_automatic_Home')
        c.connect(session_id,model['checkpoint_id'],model['checkpoint'],c.health)
        now=time.monotonic_ns()
        c.arm(UserApproval(session_id,model['checkpoint_id'],self.approval,'real',now,c.indy.activation.expires_ns))
        c.start_watchdog();self.prepared=True
    def poll(self,inference=False):
        if not self.prepared:return
        c=self.controller
        if (self.output/'STOP').exists():
            c.stop('user_abort');raise KeyboardInterrupt
        c.heartbeat(inference=inference);c.poll()
        if c.state=='ARMED':
            c.begin()
            driver=getattr(self.source,'driver',None)
            if driver is not None and c.max_run_ns is not None:driver.execution_deadline_ns=c.started_ns+c.max_run_ns
        if c.state not in ('READY','ARMED','RUNNING'):raise SafetyError('real_execution_stopped:'+str(c.reason))
        now=time.monotonic_ns()
        if now-self.last_status_ns>=500_000_000:
            tmp=self.output/'execution_status.tmp'
            status={**c.snapshot(),**self._command_counts(),
                    'controller_submitted_actions':c.submitted,'run_nonce':self.nonce,'heartbeat_monotonic_ns':now}
            tmp.write_text(json.dumps(status,indent=2));tmp.replace(self.output/'execution_status.json')
            self.last_status_ns=now
    def consume(self,record):self.controller.consume(record)
    def finish(self,reason,fault=False):
        c=self.controller;c.stop(reason,fault=fault)
        deadline=time.monotonic()+c.config.stop_timeout_ms/1000+.2
        try:
            while c.state=='STOPPING' and time.monotonic()<deadline:
                c.poll();time.sleep(.005)
        finally:c.close_watchdog()
        if c.state=='STOPPING':c.lease.quarantine()
        result=c.snapshot()
        result.update(controller_submitted_actions=result['submitted_actions'],**self._command_counts())
        result.update(feedback_samples=len(self.source.feedback_samples),measured_joint_change_rad=self.source.max_joint_change_rad,
                      motion_evidence='SDK measured joint feedback; command ACK is not physical task success',
                      automatic_Home_executed=False)
        if c.last_feedback is not None:
            result['initial_pose_error_rad']=np.abs(c.last_feedback.joints-np.asarray(self.profile.values['initial_joints_rad'])).tolist()
            result['home_complete']=(bool(c.stop_confirmed and max(result['initial_pose_error_rad'])<=self.profile.values['initial_tolerance_rad'])
                                     if self.profile.values['initial_pose_is_project_home'] else None)
        try:
            temporary=self.output/'execution_status.tmp'
            temporary.write_text(json.dumps(result,indent=2));temporary.replace(self.output/'execution_status.json')
        except OSError as exc:c.log('execution_status_write_failed',reason=str(exc))
        c.log('execution_end',**result)
        return result


def create_real_session(options,log,model,output,config_path,hardware_path,approval_text):
    if not approval_text.strip():raise SafetyError('explicit_real_approval_required')
    safety=SafetyConfig.load(config_path);profile=HardwareProfile.load(hardware_path,safety)
    output=Path(output);nonce="s_"+uuid.uuid4().hex
    (output/'real_activation.json').write_text(json.dumps({'run_nonce':nonce,'robot_serial':profile.values['robot_serial'],
        'operator':approval_text,'checkpoint_id':model['checkpoint_id'],'interlock_file':profile.values.get('interlock_file'),
        'authority_mode':profile.values.get('authority_mode','external_interlock'),
        'workspace_basis':'operator-supervised controlled clear research area; no automatic presence detection'},indent=2))
    # External mode consumes live source evidence; supervised direct mode checks local kernel state.
    interlock=make_interlock(profile,nonce,supervising_operator=approval_text);deadline=time.monotonic()+safety.mode_timeout_ms/1000
    while True:
        try:precheck_interlock(interlock);break
        except SafetyError:
            if time.monotonic()>=deadline:raise
            time.sleep(.01)
    source=RealSource(options,profile,safety,nonce,output,log,approval_text)
    try:
        activation=RealActivation(True,approval_text,profile.values['stop_validation_record'],time.monotonic_ns()+3600*10**9)
        indy=RosIndy7Adapter(source.node,source.feedback,activation,service_name=f'/capstone/act/{nonce}/indy_srv',command_token=nonce)
        indy.stop_stable_ns=round(profile.values['stop_stable_ms']*1e6)
        mark=RosMark7Transport(source.node,source.confirmed_gripper,source.feedback,activation,
                              service_namespace='/capstone/real_gripper')
        lease=CommandLease(Path(__file__).resolve().parents[2]/'tmp/runtime/act_real_session_lease.json')
        c=ExecutionController(indy,mark,lease,safety,log);source.controller=c
        return source,RealSession(source,c,profile,approval_text,nonce,output)
    except BaseException:source.close();raise
