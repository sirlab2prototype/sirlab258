"""Streaming existing client events; diagnostics are signals, not manipulation causes."""
from array import array
from collections import Counter
import math
import re

from ai.evaluate.records import loads

SAMPLE_LIMIT = 10000


def distribution(values, *, observed=None):
    numbers = sorted(values)
    def percentile(p):
        index = (len(numbers) - 1) * p
        lo, hi = math.floor(index), math.ceil(index)
        return numbers[lo] + (numbers[hi] - numbers[lo]) * (index - lo)
    return {"observed_samples": len(numbers) if observed is None else observed, "retained_samples": len(numbers),
            "truncated": observed is not None and observed > len(numbers),
            "quantile_scope": "retained chronological prefix; not all samples" if observed is not None and observed > len(numbers) else "all valid observed samples",
            **({"min": numbers[0], "median": percentile(.5), "p95": percentile(.95), "p99": percentile(.99), "max": numbers[-1]}
               if numbers else {key: None for key in ("min", "median", "p95", "p99", "max")})}


PATTERNS = {
    "camera_observation": r"camera_skew|malformed_camera|camera_\d+.*(?:observation_too_old|missing_causal)|(?:observation_too_old|missing_causal).*camera_\d+",
    "joint_state": r"stale_joint\b|invalid_joint_feedback|no_initial_state|malformed_state|(?:observation_too_old|missing_causal).*\bstate\b",
    "action_timing": r"action_timestamp|action_deadline|adapter_action_deadline|scheduler_deadline_gap|action_sequence_gap|command_sequence_gap|action_deadline_missed|observation_deadline_missed",
    "inference_timeout": r"client_inference_timeout|first_prediction_timeout|inference_timeout|inference_watchdog|inference_cannot_keep_up|response_missed_action_handover",
    "invalid_action": r"action_must_be_finite_7d|invalid_prediction_output|obsolete_session_or_checkpoint|invalid_action_index|gripper_output_range",
    "joint_limit": r"target_joint_limit|measured_joint_limit|home_target_joint_limit",
    "tracking_error": r"persistent_tracking_error",
    "gripper_service": r"gripper_service_failed|gripper_service_timeout|gripper_dispatch_failed",
    "communication": r"Inference server disconnected|server_disconnected|stop_transport_unavailable|indy_service_unavailable|mark7_service_unavailable|RPC.*(?:timeout|Timeout)|communication|send_exception",
    "home_failure": r"home_failed|home_command_failed|home_command_rejected|home_arrival_error|home_timeout|home_stop_|real_project_home_locked",
    "safety_guard": r"target_current_error|joint_step_limit|command_velocity_limit|command_acceleration_limit|measured_joint_velocity|measured_joint_acceleration|persistent_tracking_error|target_joint_limit|measured_joint_limit",
    "emergency_stop": r"\bemergency_stop\b|user_emergency_stop|emergency_stop_requested",
    "user_abort": r"\buser_abort\b|\buser_interrupt\b",
    "safety_conditions": r"controller_not_confirmed_ready|command_authority_unknown|controller_fault_or_estop|home_path_unconfirmed|home_path_became_unconfirmed|hardware_profile_not_reviewed",
    "watchdog": r"watchdog_timeout",
    "observation_stale": r"observation_too_old|stale_joint\b|stale_controller_status",
    "observation_quality": r"stale_or_invalid_observation|invalid_observation_age|invalid_observation_skew",
    "controller_timing": r"command_dispatch_over_budget|controller_command_timeout|command_dispatch_watchdog|controller_command_watchdog",
    "observation_timing": r"system_clock_jump_or_drift|ros_system_clock_mismatch|unsupported_clock|header_reversal|header_in_future|observation_skew",
    "stop_failure": r"stop_unconfirmed|stop_confirmation_failed|stop_send_failed|stop_request_failed|stop_verification_timeout",
    "act_timeout": r"\bact_timeout\b",
}


def signals(reason):
    if not isinstance(reason, str): return []
    return [code for code, pattern in PATTERNS.items() if re.search(pattern, reason)]


