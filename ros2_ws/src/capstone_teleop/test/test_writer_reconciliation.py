"""Offline writer recovery regression; no ROS or SDK connection."""
import importlib.util
import ast
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
from types import SimpleNamespace
import time
import pytest
import numpy as np

ROOT = Path(__file__).resolve().parents[4]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reconciliation_preserves_inode_and_requires_unheld_flock(tmp_path):
    module = load(ROOT/'ros2_ws/src/capstone_teleop/capstone_teleop/writer_lock.py', 'test_writer_lock')
    path = tmp_path/'lock'
    writer = module.WriterLock('QP', path)
    writer.acquire()
    with pytest.raises(BlockingIOError):
        module.WriterLock.reconcile_verified_stop(path=path, operator='site', evidence_sha256='hash')
    writer.close(confirmed=False)
    inode = path.stat().st_ino
    with pytest.raises(ValueError):
        module.WriterLock.reconcile_verified_stop(path=path, operator='', evidence_sha256='hash')
    module.WriterLock.reconcile_verified_stop(path=path, operator='site', evidence_sha256='hash')
    assert path.stat().st_ino == inode
    record = json.loads(path.read_text())
    assert record['status'] == 'released' and record['reconciled_from'] == 'quarantined'
    assert record['stop_evidence_sha256'] == 'hash'
    writer = module.WriterLock('QP-new', path)
    writer.acquire()
    writer.close(confirmed=False)
    assert json.loads(path.read_text())['stop_evidence_sha256'] == 'hash'


def test_old_held_state_requires_reconciliation(tmp_path):
    module = load(ROOT/'ros2_ws/src/capstone_teleop/capstone_teleop/writer_lock.py', 'test_writer_lock2')
    path = tmp_path/'lock'
    path.write_text(json.dumps({'owner':'QP', 'status':'held'}))
    with pytest.raises(RuntimeError, match='Unresolved'):
        module.WriterLock('QP-new', path).acquire()
    module.WriterLock.reconcile_verified_stop(path=path, operator='site', evidence_sha256='hash')
    writer = module.WriterLock('QP-new', path)
    writer.acquire()
    writer.close()


def test_qp_shutdown_releases_only_after_fresh_stable_feedback():
    path = ROOT/'ros2_ws/src/capstone_teleop/capstone_teleop/qp_teleop.py'
    cls = next(node for node in ast.parse(path.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == 'QPTeleop')
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'shutdown')
    scope = {'time':time, 'math':math}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 str(path), 'exec'), scope)
    logs = SimpleNamespace(info=lambda *args: None, error=lambda *args: None)

    def qp(stop_result, moving=False):
        indy = SimpleNamespace(
            stop_teleop=lambda: stop_result,
            get_control_data=lambda: {'op_state':5, 'qdot':[0]*6},
            get_motion_data=lambda: {'is_in_motion':moving})
        return SimpleNamespace(auto_arm_pending=False, home_rearm_pending=False,
                               armed=False, arming=False, arm_token=0,
                               waiting_for_fresh_joint=False, relative_joint=np.ones(6),
                               motion_profile=SimpleNamespace(reset=lambda:None), indy=indy,
                               get_logger=lambda: logs)

    assert scope['shutdown'](qp({'response':{'code':'0'}})) is True
    assert scope['shutdown'](qp({'code':'0', 'msg':''})) is True
    assert scope['shutdown'](qp({'response':{'code':'7'}})) is False
    assert scope['shutdown'](qp({'code':'7'})) is False
    assert scope['shutdown'](qp({'response':{'code':'0'}}, moving=True)) is False


def test_manual_reconciliation_requires_fresh_idle_controller_capture(tmp_path):
    tool = load(ROOT/'scripts/reconcile_indy7_writer.py', 'test_reconcile_tool')
    path = tmp_path/'capture.json'
    sample = {'raw': {'get_control_data': {'op_state':5, 'qdot':[0]*6},
                      'get_motion_data': {'is_in_motion':False}}}
    record = {'created_utc':datetime.now(timezone.utc).isoformat(),
              'capture_valid':True, 'robot_ip':'192.0.2.1',
              'identity':{'robot_serial':'TEST'}, 'samples':[sample]*3}
    path.write_text(json.dumps(record))
    assert len(tool.verify_capture(path, '192.0.2.1')) == 64
    record['samples'][1] = {'raw': {'get_control_data':{'op_state':17, 'qdot':[0]*6},
                                   'get_motion_data':{'is_in_motion':False}}}
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='stopped'):
        tool.verify_capture(path, '192.0.2.1')
    record['samples'][1] = sample
    record['created_utc'] = (datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='stale'):
        tool.verify_capture(path, '192.0.2.1')


def test_manual_reconciliation_clears_abandoned_act_lease_and_writer_together(tmp_path, monkeypatch):
    tool = load(ROOT/'scripts/reconcile_indy7_writer.py', 'test_reconcile_act_tool')
    writer_path, lease_path = tmp_path/'writer', tmp_path/'lease'
    writer = tool.WriterLock('ACT', writer_path)
    writer.acquire(); writer.close(confirmed=False)
    lease = tool.CommandLease(lease_path)
    lease.acquire('old-session'); lease.quarantine()
    with pytest.raises(BlockingIOError):
        tool.CommandLease.reconcile_verified_stop(path=lease_path, operator='site', evidence_sha256='hash')
    lease.stream.close()  # Simulate the old process exiting with a quarantined record.
    writer_inode, lease_inode = writer_path.stat().st_ino, lease_path.stat().st_ino
    sample = {'raw': {'get_control_data': {'op_state':5, 'qdot':[0]*6},
                      'get_motion_data': {'is_in_motion':False}}}
    capture = tmp_path/'capture.json'
    capture.write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),
        'capture_valid':True,'robot_ip':'192.0.2.1',
        'identity':{'robot_serial':'TEST'},'samples':[sample]*3}))
    monkeypatch.setattr(tool, 'LOCK_PATH', writer_path)
    monkeypatch.setattr(tool, 'ACT_LEASE_PATH', lease_path)
    monkeypatch.setattr(tool.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=''))
    assert tool.main(['--capture',str(capture),'--robot-ip','192.0.2.1',
                      '--operator','site','--physical-stop-and-writers-checked']) == 0
    assert writer_path.stat().st_ino == writer_inode and lease_path.stat().st_ino == lease_inode
    assert json.loads(writer_path.read_text())['status'] == 'released'
    assert json.loads(lease_path.read_text())['status'] == 'released'
    new_writer = tool.WriterLock('ACT-new', writer_path); new_writer.acquire(); new_writer.close()
    new_lease = tool.CommandLease(lease_path); new_lease.acquire('new-session')
    new_lease.release(confirmed_stopped=True)
