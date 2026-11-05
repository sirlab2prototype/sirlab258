"""CPU only: actual dispatch/stop code with virtual time and Fake ROS/kernel.

No ROS initialization, SDK import/construction, GPU, network or device access.
All writable fixtures live in pytest's isolated tmp_path.
"""
from dataclasses import replace
import threading
from types import SimpleNamespace as M

import numpy as np
import pytest

from ai.execute import direct_link as dl
from ai.execute.real_driver import FeedbackCache
from ai.execute.real_session import RealSource, RealSession
from ai.execute.ros_adapters import RosIndy7Adapter, RealActivation, TRAJECTORY_TOPIC
from ai.execute.safety import SafetyError
from ai.execute.test.test_execution import Rig, MS
from ai.execute.test.test_real_execution import writer_module


@pytest.mark.parametrize('interruption', [None, 'joint_stale', 'status_stale', 'mode',
                                        'ready', 'motion', 'velocity', 'pre_request'])
def test_stop_stability_requires_continuous_valid_samples(tmp_path, interruption):
    rig = Rig(tmp_path, joint_stale_ms=1000, status_stale_ms=1000)
    rig.start()
    c = rig.controller
    c.is_mock = False
    rig.indy.stop_stable_ns = 250*MS
    c.stop('test_stop')
    rig.advance(50); c.poll()  # first fresh, idle, post-request sample
    assert c.stop_stable_since_ns == rig.clock() and not c.stop_confirmed
    original = rig.indy.feedback
    bad = [True]

    def feedback(now):
        fb = original(now)
        if not bad[0]: return fb
        changes = {
            'joint_stale': {'joint_received_ns': now-2000*MS},
            'status_stale': {'status_received_ns': now-2000*MS},
            'mode': {'mode': 'joint_absolute', 'controller_op_state': 17, 'teleop_method': 10},
            'ready': {'servo_ready': False},
            'motion': {'in_motion': True},
            'velocity': {'velocity': np.full(6, .02)},
            'pre_request': {'joint_received_ns': c.stop_started_ns},
        }
        return replace(fb, **changes[interruption])

    if interruption:
        rig.indy.feedback = feedback
        rig.advance(100); c.poll()
        assert c.stop_stable_since_ns is None and not c.stop_confirmed
        bad[0] = False
        rig.advance(50); c.poll()  # recovery starts a NEW stable interval
        recovered = rig.clock()
        assert c.stop_stable_since_ns == recovered
        rig.advance(100); c.poll()  # old start+250ms must NOT confirm
        assert c.state == 'STOPPING' and not c.stop_confirmed and c.lease.stream is not None
        rig.advance(150); c.poll()
    else:
        for _ in range(5): rig.advance(50); c.poll()
    assert c.state == 'STOPPED' and c.stop_confirmed and c.lease.stream is None
    assert c.revoked.is_set() and not rig.indy.commands
    with pytest.raises(SafetyError): c.begin()


def test_stop_stability_recovery_does_not_clear_fault_latch(tmp_path):
    rig = Rig(tmp_path); rig.start(); c = rig.controller
    c.is_mock = False; rig.indy.stop_stable_ns = 250*MS
    c.fault('original_fault')
    rig.advance(50); c.poll()
    rig.indy.ready = False
    rig.advance(50); c.poll()
    assert c.stop_stable_since_ns is None
    rig.indy.ready = True
    rig.advance(50); c.poll()
    for _ in range(5): rig.advance(50); c.poll()
    assert c.stop_confirmed and c.state == 'FAULT' and c.fault_latched
    with pytest.raises(SafetyError): rig.connect()


def gate_fixture(tmp_path, monkeypatch, clock):
    calls = {'link': 0, 'tcp': 0}
    def link(*args, **kwargs):
        calls['link'] += 1
        return {'interface': 'fake-wired'}
    def tcp(*args, **kwargs):
        calls['tcp'] += 1
        return []
    monkeypatch.setattr(dl, 'check_link', link)
    monkeypatch.setattr(dl, 'foreign_connections', tcp)
    profile = M(values={'robot_ip': '192.0.2.1', 'direct_link_interface': 'fake-wired',
                        'rpc_timeout_ms': 25, 'interlock_max_age_ms': 100})
    gate = dl.DirectLinkInterlock(profile, 'nonce', clock, supervising_operator='FAKE operator')
    gate.precheck()
    writer = writer_module().WriterLock('FAKE', tmp_path/'writer.lock')
    writer.acquire(); gate.bind_writer(writer)
    return gate, writer, calls


