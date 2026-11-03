"""Read-only Home collection source. Xbox/QP remains the only motion writer.

No SDK/ROS import or object construction until explicitly started with a measured
profile. One process does sequential RTDE getters and an independent ROS loop;
Driver/QP service callback stalls cannot freeze recorded q/qdes observations.
"""
import argparse
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

import numpy as np
from ai.home_action import (LiveHomeContract, HomeArrivalVerifier, HomeActionError,
    CONTEXT_TOPIC, LIVE_CONTEXT_TOPIC, JOINTS, vector)
from scripts.observe_indy7_home_readonly import snapshot, DeadlineRTDE


class CaptureAssembler:
    def __init__(self, contract):
        self.contract = contract
        self.stream_id, self.sequence = uuid.uuid4().hex, -1
        self.base = self.base_stream = self.base_sequence = None
        self.phase, self.generation, self.transition = "QP_TELEOP", None, 0
        self.arrival = None
        self.qp_action_ns = 0
        self.qp_target = None
        self.home_started_ns = self.completed_ns = 0

    def accept_base(self, base):
        from ai.home_action import validate_context
        validate_context(base)
        if self.base_stream is not None and (base["stream_id"] != self.base_stream or
                base["sequence"] != self.base_sequence+1):
            raise HomeActionError("base_qp_stream_changed_or_gap")
        self.base, self.base_stream, self.base_sequence = base, base["stream_id"], base["sequence"]

    def build(self, row, now_ns):
        if self.base is None:
            raise HomeActionError("stale_base_qp_context")
        calls = row["calls"]
        if set(calls) != {"get_control_state", "get_control_data", "get_motion_data", "get_servo_data"} or any("error" in v for v in calls.values()):
            raise HomeActionError("home_getter_failed")
        raw = calls["get_control_state"]
        data, motion, servo = [calls[k]["raw"] for k in ("get_control_data", "get_motion_data", "get_servo_data")]
        base_age=now_ns-self.base["stamp_ns"]
        # movej's synchronous RPC can pause QP heartbeats. During an already
        # observed Home, fresh RTDE remains authoritative for measured/desired
        # joints. This grants no command permission and never holds an old qdes.
        if (data["op_state"] not in (5,6,17) or servo.get("servo_actives") != [True]*6 or
                servo.get("brake_actives") != [False]*6 or
                len(servo.get("status_codes",[])) != 6 or
                any(int(s,16)&8 for s in servo.get("status_codes", ["0x8"]*6))):
            raise HomeActionError("home_controller_not_ready")
        scale = np.pi/180 if self.contract.profile["source_unit"] == "deg" else 1.
        q, qdes, velocity = [vector(raw["raw"].get(k))*scale for k in ("q", "qdes", "qdot")]
        settled_rearm=(data["op_state"]==17 and
            np.max(abs(q-vector(self.contract.profile["home_target_rad"])))<=np.deg2rad(.5) and
            np.max(abs(velocity))<=.01)
        if not 0<=base_age<=75_000_000 and not (self.arrival is not None and
                self.base["phase"].startswith("HOME_") and (data["op_state"] in (5,6) or settled_rearm)):
            raise HomeActionError("stale_base_qp_context")
        end = raw["response_system_ns"]
        self.sequence += 1
        previous = self.phase
        new_home = self.generation is not None and self.base["home_generation"] != self.generation
        initial_home = self.generation is None and self.base["phase"] != "QP_TELEOP"
        if new_home or initial_home:
            self.phase = "HOME_REQUESTED"
            self.arrival = HomeArrivalVerifier(self.contract.profile["home_target_rad"], raw["response_monotonic_ns"])
            self.qp_action_ns = 0
            self.home_started_ns = self.base["transition_ns"]
            self.completed_ns = 0
        elif self.arrival is not None:
            if self.qp_action_ns > self.home_started_ns and (self.phase != "HOME_COMPLETE" or self.qp_action_ns < self.completed_ns):
                raise HomeActionError("qp_command_before_home_settled")
            if self.phase == "HOME_COMPLETE" and self.qp_action_ns > self.completed_ns:
                self.arrival, self.phase = None, "QP_TELEOP"
            else:
                if self.phase=="HOME_COMPLETE" and (np.max(abs(q-vector(self.contract.profile["home_target_rad"])))>np.deg2rad(.5)
                        or np.max(abs(velocity))>.01 or motion.get("is_in_motion") is not False):
                    raise HomeActionError("home_endpoint_no_longer_settled")
                self.phase = self.arrival.update(now_ns=calls["get_servo_data"]["response_monotonic_ns"],
                    sample_ns=raw["response_monotonic_ns"], status_ns=calls["get_servo_data"]["response_monotonic_ns"],
                    q_rad=q, velocity_rad_s=velocity, in_motion=motion.get("is_in_motion"),
                    connected=True, healthy=True)
                if self.phase == "HOME_COMPLETE" and not self.completed_ns:self.completed_ns=end
        else:
            self.phase = "QP_TELEOP"
        self.generation = self.base["home_generation"]
        if self.base["phase"] in ("HOME_FAILED", "HOME_CANCELLED"):
            raise HomeActionError("base_home_failed_or_cancelled")
        if previous != self.phase or not self.transition:
            self.transition = end
        packet = {"version":1,"stream_id":self.stream_id,"sequence":self.sequence,
            "stamp_ns":now_ns,"phase":self.phase,"transition_ns":self.transition,
            "home_generation":self.generation,"last_unrecordable_home_ns":0,
            "arrival_verified":self.phase=="HOME_COMPLETE", "evidence_id":self.contract.evidence_id,
            "base_context":self.base,
            "qp_action":self.qp_target,
            "rpc_timing":{name:{k:v[k] for k in ("request_monotonic_ns","response_monotonic_ns",
                "request_system_ns","response_system_ns","rpc_elapsed_ns")} for name,v in calls.items()},
            "observed_state":{"position_rad":q.tolist(),"velocity_rad_s":velocity.tolist(),
                "response_system_ns":end,"header_semantics":"RTDE_response_availability_not_sensor_sample_time"},
            "sample":{"kind":"home_qdes","simulated":False,"evidence_id":self.contract.evidence_id,
                "unit":"rad","joint_names":JOINTS,"target_rad":qdes.tolist(),"response_ok":True,
                "application_status":raw.get("application_status","unavailable"),
                **{k:raw[k] for k in ("request_system_ns","response_system_ns","request_monotonic_ns",
                    "response_monotonic_ns","rpc_elapsed_ns")}}}
        self.contract.target(packet, now_ns, end)
        return packet


