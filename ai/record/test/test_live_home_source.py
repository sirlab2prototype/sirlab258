"""Measured-profile source tests. No SDK import, connection or ROS node."""
import hashlib
import json
from types import SimpleNamespace as M

import numpy as np
import pytest
from ai.home_action import LiveHomeContract, HomeActionError
from ai.record.home_source import CaptureAssembler
from ai.record.test.test_indy7_act_record import recorder, HistoryHarness, EPOCH, MONO
from ai.record.test.test_home_action import envelope
from ai.record.test.test_recorder_context_compatibility import CallbackRig

MS=1_000_000


def profile():
    return {"version":1,"scope":"collection_only","evidence_id":"test-live",
        "source_unit":"deg","robot_ip":"192.0.2.1","robot_serial":"test-robot",
        "controller_version":"test-fw","sdk_version":"test-sdk","home_target_rad":[0]*6,
        "qdes_intermediate_observed":True,"evidence_sha256":"a"*64}


def row(ms, *, moving=False, q=0., target=0.):
    calls={}
    for i,name in enumerate(("get_control_state","get_control_data","get_motion_data","get_servo_data")):
        calls[name]={"request_system_ns":EPOCH+ms*MS+i*100_000,
            "response_system_ns":EPOCH+ms*MS+(i+1)*100_000,
            "request_monotonic_ns":MONO+ms*MS+i*100_000,
            "response_monotonic_ns":MONO+ms*MS+(i+1)*100_000,"rpc_elapsed_ns":100_000}
    calls["get_control_state"]["raw"]={"q":[q]*6,"qdes":[target]*6,"qdot":[1. if moving else 0.]*6}
    calls["get_control_data"]["raw"]={"op_state":6 if moving else 17}
    calls["get_motion_data"]["raw"]={"is_in_motion":moving}
    calls["get_servo_data"]["raw"]={"servo_actives":[True]*6,"brake_actives":[False]*6,"status_codes":["0x237"]*6}
    return {"calls":calls}


def feed(a, ms, seq, phase="QP_TELEOP", generation=0, **kwargs):
    a.accept_base(envelope(ms,seq,phase,generation))
    return a.build(row(ms,**kwargs),EPOCH+ms*MS+500_000)


def test_profile_hash_required_for_activation(tmp_path):
    p=profile();evidence=tmp_path/"evidence";evidence.write_text("fake-evidence")
    p.update(evidence_path=str(evidence),evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())
    path=tmp_path/"profile.json";path.write_text(json.dumps(p))
    assert LiveHomeContract.load(path).expected_simulated is False
    evidence.write_text("changed")
    with pytest.raises(ValueError,match="hash_mismatch"):LiveHomeContract.load(path)


def test_live_qdes_paired_state_transition_settling_and_neutral_rearm():
    c=LiveHomeContract("test-live",profile=profile());a=CaptureAssembler(c)
    assert feed(a,0,0)["phase"]=="QP_TELEOP"
    first=feed(a,50,1,"HOME_REQUESTED",1,moving=True,q=9.9,target=10.)
    np.testing.assert_allclose(first["sample"]["target_rad"],np.deg2rad([10]*6))
    assert first["observed_state"]["position_rad"]!=first["sample"]["target_rad"]
    assert feed(a,100,2,"HOME_MOVING",1,moving=True,q=4.9,target=5.)["phase"]=="HOME_MOVING"
    for seq,ms in enumerate(range(150,451,50),3):
        packet=feed(a,ms,seq,"QP_TELEOP",1)
    assert packet["phase"]=="HOME_COMPLETE" and packet["arrival_verified"]
    assert packet["sample"]["simulated"] is False
    assert feed(a,500,10,"QP_TELEOP",1)["phase"]=="HOME_COMPLETE"
    a.qp_action_ns=EPOCH+520*MS
    assert feed(a,550,11,"QP_TELEOP",1)["phase"]=="QP_TELEOP"


def test_movement_failure_and_source_loss_are_rejected():
    a=CaptureAssembler(LiveHomeContract("test-live",profile=profile()))
    feed(a,0,0);feed(a,50,1,"HOME_REQUESTED",1)
    a.qp_action_ns=EPOCH+60*MS
    with pytest.raises(HomeActionError,match="before_home_settled"):feed(a,100,2,"HOME_MOVING",1)
    with pytest.raises(HomeActionError,match="stale_base"):a.build(row(500,q=10),EPOCH+500*MS)


def test_blocked_movej_heartbeat_does_not_hold_old_desired():
    a=CaptureAssembler(LiveHomeContract("test-live",profile=profile()))
    feed(a,0,0);feed(a,50,1,"HOME_REQUESTED",1,moving=True,q=10,target=11)
    packet=a.build(row(200,moving=True,q=4,target=5),EPOCH+200*MS+500_000)
    assert packet["phase"]=="HOME_MOVING"
    np.testing.assert_allclose(packet["sample"]["target_rad"],np.deg2rad([5]*6))


