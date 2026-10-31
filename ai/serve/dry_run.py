"""Observation -> RPC -> 20Hz log-only scheduler. No robot output interface."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict, fields
import json
from pathlib import Path
import signal
import threading
import time
import uuid

import numpy as np
from ai import dataset_contract as contract
from ai.observation_timing import FixedDeadline, SyncOptions, IntegrityError
from ai.serve.action_scheduler import DryRunScheduler
from ai.serve.observations import SyntheticSource, RosSource
from ai.serve.protocol import envelope
from ai.serve.rpc import RPCClient
from ai.serve.ros_recovery import (RecoveryMonitor, add_recovery_arguments,
                                   recovery_options, stabilize, transient_camera_error)


class EventLog:
    def __init__(self, path, run_id):
        self.stream = Path(path).open("x", encoding="utf-8")
        self.run_id = run_id
        self.lock = threading.Lock()
    def __call__(self, event, **value):
        with self.lock:
            self.stream.write(json.dumps({"event": event, "run_id": self.run_id, "logged_monotonic_ns": time.monotonic_ns(),
                                          "logged_system_time_ns": time.time_ns(), **value}, allow_nan=False) + "\n")
            self.stream.flush()
    def close(self):
        with self.lock: self.stream.close()


def check_server_identity(reply, metadata, reference=None):
    for key, expected in (("checkpoint_id", metadata["checkpoint_id"]), ("fps", contract.FPS),
                          ("chunk_size", metadata["chunk_size"]), ("n_action_steps", metadata["n_action_steps"])):
        if reply.get(key) != expected:
            raise ValueError(f"server_model_or_config_changed: {key}")
    if reference is not None and reply.get("observation_monotonic_ns") != reference:
        raise ValueError("server_observation_reference_mismatch")


def run_session(source, endpoint, metadata, log, *, duration=10., rpc_timeout_ms=250, startup_timeout=5.,
                max_lateness_ms=20., prefetch_steps=3, _mock_execution=None, ros_recovery=None):
    if ros_recovery is not None and (not isinstance(source, RosSource) or _mock_execution is not None):
        raise ValueError("Observation recovery is restricted to ROS Dry Run without execution")
    if _mock_execution is not None and _mock_execution.is_mock is not True:
        raise ValueError("Dry Run runner permits only a Mock execution bridge")
    if not np.isfinite(duration) or duration <= 0 or startup_timeout <= 0:
        raise ValueError("Duration/startup timeout must be positive")
    session_id = uuid.uuid4().hex
    scheduler = DryRunScheduler(metadata["chunk_size"], metadata["n_action_steps"],
                                max_lateness_ms=max_lateness_ms, prefetch_steps=prefetch_steps)
    rpc = RPCClient(endpoint, rpc_timeout_ms)
    latencies, output_times, latenesses = [], [], []
    max_metrics = 100000  # bounded diagnostic samples; per-action truth stays on disk
    startup_errors, observation_errors = Counter(), Counter()
    valid_observations = 0
    last_reception_log_ns = 0
    recovery = RecoveryMonitor(ros_recovery) if ros_recovery is not None else None
    abandoned_request = None

    def reception_report(force=False):
        nonlocal last_reception_log_ns
        now = time.monotonic_ns()
        if hasattr(source, "diagnostics") and (force or now-last_reception_log_ns >= 1_000_000_000):
            report = source.diagnostics()
            log("reception_diagnostics", session_id=session_id, diagnostics=report)
            last_reception_log_ns = now
            return report

    summary = {"run_id": getattr(log, "run_id", None), "session_id": session_id, "status": "failed", "dry_run": True, "robot_commands_sent": 0,
               "generated_chunks": None,
               "checkpoint_id": metadata["checkpoint_id"], "checkpoint": metadata["checkpoint"],
               "device": metadata.get("device"), "sync_options": asdict(source.options)}
    try:
        log("session_start", **summary)
        reset = envelope("reset", session_id, uuid.uuid4().hex)
        reset["session_started_monotonic_ns"] = time.monotonic_ns()
        response, _, transport = rpc.control(reset)
        check_server_identity(response, metadata)
        log("reset", session_id=session_id, response=response, transport=transport)
        wait_ns = source.options.ns("receive_wait_ms")
        latest = None
        if recovery:
            log("observation_stabilization_start", session_id=session_id, settings=asdict(ros_recovery), timeout=startup_timeout)
            anchor, observation_deadline, latest = stabilize(source, recovery, log, session_id, startup_timeout, reception_report)
            log("observation_stabilized", session_id=session_id, recovery=recovery.report(time.monotonic_ns()))
        else:
            startup = time.monotonic()
            while True:
                source.update()
                reception_report()
                try:
                    anchor = source.begin()
                    break
                except IntegrityError as exc:
                    startup_errors[exc.reason] += 1
                    if startup_errors[exc.reason] == 1:
                        log("observation_not_ready", session_id=session_id, reason=exc.reason, details=exc.details)
                    if time.monotonic() - startup >= startup_timeout:
                        raise RuntimeError(f"observation_startup_timeout: {exc}") from exc
                    time.sleep(.002)
            observation_deadline = FixedDeadline(contract.FPS, anchor["sample_monotonic_ns"]+wait_ns)
        scheduler.start(session_id, anchor["sample_monotonic_ns"])
        last_requested_reference = None
        request_sent_ns = None
        started = time.monotonic_ns()
        warmup_started = started
        log("recording_started", session_id=session_id, anchor=anchor, chunk_size=scheduler.chunk_size,
            n_action_steps=scheduler.n_action_steps, action_owner="client_scheduler_only")
        while time.monotonic_ns() - started < round(duration * 1e9):
            source.update()
            reception_report()
            source.health()
            if recovery:
                recovery.watchdog(time.monotonic_ns())
            try:
                received = rpc.poll()
            except Exception as exc:
                if abandoned_request is None:
                    raise
                log("obsolete_inference_finished", session_id=session_id, request_id=abandoned_request, error=str(exc))
                abandoned_request, received = None, None
            if received is not None and abandoned_request is not None:
                recovery.ignored_responses += 1
                scheduler.ignored += 1
                log("obsolete_inference_finished", session_id=session_id, request_id=abandoned_request,
                    ignored_response=True, response=received[0])
                abandoned_request, received = None, None
            if received is not None:
                reply, chunk, transport = received
                check_server_identity(reply, metadata, scheduler.pending[1] if scheduler.pending else None)
                accepted = scheduler.response(reply["session_id"], reply["request_id"], chunk, transport["client_received_monotonic_ns"])
                log("prediction_response", session_id=session_id, accepted=accepted, response=reply, transport=transport)
                if _mock_execution: _mock_execution.poll(inference=accepted)
                if len(latencies) < max_metrics: latencies.append(reply["inference_ns"])
                request_sent_ns = None
            now = time.monotonic_ns()
            schedule = observation_deadline.consume(now)
            if schedule is not None:
                if schedule["missed_deadlines"]:
                    log("observation_deadline_missed", session_id=session_id, schedule=schedule)
                    raise RuntimeError("observation_deadline_missed")
                reference = schedule["deadline_monotonic_ns"] - wait_ns
                try:
                    latest = source.sample(reference)
                except IntegrityError as exc:
                    if recovery is None or not transient_camera_error(exc):
                        raise
                    # Clear executable state before inspecting limits or waiting
                    # for any RPC. A single old worker drains without reuse.
                    if scheduler.pending is not None:
                        abandoned_request = scheduler.pending[0]
                        recovery.abandoned_requests += 1
                    invalidated = scheduler.pause_observation(exc.reason)
                    recovery.invalidated_actions += invalidated
                    latest, request_sent_ns, last_requested_reference = None, None, None
                    observation_errors[exc.reason] += 1
                    try:
                        recovery.reject(exc, reference, now)
                    finally:
                        log("observation_recovery_pause", session_id=session_id, reference_monotonic_ns=reference,
                            reason=exc.reason, details=exc.details, invalidated_actions=invalidated,
                            abandoned_request=abandoned_request, recovery=recovery.report(now))
                    continue
                valid_observations += 1
                if recovery:
                    recovery.valid(now)
                if _mock_execution:
                    _mock_execution.observe(latest[1])
                    _mock_execution.prepare(session_id, metadata, scheduler.stop)
                log("observation", session_id=session_id, timing=latest[1], schedule=schedule)
            now = time.monotonic_ns()
            if recovery and latest is not None and abandoned_request is None and scheduler.state == "STOPPED":
                scheduler.rewarm_observation()
                warmup_started = now
            execution_ready = _mock_execution is None or getattr(_mock_execution, "ready_for_actions", True)
            if latest and execution_ready and scheduler.needs_request(now) and latest[1]["observation_monotonic_ns"] != last_requested_reference:
                observation, timing = latest
                # Request bytes are encoded on the worker, never a ROS callback.
                request = {**envelope("predict", session_id, uuid.uuid4().hex),
                           "observation_monotonic_ns": timing["observation_monotonic_ns"],
                           "observation_system_time_ns": timing["observation_system_time_ns"],
                           "clock_domain": "same_host_monotonic"}
                scheduler.requested(request["request_id"], timing["observation_monotonic_ns"])
                rpc.submit(request, observation, scheduler.chunk_size)
                last_requested_reference = timing["observation_monotonic_ns"]
                request_sent_ns = now
                log("prediction_request", session_id=session_id, request=request, timing=timing)
            if request_sent_ns and now - request_sent_ns > rpc_timeout_ms * 1_000_000:
                raise TimeoutError("client_inference_timeout")
            if scheduler.state == "WARMUP" and now - warmup_started > round(startup_timeout * 1e9):
                raise TimeoutError("first_prediction_timeout")
            action = scheduler.tick(time.monotonic_ns())
            if action is not None:
                if recovery and recovery.recovering:
                    recovery.resumed()
                    log("observation_recovery_resumed", session_id=session_id, request_id=action["request_id"],
                        recovery=recovery.report(time.monotonic_ns()))
                if _mock_execution: _mock_execution.consume({**action, "checkpoint_id": metadata["checkpoint_id"]})
                log("action", **action, checkpoint_id=metadata["checkpoint_id"])
                if len(output_times) < max_metrics:
                    output_times.append(action["actual_output_monotonic_ns"])
                    latenesses.append(action["schedule"]["lateness_ns"])
            if _mock_execution: _mock_execution.poll()
            if _mock_execution and getattr(_mock_execution, "stop_requested", False):
                summary.update(status="complete", reason="task_control_transition")
                break
            time.sleep(.001)
        if recovery and recovery.recovering:
            recovery.fail("recovery_incomplete_at_session_end")
        if scheduler.consumed == 0:
            raise RuntimeError("no_actions_produced")
        if "reason" not in summary: summary.update(status="complete", reason="duration_complete")
    except KeyboardInterrupt:
        summary.update(status="stopped", reason="user_interrupt")
    except Exception as exc:
        if isinstance(exc, IntegrityError):
            observation_errors[exc.reason] += 1
            if recovery:
                recovery.fatal_errors[exc.reason] += 1
            log("observation_invalid", session_id=session_id, reason=exc.reason, details=exc.details)
        summary.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        log("error", session_id=session_id, reason=summary["reason"],
            missed_deadlines=scheduler.missed, schedule=getattr(scheduler, "last_schedule", None))
    finally:
        if recovery:
            now = time.monotonic_ns()
            if summary["status"] != "complete":
                recovery.invalidated_actions += scheduler.pause_observation("session_terminated")
                recovery.failure = recovery.failure or summary.get("reason")
            if recovery.startup_ns is None and recovery.startup_started is not None:
                recovery.startup_ns = now-recovery.startup_started
            summary["observation_recovery"] = {**recovery.report(now), "actual_output_actions": scheduler.consumed,
                                               "robot_commands_sent": 0}
        scheduler.stop(summary.get("reason", "cleanup"))  # clear actions before waiting for RPC cleanup
        summary["observation_validation"] = {"valid_samples": valid_observations,
            "invalid_events": dict(observation_errors), "startup_attempt_errors": dict(startup_errors),
            "startup_note": "Repeated readiness polls are not frame-drop counts"}
        summary["reception_diagnostics"] = reception_report(force=True)
        summary["safety_validation"] = {"evaluated": False, "violations": None,
            "reason": "Observation-only Dry Run has no verified controller status or reviewed hardware limits"}
        if _mock_execution:
            summary["safety_validation"] = {"evaluated": True, "scope": "Mock execution profile only",
                "violations": None, "reason": "Use execution events for Guard rejection reasons"}
        if _mock_execution:
            summary["execution"] = _mock_execution.finish(summary.get("reason", "cleanup"), fault=summary["status"] == "failed")
            summary["execution_mode"] = "mock"
            if hasattr(_mock_execution, "task_result"):
                summary["task"] = _mock_execution.task_result
                summary["status"] = "complete" if summary["task"]["success"] else "failed"
                summary["reason"] = summary["task"]["reason"]
            if summary["execution"]["state"] != "STOPPED" or not summary["execution"]["stop_confirmed"]:
                summary["status"] = "failed"
        log("actions_cleared", session_id=session_id)
        rpc.close()  # bounded request timeout, discard any outstanding response
        cleanup = RPCClient(endpoint, rpc_timeout_ms)
        try:
            response, _, transport = cleanup.control(envelope("end", session_id, uuid.uuid4().hex))
            check_server_identity(response, metadata)
            summary["generated_chunks"] = response.get("session_generated_chunks")
            log("reset_end", session_id=session_id, response=response, transport=transport)
        except Exception as exc:
            log("reset_end_failed", session_id=session_id, reason=str(exc))
        finally:
            cleanup.close()
            source.close()
    intervals = np.diff(output_times)
    summary.update(chunks=scheduler.chunks, accepted_chunks=scheduler.chunks, actions=scheduler.consumed, ignored_responses=scheduler.ignored,
                   missed_action_deadlines=scheduler.missed,
                   actual_action_fps=(len(output_times) - 1) * 1e9 / (output_times[-1] - output_times[0]) if len(output_times) > 1 else None,
                   action_interval_ms_min=float(intervals.min() / 1e6) if len(intervals) else None,
                   action_interval_ms_max=float(intervals.max() / 1e6) if len(intervals) else None,
                   schedule_lateness_ms_max=max(latenesses) / 1e6 if latenesses else None,
                   inference_ms_p50=float(np.median(latencies) / 1e6) if latencies else None,
                   inference_ms_p95=float(np.percentile(latencies, 95) / 1e6) if latencies else None,
                   inference_ms_max=max(latencies) / 1e6 if latencies else None,
                   metric_samples=len(output_times), metric_sample_limit=max_metrics,
                   metrics_truncated=scheduler.consumed > max_metrics)
    log("session_end", **summary)
    return summary


def main(argv=None, *, _mock_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("synthetic", "ros"), required=True)
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--operator-initial-gripper", type=int, choices=(0, 1),
                        help="ROS Dry Run only; operator assumption, not a sensor measurement or command acknowledgement")
    add_recovery_arguments(parser)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--rpc-timeout-ms", type=int, default=250)
    parser.add_argument("--startup-timeout", type=float, default=5)
    parser.add_argument("--max-lateness-ms", type=float, default=20)
    parser.add_argument("--prefetch-steps", type=int, default=3)
    for field in fields(SyncOptions):
        parser.add_argument("--" + field.name.replace("_", "-"), type=int if field.name == "history_size" else float, default=field.default)
    args = parser.parse_args(argv)
    try:
        ros_recovery = recovery_options(args, allowed=args.source == "ros" and _mock_factory is None)
    except ValueError as exc:
        parser.error(str(exc))
    if args.operator_initial_gripper is not None and (args.source != "ros" or _mock_factory is not None):
        parser.error("--operator-initial-gripper is permitted only for ROS Dry Run, without execution")
    if any(not np.isfinite(value) or value <= 0 for value in (args.duration, args.startup_timeout, args.rpc_timeout_ms)):
        parser.error("Duration/timeouts must be finite and positive")
    if not 0 < args.max_lateness_ms < 50 or args.prefetch_steps < 1:
        parser.error("Invalid scheduler configuration")
    options = SyncOptions(**{field.name: getattr(args, field.name) for field in fields(SyncOptions)})
    if args.output_dir.exists(): raise ValueError("Client output must be a new directory")
    metadata = json.loads(args.metadata.read_text())
    if metadata.get("protocol") != "indy7_act_rgb2_v2" or metadata.get("fps") != 20:
        raise ValueError("Server metadata protocol/FPS mismatch")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    log = EventLog(args.output_dir / "events.jsonl", args.run_id or uuid.uuid4().hex)
    def interrupt(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    if hasattr(signal,"SIGUSR1"):
        def server_lost(*_): raise ConnectionError("inference_server_disconnected")
        # Owned launcher reports server failure separately from operator Ctrl+C.
        signal.signal(signal.SIGUSR1,server_lost)
    try:
        execution = None
        if _mock_factory:
            if args.source != "synthetic": raise ValueError("Mock execution uses synthetic observations only")
            source, execution = _mock_factory(options, log, metadata, args.output_dir)
        else:
            source = SyntheticSource(options) if args.source == "synthetic" else RosSource(
                options, operator_initial_gripper=args.operator_initial_gripper)
        summary = run_session(source, args.endpoint, metadata, log, duration=args.duration,
                              rpc_timeout_ms=args.rpc_timeout_ms, startup_timeout=args.startup_timeout,
                              max_lateness_ms=args.max_lateness_ms, prefetch_steps=args.prefetch_steps,
                              _mock_execution=execution, ros_recovery=ros_recovery)
        with (args.output_dir / "summary.json").open("x") as stream: json.dump(summary, stream, indent=2)
        print(json.dumps(summary, indent=2), flush=True)
        return 0 if summary["status"] in ("complete", "stopped") else 1
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
