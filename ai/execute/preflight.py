"""Offline Real profile diagnostics; never import SDK, ROS or load a model."""
import json
from pathlib import Path
from ai.execute.safety import SafetyConfig
from ai.execute.real_profile import HardwareProfile


def check(config_path, hardware_path, checkpoint=None):
    errors = []
    hardware_data = {}
    for label, path in (('safety', config_path), ('hardware', hardware_path)):
        try:
            data = json.loads(Path(path).read_text())
            if label=='hardware' and isinstance(data,dict):hardware_data=data
            for key, value in data.items():
                if label=='hardware':
                    direct=data.get('authority_mode')=='supervised_direct_link'
                    if key=='direct_link_interface' and not direct:continue
                    if key in ('interlock_file','interlock_issuer') and direct:continue
                if value is None or isinstance(value, list) and any(v is None for v in value):
                    if key != 'sdk_estop':errors.append(label + '.' + key + ': unset')
                if key == 'sdk_limit_units' and isinstance(value, dict):
                    errors.extend(label + '.' + key + '.' + k + ': unset' for k, v in value.items() if v is None and not (data.get('sdk_version')=='3.4.2.2' and data.get('controller_version')=='3.3.2-GA12 [2026.09.15]' and k in ('get_joint_limit_config','get_original_joint_limit_config')))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append(label + ': ' + str(exc))
    try:
        safety = SafetyConfig.load(config_path)
        profile = HardwareProfile.load(hardware_path, safety)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError) as exc:
        errors.append('profile validation: ' + str(exc))
        profile = None
    estop_source='external_interlock'
    if hardware_data.get('sdk_estop') is not None:
        try:
            from ai.execute.estop import require_mapping
            require_mapping(HardwareProfile(hardware_data))
            estop_source='qualified_sdk'
        except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError) as exc:
            estop_source='qualification_failed'
            errors.append('E-stop qualification: '+str(exc))
    if checkpoint and not Path(checkpoint).expanduser().exists():
        errors.append('checkpoint path does not exist: ' + str(checkpoint))
    return {'profile_valid': not errors, 'errors': errors,
            'authority_mode':hardware_data.get('authority_mode','external_interlock') if hardware_data else None,
            'estop_source': estop_source,
            'runtime_checks': ['live controller identity/configuration/units', 'fresh E-stop and writer exclusion evidence',
                               'QP lock and ROS writer conflicts', 'initial pose/servo/fault/control mode',
                               'two RGB cameras/joints/confirmed gripper and timestamps'],
            'hardware_connected': False, 'robot_commands_sent': 0,
            'note': 'Valid profiles are not runtime permission. Live checks for the selected authority mode remain mandatory.'}
