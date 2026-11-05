"""Fail-closed Real configuration and external safety-system evidence. No hardware imports."""
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import time
import numpy as np
from ai.execute.safety import SafetyConfig, SafetyError


@dataclass(frozen=True)
class HardwareProfile:
    values: dict
    @classmethod
    def load(cls, path, safety):
        value = json.loads(Path(path).read_text())
        obj = cls(value); obj.require(safety); return obj

    def require(self, safety):
        v = self.values
        if safety.reviewed_for_real is not True or not safety.review_record or v.get('reviewed_for_real') is not True:
            raise SafetyError('reviewed_real_profiles_required')
        if v.get('target_update_mode','strict') not in ('strict','bounded_rate'):raise SafetyError('unknown_target_update_mode')
        mode=v.get('authority_mode','external_interlock')
        if mode not in ('external_interlock','supervised_direct_link'):raise SafetyError('unknown_authority_mode')
        evidence=('interlock_file','interlock_issuer') if mode=='external_interlock' else ('direct_link_interface',)
        for key in ('review_record','robot_serial','controller_version','sdk_version','stop_validation_record',
                    'external_exclusion_record',*evidence):
            if not isinstance(v.get(key),str) or not v[key].strip():raise SafetyError('unverified_hardware_profile_'+key)
        ipaddress.ip_address(v['robot_ip'])
        if type(v.get('initial_pose_is_project_home')) is not bool:raise SafetyError('review_initial_pose_identity_required')
        initial = np.asarray(v.get('initial_joints_rad'),dtype=float)
        if initial.shape!=(6,) or not np.isfinite(initial).all():raise SafetyError('initial_pose_required')
        if np.any(initial<safety.joint_min_rad) or np.any(initial>safety.joint_max_rad):raise SafetyError('initial_pose_joint_limit')
        for key in ('initial_tolerance_rad','stop_stable_ms','interlock_max_age_ms','rpc_timeout_ms'):
            if type(v.get(key)) not in (int,float) or not np.isfinite(v[key]) or v[key]<=0:raise SafetyError('unverified_hardware_profile_'+key)
        if v['stop_stable_ms']>=safety.stop_timeout_ms:raise SafetyError('stop_stability_exceeds_timeout')
        if v['rpc_timeout_ms']>safety.command_timeout_ms or v['interlock_max_age_ms']>safety.status_stale_ms:
            raise SafetyError('hardware_profile_timeout_budget')
        # Existing official trajectory callback parameters; reviewed here, never silently changed.
        if v.get('sdk_vel_ratio')!=0.8 or v.get('sdk_acc_ratio')!=7.0:raise SafetyError('review_existing_driver_ratios_0.8_7.0')
        units=v.get('sdk_limit_units')
        if (not isinstance(units,dict) or not isinstance(v.get('sdk_limit_units_review_record'),str)
                or not v['sdk_limit_units_review_record'].strip() or
                any(units.get(k) not in ('deg','rad') for k in (('get_safety_limits',) if v.get('controller_version')=='3.3.2-GA12 [2026.09.15]' and v.get('sdk_version')=='3.4.2.2' else ('get_joint_limit_config','get_original_joint_limit_config','get_safety_limits')))):
            raise SafetyError('controller_limit_units_unverified')
        if v.get('joint_unit')!='deg':raise SafetyError('reviewed_SDK_joint_degree_contract_required')
        if mode=='external_interlock' and not Path(v['interlock_file']).is_absolute():raise SafetyError('absolute_interlock_path_required')
        if mode=='supervised_direct_link':
            if ipaddress.ip_address(v['robot_ip']).version!=4 or '/' in v['direct_link_interface']:
                raise SafetyError('review_direct_wired_IPv4_interface_required')
            if v.get('sdk_estop') is None:raise SafetyError('direct_link_requires_qualified_native_Estop')
        from ai.execute.estop import require_mapping
        require_mapping(self)


class ExternalInterlock:
    """Read an external safety/ACL supervisor heartbeat; never manufacture one.

    Existing verified evidence must cover E-stop and ALL remote/local writers,
    including Mark7 service/serial access. A local lock/empty graph cannot do so.
    Workspace uses the existing onsite Real approval for this supervised trial;
    no automatic workspace-clear sensor result is fabricated.
    """
    def __init__(self, profile, nonce, clock=time.monotonic_ns, *, supervising_operator=None):
        self.profile, self.nonce, self.clock = profile, nonce, clock
        self.supervising_operator = supervising_operator

    def read(self):
        v = self.profile.values
        try:
            data=json.loads(Path(v['interlock_file']).read_text())
        except (OSError,ValueError) as exc:raise SafetyError('external_interlock_unavailable') from exc
        now=self.clock();stamp=data.get('observed_monotonic_ns')
        if (data.get('version')!=1 or data.get('run_nonce')!=self.nonce or data.get('robot_serial')!=v['robot_serial']
            or data.get('issuer')!=v['interlock_issuer'] or type(stamp)is not int
            or not 0<=now-stamp<=v['interlock_max_age_ms']*1e6):raise SafetyError('stale_or_wrong_external_interlock')
        keys = ('estop_clear','all_other_writers_blocked') if v.get('sdk_estop') is None else ('all_other_writers_blocked',)
        # Native SDK E-stop is checked against fresh runtime samples by FeedbackCache.
        # Do not fabricate a positive signal before SDK construction.
        for key in keys:
            if data.get(key) is not True:raise SafetyError('external_interlock_'+key)
        if 'estop_clear' in data and data['estop_clear'] is not True:
            raise SafetyError('external_interlock_estop_clear')
        # For this explicitly supervised research trial, the existing Real approval
        # also attests a controlled, empty work area. Never override a negative
        # signal from an installed external guard, or claim automatic human detection.
        if 'workspace_clear' in data:
            if data['workspace_clear'] is not True:raise SafetyError('external_interlock_workspace_clear')
        elif not isinstance(self.supervising_operator,str) or not self.supervising_operator.strip():
            raise SafetyError('supervised_workspace_approval_missing')
        return data


def make_interlock(profile,nonce,*,supervising_operator):
    if profile.values.get('authority_mode')=='supervised_direct_link':
        from ai.execute.direct_link import DirectLinkInterlock
        return DirectLinkInterlock(profile,nonce,supervising_operator=supervising_operator)
    return ExternalInterlock(profile,nonce,supervising_operator=supervising_operator)


def precheck_interlock(interlock):
    if hasattr(interlock,'precheck'):interlock.precheck()
    else:interlock.read()
