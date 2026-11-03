#!/usr/bin/env python3
"""Prepare/collect raw qdes evidence. No Home, teleop, stop or write commands.

Import and --help/--describe never import Neuromeka or create SDK objects.
Connecting is a separate, explicitly approved operation; never used by Recorder.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import ipaddress
import json
import math
from pathlib import Path
import sys
import time

GETTERS = ("get_control_state", "get_control_data", "get_motion_data", "get_servo_data")


def safe_raw(value):
    """Preserve invalid numbers explicitly as strings, never replace them by 0."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {str(k): safe_raw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_raw(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


def vector(value):
    return (isinstance(value, list) and len(value) == 6 and
            all(type(v) in (int, float) and math.isfinite(v) for v in value))


def payload_error(name, raw):
    if not isinstance(raw, dict):
        return "SDK payload is not an object"
    # Controller 3.3.2 GA12 omits application Response on normal RTDE
    # snapshots. Protobuf int64 codes are JSON decimal strings. Neither is a
    # transport error; absence is recorded as unavailable, never an ACK.
    if "response" in raw:
        response = raw["response"]
        if not isinstance(response, dict) or type(response.get("code")) not in (int,str) or response["code"] not in (0,"0"):
            return "SDK response code is nonzero or malformed"
    try:
        json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError):
        return "nonfinite or unserializable SDK payload; raw preserved as text"
    if name == "get_control_state" and not all(vector(raw.get(k)) for k in ("q", "qdes")):
        return "q/qdes must be finite six-joint arrays; units still unverified"
    return None


def snapshot(client, *, mono=time.monotonic_ns, system=time.time_ns):
    """Sequential RPCs with individual brackets; NOT an atomic sensor snapshot."""
    row = {"kind": "raw_readonly_sample", "controller_sample_timestamp": None,
           "qdes_unit": "unverified", "calls": {}}
    for name in GETTERS:
        start_mono, start_system = mono(), system()
        try:
            raw = getattr(client, name)()
            result = {"raw": safe_raw(raw)}
            result["application_status"] = "reported" if isinstance(raw,dict) and "response" in raw else "unavailable"
            error = payload_error(name, raw)
            if error:
                result["error"] = error
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        end_mono, end_system = mono(), system()
        if end_mono < start_mono or abs((end_system-start_system)-(end_mono-start_mono)) > 10_000_000:
            result["error"] = "local RPC clock bracket is invalid"
        row["calls"][name] = {**result, "request_monotonic_ns": start_mono,
            "response_monotonic_ns": end_mono, "request_system_ns": start_system,
            "response_system_ns": end_system, "rpc_elapsed_ns": end_mono - start_mono}
        if "error" in result:
            break  # Do not issue more RPCs after a failed/unconfirmed response.
    return row


def distribution(values):
    values = sorted(values)
    return {"count": len(values), "min_ns": min(values) if values else None,
            "p95_ns": values[math.ceil(.95 * len(values))-1] if values else None,
            "max_ns": max(values) if values else None}


