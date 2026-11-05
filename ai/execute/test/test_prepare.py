"""Launcher tests: fake children only, no ROS or hardware."""
import fcntl
from pathlib import Path
import signal
import sys
import types
from types import SimpleNamespace as M
import pytest
from ai.execute import prepare as p


def test_commands():
    camera, mark = p.commands('/dev/FAKE')
    assert camera == ('cameras', [str(p.ROOT/'scripts/bin/camera'), 'show_camera:=false'])
    assert mark[0] == 'mark7'
    for value in ('publish_command_state:=true', 'device:=/dev/FAKE',
                  '/gripper/open:=/capstone/real_gripper/open',
                  '/gripper/grasp:=/capstone/real_gripper/grasp'):
        assert value in mark[1]


@pytest.mark.parametrize('failure', [None, 'spawn', 'exit', 'busy'])
def test_lifecycle(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    started = []; closed = []
    class Children:
        def start(self, name, argv):
            if name == 'mark7' and failure == 'spawn': raise OSError('spawn failed')
            started.append(name)
        def failures(self): return ['cameras child exited (1)'] if failure == 'exit' else []
        def close(self): closed.extend(started)
    def serial(device):
        if failure == 'busy': raise RuntimeError('serial busy')
    monkeypatch.setattr(p, 'Children', Children)
    monkeypatch.setattr(p, 'serial_available', serial)
    monkeypatch.setattr(p, 'initialize_gripper', lambda children: None)
    monkeypatch.setattr(p.time, 'sleep', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert p.main([]) == (0 if failure is None else 1)
    assert started == ([] if failure == 'busy' else ['cameras'] if failure == 'spawn' else ['cameras', 'mark7'])
    assert closed == started


def test_duplicate_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    folder = tmp_path/'.local/bin/.sirlab258-camera-locks'; folder.mkdir(parents=True)
    with (folder/'act-prepare.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(p, 'serial_available', lambda _: pytest.fail('must not start'))
        assert p.main([]) == 2


def test_cleanup_only_owned_groups(monkeypatch):
    killed = []; launched = []
    def launch(argv, **kwargs):
        assert kwargs['start_new_session'] is True
        child = M(pid=100+len(launched), poll=lambda: None, wait=lambda timeout: None)
        launched.append(child); return child
    monkeypatch.setattr(p.subprocess, 'Popen', launch)
    monkeypatch.setattr(p.os, 'killpg', lambda pid, sig: killed.append((pid, sig)))
    children = p.Children()
    children.start('cameras', ['FAKE']); children.start('mark7', ['FAKE'])
    children.close()
    assert killed == [(101, signal.SIGINT), (100, signal.SIGINT)]


@pytest.mark.parametrize('outcome', ['ok', 'service_failure', 'response_timeout',
                                     'wrong_state', 'no_service'])
def test_open_initialization_once(monkeypatch, outcome):
    calls = []; events = []; callback = None
    class Future:
        def done(self): return outcome != 'response_timeout'
        def result(self):
            return M(success=outcome != 'service_failure', message='serial write failed')
    class Client:
        def service_is_ready(self): return outcome != 'no_service'
        def call_async(self, request):
            calls.append(request)
            return Future()
    class Node:
        def create_subscription(self, kind, topic, receive, qos):
            nonlocal callback
            assert topic == '/capstone/gripper_state'
            callback = receive
        def create_client(self, kind, service):
            assert service == '/capstone/real_gripper/open'
            return Client()
        def destroy_node(self): events.append('destroy')
    ros = types.ModuleType('rclpy')
    ros.init = lambda: events.append('init')
    ros.shutdown = lambda: events.append('shutdown')
    ros.create_node = lambda name: Node()
    def spin_once(node, timeout_sec):
        if calls and outcome in ('ok', 'wrong_state'):
            callback(M(data=0.0 if outcome == 'ok' else 1.0))
    ros.spin_once = spin_once
    qos = types.ModuleType('rclpy.qos')
    qos.QoSProfile = lambda **kwargs: kwargs
    qos.ReliabilityPolicy = M(RELIABLE='reliable')
    qos.DurabilityPolicy = M(TRANSIENT_LOCAL='transient_local')
    std_msgs = types.ModuleType('std_msgs')
    std_msgs.msg = types.ModuleType('std_msgs.msg')
    std_msgs.msg.Float32 = type('Float32', (), {})
    std_srvs = types.ModuleType('std_srvs')
    std_srvs.srv = types.ModuleType('std_srvs.srv')
    std_srvs.srv.Trigger = type('Trigger', (), {'Request': type('Request', (), {})})
    for name, module in [('rclpy', ros), ('rclpy.qos', qos), ('std_msgs', std_msgs),
                         ('std_msgs.msg', std_msgs.msg), ('std_srvs', std_srvs),
                         ('std_srvs.srv', std_srvs.srv)]:
        monkeypatch.setitem(sys.modules, name, module)
    # Advance timeout without waiting for real ROS or hardware.
    tick = iter(range(1000))
    monkeypatch.setattr(p.time, 'monotonic', lambda: next(tick))
    children = M(failures=lambda: [])
    if outcome == 'ok':
        p.initialize_gripper(children)
    else:
        with pytest.raises(RuntimeError, match={
            'service_failure': 'serial write failed',
            'response_timeout': 'response timed out after one request',
            'wrong_state': '0.0 was not received',
            'no_service': 'service not ready',
        }[outcome]):
            p.initialize_gripper(children)
    assert len(calls) == (0 if outcome == 'no_service' else 1)
    assert events == ['init', 'destroy', 'shutdown']


def test_open_runs_by_default_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(p, 'serial_available', lambda _: None)
    monkeypatch.setattr(p, 'Children', lambda: M(start=lambda *args: None,
                                                 failures=lambda: [], close=lambda: None))
    opened = []
    monkeypatch.setattr(p, 'initialize_gripper', lambda children: opened.append(True))
    monkeypatch.setattr(p.time, 'sleep', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert p.main([]) == 0
    assert opened == [True]
    output = capsys.readouterr().out
    assert output.index('physically move') < output.index('initial gripper state registered')


def test_open_failure_does_not_report_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(p, 'serial_available', lambda _: None)
    closed = []
    monkeypatch.setattr(p, 'Children', lambda: M(start=lambda *args: None,
                                                 failures=lambda: [], close=lambda: closed.append(True)))
    def fail(children):
        raise RuntimeError('Open service failed: serial write failed')
    monkeypatch.setattr(p, 'initialize_gripper', fail)
    assert p.main([]) == 1
    assert closed == [True]
    output = capsys.readouterr().out
    assert 'Open service failed: serial write failed' in output
    assert 'initial gripper state registered' not in output
