"""Real-only guarded extension of the unchanged official Indy ROS Driver.

Imports/create SDK/ROS only in create_driver(), after explicit Real profile gates.
Legacy Home/Zero/Recover/EtherCAT/action paths are denied. No startup movement.
"""
import importlib.util
import importlib.metadata
from pathlib import Path
import sys
import threading
import time
import numpy as np
from ai.execute.safety import RobotFeedback, SafetyError
from ai.execute.controller_safety import (read_configuration,validate_limits,read_status,status_health,servo_states,request_normal_stop,response_ok,feedback_transport_connected)

ROOT=Path(__file__).resolve().parents[2]


def ack(value):
    response_ok(value,required=True)


class DeadlineStub:
    def __init__(self,stub,seconds,*,group=None,log=None):
        self.stub,self.seconds,self.group,self.log=stub,seconds,group,log
    def __getattr__(self,name):
        def call(request):
            begin=time.monotonic_ns();status='OK'
            try:return getattr(self.stub,name)(request,timeout=self.seconds)
            except BaseException as exc:
                status=type(exc).__name__
                try:status=exc.code().name
                except (AttributeError,TypeError):pass
                raise
            finally:
                end=time.monotonic_ns()
                if self.log:self.log('SDK_RPC_timing',group=self.group,rpc=name,
                    request_monotonic_ns=begin,response_monotonic_ns=end,
                    elapsed_ns=end-begin,deadline_s=self.seconds,status=status,
                    timestamp_basis='host RPC brackets, not controller sensor time')
        return call


class FeedbackCache:
    def __init__(self, profile, interlock):
        self.profile,self.interlock=profile,interlock
        from ai.execute.estop import SdkEstop
        self.sdk_estop=SdkEstop(profile) if profile.values.get('sdk_estop') is not None else None
        self.lock=threading.Lock();self.value=None;self.error='no_controller_feedback'
    def update(self, data, servo, motion, teleop, joint_ns, status_ns, native_status):
        v=self.profile.values;proof=self.interlock.read()
        for item in (data,servo,motion,teleop):
            if 'response' in item:ack(item)
        if type(motion.get('is_in_motion')) is not bool:raise SafetyError('motion_state_unknown')
        q=np.deg2rad(np.asarray(data['q'],dtype=float));qd=np.deg2rad(np.asarray(data['qdot'],dtype=float))
        if q.shape!=(6,) or qd.shape!=(6,) or not np.isfinite(q).all() or not np.isfinite(qd).all():raise SafetyError('invalid_SDK_joint_feedback')
        native=status_health(native_status,self.profile)
        estop_clear=self.sdk_estop.update({**native_status,'get_control_data':data,'get_servo_data':servo,'get_motion_data':motion}) if self.sdk_estop else proof['estop_clear']
        states=servo_states(servo)
        fault=any(state in ('FAULT','FAULT_REACTION') for state in states) or native['violation_code']!=0
        op=data['op_state'];method=teleop.get('method')
        mode='idle' if op==5 else 'joint_absolute' if op==17 and method==10 else 'unknown'
        ready=(servo.get('servo_actives')==[True]*6 and servo.get('brake_actives')==[False]*6
               and all(s=='OPERATION_ENABLED' for s in states)
               and (native['boot_ready'] is True or (native['boot_ready'] is None and self.profile.values.get('controller_version')=='3.3.2-GA12 [2026.09.15]'
                    and feedback_transport_connected(data,self.profile))))
        if native['stop_category']!=-1 and not (native['stop_category']==2 and mode=='idle'):ready=False
        if any(type(motion.get(k)) is not bool for k in ('is_stopping','is_pausing')):
            raise SafetyError('motion_stop_pause_status_unknown')
        if type(motion.get('motion_queue_size')) is not int or motion['motion_queue_size']<0:
            raise SafetyError('motion_queue_state_unknown')
        if motion['is_stopping'] or motion['is_pausing'] or motion['motion_queue_size']!=0:ready=False
        connected=feedback_transport_connected(data,self.profile)
        state=RobotFeedback(q,qd,joint_ns,status_ns,connected,ready,op in (5,17),mode,
            bool(fault or op not in (5,17)),not estop_clear,motion.get('is_in_motion'),
            proof['all_other_writers_blocked'],True,v['external_exclusion_record'],op,method)
        with self.lock:
            self.value,self.error=state,None
            self.native_status={**native,"servo_states":states,"raw":native_status,"reported_is_robot_connected":data.get("is_robot_connected"),
                                "connection_basis":"fresh SDK payload plus separate Servo/control/stop/fault gates; not E-stop clearance"}
    def fail(self,reason):
        with self.lock:self.error=str(reason)
    def get(self,now):
        self.interlock.read()  # External authority/E-stop loss invalidates even cached joints.
        if self.sdk_estop and self.sdk_estop.revoked:raise SafetyError('SDK_estop_session_revoked')
        with self.lock:
            if self.error or self.value is None:raise SafetyError(self.error or 'no_controller_feedback')
            return self.value