class Summary:
    """Raw-unit diagnostics; never certifies qdes, physical tracking or safety."""
    def __init__(self):
        self.rpc = {name: [] for name in GETTERS}
        self.times, self.errors, self.slots = [], 0, []
        self.cycles, self.session_errors, self.recorded_end_reasons = [], 0, []
        self.seen_motion = False
        self.segments = {}

    def add(self, row):
        calls = row["calls"]
        self.errors += sum("error" in c for c in calls.values())
        self.slots.append(row.get("slot"))
        for name, call in calls.items():
            self.rpc[name].append(call["rpc_elapsed_ns"])
        if calls:
            self.cycles.append(max(c["response_monotonic_ns"] for c in calls.values())-
                               min(c["request_monotonic_ns"] for c in calls.values()))
        state = calls.get("get_control_state", {})
        if "response_monotonic_ns" in state:
            self.times.append(state["response_monotonic_ns"])
        motion = calls.get("get_motion_data", {}).get("raw", {})
        moving = motion.get("is_in_motion") if isinstance(motion, dict) else None
        label = "reported_moving" if moving is True else (
            "reported_after_motion" if self.seen_motion else "reported_before_motion") if moving is False else "motion_unknown"
        self.seen_motion |= moving is True
        raw = state.get("raw", {})
        if "error" not in state and vector(raw.get("q")) and vector(raw.get("qdes")):
            segment = self.segments.setdefault(label, {"samples": 0, "qdes_min": raw["qdes"][:],
                "qdes_max": raw["qdes"][:], "max_abs_qdes_minus_q": [0]*6})
            segment["samples"] += 1
            for j, (q, desired) in enumerate(zip(raw["q"], raw["qdes"])):
                segment["qdes_min"][j] = min(segment["qdes_min"][j], desired)
                segment["qdes_max"][j] = max(segment["qdes_max"][j], desired)
                segment["max_abs_qdes_minus_q"][j] = max(segment["max_abs_qdes_minus_q"][j], abs(desired-q))

    def report(self, reason):
        intervals = [b-a for a,b in zip(self.times, self.times[1:])]
        span = self.times[-1]-self.times[0] if len(self.times) > 1 else 0
        return {"kind": "observation_summary", "reason": reason, "samples": len(self.times),
            "errors": self.errors, "session_errors": self.session_errors,
            "recorded_end_reasons": self.recorded_end_reasons,
            "qdes_unit": "unverified", "qdes_semantics_verified": False,
            "controller_sample_timestamp": None, "actual_response_hz":
                (len(self.times)-1)*1e9/span if span > 0 else None,
            "rpc": {k: distribution(v) for k,v in self.rpc.items()},
            "sequential_cycle": distribution(self.cycles),
            "response_intervals": distribution(intervals), "intervals_over_75ms": sum(i>75_000_000 for i in intervals),
            "slot_gaps": sum(type(a) is int and type(b) is int and b-a != 1 for a,b in zip(self.slots,self.slots[1:])),
            "motion_segments_approximate": self.segments,
            "note": "Motion RPC is sequential, not atomic with q/qdes; ranges do not prove trajectory semantics or unit."}


def collect(client, stream, seconds, *, mono=time.monotonic_ns, system=time.time_ns, sleep=time.sleep):
    """Bounded sequential getter loop; overrun/error ends observation only."""
    summary = Summary()
    reason, code = "duration_complete", 0
    try:
        start, slot = mono(), 0
        while True:
            deadline = start + slot * 50_000_000
            if deadline >= start + seconds*1e9:
                break
            wait = deadline - mono()
            if wait > 0:
                sleep(wait/1e9)
            if mono() >= start + seconds*1e9:
                break
            if mono() - deadline >= 50_000_000:
                reason, code = "schedule_overrun", 2
                break
            row = snapshot(client, mono=mono, system=system)
            row.update(slot=slot, scheduled_monotonic_ns=deadline)
            summary.add(row)
            stream.write(json.dumps(row, allow_nan=False) + "\n")
            stream.flush()
            if any("error" in call for call in row["calls"].values()):
                reason, code = "getter_failed", 2
                break
            slot += 1
            if mono() > start + slot*50_000_000:
                reason, code = "observation_overrun", 2
                break  # No burst catch-up and no robot stop command.
    except KeyboardInterrupt:
        reason, code = "operator_interrupted_observation", 130
    stream.write(json.dumps(summary.report(reason), allow_nan=False) + "\n")
    return code


class DeadlineRTDE:
    """Per-getter gRPC deadline; no mutation of the installed SDK source."""
    allowed = {"GetControlState", "GetControlData", "GetMotionData", "GetServoData"}

    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        if name not in self.allowed:
            raise AttributeError(name)
        call = getattr(self.original, name)
        return lambda request: call(request, timeout=.1)


