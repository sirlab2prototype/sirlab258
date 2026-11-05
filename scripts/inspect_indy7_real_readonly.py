#!/usr/bin/env python3
"""Stationary commissioning capture. Default/--help never connect.

Only --connect-readonly plus explicit approval enables GETTERS. No motion,
servo, stop, reset or recovery API is exposed, including exception cleanup.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import hashlib
import ipaddress
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
GETTERS = ('get_device_info','get_control_info','get_joint_limit_config',
           'get_original_joint_limit_config','get_safety_limits','get_safety_stop_config','get_coll_policy',
           'get_control_data','get_motion_data','get_servo_data','get_stop_state',
           'get_violation_data','get_boot_status','get_safety_control_data','get_teleop_state')


REQUIRED = {'get_device_info','get_control_info','get_motion_data','get_safety_control_data'}


def rpc_failure(exc):
    code = None
    try:
        status = exc.code()
        code = getattr(status, 'name', str(status))
    except (AttributeError, TypeError):
        pass
    return {'ok':False, 'grpc_code':code, 'error':type(exc).__name__+': '+str(exc)}


def capture(client, phase, sdk_version, samples=3, clock=time.monotonic_ns):
    from ai.execute.controller_safety import response_ok
    result = {'version':1, 'phase':phase, 'stationary_only':True,
              'created_utc':datetime.now(timezone.utc).isoformat(),
              'units':{'joint_limits':'unverified'}, 'controller_sensor_timestamp':None,
              'samples':[], 'identity':{}, 'capture_valid':True, 'required_failures':[]}
    for _ in range(samples):
        row={'raw':{}, 'timing':{}, 'calls':{}}
        for name in GETTERS:
            begin=clock()
            try:
                raw=getattr(client,name)()
                response_ok(raw)
                json.dumps(raw,allow_nan=False)
                if name=='get_motion_data' and raw.get('is_in_motion') is not False:
                    raise RuntimeError('Stationary capture only; no stop command issued. Operator must stop independently.')
                row['raw'][name]=raw
                row['calls'][name]={'ok':True,'grpc_code':'OK','required':name in REQUIRED}
            except Exception as exc:
                if isinstance(exc,RuntimeError) and str(exc).startswith('Stationary capture only'):raise
                row['calls'][name]={**rpc_failure(exc),'required':name in REQUIRED}
                if name in REQUIRED:
                    result['capture_valid']=False
                    result['required_failures'].append(name)
            end=clock()
            row['timing'][name]={'request_monotonic_ns':begin,'response_monotonic_ns':end,'rpc_elapsed_ns':end-begin}
            print(json.dumps({'getter':name,**row['calls'][name]},ensure_ascii=False),flush=True)
            if name=='get_safety_control_data' and not row['calls'][name]['ok']:
                # Official SDK alternate getter, preserved separately. Same opaque
                # uint32 schema does NOT prove equivalent E-stop semantics.
                fallback='get_safety_function_state';begin=clock()
                try:
                    raw=getattr(client,fallback)();response_ok(raw)
                    row['raw'][fallback]=raw
                    row['calls'][fallback]={'ok':True,'grpc_code':'OK','qualification':'unverified alternative; capture remains invalid'}
                except Exception as exc:row['calls'][fallback]=rpc_failure(exc)
                end=clock()
                row['timing'][fallback]={'request_monotonic_ns':begin,'response_monotonic_ns':end,'rpc_elapsed_ns':end-begin}
                print(json.dumps({'getter':fallback,**row['calls'][fallback]},ensure_ascii=False),flush=True)
        identity={'robot_serial':row['raw'].get('get_device_info',{}).get('robot_serial'),
                  'controller_version':row['raw'].get('get_control_info',{}).get('control_version'), 'sdk_version':sdk_version}
        if result['identity'] and result['identity']!=identity:raise RuntimeError('Controller identity changed')
        result['identity']=identity
        result['samples'].append(row)
    json.dumps(result,allow_nan=False)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--connect-readonly',action='store_true')
    p.add_argument('--approve-readonly',help='Onsite operator authorizing these getters only')
    p.add_argument('--ip')
    p.add_argument('--phase',choices=('clear_before','pressed','clear_after'))
    p.add_argument('--output',type=Path)
    p.add_argument('--prepare-hardware',type=Path,help='Offline: hardware template to populate from the three captures; does not approve execution')
    p.add_argument('--captures',type=Path,nargs=3,metavar=('CLEAR_BEFORE','PRESSED','CLEAR_AFTER'))
    p.add_argument('--definition-reference',help='Reviewed manufacturer/FW definition of the observed safety_state field')
    p.add_argument('--review-record',help='Onsite E-stop signal qualification record, not a Ready inference')
    p.add_argument('--supervised-direct-link',action='store_true',help='Offline prepare: bind to local physical direct Ethernet route; operator must verify cable isolation, close ALL other writers and disconnect Conty')
    a=p.parse_args(argv)
    if a.prepare_hardware:
        if a.connect_readonly or not a.captures or not a.output or not a.definition_reference or not a.review_record:
            p.error('Offline preparation needs --captures, --definition-reference, --review-record and --output; no connection')
        sys.path.insert(0,str(ROOT))
        from ai.execute.estop import require_mapping, state_pair
        from ai.execute.real_profile import HardwareProfile
        try:
            value=json.loads(a.prepare_hardware.read_text())
            evidence={}
            for phase,source in zip(('clear_before','pressed','clear_after'),a.captures):
                source=source.resolve();content=source.read_bytes();record=json.loads(content)
                if record.get('capture_valid') is False:raise ValueError('Invalid required-getter capture cannot qualify E-stop')
                evidence[phase]={'path':str(source),'sha256':hashlib.sha256(content).hexdigest()}
                if phase=='clear_before':
                    value.update(record['identity']);value['robot_ip']=record['robot_ip']
                    clear=state_pair(record['samples'][0]['raw'])
                elif phase=='pressed':pressed=state_pair(record['samples'][0]['raw'])
            value['sdk_estop']={'clear_pair':clear,'pressed_pair':pressed,'captures':evidence,
                               'review_record':a.review_record,'definition_reference':a.definition_reference}
            value['reviewed_for_real']=False  # Operating limits, stop validation and ACL review remain separate.
            require_mapping(HardwareProfile(value))
            if a.supervised_direct_link:
                from ai.execute.direct_link import check_link
                link=check_link(value['robot_ip'],None)
                value['authority_mode']='supervised_direct_link'
                value['direct_link_interface']=link['interface']
                value['interlock_file']=None;value['interlock_issuer']=None
            path=a.output.resolve()
            if not path.is_relative_to(ROOT) or path.exists():raise ValueError('Output must be new and inside this project')
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x') as f:json.dump(value,f,indent=2,allow_nan=False)
            print('Offline hardware draft saved: '+str(path)+'; complete remaining values and review before preflight.')
            return 0
        except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
            print(str(exc),file=sys.stderr);return 2
    if not a.connect_readonly:
        print(json.dumps({'connects':False,'getters':GETTERS,'required':'--connect-readonly --approve-readonly LABEL --ip IP --phase PHASE --output PATH'},indent=2))
        return 0
    if a.supervised_direct_link:p.error('--supervised-direct-link is for offline hardware preparation only')
    if not a.approve_readonly or not a.approve_readonly.strip() or not a.ip or not a.phase or not a.output:
        p.error('Explicit read-only approval, IP, phase and fresh output path required')
    ipaddress.ip_address(a.ip)
    path=a.output.expanduser().resolve()
    if not path.is_relative_to(ROOT) or path.exists():p.error('Output must be new and inside this project')
    path.parent.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(ROOT))
    from ai.execute.real_driver import DeadlineStub
    from neuromeka import IndyDCP3
    client=None
    try:
        client=IndyDCP3(a.ip)
        for group in ('boot','control','device','config','rtde','cri'):
            setattr(client,group,DeadlineStub(getattr(client,group),.2))
        result=capture(client,a.phase,importlib.metadata.version('neuromeka'))
        result['robot_ip']=a.ip
        result['operator']=a.approve_readonly
        with path.open('x') as f:json.dump(result,f,indent=2,allow_nan=False)
        print(('VALID capture saved: ' if result['capture_valid'] else 'INVALID diagnostic evidence saved: ')+str(path)+'; not safety qualification or execution permission.')
        return 0 if result['capture_valid'] else 2
    except Exception as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr)
        return 2
    finally:
        if client:
            for group in ('boot','control','device','config','rtde','cri'):
                getattr(client,group+'_channel').close()


if __name__=='__main__':raise SystemExit(main())