def require_control_available(feedback, now, safety):
    """Control eligibility, NOT an assertion that E-stop is clear.

    Reuse existing freshness/Servo/Fault/authority checks. Any failure is passed
    to the gateway's sticky trip, even if CONTY later restores the controller.
    """
    feedback.require(now,safety)
    if feedback.mode not in ('idle','joint_absolute'):
        raise SafetyError('controller_control_mode_unavailable')


def _official_driver_module():
    folder=ROOT/'ros2_ws/src/external/indy-ros2/indy_driver/indy_driver'
    sys.path.insert(0,str(folder))
    spec=importlib.util.spec_from_file_location('_capstone_official_indy_driver',folder/'indy_driver.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def create_driver(profile, safety, interlock, nonce, stop_file, log):
    module=_official_driver_module()
    from capstone_teleop.writer_lock import WriterLock
    from indy_interfaces.srv import IndyService
    from sensor_msgs.msg import JointState
    from rclpy.action import GoalResponse, CancelResponse
    class Guarded(module.IndyROSConnector):
        def __init__(self):
            self.writer=WriterLock('act_real:'+nonce);self.writer.acquire()
            self.sdk_lock=threading.Lock();self.closed=threading.Event();self.tripped=threading.Event()
            self.cache=FeedbackCache(profile,interlock);self.trip_reason=None
            self.profile,self.config,self.nonce=profile,safety,nonce
            self.stop_stable_ns=round(profile.values['stop_stable_ms']*1e6)
            self.owned_connection=False;self.confirmed_stop=False;self.execution_deadline_ns=None
            self.sdk_command_attempts=0;self.sdk_acknowledged_actions=0
            try:
                super().__init__()
                self.timer.cancel()  # Use independent bounded read-only status polling.
                from neuromeka import IndyDCP3
                self.indy=IndyDCP3(profile.values['robot_ip']);self.owned_connection=True
                for group in ('boot','control','device','config','rtde','cri'):
                    setattr(self.indy,group,DeadlineStub(getattr(self.indy,group),profile.values['rpc_timeout_ms']/1000,group=group,log=log))
                device=self.indy.get_device_info();control=self.indy.get_control_info()
                if (device.get('robot_serial')!=profile.values['robot_serial'] or
                    control.get('control_version')!=profile.values['controller_version'] or control.get('robot_model')!='Indy7' or
                    importlib.metadata.version('neuromeka')!=profile.values['sdk_version']):raise SafetyError('robot_FW_SDK_identity_mismatch')
                self.controller_configuration=read_configuration(self.indy,profile)
                self.limit_audit=validate_limits(self.controller_configuration,profile,safety)
                log('controller_safety_configuration',raw=self.controller_configuration,assessment=self.limit_audit)
                if hasattr(interlock,'bind_writer'):interlock.bind_writer(self.writer)
                self.private=self.create_service(IndyService,f'/capstone/act/{nonce}/indy_srv',self.private_command)
                self.thread=threading.Thread(target=self.monitor,name='act-driver-feedback',daemon=True);self.thread.start()
            except BaseException:
                if self.owned_connection:
                    for name in ('boot','control','device','config','rtde','cri'):
                        getattr(self.indy,name+'_channel').close()
                self.writer.close(confirmed=not self.owned_connection)
                if hasattr(self,'timer'):self.destroy_node()
                raise
        def indy_srv_callback(self,request,response):
            response.success=False;response.message='Real ACT gateway: public mode/Home/Recover/Zero disabled'
            self.trip('foreign_indy_service_request');return response
        def set_servo_rx_callback(self,msg):self.trip('foreign_EtherCAT_write_request')
        def goal_callback(self,request):
            self.trip('foreign_trajectory_action_goal');return GoalResponse.REJECT
        def cancel_callback(self,request):return CancelResponse.REJECT
        def execute_callback(self,goal_handle):raise SafetyError('trajectory_action_disabled')
        def private_command(self,request,response):
            try:
                if request.data==8:
                    self.tripped.set();self.stop_sdk()
                elif request.data==6:
                    if self.tripped.is_set():raise SafetyError('gateway_revoked')
                    self.cache.get(time.monotonic_ns()).require(time.monotonic_ns(),safety,'idle')
                    with self.sdk_lock:
                        if self.tripped.is_set():raise SafetyError('gateway_revoked')
                        ack(self.indy.start_teleop(method=10))
                    self.indy_msg_status=6
                else:raise SafetyError('only_absolute_start_and_stop_allowed')
                response.success=True;response.message='SDK ACK only; actual feedback confirmation required'
            except Exception as exc:
                response.success=False;response.message=str(exc);self.trip(str(exc))
            return response
        def joint_trajectory_callback(self,msg):
            timing={'received_ns':time.monotonic_ns()};expiry=None
            try:
                token,expiry=msg.header.frame_id.split(':');expiry=int(expiry)
                if token!=nonce or not 0<=expiry-time.monotonic_ns()<=safety.max_action_lateness_ms*1e6:raise SafetyError('foreign_or_expired_trajectory')
                if self.tripped.is_set():raise SafetyError('gateway_revoked')
                if list(msg.joint_names)!=[f'joint{i}' for i in range(6)] or len(msg.points)!=1:raise SafetyError('trajectory_contract')
                q=np.asarray(msg.points[0].positions,dtype=float)
                feedback=self.cache.get(time.monotonic_ns());feedback.require(time.monotonic_ns(),safety,'joint_absolute')
                timing['feedback_completed_ns']=time.monotonic_ns()
                if q.shape!=(6,) or not np.isfinite(q).all() or np.any(q<safety.joint_min_rad) or np.any(q>safety.joint_max_rad):raise SafetyError('driver_target_limit')
                if np.any(abs(q-feedback.joints)>safety.max_target_error_rad):raise SafetyError('driver_target_current_error')
                timing['sdk_lock_requested_ns']=time.monotonic_ns()
                with self.sdk_lock:
                    timing['sdk_lock_acquired_ns']=time.monotonic_ns()
                    if self.execution_deadline_ns is not None and time.monotonic_ns()>=self.execution_deadline_ns:raise SafetyError('five_second_execution_deadline')
                    if self.tripped.is_set() or time.monotonic_ns()>expiry:raise SafetyError('gateway_revoked_or_expired')
                    self.sdk_command_attempts+=1
                    timing['sdk_entered_ns']=time.monotonic_ns()
                    ack(self.indy.movetelej_abs(jpos=np.rad2deg(q).tolist(),vel_ratio=0.8,acc_ratio=7.0))
                    timing['sdk_ack_ns']=time.monotonic_ns()
                    self.sdk_acknowledged_actions+=1
                log('real_SDK_target_ack',target_absolute_rad=q.tolist(),expires_ns=expiry,
                    sdk_acknowledged_actions=self.sdk_acknowledged_actions,physical_execution_verified=False)
            except Exception as exc:self.trip('driver_dispatch_failed:'+str(exc))
            finally:
                timing['finished_ns']=time.monotonic_ns()
                log('real_driver_dispatch_timing',expires_ns=expiry,stages=timing,**self.command_counts())
        def command_counts(self):
            # The gateway executor is the only SDK command writer. Read the ACK
            # count first so a concurrent attempt cannot appear below its ACKs.
            acknowledged=self.sdk_acknowledged_actions
            return {'sdk_command_attempts':self.sdk_command_attempts,'sdk_acknowledged_actions':acknowledged}
        def stop_sdk(self):
            # Reviewed procedure must be established on hardware BEFORE profile activation.
            # Try both even when the first RPC fails; response alone is not stop confirmation.
            with self.sdk_lock:request_normal_stop(self.indy)
        def trip(self,reason):
            if self.tripped.is_set():return
            self.trip_reason=reason;self.tripped.set();self.cache.fail(reason)
            try:log('real_gateway_trip',reason=reason)
            except Exception:pass  # Logging failure must never suppress the stop request.
            def stop():
                try:self.stop_sdk()
                except Exception as exc:log('real_gateway_stop_unconfirmed',reason=str(exc))
            threading.Thread(target=stop,name='act-gateway-stop',daemon=True).start()
        def monitor(self):
            next_poll=time.monotonic()
            while not self.closed.is_set():
                try:
                    if Path(stop_file).exists():self.trip('operator_STOP_file')
                    if any('qp_teleop' in n for n in self.get_node_names()):raise SafetyError('QP_writer_detected')
                    # Reuse the existing 20Hz monitor, not the 20ms send path.
                    # get()/read() reject evidence older than the reviewed bound.
                    if hasattr(interlock,'refresh'):
                        try:interlock.refresh()
                        finally:log('direct_link_refresh_timing',stages=interlock.last_refresh_timing)
                    data=self.indy.get_control_data();joint_ns=time.monotonic_ns();joint_system=time.time_ns()
                    servo=self.indy.get_servo_data();motion=self.indy.get_motion_data();teleop=self.indy.get_teleop_state()
                    native=read_status(self.indy,profile)
                    self.cache.update(data,servo,motion,teleop,joint_ns,time.monotonic_ns(),native)
                    feedback=self.cache.get(time.monotonic_ns())
                    require_control_available(feedback,time.monotonic_ns(),safety)
                    log('controller_safety_feedback',status=self.cache.native_status,
                        joint_available_monotonic_ns=feedback.joint_received_ns,status_available_monotonic_ns=feedback.status_received_ns)
                    message=JointState();message.name=[f'joint{i}' for i in range(6)]
                    message.position=np.deg2rad(data['q']).tolist();message.velocity=np.deg2rad(data['qdot']).tolist()
                    message.header.stamp.sec=joint_system//10**9;message.header.stamp.nanosec=joint_system%10**9
                    self.joint_state_pub.publish(message) # Availability time, not a controller sensor timestamp.
                except Exception as exc:
                    self.cache.fail(str(exc));self.trip('controller_monitor:'+str(exc))
                next_poll+=.05
                self.closed.wait(max(0,next_poll-time.monotonic()))
                if time.monotonic()-next_poll>.05:next_poll=time.monotonic()
        def close_real(self,confirmed):
            self.closed.set()
            if hasattr(self,'thread'):self.thread.join(.5)
            self.writer.close(confirmed=confirmed)
            if self.indy:
                for name in ('boot','control','device','config','rtde','cri'):
                    getattr(self.indy,name+'_channel').close()
            self.destroy_node()  # Never call original disconnect(): its implicit motion writes are not used.
    return Guarded()