class LogSession:
    def __init__(self, session_id, path):
        self.session_id, self.path = session_id, str(path)
        self.samples = {name: array('d') for name in ("inference_ms", "rpc_roundtrip_ms", "action_interval_ms", "schedule_lateness_ms",
                        "camera_skew_ms", "observation_skew_ms", "camera_A_age_ms", "camera_B_age_ms", "state_age_ms")}
        self.sample_counts, self.events, self.evidence = Counter(), Counter(), {}
        self.first_action = self.last_action = None
        self.action_count = self.deadline_misses = 0
        self.task = self.summary = None
        self.corrupt_lines = 0
        self.first_line = None
        self.checkpoint_ids = set()
        self.synthetic_observations = False

    def number(self, name, value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0: return
        self.sample_counts[name] += 1
        if len(self.samples[name]) < SAMPLE_LIMIT: self.samples[name].append(value)

    def signal(self, code, reason, line, event):
        entries = self.evidence.setdefault(code, [])
        if len(entries) < 5:
            entries.append({"origin": "log_signal", "certainty": "reported_event_not_root_cause", "source": self.path,
                            "line": line, "event": event, "reason": reason})

    def add(self, row, line):
        name = row.get("event", "unknown")
        if row.get('checkpoint_id'):self.checkpoint_ids.add(row['checkpoint_id'])
        self.events[name] += 1
        if self.first_line is None: self.first_line = line
        reason = row.get("reason")
        for code in signals(reason): self.signal(code, reason, line, name)
        if name == "execution_action_rejected": self.signal("action_rejected", reason, line, name)
        if name == "prediction_response":
            response, transport = row.get("response", {}), row.get("transport", {})
            if response.get('checkpoint_id'):self.checkpoint_ids.add(response['checkpoint_id'])
            if response.get("checkpoint_id") and row.get("checkpoint_id") not in (None, response["checkpoint_id"]):
                self.signal("incomplete_record", "checkpoint mismatch within response", line, name)
            if type(response.get("inference_ns")) in (int, float): self.number("inference_ms", response["inference_ns"] / 1e6)
            sent, received = transport.get("client_sent_monotonic_ns"), transport.get("client_received_monotonic_ns")
            if type(sent) is int and type(received) is int: self.number("rpc_roundtrip_ms", (received-sent)/1e6)
            self.events['prediction_accepted' if row.get('accepted') is True else 'prediction_not_accepted']+=1
        elif name == "action":
            now = row.get("actual_output_monotonic_ns")
            if type(now) is int:
                if self.last_action is not None:
                    if now <= self.last_action: self.signal("action_timing", "non-increasing actual Action output time", line, name)
                    else: self.number("action_interval_ms", (now-self.last_action)/1e6)
                if self.first_action is None: self.first_action = now
                self.last_action = now
            self.action_count += 1
            schedule = row.get("schedule", {})
            if type(schedule.get("lateness_ns")) in (int,float): self.number("schedule_lateness_ms", schedule["lateness_ns"]/1e6)
            if type(schedule.get("missed_deadlines_total")) is int: self.deadline_misses = max(self.deadline_misses, schedule["missed_deadlines_total"])
        elif name == "task_end" and isinstance(row.get("result"), dict): self.task = row["result"]
        elif name == "session_end":
            self.summary = row
            if isinstance(row.get("task"), dict): self.task = row["task"]
            if type(row.get("missed_action_deadlines")) is int: self.deadline_misses = max(self.deadline_misses, row["missed_action_deadlines"])
        elif name=='observation':
            timing=row.get('timing',{});alignment=timing.get('alignment',{})
            for key in ('camera_skew','observation_skew'):
                if type(alignment.get(key+'_ns')) in (int,float):self.number(key+'_ms',alignment[key+'_ns']/1e6)
            for topic,label in (('camera_317222074298','camera_A'),('camera_844212071939','camera_B'),('state','state')):
                source=timing.get('sources',{}).get(topic,{})
                if source.get('receiver_clock',{}).get('ros_clock_type')=='synthetic_system_time':self.synthetic_observations=True
                if type(source.get('reference_age_ns')) in (int,float):self.number(label+'_age_ms',source['reference_age_ns']/1e6)
                if source.get('reused_callback_sample') is True:self.events[label+'_reused_observation']+=1
        elif name in ("error", "observation_deadline_missed"):
            schedule = row.get("schedule") or {}
            if type(schedule.get("missed_deadlines_total")) is int: self.deadline_misses = max(self.deadline_misses, schedule["missed_deadlines_total"])

    def finish(self):
        complete = bool(self.events["session_start"] and self.events["session_end"] and not self.corrupt_lines)
        flag=(self.summary or self.task or {}).get('execution',{}).get('mock')
        env='mock' if flag is True else 'real' if flag is False else 'synthetic' if self.synthetic_observations else 'unknown'
        return {"path": self.path, "session_id": self.session_id, "complete_client_log": complete,
                'environment':env,
                "corrupt_lines": self.corrupt_lines, "events": dict(self.events), "observed_codes": self.evidence,
                "action_count": self.action_count, "deadline_misses_observed": self.deadline_misses,
                "actual_action_fps": (self.action_count-1)*1e9/(self.last_action-self.first_action)
                    if self.action_count > 1 and self.last_action > self.first_action else None,
                "metrics": {name: distribution(value, observed=self.sample_counts[name]) for name,value in self.samples.items()},
                "_samples": self.samples, "_sample_counts": dict(self.sample_counts), "task": self.task}


def scan_log(path, issues):
    sessions, bad = {}, 0
    with path.open(encoding="utf-8") as stream:
        line_number=0
        while True:
            line=stream.readline(4*1024*1024+1)
            if not line:break
            line_number+=1
            try:
                if len(line) > 4*1024*1024:
                    while not line.endswith('\n'):
                        line=stream.readline(4*1024*1024+1)
                        if not line:break
                    raise ValueError("JSONL line exceeds 4Mi characters")
                row = loads(line)
                if not isinstance(row, dict): raise ValueError("JSONL event must be an object")
                session = row.get("session_id")
                if not session and isinstance(row.get("result"), dict): session = row["result"].get("session_id")
                if not session: continue
                if not isinstance(session,str): raise ValueError("Invalid session ID")
                sessions.setdefault(session, LogSession(session,path)).add(row,line_number)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                bad += 1
                if bad <= 10: issues.append({"kind":"corrupt_jsonl", "source":str(path), "line":line_number, "reason":str(exc)})
    for session in sessions.values(): session.corrupt_lines = bad
    result=[]
    for session in sessions.values():
        value=session.finish();value['checkpoint_ids']=sorted(session.checkpoint_ids);result.append(value)
    return result
