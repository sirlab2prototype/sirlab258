"""Supervised single-PC research authority, not a safety-rated remote ACL.

Physical cable isolation is checked onsite. Linux route/device, owned QP lock
and foreign TCP connections are checked here. Never starts/stops another process
or changes a network setting. Arbitrary direct SDK clients are detected, not
kernel-blocked: the operator must not launch them during this short trial.
"""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from ai.execute.safety import SafetyError


def check_link(robot_ip, interface, *, sys_net=Path('/sys/class/net'), run=subprocess.run, timeout=.05):
    try:
        ip=ipaddress.IPv4Address(robot_ip)
        routes=json.loads(run(['ip','-j','route','get',str(ip)],capture_output=True,text=True,
                              timeout=timeout,check=True).stdout)
        if len(routes)!=1:raise ValueError('ambiguous route')
        route=routes[0];device=route['dev']
        if not device or '/' in device or (interface is not None and device!=interface) or route.get('gateway') or route.get('via'):
            raise ValueError('not the reviewed directly connected interface')
        link=sys_net/device
        if (not (link/'device').exists() or (link/'type').read_text().strip()!='1'
            or (link/'carrier').read_text().strip()!='1'
            or any((link/k).exists() for k in ('wireless','bridge','brport','master','tun_flags'))):
            raise ValueError('requires unbridged physical wired carrier')
        return {'interface':device,'source':route.get('prefsrc',route.get('src')),'gateway':None}
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        raise SafetyError('direct_robot_link_not_verified:'+str(exc)) from exc


def foreign_connections(robot_ip, *, proc=Path('/proc')):
    """Reject any other process' active TCP connection to this controller, even getters."""
    try:
        target=ipaddress.ip_address(robot_ip);owned=set()
        for fd in (proc/'self/fd').iterdir():
            try:link=os.readlink(fd)
            except FileNotFoundError:continue
            if link.startswith('socket:[') and link.endswith(']'):owned.add(link[8:-1])
        foreign=[]
        for name in ('tcp','tcp6'):
            for line in (proc/'net'/name).read_text().splitlines()[1:]:
                cells=line.split();address=cells[2].split(':')[0]
                data=bytes.fromhex(address)
                host=ipaddress.ip_address(b''.join(data[i:i+4][::-1] for i in range(0,len(data),4)))
                if isinstance(host,ipaddress.IPv6Address) and host.ipv4_mapped:host=host.ipv4_mapped
                # TIME_WAIT/CLOSE/LISTEN have no active command client.
                if host==target and cells[3] not in ('06','07','0A') and cells[9] not in owned:
                    foreign.append({'table':name,'state':cells[3],'inode':cells[9]})
        return foreign
    except (OSError,ValueError,IndexError) as exc:
        raise SafetyError('local_controller_connections_unknown') from exc


class DirectLinkInterlock:
    def __init__(self,profile,nonce,clock=time.monotonic_ns,*,supervising_operator=None):
        self.profile,self.nonce,self.clock=profile,nonce,clock
        self.operator=supervising_operator;self.writer=None;self.revoked=False;self.last_check=None
        self.state_lock=threading.Lock()
        self.last_refresh_timing=None

    def precheck(self):
        """Refresh costly kernel evidence on startup / existing 20Hz monitor.

        read() never runs ip subprocesses or scans TCP tables. It still checks
        the owned lock on every call and rejects expired evidence. The original
        interlock_max_age_ms is the bound; no asynchronous retry/recovery exists.
        """
        v=self.profile.values;started=self.clock()
        timing={"started_ns":started}
        try:
            if not isinstance(self.operator,str) or not self.operator.strip():raise SafetyError('onsite_direct_link_approval_required')
            with self.state_lock:
                if self.revoked:raise SafetyError('direct_link_authority_revoked')
            link=check_link(v['robot_ip'],v['direct_link_interface'],timeout=v['rpc_timeout_ms']/1000)
            timing['link_completed_ns']=self.clock()
            foreign=foreign_connections(v['robot_ip'])
            timing['tcp_completed_ns']=self.clock()
            if foreign:raise SafetyError('foreign_local_SDK_connection')
            if not 0<=self.clock()-started<=v['interlock_max_age_ms']*1e6:
                raise SafetyError('direct_link_refresh_over_budget')
            with self.state_lock:
                if self.revoked:raise SafetyError('direct_link_authority_revoked')
                # Age from scan START, never make a slow scan look newly fresh.
                self.link,self.last_check=link,started
        except (OSError,ValueError,SafetyError):
            with self.state_lock:self.revoked=True
            raise
        finally:
            timing['finished_ns']=self.clock();self.last_refresh_timing=timing

    def refresh(self):
        self.precheck()

    def bind_writer(self,writer):
        if self.writer is not None:raise SafetyError('authority_already_bound')
        self.writer=writer

    def read(self):
        try:
            with self.state_lock:
                if self.revoked:raise SafetyError('direct_link_authority_revoked')
            writer=self.writer
            if writer is None or writer.stream is None or writer.stream.closed:raise SafetyError('owned_QP_ACT_lock_required')
            opened=os.fstat(writer.stream.fileno());current=writer.path.stat()
            if (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):raise SafetyError('QP_ACT_lock_replaced')
            record=json.loads(writer.path.read_text())
            if record.get('status')!='held' or record.get('owner')!=writer.owner:raise SafetyError('QP_ACT_lock_owner_changed')
            with self.state_lock:
                now=self.clock()
                if self.revoked:raise SafetyError('direct_link_authority_revoked')
                observed=self.last_check;link=self.link if observed is not None else None
                if observed is None or not 0<=now-observed<=self.profile.values['interlock_max_age_ms']*1e6:
                    raise SafetyError('direct_link_authority_stale_or_clock_invalid')
            return {'run_nonce':self.nonce,'all_other_writers_blocked':True,
                    'authority_scope':'supervised direct cable + owned project lock + no foreign TCP client; not a remote ACL',
                    'observed_monotonic_ns':observed,'link':link}
        except (OSError,ValueError,SafetyError) as exc:
            with self.state_lock:self.revoked=True
            if isinstance(exc,SafetyError):raise
            raise SafetyError('direct_link_authority_unknown') from exc