def test_authority_reads_are_fast_bounded_and_cannot_renew_expired_proof(tmp_path, monkeypatch):
    now = [1000*MS]
    gate, writer, calls = gate_fixture(tmp_path, monkeypatch, lambda: now[0])
    try:
        for offset in (0, 10, 50, 100):
            now[0] = (1000+offset)*MS
            assert gate.read()['observed_monotonic_ns'] == 1000*MS
        assert calls == {'link': 1, 'tcp': 1}  # repeated feedback has NO TCP/route IO
        now[0] += 1
        with pytest.raises(SafetyError, match='stale'): gate.read()
        with pytest.raises(SafetyError, match='revoked'): gate.refresh()
    finally: writer.close()


def test_authority_monitor_refresh_and_foreign_writer_trip(tmp_path, monkeypatch):
    now = [1000*MS]
    gate, writer, calls = gate_fixture(tmp_path, monkeypatch, lambda: now[0])
    try:
        now[0] += 50*MS; gate.refresh()
        assert gate.read()['observed_monotonic_ns'] == now[0]
        assert calls == {'link': 2, 'tcp': 2}
        monkeypatch.setattr(dl, 'foreign_connections', lambda *a: [{'foreign': True}])
        with pytest.raises(SafetyError, match='foreign'): gate.refresh()
        monkeypatch.setattr(dl, 'foreign_connections', lambda *a: [])
        with pytest.raises(SafetyError, match='revoked'): gate.read()
    finally: writer.close()


