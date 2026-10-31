"""Immutable operator judgments. Original Task/Checkpoint files are never written."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[2]
STAGES = ("grasp", "lift", "transport", "placement", "release", "retreat", "act", "home", "task")
OUTCOMES = ("success", "failure", "unknown", "not_attempted")
ENVIRONMENTS = ("mock", "synthetic", "real", "unknown")
CAUSES = {
    "approach_failure": "policy", "grasp_failure": "policy", "lift_failure": "policy",
    "object_drop": "policy", "placement_failure": "policy", "release_failure": "policy", "retreat_failure": "policy",
    "camera_observation": "system", "joint_state": "system", "action_timing": "system",
    "inference_timeout": "system", "invalid_action": "system", "joint_limit": "system",
    "tracking_error": "system", "gripper_service": "system", "communication": "system", "home_failure": "system",
    "safety_guard": "safety_operator", "emergency_stop": "safety_operator", "user_abort": "safety_operator",
    "safety_conditions": "safety_operator", "unknown_cause": "unknown", "unassessed": "unknown", "incomplete_record": "unknown",
    "watchdog": "system", "observation_stale": "system", "observation_timing": "system", "stop_failure": "system", "act_timeout": "system",
    "action_rejected": "system",
    "controller_timing": "system", "observation_quality": "system",
}


def canonical(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
def identity(value): return hashlib.sha256(canonical(value).encode()).hexdigest()


def local_path(path):
    result = Path(path).expanduser().resolve()
    if not result.is_relative_to(ROOT): raise ValueError(f"Path must be in this project: {result}")
    if any(part in ('private','capstone','pipet-physical-ai') for part in result.relative_to(ROOT).parts):
        raise ValueError('Other/private project paths are excluded')
    return result


def output_path(path):
    result = local_path(path)
    parts=result.relative_to(ROOT).parts
    if (parts and parts[0] in ('.git','.venv','.venv-act','ros2_ws')) or parts[:2]==('ai','lerobot_source'):
        raise ValueError('Evaluation output cannot be in protected source/environment directories')
    for parent in (result, *result.parents):
        if any((parent / marker).is_file() for marker in ("meta/info.json", "model.safetensors", "task_result.json", "run.json", "result.json", "events.jsonl")):
            raise ValueError("Output cannot be inside a Dataset, Checkpoint, or existing execution/training run")
        if parent != ROOT and parent.is_relative_to(ROOT) and (next(parent.glob('*_task_result.json'),None) or next(parent.glob('*events.jsonl'),None)):
            raise ValueError("Output cannot be inside existing Task/log artifacts")
    return result


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(text):
    value = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    canonical(value)  # Also reject overflow such as 1e999.
    return value


def read_json(path):
    path = local_path(path)
    if path.stat().st_size > 16 * 1024 * 1024: raise ValueError("Metadata JSON exceeds 16MiB")
    return loads(path.read_text(encoding="utf-8"))


def task_key(task):
    values = tuple(task.get(key) for key in ("task_run_id", "session_id", "checkpoint_id"))
    if any(not isinstance(value, str) or not value.strip() for value in values): raise ValueError("Task run/session/checkpoint IDs required")
    return values


def environment(task):
    flag = task.get("execution", {}).get("mock")
    if flag is True: return "mock"
    if flag is False: return "real"
    # Pure synthetic records can explicitly declare their source; absence is unknown.
    if task.get("environment") == "synthetic": return "synthetic"
    return "unknown"


def judgment_template(task):
    task_key(task)
    return {"environment": environment(task), "evaluator": "", "validity": "unknown", "invalid_reason": None,
            "training_run_id": None, "dataset_id": None, "related_episode_ids": [],
            "conditions": {"region": None, "yaw_deg": None, "depth_cm": None, "x_cm": None, "y_cm": None,
                           "depth_condition": None, "training_coverage": "unknown", "evaluation_protocol_id": None,
                           "workspace_id": None, "camera_setup_id": None, "object_id": None},
            "outcomes": {stage: "unknown" for stage in STAGES}, "failure_stage": None,
            "causes": [], "notes": "", "change_reason": "", "log_refs": []}


def validate_judgment(value):
    canonical(value)
    if value.get("environment") not in ENVIRONMENTS: raise ValueError("Invalid evaluation environment")
    if not isinstance(value.get("evaluator"), str) or not value["evaluator"].strip(): raise ValueError("Evaluator required")
    if value.get("validity") not in ("valid", "invalid", "unknown"): raise ValueError("Explicit validity required")
    if value["validity"] == "invalid" and not value.get("invalid_reason"): raise ValueError("Invalid trial needs a reason")
    conditions = value.get("conditions", {})
    if not isinstance(conditions, dict): raise ValueError("Conditions must be an object")
    for key in ("yaw_deg", "depth_cm", "x_cm", "y_cm"):
        number = conditions.get(key)
        if number is not None and (type(number) not in (int, float) or not math.isfinite(number)): raise ValueError(f"Invalid actual {key}")
    if conditions.get("training_coverage", "unknown") not in ("seen", "unseen", "unknown"): raise ValueError("Invalid training coverage")
    for key in ("region", "depth_condition", "evaluation_protocol_id", "workspace_id", "camera_setup_id", "object_id"):
        if conditions.get(key) is not None and not isinstance(conditions[key], str): raise ValueError(key)
    outcomes = value.get("outcomes")
    if not isinstance(outcomes, dict) or set(outcomes) != set(STAGES) or any(result not in OUTCOMES for result in outcomes.values()):
        raise ValueError("All stage outcomes must be explicit success/failure/unknown/not_attempted")
    for aggregate, children in (("act", STAGES[:6]), ("task", STAGES[:-1])):
        if outcomes[aggregate] == "success" and any(outcomes[child] in ("failure", "not_attempted") for child in children):
            raise ValueError(f"Contradictory {aggregate} success")
    if not isinstance(value.get("causes", []), list): raise ValueError("Causes must be a list")
    for cause in value.get("causes", []):
        if (not isinstance(cause, dict) or cause.get("code") not in CAUSES
                or cause.get("certainty") not in ("observed", "suspected") or not isinstance(cause.get("evidence"), str)
                or not cause["evidence"].strip()): raise ValueError("Cause needs a known code, certainty and evidence")
    episodes = value.get("related_episode_ids", [])
    if not isinstance(episodes, list) or any(type(ep) is not int or ep < 0 for ep in episodes) or len(episodes) != len(set(episodes)):
        raise ValueError("Related Episode IDs must be unique nonnegative integers")
    for key in ("training_run_id", "dataset_id"):
        if value.get(key) is not None and not isinstance(value[key], str): raise ValueError(key)
    for key in ('notes','change_reason'):
        if key in value and not isinstance(value[key],str):raise ValueError(key)
    if value.get('failure_stage') is not None and not isinstance(value['failure_stage'],str):raise ValueError('failure_stage')
    if not isinstance(value.get('log_refs',[]),list):raise ValueError('log_refs')


def validate_record(record):
    if record.get("schema_version") != 1: raise ValueError("Unknown evaluation schema")
    for key in ("evaluation_id", "evaluated_at", "task_sha256"):
        if not isinstance(record.get(key), str) or not record[key]: raise ValueError(key)
    timestamp = datetime.fromisoformat(record["evaluated_at"])
    if timestamp.tzinfo is None: raise ValueError("Evaluation time must have timezone")
    task_key(record["task_reference"])
    if type(record.get("revision")) is not int or record["revision"] < 1: raise ValueError("Invalid revision")
    if record["revision"] > 1 and (not record.get("supersedes") or not record.get("supersedes_sha256") or not record.get("change_reason")):
        raise ValueError("Revision needs parent hash and change reason")
    validate_judgment(record)


def latest_records(records):
    """Ambiguous roots, branches or broken history invalidate that Task's judgment."""
    grouped, selected, issues = {}, {}, []
    for record in records:
        grouped.setdefault(task_key(record["task_reference"]), {})[record["evaluation_id"]] = record
    for key, values in grouped.items():
        try:
            roots = [r for r in values.values() if not r.get("supersedes")]
            if len(roots) != 1 or roots[0]["revision"] != 1: raise ValueError("ambiguous evaluation roots")
            current, seen = roots[0], set()
            while True:
                seen.add(current["evaluation_id"])
                children = [r for r in values.values() if r.get("supersedes") == current["evaluation_id"]]
                if not children: break
                if len(children) != 1: raise ValueError("branched revision history")
                child = children[0]
                if (child["revision"] != current["revision"] + 1 or child["supersedes_sha256"] != identity(current)
                        or child["task_sha256"] != current["task_sha256"] or child["environment"] != current["environment"]
                        or child["evaluation_id"] in seen): raise ValueError("broken revision chain")
                current = child
            if len(seen) != len(values): raise ValueError("orphan revision")
            selected[key] = current
        except ValueError as exc: issues.append({"kind": "evaluation_history", "task_key": list(key), "reason": str(exc)})
    return selected, issues


