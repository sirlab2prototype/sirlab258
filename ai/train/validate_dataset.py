"""Read-only, local-only preflight for Recorder v3 datasets.

Never instantiate LeRobotDataset/Metadata: their constructors may download or
create cache files. Reuse official local JSON loaders and version validation;
stream Parquet batches and decode each referenced video once with PyAV.
No sensor timestamp clock conversion, interpolation, or data repair is done.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ai import dataset_contract as contract
from ai.home_action import SimulatedHomeContract, LiveHomeContract, validate_context
from lerobot.datasets.io_utils import load_info, load_stats, load_tasks
from lerobot.datasets.utils import DEFAULT_FEATURES, check_version_compatibility

# Same numerical tolerance used by LeRobotDataset for its nominal time axis.
# This is float/video-PTS precision, NOT a sensor synchronization tolerance.
TIME_EPS = 1e-4
NS = 1_000_000_000


class _Validator:
    def __init__(self, root, repo_id):
        self.root = Path(root).expanduser().resolve()
        self.repo_id = repo_id
        self.errors = Counter()
        self.examples = []
        self.warnings = []
        self.missing = set()
        self.episodes = []
        self.video_segments = {}
        self.vectors = {key: {"count": 0, "sum": np.zeros(7), "sumsq": np.zeros(7),
                              "min": np.full(7, np.inf), "max": np.full(7, -np.inf),
                              "gripper": Counter()} for key in ("observation.state", "action")}
        self.frames = self.success = self.invalid = self.decoded = 0
        self.source_counts = Counter()
        self.sidecar_repo_ids = set()
        self.actual_duration_ns = 0
        self.actual_sample_fps = []
        self.video_validation_s = 0.0

    def error(self, code, detail):
        self.errors[code] += 1
        if len(self.examples) < 100:
            self.examples.append({"code": code, "detail": str(detail)})

    def check(self, condition, code, detail):
        if not condition:
            self.error(code, detail)
        return condition

    def path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Path escapes Dataset: {relative}")
        if not path.is_file():
            if str(path) not in self.missing:
                self.missing.add(str(path))
                self.error("missing_file", path)
            return None
        return path

    def read_json(self, relative):
        path = self.path(relative)
        return json.loads(path.read_text(encoding="utf-8")) if path else None

    def metadata(self):
        if not self.root.is_dir():
            self.error("dataset_path", f"Local Dataset directory does not exist: {self.root}")
            return False
        required = [self.path(name) for name in ("meta/info.json", "meta/stats.json", "meta/tasks.parquet")]
        paths = sorted((self.root / "meta/episodes").glob("*/*.parquet"))
        if not paths:
            self.error("missing_file", "meta/episodes/*/*.parquet")
            self.missing.add("meta/episodes/*/*.parquet")
        if not all(required) or not paths:
            return False
        self.info = load_info(self.root)
        check_version_compatibility(self.repo_id or "local/indy7_act", self.info["codebase_version"], "v3.0")
        self.check(self.info["codebase_version"] == "v3.0", "metadata", "Expected current Recorder Dataset format v3.0")
        self.check(self.info.get("fps") == contract.FPS, "schema", "FPS must be 20")
        self.check(self.info.get("robot_type") == "indy7", "schema", "robot_type must be indy7")
        expected = {**contract.dataset_features(), **DEFAULT_FEATURES}
        features = self.info["features"]
        self.check(set(features) == set(expected), "schema", "Feature names must match the Recorder contract exactly")
        for key, spec in expected.items():
            actual = features.get(key, {})
            self.check(all(actual.get(field) == spec[field] for field in ("dtype", "shape", "names")),
                       "schema", f"Feature dtype/shape/order mismatch: {key}")
        self.tasks = load_tasks(self.root)
        self.check(len(self.tasks) == self.info.get("total_tasks") and len(self.tasks) > 0,
                   "metadata", "Task count mismatch or empty tasks")
        stats = load_stats(self.root)
        self.stored_stats = stats
        for key in self.vectors:
            for name in ("min", "max", "mean", "std", "q10", "q90"):
                value = np.asarray((stats or {}).get(key, {}).get(name, []))
                self.check(value.shape == (7,) and np.isfinite(value).all(), "statistics", f"Invalid stored stats: {key}/{name}")
            item = (stats or {}).get(key, {})
            if all(name in item for name in ("min", "max", "mean", "std", "q10", "q90")):
                self.check(np.all(item["std"] >= 0) and np.all(item["min"] <= item["max"])
                           and np.all(item["q10"] <= item["q90"]), "statistics", f"Inconsistent stored stats: {key}")
            count = np.asarray(item.get("count", []))
            self.check(count.shape == (1,) and np.isfinite(count).all() and count[0] == self.info.get("total_frames"),
                       "statistics", f"Stored stats count mismatch: {key}")
        for key in contract.CAMERA_KEYS:
            item = (stats or {}).get(key, {})
            for name in ("min", "max", "mean", "std"):
                value = np.asarray(item.get(name, []))
                self.check(value.shape == (3, 1, 1) and np.isfinite(value).all(),
                           "statistics", f"Invalid image stats: {key}/{name}")
        for path in paths:
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("Episode metadata path escapes Dataset")
            for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
                self.episodes.extend(batch.to_pylist())
        self.check(bool(self.episodes), "empty_dataset", "Dataset contains no episodes")
        indices = [ep.get("episode_index") for ep in self.episodes]
        self.check(indices == list(range(len(self.episodes))), "episode_index", "Episode indices must be unique and contiguous from 0")
        self.check(len(self.episodes) == self.info.get("total_episodes"), "episode_index", "total_episodes mismatch")
        return True

    def sidecar(self, ep):
        index, length = ep["episode_index"], ep["length"]
        relative = f"{contract.TIMING_DIRECTORY}/episode-{index:06d}"
        meta = self.read_json(f"{relative}/episode.json")
        timing = self.path(f"{relative}/frames.jsonl")
        if meta is None:
            return
        self.check(meta.get("dataset_episode_index") == index, "sidecar", f"Episode {index}: index mismatch")
        self.check(meta.get("frames") == length and meta.get("fps") == contract.FPS
                   and meta.get("timing_version") == 1 and meta.get("status") == "saved",
                   "sidecar", f"Episode {index}: frames/fps/version/status mismatch")
        features = meta.get("features", {})
        for key, expected in contract.dataset_features().items():
            actual = features.get(key, {})
            self.check(actual.get("dtype") == expected["dtype"] and tuple(actual.get("shape", ())) == expected["shape"]
                       and actual.get("names") == expected["names"], "sidecar", f"Episode {index}: feature mismatch {key}")
        self.check(meta.get("outcome") == "success", "outcome", f"Episode {index}: success not confirmed")
        self.success += meta.get("outcome") == "success"
        integrity = meta.get("integrity", {})
        valid = integrity.get("valid") is True and integrity.get("violations") == []
        self.invalid += not valid
        self.check(valid, "integrity", f"Episode {index}: invalid or unconfirmed integrity")
        if meta.get("home_return_required"):
            endpoint = meta.get("action_context_stop") or {}
            self.check(endpoint.get("phase") == "HOME_COMPLETE" and endpoint.get("arrival_verified") is True
                       and meta.get("home_action_acceptance") in ("simulated_fixture_only","live_qdes_profile_v1"),
                       "integrity", f"Episode {index}: required Home endpoint missing or live-unverified")
        if self.repo_id:
            self.check(meta.get("dataset_repo_id") == self.repo_id, "sidecar", f"Episode {index}: repo id mismatch")
        identity = meta.get("dataset_repo_id")
        self.check(isinstance(identity, str) and bool(identity), "sidecar", f"Episode {index}: missing repo id")
        if isinstance(identity, str):
            self.sidecar_repo_ids.add(identity)
        # dataset_root is provenance, not a restriction on relocating/copying data.
        summary = meta.get("timing_summary", {})
        self.check(summary.get("frames") == length, "sidecar", f"Episode {index}: summary count mismatch")
        for key in ("missed_deadlines", "stale_sample_skips"):
            self.check(summary.get(key) == 0, "timestamp", f"Episode {index}: {key} is nonzero or unknown")
        # Recorder permits cancelling ONE currently due release on r. This is
        # an episode boundary, not an interior missing frame compressed in time.
        self.check(summary.get("unprocessed_deadlines_at_stop") in (0, 1),
                   "timestamp", f"Episode {index}: missed releases before stop")
        duration = summary.get("recording_duration_ns")
        if isinstance(duration, int) and duration >= 0:
            self.actual_duration_ns += duration
        else:
            self.error("sidecar", f"Episode {index}: unknown recording duration")
        rate = summary.get("actual_sample_fps")
        if isinstance(rate, (int, float)) and np.isfinite(rate) and rate > 0:
            self.actual_sample_fps.append(rate)
        if not timing:
            return
        count = 0
        previous = previous_ref = previous_system = None
        first = None
        with timing.open(encoding="utf-8") as stream:
            for frame_index, line in enumerate(stream):
                count += 1
                try:
                    row = json.loads(line)
                    self.check(row.get("frame_index") == frame_index, "timestamp", f"Episode {index}: sidecar frame {frame_index}")
                    self.check(abs(row["dataset_nominal_timestamp_s"] - frame_index / contract.FPS) <= TIME_EPS,
                               "timestamp", f"Episode {index}: nominal timestamp {frame_index}")
                    sample = row["sample_monotonic_ns"]
                    first = sample if first is None else first
                    self.check(type(sample) is int and sample >= meta["start_monotonic_ns"],
                               "timestamp", f"Episode {index}: sample before start")
                    self.check(row["episode_elapsed_ns"] == sample - meta["start_monotonic_ns"],
                               "timestamp", f"Episode {index}: elapsed mismatch")
                    self.check((previous is None and row.get("actual_interval_ns") is None) or
                               (previous is not None and sample > previous and row.get("actual_interval_ns") == sample - previous),
                               "timestamp", f"Episode {index}: actual interval mismatch")
                    previous = sample
                    schedule = row.get("schedule")
                    if schedule is not None:
                        self.check(schedule.get("missed_deadlines") == 0 and schedule.get("missed_deadlines_total") == 0
                                   and schedule.get("slot") == frame_index + 1,
                                   "timestamp", f"Episode {index}: missed schedule slot")
                    if meta.get("alignment_mode") == "bounded_history":
                        alignment = row["alignment"]
                        ref = alignment["reference_monotonic_ns"]
                        system = alignment["reference_system_time_ns"]
                        self.check(alignment.get("selection_valid") is True and schedule is not None,
                                   "integrity", f"Episode {index}: unaligned frame")
                        self.check(ref > meta["start_monotonic_ns"] and sample >= ref
                                   and (previous_ref is None or ref - previous_ref == NS // contract.FPS)
                                   and (previous_system is None or system - previous_system == NS // contract.FPS),
                                   "timestamp", f"Episode {index}: reference grid discontinuity")
                        previous_ref, previous_system = ref, system
                        self.alignment(row, meta, index)
                    for name, source in row.get("sources", {}).items():
                        if source.get("reused_callback_sample"):
                            self.source_counts[f"{name}:reused"] += 1
                        if source.get("history_error") or source.get("receipt_age_ns", 0) < 0:
                            self.error("integrity", f"Episode {index}: invalid source {name}")
                except (ValueError, TypeError, KeyError) as exc:
                    self.error("sidecar", f"Episode {index}, timing line {frame_index}: {exc}")
        self.check(count == length, "timestamp", f"Episode {index}: timing row count {count} != {length}")
        if first is not None and previous is not None:
            span = previous - first
            self.check(summary.get("first_sample_monotonic_ns") == first and summary.get("last_sample_monotonic_ns") == previous
                       and summary.get("sample_span_ns") == span, "timestamp", f"Episode {index}: sample summary mismatch")
            self.check(span > 0 and rate is not None and np.isfinite(rate)
                       and abs(rate - (count - 1) * NS / span) <= 1e-6,
                       "timestamp", f"Episode {index}: actual FPS summary mismatch")
        self.check(summary.get("stop_monotonic_ns", -1) - meta["start_monotonic_ns"] == duration,
                   "timestamp", f"Episode {index}: duration summary mismatch")

    def alignment(self, row, meta, index):
        """Recheck recorded causal relations within their declared clock domains."""
        alignment, sources = row["alignment"], row["sources"]
        ref, epoch = alignment["reference_monotonic_ns"], alignment["reference_system_time_ns"]
        options = meta["sync_options"]
        stamps = []
        for name in (*contract.CAMERA_NAMES, "state", "gripper"):
            source = sources[name]
            receipt = source["callback_monotonic_ns"]
            self.check(row["sample_monotonic_ns"] - receipt == source["receipt_age_ns"] >= 0,
                       "timestamp", f"Episode {index}: callback age {name}")
            if name == "gripper":
                self.check(source["header_stamp"] is None and receipt <= ref and source["reference_age_ns"] == ref - receipt,
                           "timestamp", f"Episode {index}: noncausal gripper")
            else:
                header = source["header_stamp"]
                stamp = header["sec"] * NS + header["nanosec"]
                age = epoch - stamp
                limit = options["state_max_age_ms" if name == "state" else "camera_max_age_ms"] * 1e6
                self.check(0 <= age <= limit and source["reference_age_ns"] == age,
                           "timestamp", f"Episode {index}: noncausal/stale {name}")
                stamps.append(stamp)
        self.check(abs(stamps[0] - stamps[1]) == alignment["camera_skew_ns"] <= options["camera_skew_ms"] * 1e6
                   and max(stamps) - min(stamps) == alignment["observation_skew_ns"] <= options["observation_skew_ms"] * 1e6,
                   "timestamp", f"Episode {index}: observation skew mismatch")
        origin = row["recorded_action"]
        if origin["kind"] == "qp_target":
            header = origin["source"]["header_stamp"]
            stamp = header["sec"] * NS + header["nanosec"]
            self.check(meta["start_clock_observation"]["sample_system_time_ns"] <= stamp <= epoch,
                       "timestamp", f"Episode {index}: pre-episode/future action")
        elif origin["kind"] == "home_qdes":
            # No live contract is approved. Support explicit offline fixtures
            # without allowing a message's 'verified' flag to enable hardware data.
            live = meta.get("home_action_acceptance") == "live_qdes_profile_v1"
            self.check(meta.get("home_action_acceptance") in ("simulated_fixture_only","live_qdes_profile_v1"),
                       "integrity", f"Episode {index}: live Home action is locked")
            context = validate_context(origin["action_context"])
            sample = origin["source"]["sample"]
            self.check(context.get("sample") == sample, "integrity", f"Episode {index}: Home sample mismatch")
            validator = (LiveHomeContract(sample["evidence_id"],profile=meta.get("home_capture_profile")) if live
                         else SimulatedHomeContract(sample["evidence_id"]))
            validator.target(context, epoch, stamps[2])
            note = "Simulated Home targets prove software wiring, not physical Home demonstrations."
            if not live and note not in self.warnings:
                self.warnings.append(note)
        else:
            self.check(origin["kind"] == "initial_hold", "integrity", f"Episode {index}: unknown action source")

    def numeric(self, key, value, context):
        array = np.asarray(value, dtype=np.float64)
        if not self.check(array.shape == (7,), "value", f"{context}: {key} shape {array.shape}"):
            return
        if not self.check(np.isfinite(array).all(), "value", f"{context}: {key} NaN/Inf"):
            return
        self.check(array[6] in (0, 1), "gripper", f"{context}: {key} gripper must be binary 0/1")
        stats = self.vectors[key]
        stats["count"] += 1
        stats["sum"] += array
        stats["sumsq"] += array * array
        stats["min"] = np.minimum(stats["min"], array)
        stats["max"] = np.maximum(stats["max"], array)
        stats["gripper"][str(float(array[6]))] += 1

    def data(self):
        groups = {}
        global_end = 0
        for ep in self.episodes:
            index, length = ep["episode_index"], ep["length"]
            self.check(type(index) is int and type(length) is int and length >= 2,
                       "episode_index", f"Invalid index/length: {index}/{length}")
            self.check(ep["dataset_from_index"] == global_end and ep["dataset_to_index"] == global_end + length,
                       "episode_index", f"Episode {index}: global interval mismatch")
            global_end += length
            self.sidecar(ep)
            relative = self.info["data_path"].format(chunk_index=ep["data/chunk_index"], file_index=ep["data/file_index"])
            path = self.path(relative)
            if path:
                groups.setdefault(path, []).append(ep)
            for key in contract.CAMERA_KEYS:
                relative = self.info["video_path"].format(video_key=key, chunk_index=ep[f"videos/{key}/chunk_index"],
                                                          file_index=ep[f"videos/{key}/file_index"])
                path = self.path(relative)
                start, end = ep[f"videos/{key}/from_timestamp"], ep[f"videos/{key}/to_timestamp"]
                self.check(np.isfinite(start) and start >= 0 and abs(end - start - length / contract.FPS) <= TIME_EPS,
                           "timestamp", f"Episode {index}: video interval mismatch {key}")
                if path:
                    self.video_segments.setdefault(path, []).append((start, length, index, key))
        self.check(global_end == self.info.get("total_frames"), "frame_count", "Episode lengths != total_frames")
        for path, episodes in groups.items():
            try:
                expected_rows = ((ep, local) for ep in episodes for local in range(ep["length"]))
                parquet = pq.ParquetFile(path)
                columns = list(self.vectors) + list(DEFAULT_FEATURES)
                self.check(set(parquet.schema_arrow.names) == set(columns), "schema", f"Physical columns mismatch: {path}")
                for key in self.vectors:
                    field = parquet.schema_arrow.field(key).type
                    self.check((pa.types.is_list(field) or pa.types.is_fixed_size_list(field)) and field.value_type == pa.float32(),
                               "schema", f"Physical float32 vector schema mismatch: {path}/{key}")
                for batch in parquet.iter_batches(batch_size=256, columns=columns):
                    for row in batch.to_pylist():
                        self.frames += 1
                        expected = next(expected_rows, None)
                        if expected is None:
                            self.error("frame_count", f"Unreferenced data row in {path}")
                            continue
                        ep, local = expected
                        context = f"episode {ep['episode_index']} frame {local}"
                        self.check(row["episode_index"] == ep["episode_index"] and row["frame_index"] == local
                                   and row["index"] == ep["dataset_from_index"] + local,
                                   "frame_index", context)
                        self.check(np.isfinite(row["timestamp"]) and abs(row["timestamp"] - local / contract.FPS) <= TIME_EPS,
                                   "timestamp", context)
                        self.check(row["task_index"] in self.tasks.task_index.values, "metadata", f"{context}: invalid task index")
                        for key in self.vectors:
                            self.numeric(key, row[key], context)
                self.check(next(expected_rows, None) is None, "frame_count", f"Missing data rows: {path}")
            except Exception as exc:
                self.error("data_read", f"{path}: {exc}")
        self.check(self.frames == self.info.get("total_frames"), "frame_count", "Physical rows != total_frames")
        for key, actual in self.vectors.items():
            stored = (self.stored_stats or {}).get(key, {})
            if actual["count"]:
                for name, value in (("min", actual["min"]), ("max", actual["max"]),
                                    ("mean", actual["sum"] / actual["count"])):
                    estimate = np.asarray(stored.get(name, []))
                    self.check(estimate.shape == (7,) and np.allclose(estimate, value, rtol=1e-5, atol=1e-6),
                               "statistics", f"Stored {key}/{name} differs from physical data (float32 precision tolerance)")
        self.check(len(self.sidecar_repo_ids) == 1, "sidecar", "Inconsistent or missing sidecar repo identities")
        actual = {p.resolve() for p in (self.root / "data").rglob("*.parquet")}
        self.check(actual == set(groups), "metadata", "Unreferenced/missing data parquet files")
        expected_sidecars = {f"episode-{ep['episode_index']:06d}" for ep in self.episodes}
        actual_sidecars = {p.name for p in (self.root / contract.TIMING_DIRECTORY).iterdir()} if (self.root / contract.TIMING_DIRECTORY).is_dir() else set()
        self.check(actual_sidecars == expected_sidecars, "sidecar", "Extra or missing timing episode directories")

    def videos(self):
        started = time.perf_counter()
        for path, segments in self.video_segments.items():
            try:
                segments.sort()
                end = 0
                for start, length, index, key in segments:
                    self.check(abs(start - end) <= TIME_EPS, "timestamp", f"Video segment gap/overlap: {path}, episode {index}")
                    end = start + length / contract.FPS
                expected = (start + i / contract.FPS for start, length, _, _ in segments for i in range(length))
                count = 0
                with av.open(str(path), mode="r") as container:
                    for frame in container.decode(video=0):
                        count += 1
                        self.decoded += 1
                        target = next(expected, None)
                        self.check(target is not None and frame.time is not None and abs(float(frame.time) - target) <= TIME_EPS,
                                   "video_timestamp", f"{path}: decoded frame {count - 1} PTS mismatch")
                        image = frame.to_ndarray(format="rgb24")
                        self.check(not frame.is_corrupt and image.shape == contract.IMAGE_SHAPE and image.dtype == np.uint8,
                                   "video_decode", f"Invalid RGB frame: {path}/{count - 1}")
                self.check(count > 0 and next(expected, None) is None, "video_decode", f"Missing video frames: {path}")
            except Exception as exc:
                self.error("video_decode", f"{path}: {exc}")
        actual = {p.resolve() for p in (self.root / "videos").rglob("*.mp4")}
        self.check(actual == set(self.video_segments), "metadata", "Unreferenced/missing video files")
        self.video_validation_s = time.perf_counter() - started

    def result(self, elapsed):
        stats = {}
        for key, item in self.vectors.items():
            n = item["count"]
            stats[key] = {"count": n, "gripper_distribution": dict(item["gripper"])}
            if n:
                mean = item["sum"] / n
                stats[key].update(mean=mean.tolist(), std=np.sqrt(np.maximum(0, item["sumsq"] / n - mean * mean)).tolist(),
                                  min=item["min"].tolist(), max=item["max"].tolist())
                stationary = np.flatnonzero(item["max"][:6] == item["min"][:6]).tolist()
                if stationary:
                    self.warnings.append(f"{key}: constant joint dimensions {stationary}; inspect coverage (pilot data may be stationary)")
                if len(item["gripper"]) == 1:
                    self.warnings.append(f"{key}: only one gripper command state observed; inspect task coverage")
        if 0 < len(self.episodes) < 108:
            self.warnings.append("Below the 108-success collection goal; pilot training is allowed.")
        return {"report_version": 1, "dataset_path": str(self.root), "contract": {
                    "fps": contract.FPS, "features": contract.dataset_features(), "gripper_index": contract.GRIPPER_INDEX,
                    "joint_state": contract.JOINT_STATE_SEMANTICS, "joint_action": contract.JOINT_ACTION_SEMANTICS,
                    "gripper": contract.GRIPPER_SEMANTICS},
                "trainable": not any(self.errors.values()), "total_episodes": len(self.episodes), "total_frames": self.frames,
                "declared_total_frames": getattr(self, "info", {}).get("total_frames"),
                "nominal_duration_s": self.frames / contract.FPS, "success_episodes": self.success,
                "actual_recording_duration_s": self.actual_duration_ns / NS,
                "episode_actual_fps_range": [min(self.actual_sample_fps), max(self.actual_sample_fps)] if self.actual_sample_fps else None,
                "invalid_episodes": self.invalid, "missing_files": len(self.missing),
                "timestamp_errors": self.errors["timestamp"] + self.errors["video_timestamp"],
                "dimension_value_errors": self.errors["schema"] + self.errors["value"] + self.errors["gripper"],
                "error_counts": {key: value for key, value in self.errors.items() if value}, "error_examples": self.examples, "warnings": self.warnings,
                "statistics": stats, "source_reuse_counts": dict(self.source_counts),
                "decoded_video_frames": self.decoded, "elapsed_s": elapsed,
                "video_validation_s": self.video_validation_s, "video_check_scope": "all frames in every referenced video, sequential RGB decode and PTS check",
                "episode_manifest": [{key: ep.get(key) for key in ("episode_index", "length", "dataset_from_index", "dataset_to_index")}
                                     for ep in self.episodes]}


def validate_dataset(root: str | Path, *, repo_id: str | None = None) -> dict:
    """Return a JSON-serializable report. Any unconfirmed check blocks training.

    Reads original files only; no download, Dataset construction, cache, repair,
    or report writes. All episodes are checked, independently of the 108 goal.
    """
    started = time.perf_counter()
    validator = _Validator(root, repo_id)
    try:
        if validator.metadata():
            validator.data()
            validator.videos()
    except Exception as exc:
        validator.error("validation_read", f"{type(exc).__name__}: {exc}")
    return validator.result(time.perf_counter() - started)


def format_report(report: dict) -> str:
    lines = [f"Dataset: {report['dataset_path']}", "Contract: 20 FPS / 2 RGB 640x480 / state 7D / action 7D",
             f"Episodes: {report['total_episodes']} (success {report['success_episodes']}, invalid {report['invalid_episodes']})",
             f"Frames: {report['total_frames']} / nominal duration: {report['nominal_duration_s']:.3f}s",
             f"Actual recording duration: {report['actual_recording_duration_s']:.3f}s / episode sample FPS range: {report['episode_actual_fps_range']}",
             f"Video check: all decoded frames, {report['decoded_video_frames']} RGB frames / {report['video_validation_s']:.3f}s",
             f"Missing files: {report['missing_files']} / timestamp errors: {report['timestamp_errors']} / dimension-value errors: {report['dimension_value_errors']}",
             f"Training: {'ALLOWED' if report['trainable'] else 'BLOCKED'}",
             f"Error counts: {report['error_counts']}"]
    lines.extend(f"  {item['code']}: {item['detail']}" for item in report["error_examples"])
    lines.extend(f"Warning: {warning}" for warning in report["warnings"])
    lines.append("Statistics: " + json.dumps(report["statistics"], ensure_ascii=False))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path(contract.SUCCESS_ROOT))
    parser.add_argument("--dataset-repo-id", default=None, help="Optional expected identity; checked against each sidecar")
    parser.add_argument("--report", type=Path, help="Write JSON outside the original Dataset (no overwrite)")
    args = parser.parse_args(argv)
    if args.report and args.report.expanduser().resolve().is_relative_to(args.dataset_dir.expanduser().resolve()):
        parser.error("Report must be outside the original Dataset")
    report = validate_dataset(args.dataset_dir, repo_id=args.dataset_repo_id)
    print(format_report(report))
    if args.report:
        # Explicit report destination only. Never overwrite an existing file.
        with args.report.expanduser().open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    return 0 if report["trainable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
