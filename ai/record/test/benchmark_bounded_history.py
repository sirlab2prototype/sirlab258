"""Offline wall-clock benchmark; synthetic messages only, never creates a Dataset.

Run with .venv-act/bin/python -B ai/record/test/benchmark_bounded_history.py
All generated pending files live in a fresh project-local directory and are
removed on exit. ROS imports are stubbed; no ROS Node is initialized or spun.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import types

import numpy as np
import psutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    project = Path(__file__).resolve().parents[3]
    for name in ("rclpy", "rclpy.node", "sensor_msgs", "sensor_msgs.msg", "std_msgs", "std_msgs.msg"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["rclpy.node"].Node = object
    sys.modules["sensor_msgs.msg"].Image = object
    sys.modules["sensor_msgs.msg"].JointState = object
    sys.modules["std_msgs.msg"].Float32 = object
    spec = importlib.util.spec_from_file_location("bench_recorder", project / "ai/record/indy7_act_record.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    root = Path(tempfile.mkdtemp(prefix=".act-bounded-benchmark-", dir=project))
    print("TEST_ROOT", root, flush=True)
    options = module.SyncOptions()
    history = module.BoundedHistory(options)
    lock = threading.Lock()
    sources = {}
    sequences = {}
    stop = threading.Event()
    rng = np.random.default_rng(258)
    bases = [rng.integers(0, 256, module.IMAGE_SHAPE, dtype=np.uint8) for _ in range(2)]
    start_mono, start_system = time.monotonic_ns(), time.time_ns()

    def timing(mono=None, system=None):
        mono = time.monotonic_ns() if mono is None else mono
        system = time.time_ns() if system is None else system
        return {"sample_monotonic_ns": mono, "sample_system_time_ns": system,
                "receiver_clock": {"ros_time_ns": system, "use_sim_time": False, "ros_clock_type": "synthetic_system"},
                "sources": sources.copy(), "history_fault_overflows": history.fault_overflows}

    def push(name, header_ns, mono, system, value):
        sequence = sequences.get(name, 0) + 1
        sequences[name] = sequence
        source = {"header_stamp": None if header_ns is None else {
                    "sec": header_ns // 1_000_000_000, "nanosec": header_ns % 1_000_000_000},
                  "callback_monotonic_ns": mono, "callback_system_time_ns": system,
                  "receiver_clock": timing(mono, system)["receiver_clock"], "receipt_sequence": sequence}
        history.append(name, value, source)
        sources[name] = source

    # Synthetic pre-roll provides a known initial pose and gripper command.
    push("camera_317222074298", start_system - 40_000_000, start_mono - 5_000_000, start_system - 5_000_000, bases[0])
    push("camera_844212071939", start_system - 40_000_000, start_mono - 4_000_000, start_system - 4_000_000, bases[1])
    push("state", start_system - 5_000_000, start_mono - 3_000_000, start_system - 3_000_000, np.zeros(6, dtype=np.float32))
    push("gripper", None, start_mono - 1_000_000, start_system - 1_000_000, 0.0)
    start_timing = timing(start_mono, start_system)
    aligner = module.HistoryAligner.prepare(options, history.snapshot(start_mono), start_timing)

    def no_dataset(_):
        raise AssertionError("Benchmark must never create a Dataset")

    session = module.EpisodeSession(root / "pending", no_dataset, 20, "synthetic history benchmark")
    session.start(aligner.target, start_mono / 1e9, start_timing, aligner)

    def producer():
        streams = [("camera_317222074298", 29.98, 33.2, 0),
                   ("camera_844212071939", 29.76, 36.2, 1), ("state", 20, 1.5, None)]
        indices = [0] * len(streams)
        while not stop.is_set():
            due = [start_mono + round(indices[i] * 1e9 / hz + delay * 1e6)
                   for i, (_, hz, delay, _) in enumerate(streams)]
            i = min(range(len(streams)), key=lambda j: due[j])
            if stop.wait(max(0, (due[i] - time.monotonic_ns()) / 1e9)):
                return
            name, hz, _, camera = streams[i]
            value = np.zeros(6, dtype=np.float32) if camera is None else np.roll(
                bases[camera], (indices[i] % 13, indices[i] % 17), (0, 1))
            mono, system = time.monotonic_ns(), time.time_ns()
            with lock:
                push(name, start_system + round(indices[i] * 1e9 / hz), mono, system, value)
            indices[i] += 1

    thread = threading.Thread(target=producer, daemon=True)
    process = psutil.Process()
    cpu0 = process.cpu_times()
    rss = []
    latencies = []
    max_counts = {}
    thread.start()
    try:
        while time.monotonic_ns() < start_mono + round(args.seconds * 1e9) and session.state == "RECORDING":
            tick = session.deadline.consume(time.monotonic_ns())
            if tick is None:
                time.sleep(min(.01, max(0, (session.deadline.next_ns - time.monotonic_ns()) / 1e9)))
                continue
            with lock:
                observed = timing()
                snapshot = history.snapshot(observed["sample_monotonic_ns"])
            for name, count in snapshot["counts"].items():
                max_counts[name] = max(max_counts.get(name, 0), count)
            begin = time.perf_counter_ns()
            session.record_aligned(snapshot, observed, tick)
            snapshot = None
            latencies.append((time.perf_counter_ns() - begin) / 1e6)
            rss.append(process.memory_info().rss)
        stop.set()
        thread.join()
        ended = time.monotonic_ns()
        with lock:
            final_timing = timing(ended, time.time_ns())
            final_history = history.snapshot(ended)
        session.finish_aligned(final_history, final_timing)
        final_history = None
        cpu1 = process.cpu_times()
        rows = [json.loads((session.path / f"{i:08d}.timing.json").read_text()) for i in range(session.frames)]
        intervals = [row["actual_interval_ns"] / 1e6 for row in rows[1:]]
        result = {"duration_s": (ended - start_mono) / 1e9, "frames": session.frames,
                  "summary": session.timing_summary(), "integrity": session.integrity,
                  "interval_ms_p50_p99_max": np.percentile(intervals, [50, 99, 100]).tolist() if intervals else [],
                  "selection_plus_write_ms_p50_p99_max": np.percentile(latencies, [50, 99, 100]).tolist() if latencies else [],
                  "rss_mib_first_last_peak": [rss[0] / 2**20, rss[-1] / 2**20, max(rss) / 2**20] if rss else [],
                  "cpu_percent_one_core": 100 * ((cpu1.user + cpu1.system) - (cpu0.user + cpu0.system)) / ((ended - start_mono) / 1e9),
                  "history_max_counts": max_counts,
                  "raw_camera_history_cap_mib": 2 * options.history_size * int(np.prod(module.IMAGE_SHAPE)) / 2**20,
                  "pending_bytes": sum(path.stat().st_size for path in session.path.iterdir()),
                  "dataset_created": False}
        print("BOUNDED_BENCHMARK_RESULT", json.dumps(result), flush=True)
        return 0 if session.integrity["valid"] else 1
    finally:
        stop.set()
        thread.join()
        if root.parent == project and root.name.startswith(".act-bounded-benchmark-"):
            shutil.rmtree(root)
            print("TEST_CLEANUP", not root.exists(), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