def test_slow_authority_refresh_does_not_block_stale_revocation(tmp_path, monkeypatch):
    now = [1000*MS]
    gate, writer, _ = gate_fixture(tmp_path, monkeypatch, lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    errors = []
    def blocked_tcp(*args):
        entered.set()
        assert release.wait(1)
        return []
    monkeypatch.setattr(dl, 'foreign_connections', blocked_tcp)
    def refresh():
        try: gate.refresh()
        except SafetyError as exc: errors.append(str(exc))
    worker = threading.Thread(target=refresh)
    try:
        worker.start(); assert entered.wait(1)
        now[0] += 101*MS
        with pytest.raises(SafetyError, match='stale'): gate.read()
        release.set(); worker.join(1)
        assert not worker.is_alive() and errors
        with pytest.raises(SafetyError, match='revoked'): gate.read()
    finally:
        release.set(); worker.join(1); writer.close()


class FakeNode:
    def __init__(self, clock):
        self.clock, self.service_queries, self.messages = clock, 0, []
        self.services = ['/capstone/real_gripper/open', '/capstone/real_gripper/grasp']
    def get_name(self): return 'fake_act_client'
    def get_namespace(self): return '/'
    def get_node_names(self):
        self.clock.advance(.1)
        return ['indy_driver', 'mark7_node', self.get_name()]
    def get_publishers_info_by_topic(self, topic):
        self.clock.advance(.1)
        name = ('indy_driver' if topic == '/joint_states' else
                self.get_name() if topic == TRAJECTORY_TOPIC else 'mark7_node')
        return [M(node_name=name, node_namespace='/')]
    def get_service_names_and_types(self):
        self.service_queries += 1; self.clock.advance(.1)
        return [(name, []) for name in self.services]
    def get_clock(self): return M(now=lambda: M(to_msg=lambda: M()))
    def publish(self, msg): self.messages.append(msg)


def test_controller_adapter_real_source_dispatch_keeps_20ms_budget(tmp_path, monkeypatch):
    rig = Rig(tmp_path, reviewed_for_real=True, review_record='FAKE')
    rig.start(); robot = rig.indy
    gate, writer, calls = gate_fixture(tmp_path, monkeypatch, rig.clock)
    source = object.__new__(RealSource)  # no ROS __init__, SDK or threads
    source.feedback_diagnostics = threading.local()
    source.node = node = FakeNode(rig.clock)
    source.initial_feedback = None; source.feedback_samples = set(); source.max_joint_change_rad = 0.
    cache = FeedbackCache.__new__(FeedbackCache)
    cache.interlock = gate; cache.sdk_estop = None; cache.lock = threading.Lock()
    cache.error = None; cache.value = None
    reads = []
    def get(now):
        reads.append(now); cache.value = robot.feedback(now)
        return FeedbackCache.get(cache, now)
    source.driver = M(cache=M(get=get))
    adapter = RosIndy7Adapter(node, source.feedback,
        RealActivation(True, 'FAKE', 'FAKE stop', rig.clock()+10000*MS), clock=rig.clock, command_token='nonce')
    adapter.publisher = node; adapter.permit = rig.controller.revoked; adapter.config = rig.config
    adapter.trajectory_type = lambda: M(header=M())
    adapter.point_type = lambda: M(time_from_start=M())
    rig.controller.indy = adapter
    try:
        rig.advance(50)
        record = rig.action(.001); record['schedule']['deadline_monotonic_ns'] -= 4*MS
        rig.controller.consume(record)
        assert len(node.messages) == 1 and rig.controller.submitted == 1
        # pre-guard poll, adapter pre-send, final ACK/tracking poll. No extra
        # Controller feedback lookup between poll and Guard.
        assert len(reads) == 3 and node.service_queries == 3
        assert calls == {'link': 1, 'tcp': 1}
        timing = next(r for r in rig.events if r['event'] == 'execution_dispatch_timing')
        published = timing['adapter']['published_ns']
        assert published <= record['schedule']['deadline_monotonic_ns']+20*MS
        assert published-timing['stages']['entered_ns'] < 2*MS
        assert timing['adapter']['graph_completed_ns'] <= published
        assert 'authority_and_cache_completed_ns' in timing['adapter']['provider']
    finally:
        if rig.lease.stream: rig.lease.stream.close()
        writer.close()


def test_adapter_timing_retains_failed_stage_and_deadline(tmp_path):
    rig = Rig(tmp_path, reviewed_for_real=True, review_record='FAKE'); rig.start()
    node = FakeNode(rig.clock)
    def slow_feedback(now):
        rig.clock.advance(21)
        return rig.indy.feedback(rig.clock())
    adapter = RosIndy7Adapter(node, slow_feedback,
        RealActivation(True, 'FAKE', 'FAKE stop', rig.clock()+10000*MS), clock=rig.clock)
    adapter.publisher = node; adapter.permit = rig.controller.revoked; adapter.config = rig.config
    adapter.trajectory_type = lambda: M(header=M())
    adapter.point_type = lambda: M(time_from_start=M())
    try:
        expires = rig.clock()+20*MS
        with pytest.raises(SafetyError, match='deadline_expired'):
            adapter.send_absolute(np.zeros(6), expires_ns=expires)
        assert not node.messages
        assert adapter.last_dispatch_timing['finished_ns'] > expires
        assert 'feedback_completed_ns' in adapter.last_dispatch_timing
        assert 'published_ns' not in adapter.last_dispatch_timing
    finally: rig.lease.stream.close()


@pytest.mark.parametrize('case', ['missing_open', 'missing_grasp', 'public_open', 'public_grasp'])
def test_single_service_snapshot_keeps_all_mark7_graph_checks(tmp_path, case):
    rig = Rig(tmp_path); rig.start()
    source = object.__new__(RealSource)
    source.node = node = FakeNode(rig.clock)
    source.driver = M(cache=M(get=rig.indy.feedback))
    if case.startswith('missing'):
        node.services.remove('/capstone/real_gripper/'+case.split('_')[1])
    else:
        node.services.append('/gripper/'+case.split('_')[1])
    try:
        with pytest.raises(SafetyError, match='Mark7'):
            source.feedback(rig.clock())
        assert node.service_queries == 1
    finally: rig.lease.stream.close()


@pytest.mark.parametrize('submitted,acknowledged', [(0, 0), (2, 0), (2, 1)])
def test_real_finish_keeps_controller_and_sdk_counts_separate(tmp_path, submitted, acknowledged):
    rig = Rig(tmp_path); rig.start(); rig.controller.submitted = submitted
    source = M(feedback_samples=set(), max_joint_change_rad=0.,
               command_counts=lambda: {'sdk_command_attempts': submitted, 'sdk_acknowledged_actions': acknowledged})
    profile = M(values={'initial_joints_rad': [0]*6, 'initial_tolerance_rad': .01,
                        'initial_pose_is_project_home': True})
    result = RealSession(source, rig.controller, profile, 'FAKE', 'nonce', tmp_path).finish('test')
    assert result['controller_submitted_actions'] == submitted
    assert result['sdk_acknowledged_actions'] == acknowledged
    assert not result['physical_execution_verified']
