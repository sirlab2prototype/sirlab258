"""Fake clocks/RPC and real bounded alignment/scheduler. No ROS/DDS or devices."""
from types import SimpleNamespace as Message

import numpy as np
import pytest

from ai import dataset_contract as contract
from ai.observation_timing import IntegrityError, SyncOptions
from ai.serve import dry_run
from ai.serve.observations import HistorySource, RosSource
from ai.serve.ros_recovery import RecoveryMonitor, RecoveryOptions, transient_camera_error

MS = 1_000_000
META = {"checkpoint_id":"model", "checkpoint":"fake", "fps":20, "chunk_size":12, "n_action_steps":4}


class Clock:
    def __init__(self): self.ns = 1_000_000_000
    def sleep(self, seconds): self.ns += round(seconds*1e9)


class ScriptedRos(RosSource):
    def __init__(self, clock, errors=None, *, initial_delay=0, stop_cameras=None, gripper=True, camera_phase_ms=0):
        HistorySource.__init__(self)
        self.operator_gripper = None
        self.clock, self.origin = clock, clock.ns
        self.errors = errors or {}
        self.initial_delay, self.stop_cameras = initial_delay, stop_cameras
        self.camera_phase_ms = camera_phase_ms
        self.indices = {name:0 for name in (*contract.CAMERA_NAMES, "state")}
        self.image = np.zeros(contract.IMAGE_SHAPE, dtype=np.uint8)
        self.samples = 0
        self.closed = False
        if gripper: self.append("gripper", 0., None)

    def timing(self):
        mono, epoch = self.clock.ns, 1_700_000_000_000_000_000+self.clock.ns
        return {"sample_monotonic_ns":mono,"sample_system_time_ns":epoch,"clock_read_span_ns":0,
                "receiver_clock":{"ros_time_ns":epoch,"use_sim_time":False,"ros_clock_type":"ROS_TIME"}}

    def update(self):
        elapsed = self.clock.ns-self.origin
        for name in self.indices:
            camera = name in contract.CAMERA_NAMES
            if camera and (elapsed < self.initial_delay*MS or
                           self.stop_cameras is not None and elapsed >= self.stop_cameras*MS):
                continue
            period = 1e9/(30 if camera else 20)
            phase = self.camera_phase_ms*MS if name == contract.CAMERA_NAMES[1] else 0
            delay = (36 if camera else 1.5)*MS
            while round(self.indices[name]*period)+phase+delay <= elapsed:
                generation = round(self.indices[name]*period)+phase
                self.indices[name] += 1
                if elapsed-generation > 400*MS: continue
                header = 1_700_000_000_000_000_000+self.origin+generation
                self.append(name, self.image if camera else np.zeros(6,dtype=np.float32),
                            {"sec":header//1_000_000_000,"nanosec":header%1_000_000_000})

    def sample(self, reference):
        self.samples += 1
        error = self.errors.get(self.samples)
        if error is not None: raise error
        return super().sample(reference)

    def close(self): self.closed = True


@pytest.fixture
def rig(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(dry_run.time,"monotonic_ns",lambda:clock.ns)
    monkeypatch.setattr(dry_run.time,"monotonic",lambda:clock.ns/1e9)
    monkeypatch.setattr(dry_run.time,"sleep",clock.sleep)
    calls, rows = [], []
    delays = [5]
    class RPC:
        def __init__(self,*args): self.future = None;self.generated=0
        def control(self,header):
            return ({**META,**header,"session_generated_chunks":self.generated},None,{})
        def submit(self,header,observation,chunk_size):
            assert self.future is None
            calls.append(dict(header))
            self.future = (dict(header),clock.ns+delays[0]*MS)
        def poll(self):
            if self.future is None or clock.ns < self.future[1]: return None
            header, _ = self.future;self.future=None;self.generated+=1
            return ({**META,**header,"inference_ns":5*MS},np.zeros((12,7),np.float32),
                    {"client_received_monotonic_ns":clock.ns})
        def close(self): self.future=None
    monkeypatch.setattr(dry_run,"RPCClient",RPC)
    def log(event,**values): rows.append({"event":event,"at":clock.ns,**values})
    def run(errors=None, *, options=True, duration=1.1, source=None, **source_args):
        source = source or ScriptedRos(clock,errors,**source_args)
        result = dry_run.run_session(source,"unused",META,log,duration=duration,
            startup_timeout=1.,ros_recovery=RecoveryOptions() if options is True else options if options else None)
        return result, source
    return Message(clock=clock,rows=rows,calls=calls,delays=delays,run=run)


def camera_error(reason="camera_skew"):
    return IntegrityError(reason, **({"topic":contract.CAMERA_NAMES[0]} if reason!="camera_skew" else {"skew_ns":45*MS}))


def test_normal_stabilizes_before_first_inference(rig):
    report,source=rig.run()
    assert report["status"]=="complete" and report["robot_commands_sent"]==0
    monitor=report["observation_recovery"]
    assert monitor["startup_valid_observations"]==3 and monitor["discarded_observations"]==0
    assert monitor["initial_stabilization_ms"]>=200
    assert source.closed
    ready=next(row["at"] for row in rig.rows if row["event"]=="observation_stabilized")
    assert all(row["at"]>=ready for row in rig.rows if row["event"] in ("prediction_request","action"))


def test_initial_missing_frames_wait_without_counting_active_drops(rig):
    report,_=rig.run(initial_delay=180)
    assert report["status"]=="complete"
    m=report["observation_recovery"]
    assert m["initial_stabilization_ms"]>300 and m["discarded_observations"]==0
    assert m["recent_10s_errors"]==0 and m["startup_readiness_waits"]
    assert m["startup_discarded_observations"]==0


def test_out_of_phase_cameras_stabilize_on_delayed_references(rig):
    report,_=rig.run(camera_phase_ms=16)
    assert report["status"]=="complete"
    assert report["observation_recovery"]["startup_valid_observations"]==3


def test_startup_rejection_resets_stable_streak_only(rig):
    report,_=rig.run({1:camera_error()})
    m=report["observation_recovery"]
    assert report["status"]=="complete" and m["startup_discarded_observations"]==1
    assert m["startup_valid_observations"]==3 and m["discarded_observations"]==0


@pytest.mark.parametrize("reason",["camera_skew","observation_too_old","missing_causal_observation"])
def test_single_camera_error_discards_and_reinfers(rig,reason):
    report,_=rig.run({4:camera_error(reason)})  # Three startup samples, then the first active slot.
    assert report["status"]=="complete"
    m=report["observation_recovery"]
    assert m["discarded_observations"]==1 and m["error_counts"]=={reason:1}
    assert m["recovery_successes"]==1 and m["invalidated_actions"]>0
    assert m["had_recovery"] and m["quality"]=="recovered"
    pause=next(r for r in rig.rows if r["event"]=="observation_recovery_pause")
    resume=next(r for r in rig.rows if r["event"]=="observation_recovery_resumed")
    assert not any(r["event"]=="action" and pause["at"]<=r["at"]<resume["at"] for r in rig.rows)
    requests=[r for r in rig.rows if r["event"]=="prediction_request" and r["at"]>pause["at"]]
    assert requests and requests[0]["timing"]["observation_monotonic_ns"]>pause["reference_monotonic_ns"]


def test_late_old_response_cannot_resume_or_output(rig):
    rig.delays[0]=80
    report,_=rig.run({6:camera_error()})
    assert report["status"]=="complete"
    m=report["observation_recovery"]
    assert m["abandoned_inference_requests"]==1 and m["ignored_obsolete_responses"]==1
    pause=next(r for r in rig.rows if r["event"]=="observation_recovery_pause")
    old=pause["abandoned_request"]
    assert old and not any(r["event"]=="action" and r["request_id"]==old for r in rig.rows)
    assert any(r["event"]=="obsolete_inference_finished" for r in rig.rows)


def test_consecutive_three_errors_terminate_with_queue_empty(rig):
    report,_=rig.run({i:camera_error() for i in (4,5,6)})
    assert report["status"]=="failed"
    m=report["observation_recovery"]
    assert m["failure_reason"]=="consecutive_observation_error_limit" and m["discarded_observations"]==3
    final_pause=[r for r in rig.rows if r["event"]=="observation_recovery_pause"][-1]
    assert not any(r["event"]=="action" and r["at"]>=final_pause["at"] for r in rig.rows)


def test_six_separated_errors_in_ten_seconds_terminate(rig):
    report,_=rig.run({i:camera_error() for i in (4,6,8,10,12,14)},duration=2.)
    m=report["observation_recovery"]
    assert report["status"]=="failed" and m["failure_reason"]=="observation_error_window_limit"
    assert m["discarded_observations"]==6 and m["maximum_consecutive_errors"]==1


def test_errors_outside_rolling_window_do_not_accumulate_forever(rig):
    report,_=rig.run({i:camera_error() for i in (4,46,88,130,172,214)},duration=14.)
    m=report["observation_recovery"]
    assert report["status"]=="complete" and m["recovery_successes"]==6
    assert m["discarded_observations"]==6 and m["recent_10s_errors"]<6


@pytest.mark.parametrize("duration",[10.,60.])
def test_long_fake_clock_run_is_bounded_and_has_zero_robot_commands(rig,duration):
    report,source=rig.run(duration=duration)
    assert report["status"]=="complete" and report["robot_commands_sent"]==0
    assert report["missed_action_deadlines"]==0 and 19.99<report["actual_action_fps"]<20.01
    assert all(len(items)<=source.options.history_size for items in source.history.items.values())


def test_sensor_stops_instead_of_replaying_actions(rig):
    report,_=rig.run(stop_cameras=400)
    assert report["status"]=="failed"
    m=report["observation_recovery"]
    assert m["discarded_observations"]==3 and m["failure_reason"]=="consecutive_observation_error_limit"


def test_all_sensor_inputs_stop_and_no_action_is_repeated(rig):
    source=ScriptedRos(rig.clock)
    update=source.update
    source.update=lambda:update() if rig.clock.ns-source.origin<400*MS else None
    report,_=rig.run(source=source)
    assert report["status"]=="failed" and report["robot_commands_sent"]==0
    first_pause=next(r for r in rig.rows if r["event"]=="observation_recovery_pause")
    assert not any(r["event"]=="action" and r["at"]>=first_pause["at"] for r in rig.rows)


def test_configured_stable_count_is_applied(rig):
    report,_=rig.run(options=RecoveryOptions(stable_observations=5))
    assert report["status"]=="complete" and report["observation_recovery"]["startup_valid_observations"]==5


@pytest.mark.parametrize("error",[IntegrityError("header_reversal"),IntegrityError("system_clock_jump_or_drift"),
    IntegrityError("observation_too_old",topic="state"),IntegrityError("unknown_initial_gripper"),
    IntegrityError("missing_causal_observation",topic="state"),IntegrityError("observation_skew")])
def test_serious_or_non_camera_errors_fail_immediately(rig,error):
    report,_=rig.run({4:error})
    assert report["status"]=="failed" and error.reason in report["reason"]
    assert report["observation_recovery"]["discarded_observations"]==0


def test_default_fail_fast_is_unchanged(rig):
    report,_=rig.run({1:camera_error()},options=False)
    assert report["status"]=="failed" and "camera_skew" in report["reason"]
    assert "observation_recovery" not in report


def test_initial_timeout_and_unknown_gripper_not_bypassed(rig):
    report,_=rig.run(gripper=False)
    assert report["status"]=="failed" and "stabilization_timeout" in report["reason"]
    m=report["observation_recovery"]
    assert m["discarded_observations"]==0 and not m["initial_stabilization_succeeded"]
    assert not rig.calls


def test_missing_valid_observation_watchdog_terminates_run(rig):
    source=ScriptedRos(rig.clock,{4:camera_error()})
    update=source.update
    once=[False]
    def delayed():
        if source.samples>=4 and not once[0]:
            once[0]=True
            rig.clock.sleep(1.1)  # Both epoch/monotonic advance, not a clock mismatch.
        update()
    source.update=delayed
    report,_=rig.run(source=source,duration=3.)
    assert report["observation_recovery"]["failure_reason"]=="no_valid_observation_timeout"


def test_actual_startup_header_fault_is_not_retried(rig):
    source=ScriptedRos(rig.clock)
    source.append("state",np.zeros(6),{"sec":0,"nanosec":0})
    report,_=rig.run(source=source)
    assert report["status"]=="failed" and "zero_header" in report["reason"] and not rig.calls


def test_processing_deadline_miss_is_not_camera_recovery(rig):
    source=ScriptedRos(rig.clock)
    update=source.update
    once=[False]
    def delayed():
        if source.samples>=4 and not once[0]:
            once[0]=True;rig.clock.sleep(.08)
        update()
    source.update=delayed
    report,_=rig.run(source=source)
    assert report["status"]=="failed" and "deadline_missed" in report["reason"]
    assert report["observation_recovery"]["discarded_observations"]==0


def test_error_count_is_per_reference_and_window_is_bounded():
    monitor=RecoveryMonitor(RecoveryOptions())
    monitor.reject(camera_error(),50*MS,100*MS)
    monitor.reject(camera_error(),50*MS,101*MS)
    assert monitor.discarded==1 and monitor.consecutive==1
    monitor.valid(150*MS)
    monitor.reject(camera_error(),100*MS,11_000*MS)
    assert monitor.report(11_000*MS)["recent_10s_errors"]==1
    with pytest.raises(RuntimeError,match="no_valid_observation_timeout"):
        monitor.watchdog(12_000*MS)


@pytest.mark.parametrize("mock",[None,Message(is_mock=True)])
def test_function_recovery_cannot_apply_to_execution_or_synthetic(rig,mock):
    source=ScriptedRos(rig.clock) if mock else HistorySource()
    with pytest.raises(ValueError,match="restricted to ROS Dry Run"):
        dry_run.run_session(source,"unused",META,lambda *a,**k:None,
            ros_recovery=RecoveryOptions(),_mock_execution=mock)


@pytest.mark.parametrize("entry",["launcher","client"])
@pytest.mark.parametrize("execution",[False,True])
def test_cli_scope_rejects_before_startup(tmp_path,entry,execution):
    if entry=="launcher":
        from ai.serve.infer import main
        args=["--checkpoint",str(tmp_path)]
        kwargs={"_mock_options":{}} if execution else {}
    else:
        main=dry_run.main
        args=["--endpoint","unused","--metadata","missing","--output-dir",str(tmp_path/"unused")]
        kwargs={"_mock_factory":lambda:None} if execution else {}
    with pytest.raises(SystemExit) as error:
        main([*args,"--source","ros" if execution else "synthetic","--dry-run","--ros-observation-recovery"],**kwargs)
    assert error.value.code==2


def test_launcher_forwards_recovery_only_to_ros_client(monkeypatch,tmp_path):
    import json
    from ai.serve import infer
    checkpoint,output=tmp_path/"checkpoint",tmp_path/"run"
    checkpoint.mkdir()
    commands=[]
    class Process:
        def __init__(self,command,**kwargs):
            commands.append(command)
            self.server="ai.serve.zmq_act_server" in command
            self.returncode=None if self.server else 0
            if self.server:
                (output/"server_ready.json").write_text(json.dumps({"device":"fake","checkpoint_id":"model"}))
            else:
                (output/"client").mkdir()
                (output/"client/summary.json").write_text(json.dumps({"status":"complete","robot_commands_sent":0}))
        def poll(self):return self.returncode
        def terminate(self):self.returncode=0
        def wait(self,**kwargs):return self.returncode
    monkeypatch.setattr(infer.subprocess,"Popen",Process)
    assert infer.main(["--checkpoint",str(checkpoint),"--source","ros","--dry-run",
        "--ros-observation-recovery","--ros-stable-observations","5","--ros-no-valid-timeout",".8",
        "--output-dir",str(output)])==0
    assert "--ros-observation-recovery" not in commands[0]
    assert "--ros-observation-recovery" in commands[1][2]
    assert "--ros-stable-observations 5" in commands[1][2] and "--ros-no-valid-timeout 0.8" in commands[1][2]


def test_tuning_requires_explicit_enable(tmp_path):
    from ai.serve.infer import main
    with pytest.raises(SystemExit) as error:
        main(["--checkpoint",str(tmp_path),"--source","ros","--dry-run","--ros-stable-observations","3"])
    assert error.value.code==2


@pytest.mark.parametrize("command",["execute","task"])
def test_real_execution_with_recovery_flag_is_still_locked(tmp_path,command,capsys):
    if command=="execute":
        from ai.execute.main import main
        args=[]
    else:
        from ai.execute.task_main import main
        args=["--task","indy7_basket_to_table1"]
    with pytest.raises(SystemExit) as error:
        main([*args,"--mode","real","--checkpoint",str(tmp_path),"--ros-observation-recovery"])
    assert error.value.code==2 and "locked" in capsys.readouterr().err


def test_scheduler_discards_exact_queued_tail_without_resetting_totals():
    from ai.serve.action_scheduler import DryRunScheduler
    scheduler=DryRunScheduler(12,3)
    scheduler.start("s",0)
    values=np.zeros((12,7),np.float32)
    scheduler.requested("old",0);scheduler.response("s","old",values,0)
    scheduler.tick(50*MS)
    scheduler.requested("prefetched",50*MS);scheduler.response("s","prefetched",values,60*MS)
    assert scheduler.pause_observation("camera_skew")==5
    assert scheduler.is_empty_and_stopped and scheduler.tick(100*MS) is None
    assert not scheduler.response("s","prefetched",values,110*MS)
    scheduler.rewarm_observation();scheduler.requested("fresh",500*MS)
    scheduler.response("s","fresh",values,520*MS)
    action=scheduler.tick(550*MS)
    assert action["request_id"]=="fresh" and scheduler.consumed==2


def test_real_rpc_worker_discards_delayed_response_with_fake_ros(monkeypatch,tmp_path):
    # A real localhost RPC/REP pair and worker thread, but no rclpy initialization.
    import json
    import time
    from ai.serve.observations import SyntheticSource
    from ai.serve.test.test_inference import FakeBackend, InferenceService, server_thread
    class StreamingRos(RosSource):
        def __init__(self):
            HistorySource.__init__(self)
            self.operator_gripper=None
            self.sensors=SyntheticSource()
            self.copied={}
            self.count=0
            self.append("gripper",0.,None)
        def update(self):
            self.sensors.update()
            for name in (*contract.CAMERA_NAMES,"state"):
                items=self.sensors.history.items.get(name)
                if items:
                    value,source=items[-1]
                    if self.copied.get(name)!=source["receipt_sequence"]:
                        self.append(name,value,source["header_stamp"])
                        self.copied[name]=source["receipt_sequence"]
        def sample(self,reference):
            self.count+=1
            if self.count==6:raise camera_error()
            return super().sample(reference)
        def close(self):pass
    source=StreamingRos()
    log=dry_run.EventLog(tmp_path/"events.jsonl","real-rpc-fake-ros")
    try:
        with server_thread(InferenceService(FakeBackend(delay=.08),"model")) as endpoint:
            report=dry_run.run_session(source,endpoint,META,log,duration=1.4,
                startup_timeout=2.,ros_recovery=RecoveryOptions())
    finally:log.close()
    assert report["status"]=="complete",report
    m=report["observation_recovery"]
    assert m["recovery_successes"]==1 and m["ignored_obsolete_responses"]==1
    assert report["robot_commands_sent"]==0
    (tmp_path/"summary.json").write_text(json.dumps(report,indent=2))
