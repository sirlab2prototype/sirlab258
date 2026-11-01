"""Indy7 ACT collection definitions. JSON only; no ROS or Dataset imports."""
from copy import deepcopy
import hashlib
import json
import re


def read_json(path):
    if path.is_symlink():
        raise ValueError("JSON symlink 파일은 지원하지 않습니다")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"중복 JSON 키: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f"유효하지 않은 JSON 숫자: {value}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: 비어 있지 않은 문자열이 필요합니다")
    return value


def identifier(value, *, action=False):
    pattern = r"act[1-9][0-9]*" if action else r"[a-z][a-z0-9_-]{0,63}"
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError("행동 ID는 act1, act2 등의 형식입니다" if action else "잘못된 계획 ID")
    return value


def validate_action(value):
    if not isinstance(value, dict) or set(value) != {"id", "description"}:
        raise ValueError("행동에는 id, description이 필요합니다")
    identifier(value["id"], action=True)
    text(value["description"], "행동 설명")
    return deepcopy(value)


def validate_plan(value):
    if not isinstance(value, dict) or set(value) != {"version", "id", "description", "kind", "conditions"}:
        raise ValueError("계획 필드: version, id, description, kind, conditions")
    if type(value["version"]) is not int or value["version"] != 1 or value["kind"] not in ("custom", "core108"):
        raise ValueError("지원하지 않는 계획 버전/종류")
    identifier(value["id"])
    text(value["description"], "계획 설명")
    rows = value["conditions"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("하나 이상의 조건이 필요합니다")
    ids, orders = [], []
    for row in rows:
        required = ({"condition_id", "collection_order", "description", "fields"} if value["kind"] == "custom"
                    else {"condition_id", "collection_order", "position_region", "yaw_bin", "depth_coverage"})
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError(f"잘못된 조건 필드: {required}")
        for key in ("condition_id", "collection_order"):
            if type(row[key]) is not int or row[key] < 1:
                raise ValueError("조건 ID와 순서는 양의 정수여야 합니다")
        ids.append(row["condition_id"]); orders.append(row["collection_order"])
        if value["kind"] == "custom":
            text(row["description"], "조건 설명")
            if not isinstance(row["fields"], dict) or any(not isinstance(k, str) or not k.strip() for k in row["fields"]):
                raise ValueError("fields는 이름이 있는 JSON 객체여야 합니다")
    if len(set(ids)) != len(ids) or sorted(orders) != list(range(1, len(rows) + 1)):
        raise ValueError("조건 ID 중복 또는 순서 중복/누락")
    if value["kind"] == "core108":
        from collections import Counter
        from ai.record.collection import DEPTHS, REGIONS
        if len(rows) != 108 or set(ids) != set(range(1, 109)) or Counter(
                (r["position_region"], r["yaw_bin"], r["depth_coverage"]) for r in rows) != Counter(
                (region, f"A{yaw}", depth) for region in REGIONS for yaw in range(1, 7) for depth in DEPTHS):
            raise ValueError("Core-108 조건 범위/분포 불일치")
    result = deepcopy(value)
    result["conditions"].sort(key=lambda row: row["collection_order"])
    # Reject non-JSON values, nonfinite floats and unserializable field contents.
    json.dumps(result, allow_nan=False)
    return result


def builtin_plan():
    from ai.record.collection import load_plan
    return {"version": 1, "id": "core108", "description": "기존 바구니 Core-108 원본 순서",
            "kind": "core108", "conditions": load_plan()}


BUILTIN_ACTION = {"id": "act1", "description": "Grasp basket handle from shelf and place on table"}


def validate_definition(value):
    if not isinstance(value, dict) or set(value) != {"version", "dataset_id", "action", "plan", "robot", "policy"}:
        raise ValueError("잘못된 Collection 정의")
    if type(value["version"]) is not int or value["version"] != 1 or value["robot"] != "indy7" or value["policy"] != "act":
        raise ValueError("Indy7 ACT 정의만 지원합니다")
    action = validate_action(value["action"])
    plan = validate_plan(value["plan"])
    if not isinstance(value["dataset_id"], str) or re.fullmatch(
            re.escape(action["id"]) + f"_{len(plan['conditions']):03d}_" + r"[0-9]{3,}", value["dataset_id"]) is None:
        raise ValueError("Dataset ID의 행동/계획 조건 수 불일치")
    if int(value["dataset_id"].rsplit("_", 1)[1]) < 1:
        raise ValueError("수집본 일련번호는 1 이상입니다")
    return {**deepcopy(value), "action": action, "plan": plan}


def definition_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()
