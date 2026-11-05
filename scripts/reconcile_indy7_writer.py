#!/usr/bin/env python3
"""Manually reconcile a stale QP/ACT writer after fresh independent stop evidence.

The capture must come from inspect_indy7_real_readonly.py. This script makes
no robot connection or control call and never deletes the lock inode.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'ros2_ws/src/capstone_teleop'))
from capstone_teleop.writer_lock import LOCK_PATH, WriterLock
from ai.execute.authority import CommandLease

ACT_LEASE_PATH = ROOT / 'tmp/runtime/act_real_session_lease.json'


def verify_capture(path, robot_ip):
    raw = path.read_bytes()
    record = json.loads(raw)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(record['created_utc'])).total_seconds()
    if not 0 <= age <= 60 or record.get('capture_valid') is not True:
        raise ValueError('Stop capture is stale or invalid')
    if record.get('robot_ip') != robot_ip or len(record.get('samples', [])) != 3:
        raise ValueError('Stop capture robot or sample count mismatch')
    if not record.get('identity', {}).get('robot_serial'):
        raise ValueError('Robot identity missing')
    for sample in record['samples']:
        data = sample['raw']['get_control_data']
        motion = sample['raw']['get_motion_data']
        qdot = data.get('qdot')
        if (data.get('op_state') != 5 or motion.get('is_in_motion') is not False or
            not isinstance(qdot, list) or len(qdot) != 6 or
            any(type(v) not in (int, float) or not math.isfinite(v) or
                abs(v) > math.degrees(0.01) for v in qdot)):
            raise ValueError('Independent controller feedback does not confirm stopped')
    return hashlib.sha256(raw).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', required=True, type=Path)
    p.add_argument('--robot-ip', required=True)
    p.add_argument('--operator', required=True, help='Onsite person who checked physical stop and remote writers')
    p.add_argument('--physical-stop-and-writers-checked', action='store_true')
    a = p.parse_args(argv)
    if not a.physical_stop_and_writers_checked or not a.operator.strip():
        p.error('Onsite physical stop and all other writer connections must be checked')
    try:
        evidence = verify_capture(a.capture, a.robot_ip)
        sockets = subprocess.run(['ss', '-H', '-tn'], check=True, capture_output=True, text=True).stdout
        if any(a.robot_ip in line for line in sockets.splitlines()):
            raise RuntimeError('A local TCP connection to the robot is still present')
        lease_reconciled = CommandLease.reconcile_verified_stop(
            path=ACT_LEASE_PATH, operator=a.operator.strip(), evidence_sha256=evidence)
        WriterLock.reconcile_verified_stop(path=LOCK_PATH, operator=a.operator.strip(), evidence_sha256=evidence)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f'Writer remains unresolved: {exc}', file=sys.stderr)
        return 2
    print(f'Writer and ACT lease reconciled (lease_changed={lease_reconciled}) using capture SHA256 {evidence}; no robot command sent')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
