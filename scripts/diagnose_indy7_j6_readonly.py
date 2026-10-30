#!/usr/bin/env python3
"""Read-only IndyDCP3 diagnostics focused on Indy7 joint 6."""

from __future__ import annotations

import argparse
import pprint
from datetime import datetime
from typing import Any, Callable

from neuromeka import IndyDCP3


READ_ONLY_CALLS = (
    "get_control_state",
    "get_robot_data",
    "get_violation_data",
    "get_servo_data",
    "get_joint_limit_config",
    "get_safety_limits",
)
J6_INDEX = 5


def _at(value: Any, index: int = J6_INDEX) -> Any:
    try:
        return value[index]
    except (IndexError, KeyError, TypeError):
        return None


def _field(data: Any, name: str, default: Any = None) -> Any:
    return data.get(name, default) if isinstance(data, dict) else default


def _call(method: Callable[[], Any]) -> Any:
    try:
        return method()
    except Exception as exc:  # Preserve per-call failures in the raw report.
        return {"diagnostic_exception": f"{type(exc).__name__}: {exc}"}


def _print_raw(results: dict[str, Any]) -> None:
    print("\n========== RAW INDYDCP3 RESULTS ==========")
    for name in READ_ONLY_CALLS:
        print(f"\n--- {name}() ---")
        print(pprint.pformat(results[name], sort_dicts=False, width=120))


def _print_j6_summary(results: dict[str, Any]) -> None:
    control = results["get_control_state"]
    robot = results["get_robot_data"]
    violation = results["get_violation_data"]
    servo = results["get_servo_data"]
    joint_limits = results["get_joint_limit_config"]
    safety = results["get_safety_limits"]

    actual = _at(_field(control, "q"))
    if actual is None:
        actual = _at(_field(robot, "q"))
    target = _at(_field(control, "qdes"))

    pos_limits = _field(joint_limits, "joint_pos_limits", {})
    joint_lower = _at(_field(pos_limits, "lower"))
    joint_upper = _at(_field(pos_limits, "upper"))
    safety_lower = _at(_field(safety, "joint_lower_limits"))
    safety_upper = _at(_field(safety, "joint_upper_limits"))

    violation_joint = _field(violation, "j_index")
    violation_code = _field(violation, "violation_code")
    violation_id = _field(violation, "violation_id")
    violation_message = _field(violation, "violation_str")

    print("\n========== J6 SUMMARY (array index 5) ==========")
    print(f"actual position q (deg, SDK value): {actual!r}")
    print(f"target/commanded position qdes (deg, SDK value): {target!r}")
    print(f"servo status code: {_at(_field(servo, 'status_codes'))!r}")
    print(f"servo active: {_at(_field(servo, 'servo_actives'))!r}")
    print(f"brake active: {_at(_field(servo, 'brake_actives'))!r}")
    print(f"servo temperature: {_at(_field(servo, 'temperatures'))!r}")
    print(f"servo voltage: {_at(_field(servo, 'voltages'))!r}")
    print(f"servo current: {_at(_field(servo, 'currents'))!r}")
    print(f"servo torque: {_at(_field(servo, 'torques'))!r}")
    print(f"joint config min (SDK value): {joint_lower!r}")
    print(f"joint config max (SDK value): {joint_upper!r}")
    print(f"safety min (deg, SDK value): {safety_lower!r}")
    print(f"safety max (deg, SDK value): {safety_upper!r}")
    print(f"violation code: {violation_code!r}")
    print(f"violation id: {violation_id!r}")
    print(f"violation joint index (SDK raw): {violation_joint!r}")
    print(f"violation message: {violation_message!r}")
    print(f"violation integer args: {_field(violation, 'i_args')!r}")
    print(f"violation float args: {_field(violation, 'f_args')!r}")
    print(f"control response: {_field(control, 'response')!r}")
    print(f"servo response: {_field(servo, 'response')!r}")
    print(f"violation response: {_field(violation, 'response')!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default="192.168.1.114")
    args = parser.parse_args()

    print("Indy7 read-only diagnostic")
    print(f"timestamp: {datetime.now().astimezone().isoformat()}")
    print(f"robot IP: {args.ip}")
    print("calls: " + ", ".join(f"{name}()" for name in READ_ONLY_CALLS))

    indy = IndyDCP3(args.ip)
    results = {name: _call(getattr(indy, name)) for name in READ_ONLY_CALLS}
    _print_raw(results)
    _print_j6_summary(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