class ReaderProcess:
    def __init__(self, profile):
        self.profile, self.process = Path(profile).resolve(), None
    def start(self):
        root=Path(__file__).resolve().parents[2]
        self.process=subprocess.Popen([str(root/".venv/bin/python"),"-B","-m","ai.record.home_source",
                                      "--profile",str(self.profile)],cwd=root)
    def close(self):
        if self.process and self.process.poll() is None:
            import signal
            self.process.send_signal(signal.SIGINT)
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile",type=Path,required=True)
    args=parser.parse_args(argv)
    contract=LiveHomeContract.load(args.profile)
    from neuromeka import IndyDCP3
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy
    from std_msgs.msg import String
    from sensor_msgs.msg import JointState
    assembler=CaptureAssembler(contract)
    rclpy.init()
    node=Node("act_home_collection_source")
    qos=QoSProfile(depth=32,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
    pub=node.create_publisher(String,LIVE_CONTEXT_TOPIC,qos)
    fault=[None]
    def base(msg):
        try:assembler.accept_base(json.loads(msg.data))
        except (ValueError,KeyError,TypeError) as exc:fault[0]=str(exc)
    def action(msg):
        assembler.qp_action_ns=msg.header.stamp.sec*10**9+msg.header.stamp.nanosec
        values=dict(zip(msg.name,msg.position))
        if all(k in values for k in JOINTS):
            assembler.qp_target={"stamp_ns":assembler.qp_action_ns,"target_rad":[values[k] for k in JOINTS]}
    node.create_subscription(String,CONTEXT_TOPIC,base,qos)
    node.create_subscription(JointState,"/capstone/teleop_action",action,20)
    client=IndyDCP3(contract.profile["robot_ip"])
    class IdentityOnly:
        def __init__(self,stub,allowed):self.stub,self.allowed=stub,allowed
        def __getattr__(self,name):
            if name not in self.allowed:raise AttributeError(name)
            return lambda req:getattr(self.stub,name)(req,timeout=.2)
    client.device=IdentityOnly(client.device,{"GetDeviceInfo"})
    client.control=IdentityOnly(client.control,{"GetControlInfo"})
    client.rtde=DeadlineRTDE(client.rtde)
    try:
        if (client.get_device_info()["robot_serial"]!=contract.profile["robot_serial"] or
            client.get_control_info()["control_version"]!=contract.profile["controller_version"] or
            importlib.metadata.version("neuromeka")!=contract.profile["sdk_version"]):
            raise ValueError("home_profile_device_version_mismatch")
        deadline=time.monotonic_ns()
        polls_missed=0
        while rclpy.ok():
            # Drain both retained context and action callbacks; no control callbacks.
            for _ in range(40):rclpy.spin_once(node,timeout_sec=0)
            if time.monotonic_ns()<deadline:
                rclpy.spin_once(node,timeout_sec=min(.005,(deadline-time.monotonic_ns())/1e9));continue
            now=time.monotonic_ns()
            lateness=now-deadline
            if lateness>=50_000_000:
                polls_missed+=lateness//50_000_000
                deadline=now  # No duplicate/catch-up samples; Recorder grid remains strict.
            deadline+=50_000_000
            if assembler.base is None:continue
            row=snapshot(client)
            for _ in range(4):rclpy.spin_once(node,timeout_sec=0)
            if fault[0]:raise ValueError(fault[0])
            packet=assembler.build(row,node.get_clock().now().nanoseconds)
            packet.update(reader_poll_lateness_ns=lateness,reader_missed_polls=polls_missed)
            msg=String();msg.data=json.dumps(packet,allow_nan=False);pub.publish(msg)
    except KeyboardInterrupt:pass
    finally:
        for name in ("boot","control","device","config","rtde","cri"):
            getattr(client,name+"_channel").close()
        node.destroy_node();rclpy.try_shutdown()


if __name__=="__main__":main()
