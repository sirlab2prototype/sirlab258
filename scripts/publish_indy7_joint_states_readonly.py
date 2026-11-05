#!/usr/bin/env python3
"""Temporary getter-only /joint_states source for ACT Dry Run.

No command interfaces, Servo/mode change, stop, Home, or Writer lock are used.
Run only when no other joint-state publisher or controller SDK client is active.
"""
import argparse
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--connect-readonly', action='store_true')
    p.add_argument('--approve-readonly', required=True)
    p.add_argument('--ip', required=True)
    p.add_argument('--expected-serial', required=True)
    p.add_argument('--duration', type=float, default=60)
    a = p.parse_args(argv)
    if not a.connect_readonly or not a.approve_readonly.strip() or not 0 < a.duration <= 180:
        p.error('Explicit read-only approval and duration in (0,180] seconds required')
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import JointState
    from neuromeka import IndyDCP3
    from ai.execute.real_driver import DeadlineStub

    rclpy.init()
    node = Node('indy7_act_readonly_joint_states')
    client = None
    try:
        # Discovery must settle before rejecting another publisher.
        time.sleep(1)
        if node.get_publishers_info_by_topic('/joint_states'):
            raise RuntimeError('Another /joint_states publisher is active')
        client = IndyDCP3(a.ip)
        for group in ('boot', 'control', 'device', 'config', 'rtde', 'cri'):
            setattr(client, group, DeadlineStub(getattr(client, group), .2))
        identity = client.get_device_info().get('robot_serial')
        if identity != a.expected_serial:
            raise RuntimeError('Indy7 serial mismatch')
        pub = node.create_publisher(JointState, '/joint_states',
                                    QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE))
        next_tick = time.monotonic()
        end = next_tick + a.duration
        count = 0
        while time.monotonic() < end:
            if len(node.get_publishers_info_by_topic('/joint_states')) != 1:
                raise RuntimeError('Joint-state publisher conflict')
            data = client.get_control_data()
            motion = client.get_motion_data()
            q, qdot = data.get('q'), data.get('qdot')
            if (not isinstance(q, list) or not isinstance(qdot, list) or len(q) != 6 or len(qdot) != 6 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in q + qdot) or
                motion.get('is_in_motion') is not False):
                raise RuntimeError('Indy7 state invalid or moving; publication stopped')
            msg = JointState()
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.name = [f'joint{i}' for i in range(6)]
            msg.position = [math.radians(v) for v in q]
            msg.velocity = [math.radians(v) for v in qdot]
            pub.publish(msg)
            count += 1
            if count == 1 or count % 20 == 0:
                print(f'getters-only sample={count} q_deg={q}', flush=True)
            next_tick += .05
            time.sleep(max(0, next_tick-time.monotonic()))
        print(f'Published {count} real joint-state samples; no robot command sent', flush=True)
        return 0
    finally:
        if client:
            for group in ('boot', 'control', 'device', 'config', 'rtde', 'cri'):
                getattr(client, group+'_channel').close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
