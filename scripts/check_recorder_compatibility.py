#!/usr/bin/env python3
"""Static path check only: no ROS/SDK import, graph access, process or connection.

An installed source match cannot identify code already loaded by a running QP.
Runtime compatibility still requires a fresh valid action_context v1 stream.
"""
import json
from pathlib import Path


def inspect(root):
    root = Path(root).resolve()
    source = root / "ros2_ws/src/capstone_teleop/capstone_teleop"
    installed = root / "ros2_ws/install/capstone_teleop/lib/python3.12/site-packages"
    result = {"kind": "static_recorder_compatibility", "root": str(root),
        "running_qp_version": "unknown_no_runtime_observation",
        "required_topic": "/capstone/action_context", "required_protocol": 1,
        "root_act_record_exists": (root / "act-record").is_file(),
        "supported_entrypoints": [str(root / "scripts/bin/act"), str(root / "scripts/bin/act-record")],
        "installed_source_match": False}
    try:
        egg = installed / "capstone-teleop.egg-link"
        if egg.exists():
            if not egg.resolve().is_relative_to(root):
                result["reason"] = "egg_link_outside_this_project"
                return result
            target = Path(egg.read_text().splitlines()[0])
            if not target.is_absolute():
                target = installed / target
            package = target.resolve() / "capstone_teleop"
        else:
            package = installed / "capstone_teleop"
        result["installed_package"] = str(package.resolve())
        # Do not inspect another clone/project via stale links.
        if not package.resolve().is_relative_to(root):
            result["reason"] = "installed_package_outside_this_project"
            return result
        match = {}
        for name in ("qp_teleop.py", "action_context.py"):
            candidate = (package / name).resolve()
            if not candidate.is_relative_to(root):
                match[name] = False
            else:
                match[name] = candidate.is_file() and candidate.read_bytes() == (source / name).read_bytes()
        result["files_match"] = match
        result["installed_source_match"] = all(match.values())
        result["reason"] = "static_source_match_runtime_unknown" if all(match.values()) else "installed_source_missing_or_stale"
    except (OSError, ValueError, IndexError) as exc:
        result["reason"] = f"static_check_failed: {exc}"
    return result


def main():
    report = inspect(Path(__file__).resolve().parents[1])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["installed_source_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