def main(argv=None, *, sdk_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--analyze", type=Path, help="offline summary of an existing observation JSONL; never connects")
    parser.add_argument("--connect-readonly", action="store_true",
                        help="requires separate operator approval for this robot connection")
    parser.add_argument("--ip")
    parser.add_argument("--approval-record", help="operator's separately approved observation record")
    parser.add_argument("--output", type=Path, help="new JSONL path; existing files refused")
    parser.add_argument("--seconds", type=float, default=3.)
    parser.add_argument("--label", default="stationary", help="operator label, e.g. stationary or home_observation; never a command")
    args = parser.parse_args(argv)
    if args.connect_readonly and (args.describe or args.analyze):
        parser.error("offline inspection cannot be combined with --connect-readonly")
    if args.describe:
        print(json.dumps({"getters": GETTERS, "requested_hz": 20,
            "commands": [], "qdes_semantics": "unverified",
            "controller_sample_timestamp": None, "connects": False}))
        return 0
    if args.analyze:
        try:
            summary = Summary()
            with args.analyze.open(encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line)
                    if row.get("kind") == "raw_readonly_sample":
                        summary.add(row)
                    elif row.get("kind") == "observation_error":
                        summary.session_errors += 1
                    elif row.get("kind") == "observation_summary":
                        summary.recorded_end_reasons.append(row.get("reason", "unknown"))
            print(json.dumps(summary.report("offline_analysis"), allow_nan=False))
            return 0
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            print(f"Observation analysis failed: {exc}", file=sys.stderr)
            return 2
    if not args.connect_readonly or not args.ip or not args.approval_record or not args.approval_record.strip() or not args.output:
        parser.error("connection disabled: --connect-readonly, --ip, --approval-record and --output required")
    if not 0 < args.seconds <= 120:
        parser.error("--seconds must be finite and in (0, 120]")
    try:
        ipaddress.ip_address(args.ip)
    except ValueError:
        parser.error("--ip must be the separately verified literal controller address")
    # Exclusive destination creation before any SDK initialization.
    try:
        stream = args.output.open("x", encoding="utf-8")
    except OSError as exc:
        print(f"Observation output refused: {exc}", file=sys.stderr)
        return 2
    with stream:
        client, code = None, 2
        try:
            try:
                sdk_version = importlib.metadata.version("neuromeka")
            except importlib.metadata.PackageNotFoundError:
                sdk_version = None
            stream.write(json.dumps({"kind": "observation_session", "sdk_version": sdk_version,
                "robot_ip": args.ip, "approval_record": args.approval_record,
                "started_utc": datetime.now(timezone.utc).isoformat(), "requested_hz": 20, "label": args.label,
                "motion_commands": [], "sequential_rpcs": True,
                "qdes_semantics_verified": False, "qdes_unit": "unverified"}) + "\n")
            stream.flush()
            if sdk_factory is None:
                from neuromeka import IndyDCP3
                sdk_factory = IndyDCP3
            client = sdk_factory(args.ip)
            client.rtde = DeadlineRTDE(client.rtde)
            code = collect(client, stream, args.seconds)
        except KeyboardInterrupt:
            code = 130
            stream.write(json.dumps({"kind": "observation_error", "error": "operator_interrupted_initialization"})+"\n")
        except Exception as exc:
            print(f"Observation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            try:
                stream.write(json.dumps({"kind": "observation_error", "error": f"{type(exc).__name__}: {exc}"})+"\n")
            except OSError:
                pass
        finally:
            if client is not None:
                for name in ("boot", "control", "device", "config", "rtde", "cri"):
                    try:
                        getattr(client, name + "_channel").close()
                    except Exception as exc:
                        code = 2
                        print(f"Observer channel close failed ({name}): {exc}", file=sys.stderr)
                        try:
                            stream.write(json.dumps({"kind": "observation_error", "stage": "channel_close",
                                "channel": name, "error": f"{type(exc).__name__}: {exc}"})+"\n")
                        except OSError:
                            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
