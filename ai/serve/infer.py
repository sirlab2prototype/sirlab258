"""Explicit local inference launcher: owned server + log-only client processes."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import time
import uuid

from ai.observation_timing import SyncOptions
from dataclasses import fields
from ai.serve.ros_recovery import add_recovery_arguments, recovery_options

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None, *, _mock_options=None, _real_options=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--source", choices=("synthetic", "ros"), required=True)
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--operator-initial-gripper", type=int, choices=(0, 1),
                        help="ROS Dry Run only: operator-assumed initial state (0=Open, 1=Grasp), not feedback")
    add_recovery_arguments(parser)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--rpc-timeout-ms", type=int, default=250)
    parser.add_argument("--startup-timeout", type=float, default=60, help="Server checkpoint verification timeout")
    parser.add_argument("--observation-startup-timeout", type=float, default=5)
    parser.add_argument("--max-observation-age-ms", type=float, default=250)
    parser.add_argument("--max-lateness-ms", type=float, default=20)
    parser.add_argument("--prefetch-steps", type=int, default=3)
    parser.add_argument("--torch-threads", type=int, default=2)
    for field in fields(SyncOptions):
        parser.add_argument("--" + field.name.replace("_", "-"), type=int if field.name == "history_size" else float, default=field.default)
    args = parser.parse_args(argv)
    try:
        recovery = recovery_options(args, allowed=args.source == "ros" and _mock_options is None and _real_options is None)
    except ValueError as exc:
        parser.error(str(exc))
    if args.operator_initial_gripper is not None and (args.source != "ros" or _mock_options is not None or _real_options is not None):
        parser.error("--operator-initial-gripper is permitted only for ROS Dry Run, without execution")
    if _real_options and (args.source != "ros" or _mock_options): parser.error("Real execution requires ROS source")
    if _mock_options and args.source != "synthetic": parser.error("Mock execution requires synthetic source")
    if not args.checkpoint.expanduser().exists(): parser.error("Checkpoint path does not exist")
    if any(not math.isfinite(value) or value <= 0 for value in (args.duration, args.startup_timeout,
           args.observation_startup_timeout, args.max_observation_age_ms, args.rpc_timeout_ms)):
        parser.error("Duration, timeouts and ages must be finite and positive")
    if not 0 < args.max_lateness_ms < 50 or args.prefetch_steps < 1 or args.torch_threads < 1:
        parser.error("Invalid scheduler/threads configuration")
    SyncOptions(**{field.name: getattr(args, field.name) for field in fields(SyncOptions)})
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = (args.output_dir or ROOT / "outputs" / f"act_infer_{run_id}").expanduser().resolve()
    checkpoint = args.checkpoint.expanduser().resolve()
    if _real_options and not output.is_relative_to(ROOT):parser.error("Real run output must be inside this project")
    if output.exists() or output.is_relative_to(checkpoint):
        raise ValueError("Output must be new and outside the Checkpoint")
    if any((parent / "meta/info.json").is_file() or (parent / "model.safetensors").is_file() for parent in (output, *output.parents)):
        raise ValueError("Output cannot be inside an existing Dataset or Checkpoint")
    output.mkdir(parents=True, exist_ok=False)
    # Choose a local port; the child bind is authoritative and fails safely on a race.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = f"tcp://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
               HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1")
    server_cmd = [str(ROOT / ".venv-act/bin/python"), "-m", "ai.serve.zmq_act_server", "--checkpoint", str(checkpoint),
                  "--endpoint", endpoint, "--device", args.device, "--ready-file", str(output / "server_ready.json"),
                  "--log", str(output / "server_events.jsonl"), "--torch-threads", str(args.torch_threads),
                  "--run-id", run_id,
                  "--max-observation-age-ms", str(args.max_observation_age_ms)]
    server = client = None
    result = {"run_id": run_id, "checkpoint": str(checkpoint), "source": args.source, "dry_run": _real_options is None,
              "endpoint": endpoint, "status": "failed", "robot_commands_sent": 0}
    if args.operator_initial_gripper is not None:
        result["operator_initial_gripper"] = {"value": args.operator_initial_gripper, "origin": "operator_assumed",
                                              "sensor_measurement": False, "command_success_evidence": False}
    if _mock_options: result["execution_mode"] = "mock"
    if _real_options: result.update(execution_mode="real", robot_commands_sent=None)
    if recovery is not None:
        from dataclasses import asdict
        result["ros_observation_recovery"] = asdict(recovery)
    try:
        with (output / "server.log").open("x") as server_log, (output / "client.log").open("x") as client_log:
            server = subprocess.Popen(server_cmd, stdout=server_log, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
            deadline = time.monotonic() + args.startup_timeout
            while True:
                if server.poll() is not None: raise RuntimeError("Inference server failed; see server.log")
                try:
                    metadata = json.loads((output / "server_ready.json").read_text())
                    break
                except (FileNotFoundError, json.JSONDecodeError):
                    if time.monotonic() >= deadline: raise TimeoutError("Server readiness timeout")
                    time.sleep(.02)
            result.update(server=metadata,checkpoint_id=metadata['checkpoint_id'])
            mode_label = "Explicit Mock execution (software devices only)" if _mock_options else "Explicit Dry Run"
            if _real_options: mode_label = "Explicit REAL execution; hardware writes only after all readiness/authority gates"
            suffix = "" if _real_options else "; no physical robot commands"
            print(f"Verified inference server ready ({metadata['device']}). {mode_label}{suffix}. Logs: {output}", flush=True)
            if _real_options: print(f"STOP: act execute --stop-run {output}; status: act execute --status-run {output}",flush=True)
            if _mock_options and "task_config" in _mock_options:
                print(f"Mock Task controls: append session-bound complete/abort/emergency_stop JSON lines to {output / 'client/task_commands.jsonl'}; Session ID is in client/events.jsonl", flush=True)
            client_args = ["-m", "ai.execute.real_client" if _real_options else "ai.execute.mock_client" if _mock_options else "ai.serve.dry_run", "--source", args.source, "--dry-run", "--endpoint", endpoint,
                           "--metadata", str(output / "server_ready.json"), "--output-dir", str(output / "client"),
                           "--run-id", run_id,
                           "--duration", str(args.duration), "--rpc-timeout-ms", str(args.rpc_timeout_ms),
                           "--startup-timeout", str(args.observation_startup_timeout), "--max-lateness-ms", str(args.max_lateness_ms),
                           "--prefetch-steps", str(args.prefetch_steps)]
            for field in fields(SyncOptions): client_args += ["--" + field.name.replace("_", "-"), str(getattr(args, field.name))]
            if args.operator_initial_gripper is not None:
                client_args += ["--operator-initial-gripper", str(args.operator_initial_gripper)]
            if recovery is not None:
                client_args += ["--ros-observation-recovery", "--ros-stable-observations", str(recovery.stable_observations),
                                "--ros-no-valid-timeout", str(recovery.no_valid_timeout)]
            if _mock_options:
                client_args += ["--execution-config", str(_mock_options["config"]), "--approve-mock", _mock_options["approval"]]
                if _mock_options.get("mock_initial_joints") is not None:
                    client_args += ["--mock-initial-joints", *map(str,_mock_options["mock_initial_joints"])]
                if "task_config" in _mock_options:
                    client_args += ["--task-config", str(_mock_options["task_config"]), "--home-path-evidence", _mock_options["path_evidence"]]
                    if _mock_options.get("complete_after_actions") is not None:
                        client_args += ["--mock-complete-after-actions", str(_mock_options["complete_after_actions"])]
            if _real_options:
                client_args += ["--execution-config", str(_real_options["config"]), "--hardware-profile", str(_real_options["hardware"]), "--approve-real", _real_options["approval"]]
            if args.source == "ros":
                import shlex
                ros_python = ROOT / ".venv/bin/python"
                if not ros_python.is_file(): raise ValueError("ROS .venv/bin/python is missing")
                script = "\n".join(["set -e", "source /opt/ros/jazzy/setup.bash",
                         "source " + shlex.quote(str(ROOT / "ros2_ws/install/setup.bash")),
                         "export PYTHONPATH=" + shlex.quote(str(ROOT)) + ':"${PYTHONPATH:-}"',
                         "exec " + shlex.join([str(ros_python), *client_args])])
                client_cmd = ["bash", "-c", script]
            else:
                client_cmd = [str(ROOT / ".venv-act/bin/python"), *client_args]
            client = subprocess.Popen(client_cmd, stdout=client_log, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
            while client.poll() is None:
                if server.poll() is not None:
                    # Do not turn a server crash into an operator SIGTERM abort.
                    # Only our owned child is signalled; let it revoke/clear/stop.
                    try:client.send_signal(getattr(signal,'SIGUSR1',signal.SIGTERM))
                    except ProcessLookupError:pass
                    try:client.wait(timeout=3)
                    except subprocess.TimeoutExpired:pass  # bounded fallback cleanup below
                    raise RuntimeError("Inference server disconnected")
                time.sleep(.02)
            summary_path = output / "client/summary.json"
            summary = json.loads(summary_path.read_text()) if summary_path.exists() else {"reason": "Client startup failed; see client.log"}
            result.update(status="complete" if client.returncode == 0 and summary.get("status") == "complete" else "failed",
                          server=metadata, client=summary)
            if "task" in summary: result["task"] = summary["task"]
    except KeyboardInterrupt:
        result.update(status="stopped", reason="user_interrupt")
    except Exception as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
    finally:
        for process in (client, server):
            if process is not None and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=(_real_options["stop_timeout_s"]+2 if _real_options else 3))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        # A supervisor failure may occur before the normal result-loading branch.
        # Preserve the child's actual stop/Task outcome and keep the parent reason.
        summary_path=output/'client/summary.json'
        if summary_path.is_file():
            try:
                summary=json.loads(summary_path.read_text())
                result['client']=summary
                if _real_options: result['robot_commands_sent']=summary.get('robot_commands_sent')
                if 'task' in summary:result['task']=summary['task']
            except (OSError,ValueError) as exc:result['client_summary_read_error']=str(exc)
        with (output / "result.json").open("x") as stream: json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result["status"] in ("complete", "stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
