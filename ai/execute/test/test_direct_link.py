"""Supervised physical-link authority with fake kernel files and Fake RPC peers."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
from types import SimpleNamespace as M
import pytest
from ai.execute import direct_link as dl
from ai.execute.real_profile import make_interlock,precheck_interlock
from ai.execute.safety import SafetyConfig,SafetyError
from ai.execute.test.test_commissioning import captures,ROOT
from ai.execute.test.test_real_execution import writer_module


def fake_link(tmp_path):
    root=tmp_path/'sys';dev=root/'enp1s0';dev.mkdir(parents=True)
    (dev/'device').mkdir();(dev/'type').write_text('1');(dev/'carrier').write_text('1')
    route=[{'dst':'192.0.2.1','dev':'enp1s0','prefsrc':'192.0.2.2'}]
    def run(cmd,**kw):
        assert cmd==['ip','-j','route','get','192.0.2.1'] and kw['timeout']<=.05
        return M(stdout=json.dumps(route))
    return root,dev,route,run


@pytest.mark.parametrize('case',['ok','gateway','wrong_device','wifi','bridge','master','carrier','virtual'])
def test_live_direct_physical_link_required(tmp_path,case):
    root,dev,route,run=fake_link(tmp_path)
    if case=='gateway':route[0]['gateway']='192.0.2.254'
    elif case=='wrong_device':route[0]['dev']='different'
    elif case in ('wifi','bridge','master'):(dev/('wireless' if case=='wifi' else case)).mkdir()
    elif case=='carrier':(dev/'carrier').write_text('0')
    elif case=='virtual':(dev/'device').rmdir()
    check=lambda:dl.check_link('192.0.2.1','enp1s0',sys_net=root,run=run)
    if case=='ok':assert check()['interface']=='enp1s0'
    else:
        with pytest.raises(SafetyError):check()


def fake_proc(tmp_path):
    proc=tmp_path/'proc';(proc/'self/fd').mkdir(parents=True);(proc/'net').mkdir()
    os.symlink('socket:[100]',proc/'self/fd/4')
    header='header\n'
    (proc/'net/tcp').write_text(header)
    (proc/'net/tcp6').write_text(header)
    return proc


def row(address,inode,state='01'):
    return f' 0: 00000000:0000 {address}:4E21 {state} 0:0 00:0 0 1000 0 {inode}\n'


def test_foreign_tcp_clients_even_getter_sessions_are_rejected(tmp_path):
    proc=fake_proc(tmp_path)
    (proc/'net/tcp').write_text('header\n'+row('010200C0',100))
    assert not dl.foreign_connections('192.0.2.1',proc=proc)
    with (proc/'net/tcp').open('a') as f:f.write(row('010200C0',101))
    assert dl.foreign_connections('192.0.2.1',proc=proc)==[{'table':'tcp','state':'01','inode':'101'}]
    (proc/'net/tcp').write_text('header\n'+row('010200C0',0,'06'))
    assert not dl.foreign_connections('192.0.2.1',proc=proc)
    # IPv4-mapped IPv6 socket for the same controller must also count.
    (proc/'net/tcp6').write_text('header\n'+row('0000000000000000FFFF0000010200C0',102))
    assert dl.foreign_connections('192.0.2.1',proc=proc)[0]['inode']=='102'
    (proc/'net/tcp').unlink()
    with pytest.raises(SafetyError,match='unknown'):dl.foreign_connections('192.0.2.1',proc=proc)


def direct_profile(tmp_path):
    p,_=captures(tmp_path)
    p.values.update(authority_mode='supervised_direct_link',direct_link_interface='enp1s0',
                    interlock_file=None,interlock_issuer=None)
    return p


def test_native_direct_profile_needs_no_external_heartbeat_but_requires_estop(tmp_path):
    p=direct_profile(tmp_path)
    cfg=replace(SafetyConfig.load(ROOT/'ai/execute/config/mock.json'),reviewed_for_real=True,review_record='FAKE')
    p.require(cfg)
    p.values['sdk_estop']=None
    with pytest.raises(SafetyError,match='qualified_native'):p.require(cfg)


@pytest.mark.parametrize('case',['foreign','link','lock_owner','lock_replaced','closed_lock'])
def test_authority_loss_revokes_session_and_never_auto_resumes(tmp_path,monkeypatch,case):
    p=direct_profile(tmp_path);bad=[False];now=[1000]
    def link(*a,**kw):
        if case=='link' and bad[0]:raise SafetyError('cable_unplugged')
        return {'interface':'enp1s0'}
    monkeypatch.setattr(dl,'check_link',link)
    monkeypatch.setattr(dl,'foreign_connections',lambda *a:[{'foreign':True}] if case=='foreign' and bad[0] else [])
    gate=dl.DirectLinkInterlock(p,'nonce',lambda:now[0],supervising_operator='onsite')
    gate.precheck()
    writer=writer_module().WriterLock('act_test',tmp_path/'lock');writer.acquire();gate.bind_writer(writer)
    try:
        assert gate.read()['all_other_writers_blocked'] is True
        bad[0]=True;now[0]+=200_000_000
        if case=='lock_owner':writer.path.write_text(json.dumps({'owner':'foreign','status':'held'}))
        elif case=='lock_replaced':writer.path.unlink();writer.path.write_text('{}')
        elif case=='closed_lock':writer.close()
        if case in ('foreign','link'):
            # The existing Driver monitor refreshes expensive kernel evidence.
            with pytest.raises(SafetyError):gate.refresh()
        with pytest.raises(SafetyError):gate.read()
        bad[0]=False
        with pytest.raises(SafetyError,match='revoked'):gate.read()
    finally:writer.close()


def test_factory_and_precheck_do_not_claim_exclusion_before_owned_lock(tmp_path,monkeypatch):
    p=direct_profile(tmp_path)
    monkeypatch.setattr(dl,'check_link',lambda *a,**k:{'interface':'enp1s0'})
    monkeypatch.setattr(dl,'foreign_connections',lambda *a:[])
    gate=make_interlock(p,'nonce',supervising_operator='onsite')
    precheck_interlock(gate)
    with pytest.raises(SafetyError,match='lock_required'):gate.read()


def test_real_direct_trial_defaults_to_five_seconds_and_rejects_longer(tmp_path,monkeypatch):
    import ai.execute.main as cli
    p=direct_profile(tmp_path)
    cfg=replace(SafetyConfig.load(ROOT/'ai/execute/config/mock.json'),reviewed_for_real=True,review_record='FAKE')
    cfg_path=tmp_path/'safe.json';cfg_path.write_text(json.dumps(asdict(cfg)))
    hw=tmp_path/'hw.json';hw.write_text(json.dumps(p.values))
    calls=[];monkeypatch.setattr(cli,'inference_main',lambda args,**kw:calls.append(args) or 0)
    args=['--mode','real','--checkpoint','FAKE','--config',str(cfg_path),'--hardware-profile',str(hw),'--approve-real','onsite']
    assert cli.main(args)==0
    assert calls[0][-2:]==['--duration','5.0']
    for duration in ('6','nan','0','inf'):
        with pytest.raises(SystemExit):cli.main([*args,'--duration',duration])
    assert len(calls)==1


def test_foreign_sdk_detection_invokes_existing_watchdog_stop(tmp_path,monkeypatch):
    from ai.execute.test.test_execution import Rig
    p=direct_profile(tmp_path);bad=[False]
    monkeypatch.setattr(dl,'check_link',lambda *a,**k:{'interface':'enp1s0'})
    monkeypatch.setattr(dl,'foreign_connections',lambda *a:[{'foreign':True}] if bad[0] else [])
    rig=Rig(tmp_path/'rig')
    gate=dl.DirectLinkInterlock(p,'nonce',rig.clock,supervising_operator='onsite')
    gate.precheck();writer=writer_module().WriterLock('act',tmp_path/'lock');writer.acquire();gate.bind_writer(writer)
    original=rig.indy.feedback
    def feedback(now):
        gate.read()
        return original(now)
    rig.indy.feedback=feedback
    try:
        rig.start();bad[0]=True
        with pytest.raises(SafetyError,match='foreign'):gate.refresh()
        rig.controller.watchdog_check()
        assert rig.controller.revoked.is_set() and rig.indy.stop_requests==1
        assert not rig.indy.commands
        bad[0]=False
        with pytest.raises(SafetyError,match='revoked'):gate.read()
    finally:
        rig.controller.close_watchdog()
        if rig.lease.stream:rig.lease.stream.close()
        writer.close()


def test_direct_mode_binds_owned_lock_in_actual_guarded_driver_callbacks(tmp_path,monkeypatch):
    from ai.execute import real_driver as rd
    from ai.execute.test.test_real_execution import test_guarded_official_Driver_denies_foreign_command_and_uses_absolute_degrees
    capture_dir=tmp_path/'captures';capture_dir.mkdir();p=direct_profile(capture_dir)
    monkeypatch.setattr(dl,'check_link',lambda *a,**k:{'interface':'enp1s0'})
    monkeypatch.setattr(dl,'foreign_connections',lambda *a:[])
    original=rd.create_driver;bound=[]
    def create(profile,safety,interlock,nonce,stop_file,log):
        gate=make_interlock(p,nonce,supervising_operator='onsite');precheck_interlock(gate)
        driver=original(p,safety,gate,nonce,stop_file,log)
        assert gate.writer is driver.writer
        assert gate.read()['authority_scope'].startswith('supervised direct cable')
        bound.append(True)
        return driver
    monkeypatch.setattr(rd,'create_driver',create)
    # Reuse existing Fake SDK/ROS fixtures and rad->degree/foreign-command/stop assertions.
    test_guarded_official_Driver_denies_foreign_command_and_uses_absolute_degrees(tmp_path,monkeypatch,sdk_reply_ok=True)
    assert bound==[True]


def test_hardware_draft_selects_direct_link_without_external_file(tmp_path,monkeypatch):
    from ai.execute.test.test_commissioning import tool
    p,files=captures(tmp_path);monkeypatch.setattr(tool,'ROOT',tmp_path)
    monkeypatch.setattr(dl,'check_link',lambda *a,**k:{'interface':'enp1s0'})
    template=tmp_path/'template.json';template.write_text(json.dumps(p.values));output=tmp_path/'direct.json'
    assert tool.main(['--prepare-hardware',str(template),'--captures',*[str(f) for f in files],
                      '--definition-reference','FAKE manual','--review-record','FAKE test',
                      '--supervised-direct-link','--output',str(output)])==0
    value=json.loads(output.read_text())
    assert value['authority_mode']=='supervised_direct_link' and value['direct_link_interface']=='enp1s0'
    assert value['interlock_file'] is None and value['interlock_issuer'] is None
    assert not value['reviewed_for_real']