def append_evaluation(store, task, judgment, *, supersedes=None, source_path=None):
    key = task_key(task)
    validate_judgment(judgment)
    if judgment["environment"] != environment(task): raise ValueError("Evaluation cannot relabel Mock/Synthetic/unknown Task as Real")
    store = output_path(store)
    store.mkdir(parents=True, exist_ok=True)
    with (store / ".writer.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = [read_json(p) for p in sorted(store.glob("evaluation-*.json"))]
        for record in records: validate_record(record)
        heads, issues = latest_records(records)
        if issues: raise ValueError(f"Resolve record history errors before append: {issues}")
        previous = heads.get(key)
        if (previous is not None and previous["evaluation_id"] != supersedes) or (previous is None and supersedes is not None):
            raise ValueError("Must supersede the current Task evaluation head")
        if previous and (not judgment.get("change_reason") or previous["task_sha256"] != identity(task)):
            raise ValueError("Revision needs change reason and unchanged original Task")
        record = {**judgment, "schema_version": 1, "evaluation_id": uuid.uuid4().hex,
                  "evaluated_at": datetime.now(timezone.utc).isoformat(),
                  "task_reference": {name: task[name] for name in ("task_id", "task_run_id", "session_id", "checkpoint_id")},
                  "task_sha256": identity(task), "source_path": str(source_path) if source_path else None,
                  "revision": previous["revision"] + 1 if previous else 1,
                  "supersedes": supersedes, "supersedes_sha256": identity(previous) if previous else None}
        validate_record(record)
        destination = store / f"evaluation-{record['evaluation_id']}.json"
        temporary = store / f".pending-{record['evaluation_id']}"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush(); os.fsync(stream.fileno())
            os.link(temporary, destination)  # atomic publication, never overwrite
        finally:
            if temporary.exists(): temporary.unlink()
        return destination
