"""Deterministic cache-replacement interleaving, no ROS/SDK/devices."""
from dataclasses import replace
import pytest
from ai.execute.test.test_execution import Rig, MS
from ai.execute.controller import UserApproval
from ai.execute.safety import SafetyError


@pytest.mark.parametrize('site', ['connect', 'arm', 'begin', 'poll', 'watchdog', 'stop'])
def test_cache_updated_during_feedback_lookup_is_not_future(tmp_path, site):
    rig = Rig(tmp_path); c = rig.controller
    approval = UserApproval(rig.session, rig.checkpoint, 'FAKE', 'mock', rig.clock(), rig.clock()+10000*MS)
    if site == 'arm': rig.connect()
    elif site == 'begin':
        rig.connect(); c.arm(approval); c.poll()
    elif site != 'connect': rig.start()
    if site == 'stop': c.stop('test')
    original = rig.indy.feedback
    def replacement(now):
        # Reader captures t, then the monitor publishes a new cached status at
        # t+1ms before lookup returns. Status is fresh at actual check time.
        rig.clock.advance(1)
        return original(rig.clock())
    rig.indy.feedback = replacement
    try:
        if site == 'connect': rig.connect(); assert c.state == 'READY'
        elif site == 'arm': c.arm(approval); assert c.mode_future is not None
        elif site == 'begin': c.begin(); assert c.state == 'RUNNING'
        elif site == 'poll': c.poll(); assert c.state == 'RUNNING'
        elif site == 'watchdog': c.watchdog_check(); assert c.state == 'RUNNING'
        else: c.poll(); assert c.stop_confirmed
        assert not c.fault_latched
    finally:
        if rig.lease.stream: rig.lease.stream.close()


def test_actual_expired_status_still_faults_and_logs_signed_age(tmp_path):
    rig = Rig(tmp_path); rig.start(); c = rig.controller
    original = rig.indy.feedback
    rig.indy.feedback = lambda now: replace(original(now), status_received_ns=now-101*MS)
    try:
        c.poll()
        assert c.fault_latched and c.revoked.is_set()
        row = next(r for r in rig.events if r['event'] == 'execution_feedback_rejected')
        assert row['status_age_ns'] == 101*MS
    finally:
        if rig.lease.stream: rig.lease.stream.close()


def test_truly_future_status_still_rejected(tmp_path):
    rig = Rig(tmp_path)
    fb = rig.indy.feedback(rig.clock())
    with pytest.raises(SafetyError, match='stale_controller_status'):
        replace(fb, status_received_ns=rig.clock()+MS).require(rig.clock(), rig.config)
