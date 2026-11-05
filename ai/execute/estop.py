"""Firmware-qualified SDK E-stop input. Never infer clearance from Ready.

Qualification needs three stationary captures and a reviewed firmware definition.
The capture comparison is evidence, not an automatic determination of semantics.
Any non-clear value revokes this session permanently; no reset API is exposed.
"""
import hashlib
import json
from pathlib import Path
from ai.execute.safety import SafetyError


def state_pair(raw):
    try:
        value = raw['get_safety_control_data']['safety_state']
        pair = [value['id'], value['state']]
    except (KeyError, TypeError):
        raise SafetyError('SDK_estop_field_missing') from None
    if any(type(v) is not int or v < 0 for v in pair):
        raise SafetyError('SDK_estop_field_unknown')
    return pair


def ga12_control_available(raw):
    """Qualified control availability, NOT decoding safety_state 255."""
    from ai.execute.controller_safety import servo_states
    try:
        d=raw['get_control_data'];servo=raw['get_servo_data'];motion=raw['get_motion_data']
        code=raw['get_violation_data']['violation_code'];category=raw['get_stop_state']['category']
        if type(code) not in (str,int) or str(code)!='0':return False
        op=d['op_state']
        return (type(op) is int and op in (5,17) and d.get('sim_mode') is False
                and servo.get('servo_actives')==[True]*6 and servo.get('brake_actives')==[False]*6
                and servo_states(servo)==['OPERATION_ENABLED']*6
                and type(category) is int and (category==-1 or category==2 and op==5)
                and motion.get('is_stopping') is False and motion.get('is_pausing') is False
                and type(motion.get('motion_queue_size')) is int and motion['motion_queue_size']==0)
    except (KeyError,TypeError):raise SafetyError('GA12_control_availability_missing') from None


def require_mapping(profile):
    """Verify immutable onsite evidence, firmware binding and explicit qualification."""
    mapping = profile.values.get('sdk_estop')
    if mapping is None:
        return None  # Existing external input remains mandatory.
    if not isinstance(mapping, dict):raise SafetyError('SDK_estop_mapping_invalid')
    for name in ('review_record', 'definition_reference'):
        if not isinstance(mapping.get(name), str) or not mapping[name].strip():
            raise SafetyError('SDK_estop_qualification_missing:' + name)
    composite=mapping.get('kind')=='ga12_control_availability'
    if composite and (profile.values.get('sdk_version')!='3.4.2.2' or profile.values.get('controller_version')!='3.3.2-GA12 [2026.09.15]'):
        raise SafetyError('GA12_qualification_wrong_version')
    pairs = {}
    for phase in ('clear_before', 'pressed', 'clear_after'):
        try:
            record = mapping['captures'][phase]
            path = Path(record['path'])
            content = path.read_bytes()
            if not path.is_absolute() or hashlib.sha256(content).hexdigest() != record['sha256']:
                raise SafetyError('SDK_estop_evidence_changed')
            capture = json.loads(content)
            if capture['phase'] != phase or capture['stationary_only'] is not True:
                raise SafetyError('SDK_estop_capture_phase_invalid')
            for field in ('robot_serial', 'controller_version', 'sdk_version'):
                if capture['identity'][field] != profile.values[field]:
                    raise SafetyError('SDK_estop_identity_mismatch')
            samples = capture['samples']
            if len(samples) < 2:
                raise SafetyError('SDK_estop_capture_too_short')
            if composite:
                if capture.get('capture_valid') is not True:raise SafetyError('GA12_capture_invalid')
                values=[ga12_control_available(s['raw']) for s in samples]
                if phase=='pressed':
                    for sample in samples:
                        raw=sample['raw'];violation=raw['get_violation_data']
                        if (raw['get_control_data']['op_state']!=2 or str(violation['violation_code'])!='262144'
                            or violation.get('violation_str')!='EMG Button Activated'
                            or raw['get_servo_data']['servo_actives']!=[False]*6
                            or raw['get_servo_data']['brake_actives']!=[True]*6
                            or raw['get_stop_state']['category']!=0):raise SafetyError('GA12_pressed_evidence_incomplete')
                elif not all(values):raise SafetyError('GA12_normal_evidence_not_eligible')
            else:values = [state_pair(s['raw']) for s in samples]
            if any(v != values[0] for v in values):
                raise SafetyError('SDK_estop_capture_not_stable')
            pairs[phase] = values[0]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SafetyError('SDK_estop_evidence_invalid:' + phase) from exc
    if pairs['clear_before'] != pairs['clear_after'] or pairs['pressed'] == pairs['clear_before']:
        raise SafetyError('SDK_estop_no_distinct_repeatable_signal')
    if not composite and (mapping.get('clear_pair') != pairs['clear_before'] or mapping.get('pressed_pair') != pairs['pressed']):
        raise SafetyError('SDK_estop_mapping_not_captured')
    return mapping


class SdkEstop:
    def __init__(self, profile):
        self.mapping = require_mapping(profile)
        self.revoked = False

    def update(self, raw):
        if self.mapping is None:
            raise SafetyError('SDK_estop_not_qualified')
        try:
            clear = (ga12_control_available(raw) if self.mapping.get('kind')=='ga12_control_availability'
                     else state_pair(raw) == self.mapping['clear_pair'])
        except SafetyError:
            self.revoked = True
            raise
        if not clear:
            self.revoked = True
        return clear and not self.revoked