def test_live_packet_passes_existing_20fps_frame_engine(recorder,tmp_path):
    h=HistoryHarness(recorder,tmp_path);p=profile();c=LiveHomeContract("test-live",profile=p)
    a=CaptureAssembler(c);packet=feed(a,-1,0)
    h.push("action_context",-.5,-.4,packet)
    timing=h.timing(0);timing.update(require_action_context=True,home_capture_profile=p)
    aligner=recorder.HistoryAligner.prepare(h.options,h.history.snapshot(MONO),timing,c)
    h.session.start(aligner.target,MONO/1e9,timing,aligner);h.generate(600)
    for seq,ms in enumerate(range(20,601,20),1):
        phase="QP_TELEOP" if ms<60 else "HOME_REQUESTED" if ms==60 else "HOME_MOVING"
        pkt=feed(a,ms,seq,phase,int(ms>=60),moving=60<=ms<180,q=0.1 if ms<180 else 0,target=.2 if ms<180 else 0)
        h.events.append((ms+1,"action_context",ms+.5,pkt))
    h.events.sort(key=lambda x:x[0])
    for ms in range(50,501,50):h.tick(ms)
    assert h.session.integrity["valid"],h.session.integrity
    assert h.row(1)["recorded_action"]["kind"]=="home_qdes"
    h.session.finish_aligned(h.history.snapshot(MONO+560*MS),h.timing(560))
    assert h.session.integrity["valid"]
    assert h.session.metadata["home_action_acceptance"]=="live_qdes_profile_v1"
    from ai.train.validate_dataset import _Validator
    validator=_Validator(tmp_path,None)
    validator.alignment(h.row(1),h.session.metadata,0)
    assert not validator.errors and not validator.warnings


def test_live_callbacks_keep_measured_q_separate_and_original_qp_stamp(recorder,tmp_path,monkeypatch):
    rig=CallbackRig(recorder,tmp_path,monkeypatch)
    try:
        c=LiveHomeContract("test-live",profile=profile());a=CaptureAssembler(c)
        rig.node.home_contract=c
        packet=feed(a,0,0,q=1,target=2)
        packet["qp_action"]={"stamp_ns":EPOCH+200_000,"target_rad":[.5]*6}
        rig.send(1,"/capstone/action_context",M(data=json.dumps(packet)))
        np.testing.assert_allclose(rig.node.state[0],np.deg2rad([1]*6))
        np.testing.assert_allclose(rig.node.action[0],[.5]*6)
        source=rig.node.source_timing["action"]
        assert source["header_stamp"]["nanosec"]==200_000
        rig.send(2,"/joint_states",rig.joint(2,[99]*6))
        np.testing.assert_allclose(rig.node.state[0],np.deg2rad([1]*6))
    finally:rig.close()


def test_next_episode_can_start_at_verified_live_home_complete(recorder,tmp_path):
    h=HistoryHarness(recorder,tmp_path);p=profile();c=LiveHomeContract("test-live",profile=p)
    packet=envelope(-1,0,"HOME_COMPLETE",1)
    packet.update(arrival_verified=True,evidence_id="test-live")
    packet["sample"].update(simulated=False,evidence_id="test-live")
    h.push("action_context",-1,-.5,packet)
    aligner=recorder.HistoryAligner.prepare(h.options,h.history.snapshot(MONO),h.timing(0),c)
    assert aligner.context_guard.complete and not aligner.context_guard.home_seen


def test_home_endpoint_cannot_remain_complete_after_uncommanded_motion():
    c=LiveHomeContract("test-live",profile=profile());a=CaptureAssembler(c)
    feed(a,0,0);feed(a,50,1,"HOME_REQUESTED",1)
    for seq,ms in enumerate(range(100,451,50),2):feed(a,ms,seq,"QP_TELEOP",1)
    assert a.phase=="HOME_COMPLETE"
    with pytest.raises(HomeActionError,match="no_longer_settled"):
        feed(a,500,10,"QP_TELEOP",1,q=1)


def test_gui_marks_home_capture_without_declaring_task_success():
    from ai.record.test.test_gui_design import view_for
    from ai.record.gui_presentation import operator_fields
    view=view_for("RECORDING");view.update(home_capture=True,home_phase="HOME_COMPLETE",home_return_required=True)
    fields=operator_fields(view)
    assert "Home 포함" in fields["mode"]
    assert "실제 작업 결과" in fields["default_notice"]
    view["home_return_required"] = False
    assert "녹화를 종료" not in operator_fields(view)["default_notice"]
