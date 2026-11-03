#!/usr/bin/env python3
"""Indy7 ACT Recorder: s로 준비·시작, x로 교정 전환, r로 REVIEW, o로 저장.

기존 관측 구독·20Hz 고정 그리드·60ms 시작 검증·시간 무결성 검사를 사용한다.
실물 Home qdes는 장비별 실측 프로필과 시간/도착 검사를 통과해야 저장한다.
수집 범위/추론 제한: docs/ACT_HOME_RECORDING.md.
Recorder는 로봇/카메라/그리퍼를 시작하거나 제어하지 않는다.
--collection-dir은 고정 Core-108 장부와 Success/Correction 저장·대조를 사용한다.
과거 Failure Dataset은 읽기·대조만 지원하고 새 Failure Episode는 추가하지 않는다.
세부 사용법과 교정 입력 시각의 의미: ai/record/COLLECTION.md.
"""

from __future__ import annotations

import argparse
from array import array
from bisect import bisect_left
from copy import deepcopy
from contextlib import nullcontext
import json
import os
import queue
import shutil
import shlex
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import rclpy
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.datasets.utils import DEFAULT_FEATURES
from rclpy.node import Node
from sensor_msgs.msg import Image, JointState
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import Float32, String
from ai.home_action import CONTEXT_TOPIC, LIVE_CONTEXT_TOPIC, LiveHomeContract, validate_context, vector
from ai import dataset_contract as contract
from ai.record import terminal_ui as ui
from ai.record.correction_marker import reference as frame_reference, validate as validate_correction
from ai.observation_timing import (
    FixedDeadline, SyncOptions, IntegrityError, BoundedHistory, HistoryAligner,
    _check_clock, _header_ns,
)


JOINT_NAMES = list(contract.JOINT_NAMES)
IMAGE_SHAPE = contract.IMAGE_SHAPE
NANOSECONDS = 1_000_000_000
COMMAND_ALIASES = {
    "s": "start",
    "r": "stop",
    "o": "success",
    "x": "correction",
    "d": "discard",
    "q": "quit",
    "start": "start",
    "stop": "stop",
    "success": "success",
    "discard": "discard",
    "delete": "discard",
    "quit": "quit",
}


def _normalize_command(command: str) -> str | None:
    return COMMAND_ALIASES.get(command.strip().lower())


def _print_controls() -> None:
    print(ui.help_text())


def _image_to_rgb(msg: Image) -> np.ndarray:
    if msg.encoding not in ("rgb8", "bgr8"):
        raise ValueError(f"expected rgb8/bgr8 image, received {msg.encoding}")
    row_bytes = msg.width * 3
    rows = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)
    image = rows[:, :row_bytes].reshape(msg.height, msg.width, 3).copy()
    if msg.encoding == "bgr8":
        image = image[:, :, ::-1].copy()
    if image.shape != IMAGE_SHAPE:
        raise ValueError(f"expected {IMAGE_SHAPE} image, received {image.shape}")
    return image


def _atomic_json(path: Path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


class Indy7Observations(Node):
    def __init__(
        self,
        camera_317_topic: str,
        camera_844_topic: str,
        state_topic: str,
        action_topic: str,
        gripper_topic: str,
        sync_options: SyncOptions | None = None,
        *, context_topic=CONTEXT_TOPIC,
    ):
        super().__init__("indy7_act_recorder")
        self.lock = threading.Lock()
        self.images: dict[str, tuple[np.ndarray, float] | None] = {
            "camera_317222074298": None,
            "camera_844212071939": None,
        }
        self.state: tuple[np.ndarray, float] | None = None
        self.action: tuple[np.ndarray, float] | None = None
        self.gripper: tuple[float, float] | None = None
        self.source_timing: dict[str, dict] = {}
        self.history = BoundedHistory(sync_options or SyncOptions())
        self.require_action_context = True
        self.require_home_complete = False
        self.home_contract = None
        self.home_qp_action_stamp = 0
        self.last_home_phase = None
        self.create_subscription(String, context_topic, self._action_context,
            QoSProfile(depth=32, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_subscription(Image, camera_317_topic, lambda msg: self._image("camera_317222074298", msg), 5)
        self.create_subscription(Image, camera_844_topic, lambda msg: self._image("camera_844212071939", msg), 5)
        if context_topic != LIVE_CONTEXT_TOPIC:
            self.create_subscription(JointState, state_topic, self._state, 20)
            self.create_subscription(JointState, action_topic, self._action, 20)
        self.create_subscription(
            Float32,
            gripper_topic,
            self._gripper,
            QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
            ),
        )

    def _image(self, name: str, msg: Image) -> None:
        receipt = self._receipt(msg)
        try:
            image = _image_to_rgb(msg)
        except ValueError as exc:
            self.get_logger().error(str(exc))
            return
        with self.lock:
            self.images[name] = (image, receipt["callback_monotonic_ns"] / NANOSECONDS)
            self._remember_timing(name, receipt, image)

    def _receiver_clock(self):
        clock = self.get_clock()
        return {"ros_time_ns": int(clock.now().nanoseconds),
                "ros_clock_type": str(clock.clock_type),
                "use_sim_time": bool(self.get_parameter("use_sim_time").value)}

    def _receipt(self, msg):
        # Callback entry, before image conversion. Never subtract header time
        # from this clock: publisher/header clock relationships are unverified.
        mono = time.monotonic_ns()
        system = time.time_ns()
        stamp = getattr(getattr(msg, "header", None), "stamp", None)
        clock = self._receiver_clock()
        return {"header_stamp": None if stamp is None else {
                    "sec": int(stamp.sec), "nanosec": int(stamp.nanosec)},
                "callback_monotonic_ns": mono, "callback_system_time_ns": system,
                "clock_read_span_ns": time.monotonic_ns() - mono, "receiver_clock": clock}

    def _remember_timing(self, name, receipt, value):
        previous = self.source_timing.get(name)
        stamp = receipt["header_stamp"]
        old_stamp = previous["header_stamp"] if previous else None
        # Same-topic raw ordering diagnostic only; a clock reset can also cause
        # a decrease. This is not a cross-clock comparison or rejection policy.
        reversed_stamp = stamp is not None and old_stamp is not None and (
            stamp["sec"], stamp["nanosec"]) < (old_stamp["sec"], old_stamp["nanosec"])
        self.source_timing[name] = {
            **receipt, "receipt_sequence": previous["receipt_sequence"] + 1 if previous else 1,
            "header_decreased_since_previous_callback": reversed_stamp,
            "header_decreases_total": (previous["header_decreases_total"] if previous else 0) + int(reversed_stamp),
            "header_clock_relation": "unverified" if stamp is not None else "no_header",
        }
        if not hasattr(self, "history"):
            self.history = BoundedHistory(SyncOptions())
        self.history.append(name, value, self.source_timing[name])

    @staticmethod
    def _joint_vector(msg: JointState) -> np.ndarray | None:
        positions = dict(zip(msg.name, msg.position))
        if not all(name in positions for name in JOINT_NAMES):
            return None
        return np.asarray([positions[name] for name in JOINT_NAMES], dtype=np.float32)

    def _state(self, msg: JointState) -> None:
        if getattr(self,"home_contract",None) is not None:
            return  # The independent reader supplies paired measured q + qdes.
        receipt = self._receipt(msg)
        vector = self._joint_vector(msg)
        if vector is not None:
            with self.lock:
                self.state = (vector, receipt["callback_monotonic_ns"] / NANOSECONDS)
                self._remember_timing("state", receipt, vector)

    def _action(self, msg: JointState) -> None:
        if getattr(self,"home_contract",None) is not None:
            return  # Live source pairs the unchanged QP target with its provenance.
        receipt = self._receipt(msg)
        vector = self._joint_vector(msg)
        if vector is not None:
            with self.lock:
                self.action = (vector, receipt["callback_monotonic_ns"] / NANOSECONDS)
                self._remember_timing("action", receipt, vector)

    def _gripper(self, msg: Float32) -> None:
        receipt = self._receipt(msg)
        with self.lock:
            self.gripper = (float(msg.data), receipt["callback_monotonic_ns"] / NANOSECONDS)
            self._remember_timing("gripper", receipt, self.gripper[0])

    def _action_context(self, msg: String) -> None:
        # One envelope binds source, phase, Home generation and optional sample.
        receipt = self._receipt(msg)
        value = None
        try:
            value = json.loads(msg.data)
            validate_context(value)
            if getattr(self,"home_contract",None) is not None:
                if value.get("evidence_id") != self.home_contract.evidence_id:
                    raise ValueError("home_profile_mismatch")
                measured = vector(value["observed_state"]["position_rad"]).astype(np.float32)
            stamp = value["stamp_ns"]
        except (ValueError, TypeError, KeyError):
            # Latch malformed telemetry as an integrity fault, never drop it.
            receipt["input_error"] = ("action_context_protocol_mismatch" if isinstance(value, dict)
                and value.get("version") != 1 else "invalid_action_context")
            value, stamp = {}, 0
        receipt["header_stamp"] = {"sec": stamp // NANOSECONDS, "nanosec": stamp % NANOSECONDS}
        with self.lock:
            self._remember_timing("action_context", receipt, value)
            self.last_home_phase=value.get("phase")
            if getattr(self,"home_contract",None) is not None and stamp:
                state_source = {**receipt,"header_stamp":{
                    "sec":value["observed_state"]["response_system_ns"]//NANOSECONDS,
                    "nanosec":value["observed_state"]["response_system_ns"]%NANOSECONDS},
                    "header_semantics":"RTDE_response_availability_not_sensor_sample_time"}
                self.state=(measured,receipt["callback_monotonic_ns"]/NANOSECONDS)
                self._remember_timing("state",state_source,measured)
                qp=value.get("qp_action")
                if qp and qp["stamp_ns"]>self.home_qp_action_stamp:
                    target=vector(qp["target_rad"]).astype(np.float32)
                    self.home_qp_action_stamp=qp["stamp_ns"]
                    source={**receipt,"header_stamp":{"sec":qp["stamp_ns"]//NANOSECONDS,
                        "nanosec":qp["stamp_ns"]%NANOSECONDS},
                        "header_semantics":"QP_post_SDK_return_target_via_collection_source"}
                    self.action=(target,receipt["callback_monotonic_ns"]/NANOSECONDS)
                    self._remember_timing("action",source,target)

    def snapshot(self):
        with self.lock:
            return self.images.copy(), self.state, self.action, self.gripper

    def snapshot_with_timing(self):
        with self.lock:
            timing = self._sample_timing()
            return self.images.copy(), self.state, self.action, self.gripper, timing

    def _sample_timing(self):
        mono = time.monotonic_ns()
        system = time.time_ns()
        clock = self._receiver_clock()
        return {"sample_monotonic_ns": mono, "sample_system_time_ns": system,
                "require_action_context": getattr(self, "require_action_context", False),
                "require_home_complete": getattr(self, "require_home_complete", False),
                "home_capture_profile": self.home_contract.profile if getattr(self,"home_contract",None) else None,
                "clock_read_span_ns": time.monotonic_ns() - mono,
                "receiver_clock": clock, "sources": self.source_timing.copy()}

    def history_snapshot(self):
        with self.lock:
            timing = self._sample_timing()
            timing["history_fault_overflows"] = self.history.fault_overflows
            return timing, self.history.snapshot(timing["sample_monotonic_ns"])


def _features() -> dict:
    return contract.dataset_features(IMAGE_SHAPE)


class PendingStart:
    """One delayed validation of the s-processing reference, driven by the loop.

    READY is retained during reception. No RGB snapshots, frames, Dataset or
    temporary take are stored. There is no retry/stability window or re-anchoring.
    """
    def __init__(self, options):
        self.options = options
        self.active = False
        self.request_timing = self.initial_gripper = None
        self.release_ns = None

    def request(self, timing, history):
        if self.active:
            return "[시작 검사] 이미 확인 중입니다. 기존 시작 기준은 유지합니다. r/d : 취소"
        _check_clock(timing, None, self.options)
        self.request_timing = timing
        # A headerless event received later cannot establish its value at t0.
        # Keep this scalar/event source only; do not keep the RGB history.
        self.initial_gripper = history["gripper_carry"]
        self.release_ns = timing["sample_monotonic_ns"]+self.options.ns("receive_wait_ms")
        self.active = True
        return "[시작 검사] 관측 데이터 확인 중..."

    def cancel(self, reason):
        self.active = False
        self.request_timing = self.initial_gripper = None
        self.release_ns = None
        return f"[시작 검사] 취소: {reason} (Pending take 생성 없음)"

    def poll(self, session, history, timing):
        """Validate t0 once after its release; never sleep or spin inside here."""
        if not self.active:
            return None
        if session.state != "READY":
            return self.cancel("episode_state_changed")
        now = timing["sample_monotonic_ns"]
        try:
            _check_clock(timing, self.request_timing, self.options)
            if history["fault_overflows"] != self.request_timing.get("history_fault_overflows", 0):
                raise IntegrityError("history_fault_overflow")
            if now < self.release_ns:
                return None
            # Completing after the first recording release would risk losing
            # slot 1. Do not catch up or shift t0 to disguise a delayed start.
            first_frame_ns = self.release_ns+(NANOSECONDS+session.fps-1)//session.fps
            if now >= first_frame_ns:
                return self.cancel("start_receive_deadline_exceeded: first recording release reached")
            validation_history = {**history, "gripper_carry": self.initial_gripper}
            profile = self.request_timing.get("home_capture_profile")
            live = LiveHomeContract(profile["evidence_id"],profile=profile) if profile else None
            aligner = HistoryAligner.prepare(self.options, validation_history, self.request_timing, home_contract=live)
            # prepare validates the fixed epoch; also verify the actual paired
            # clock reading after reception, without changing the start epoch.
            aligner.check_integrity(history, timing)
            anchor = self.request_timing
            waited_ms = (now-anchor["sample_monotonic_ns"])/1e6
            message = session.start(aligner.target, anchor["sample_monotonic_ns"]/NANOSECONDS,
                                    timing=anchor, aligner=aligner)
            self.active = False
            self.request_timing = self.initial_gripper = None
            self.release_ns = None
            return message
        except IntegrityError as exc:
            return self.cancel(f"{exc.reason}: {exc.details}")


class EpisodeSession:
    """One disk-backed pending take; only public Dataset methods write destinations.

    No RGB history is held in RAM. NPZ files are uncompressed to avoid adding
    compression work to the recording loop (~35 MiB/s for two VGA RGB streams).
    A failed commit is never retried automatically: LeRobot saves are not atomic.
    """

    def __init__(self, pending_root: Path, dataset_factory, fps: int, task: str, collection=None):
        self.pending_root = pending_root
        self.dataset_factory = dataset_factory
        self.fps = fps
        self.task = task
        self.state = "READY"
        self.path: Path | None = None
        self.frames = 0
        self.started = 0.0
        self.action = None
        self.save_failed = False
        self.metadata = {}
        self.started_ns = 0
        self.stopped_ns = None
        self.first_sample_ns = None
        self.last_sample_ns = None
        self.previous_sources = {}
        self.action_origin = {}
        self.deadline = None
        self.stale_sample_skips = 0
        self.aligner = None
        self.receive_wait_ns = 0
        self.integrity = {"valid": True, "violations": []}
        self.collection = collection
        self.demonstration_type = "normal"
        self.correction = None
        self._frame_refs = array('q')
        self._frame_system_refs = array('q')
        self.last_saved = None

    def _metadata(self, **updates):
        self.metadata["demonstration"] = {"type": self.demonstration_type, "correction": self.correction}
        self.metadata.update(updates, frames=self.frames)
        _atomic_json(self.path / "episode.json", self.metadata)

    def start(self, initial_state, now: float, timing=None, aligner=None):
        if self.state != "READY":
            return "[시작 거부] 현재 시연을 먼저 저장하거나 폐기하세요."
        if self.collection:
            self.collection.context()  # Identity/placement fixed before the s reference.
        self.pending_root.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix="take-", dir=self.pending_root))
        self.frames = 0
        self.demonstration_type = "normal"
        self.correction = None
        self._frame_refs = array('q')
        self._frame_system_refs = array('q')
        self.last_saved = None
        self.started = now
        self.started_ns = timing["sample_monotonic_ns"] if timing else round(now * NANOSECONDS)
        self.stopped_ns = None
        self.first_sample_ns = None
        self.last_sample_ns = None
        self.previous_sources = {}
        self.stale_sample_skips = 0
        self.aligner = aligner
        self.receive_wait_ns = aligner.options.ns("receive_wait_ms") if aligner else 0
        self.deadline = FixedDeadline(self.fps, self.started_ns + self.receive_wait_ns)
        self.integrity = {"valid": True, "violations": []}
        self.action = np.asarray(initial_state, dtype=np.float32).copy()
        self.action_origin = {"kind": "initial_hold", "source": (timing or {}).get("sources", {}).get("state")}
        self.state = "RECORDING"
        self.metadata = {"fps": self.fps, "task": self.task, "features": _features(),
                         "timing_version": 1, "start_monotonic_ns": self.started_ns,
                         "start_clock_observation": timing,
                         "alignment_mode": "bounded_history" if aligner else "offline_direct_frame",
                         "sync_options": asdict(aligner.options) if aligner else None,
                         "integrity": self.integrity,
                         "home_action_acceptance": ("live_qdes_profile_v1" if aligner and type(aligner.context_guard.contract) is LiveHomeContract else
                            "simulated_fixture_only" if aligner and aligner.context_guard.contract else "live_locked"),
                         "home_capture_profile": (timing or {}).get("home_capture_profile"),
                         "home_return_required": bool((timing or {}).get("require_home_complete", False)),
                         "clock_policy": "Operator verified Unix-epoch headers; paired start anchors a system reference grid. Monotonic controls release deadlines. Header-minus-monotonic is never used."}
        if self.collection:
            self.metadata["collection"] = self.collection.context()
            self.collection.recording(self.path)
        self._metadata(status="recording")
        return ui.started(self)

    def _sync_demonstration(self):
        if self.collection and self.collection.current:
            self.collection.current.update(demonstration_type=self.demonstration_type, correction=deepcopy(self.correction))

    def _bind_correction(self):
        if self.correction is None or self.correction["start_frame_index"] is not None:
            return
        index = bisect_left(self._frame_refs, self.correction["boundary_monotonic_ns"])
        if index < len(self._frame_refs):
            system = self._frame_system_refs[index]
            self.correction.update(start_frame_index=index, start_dataset_timestamp_s=index/self.fps,
                                   start_reference_monotonic_ns=self._frame_refs[index],
                                   start_reference_system_time_ns=None if system == -1 else system)

    def mark_correction(self, command=None):
        if self.state != "RECORDING":
            return "[교정 전환 거부] RECORDING 상태에서만 x를 사용할 수 있습니다."
        if self.correction is not None:
            return "[교정 전환] 이미 교정 시연입니다. 첫 교정 시작점은 유지합니다."
        processed = time.monotonic_ns()
        received = getattr(command, "received_monotonic_ns", None)
        system = getattr(command, "received_system_time_ns", None)
        if received is not None and (type(received) is not int or received > processed):
            return "[교정 전환 거부] 입력 수신 시각이 처리 시각보다 미래입니다."
        self.demonstration_type = "correction"
        self.correction = {"version": 1, "frame_index_origin": 0,
                           "input_received_monotonic_ns": received, "input_received_system_time_ns": system,
                           "command_processed_monotonic_ns": processed,
                           "boundary_monotonic_ns": processed if received is None else received,
                           "boundary_source": "command_processing_fallback" if received is None else getattr(command, "boundary_source", "stdin_line_received"),
                           "start_frame_index": None, "start_dataset_timestamp_s": None,
                           "start_reference_monotonic_ns": None, "start_reference_system_time_ns": None}
        self._bind_correction()
        self._metadata()
        self._sync_demonstration()
        if self.collection:
            self.collection._persist()
        return ui.correction(self)

    def target_action(self, received, source_timing=None):
        """Legacy direct-frame fixture helper; live recording uses record_aligned()."""
        # Receipt-time boundary excludes already-cached commands, but cannot
        # identify an old command that arrives after this take starts.
        # A valid target is held without an age timeout: neutral QP emits nothing.
        if received is not None and received[1] > self.started:
            self.action = received[0].copy()
            self.action_origin = {"kind": "qp_target", "source": source_timing,
                                  "callback_monotonic_ns": round(received[1] * NANOSECONDS)}
        return self.action.copy()

    def invalidate(self, reason, now_ns=None, **details):
        if self.state != "RECORDING":
            return
        self.integrity["valid"] = False
        self.integrity["violations"].append({"reason": reason, "frame_index": self.frames,
                                             "detected_monotonic_ns": time.monotonic_ns() if now_ns is None else now_ns,
                                             **details})
        self.state = "REVIEW"
        self.stopped_ns = time.monotonic_ns() if now_ns is None else now_ns
        self._metadata(status="review_invalid", timing_summary=self.timing_summary())
        if self.collection:
            self._sync_demonstration()
            self.collection.review(self.integrity)

    def record_aligned(self, history, timing, schedule):
        if self.state != "RECORDING":
            return
        if self.aligner is None:
            raise ValueError("No history aligner configured")
        reference_mono = schedule["deadline_monotonic_ns"] - self.receive_wait_ns
        # Add a duration to an observed Unix anchor; never subtract a ROS header
        # from monotonic. Clock consistency is checked before selecting any data.
        reference_system = self.aligner.start_system + reference_mono - self.started_ns
        context = {"reference_monotonic_ns": reference_mono,
                   "reference_system_time_ns": reference_system, "schedule": schedule}
        try:
            if schedule["missed_deadlines"] or schedule["slot"] != self.frames + 1:
                raise IntegrityError("missed_deadline_or_grid_gap")
            selected, alignment, target = self.aligner.select(history, timing, reference_mono, reference_system)
            grip = np.asarray([selected["gripper"][0]], dtype=np.float32)
            frame = {f"observation.images.{name}": selected[name][0]
                     for name in ("camera_317222074298", "camera_844212071939")}
            frame.update({"observation.state": np.concatenate((selected["state"][0], grip)),
                          "action": np.concatenate((target, grip)), "task": self.task})
            self.action_origin = alignment["recorded_action"]
            if self.action_origin["kind"] == "initial_hold":
                self.action_origin["source"] = getattr(self.aligner, "initial_state_source", None)
            aligned_timing = {**timing, "sources": {name: item[1] for name, item in selected.items()},
                              "alignment": {**alignment, "receive_wait_ns": self.receive_wait_ns,
                                            "selection_valid": True, "header_epoch_verified_by_operator": True}}
            self.add_frame(frame, aligned_timing, schedule)
        except IntegrityError as exc:
            self.invalidate(exc.reason, timing["sample_monotonic_ns"], details=exc.details, **context)

    def add_frame(self, frame, timing=None, schedule=None):
        if self.state != "RECORDING":
            return
        if schedule is not None and schedule["missed_deadlines"]:
            self.invalidate("missed_deadline", schedule=schedule)
            return
        if self.aligner is not None and (timing is None or not timing.get("alignment", {}).get("selection_valid")):
            self.invalidate("unaligned_frame_rejected")
            return
        expected = _features()
        if set(frame) != set(expected) | {"task"} or not isinstance(frame["task"], str):
            raise ValueError("Invalid pending frame keys/task")
        for key, feature in expected.items():
            array = frame[key]
            dtype = np.uint8 if feature["dtype"] == "video" else np.float32
            if not isinstance(array, np.ndarray) or array.shape != feature["shape"] or array.dtype != dtype:
                raise ValueError(f"Invalid pending frame: {key}")
            if feature["dtype"] != "video" and not np.isfinite(array).all():
                raise ValueError(f"Non-finite values: {key}")
        temporary = self.path / f"{self.frames:08d}.npz.tmp"
        try:
            sample = dict(timing) if timing is not None else {
                "sample_monotonic_ns": time.monotonic_ns(), "sample_system_time_ns": time.time_ns(),
                "receiver_clock": None, "sources": {}}
            sample_ns = sample["sample_monotonic_ns"]
            sources = {}
            for name, source in sample.get("sources", {}).items():
                previous = self.previous_sources.get(name)
                stamp = source["header_stamp"]
                sources[name] = {
                    **source, "receipt_age_ns": sample_ns - source["callback_monotonic_ns"],
                    "reused_callback_sample": previous is not None and source["receipt_sequence"] == previous["receipt_sequence"],
                    "same_header_as_previous_frame": previous is not None and stamp is not None and stamp == previous["header_stamp"],
                }
            sample.update(
                timing_version=1, frame_index=self.frames, sources=sources,
                episode_elapsed_ns=sample_ns - self.started_ns,
                actual_interval_ns=None if self.last_sample_ns is None else sample_ns - self.last_sample_ns,
                dataset_nominal_timestamp_s=self.frames / self.fps,
                elapsed_minus_nominal_s=(sample_ns - self.started_ns) / NANOSECONDS - self.frames / self.fps,
                schedule=schedule, recorded_action=self.action_origin,
                sample_lateness_ns=None if schedule is None else sample_ns - schedule["deadline_monotonic_ns"],
                npz_write_started_monotonic_ns=time.monotonic_ns(),
            )
            with temporary.open("wb") as stream:
                np.savez(stream, **frame)
            temporary.replace(self.path / f"{self.frames:08d}.npz")
            sample["npz_write_completed_monotonic_ns"] = time.monotonic_ns()
            _atomic_json(self.path / f"{self.frames:08d}.timing.json", sample)
            self.first_sample_ns = sample_ns if self.first_sample_ns is None else self.first_sample_ns
            self.last_sample_ns = sample_ns
            self.previous_sources = sample.get("sources", {})
            self.frames += 1
            mono, system = frame_reference(sample)
            self._frame_refs.append(mono)
            self._frame_system_refs.append(-1 if system is None else system)
            was_unmapped = self.correction is not None and self.correction["start_frame_index"] is None
            self._bind_correction()
            if was_unmapped and self.correction["start_frame_index"] is not None:
                self._metadata()  # Persist the one transition, not an extra write on every frame.
        except Exception:
            try:
                self.invalidate("pending_frame_write_error")
            except OSError:
                self.integrity["valid"] = False
                self.state = "REVIEW"
            raise

    def timing_summary(self):
        span = None if self.first_sample_ns is None else self.last_sample_ns - self.first_sample_ns
        duration = None if self.stopped_ns is None else self.stopped_ns - self.started_ns
        return {"target_fps": self.fps, "frames": self.frames,
                "start_monotonic_ns": self.started_ns, "stop_monotonic_ns": self.stopped_ns,
                "first_sample_monotonic_ns": self.first_sample_ns, "last_sample_monotonic_ns": self.last_sample_ns,
                "sample_span_ns": span, "recording_duration_ns": duration,
                "actual_sample_fps": (self.frames - 1) * NANOSECONDS / span if span and span > 0 else None,
                "frames_per_recording_second": self.frames * NANOSECONDS / duration if duration and duration > 0 else None,
                "missed_deadlines": self.deadline.missed if self.deadline else 0,
                "unprocessed_deadlines_at_stop": max(0, (duration - self.receive_wait_ns) * self.fps // NANOSECONDS - self.deadline.next_slot + 1)
                    if duration is not None and self.deadline else 0,
                "stale_sample_skips": self.stale_sample_skips}

    def stop(self, now_ns=None):
        if self.state != "RECORDING":
            return "[종료 거부] 현재 녹화 중이 아닙니다."
        self.state = "REVIEW"
        self.stopped_ns = time.monotonic_ns() if now_ns is None else now_ns
        self._metadata(status="review", timing_summary=self.timing_summary())
        if self.collection:
            self._sync_demonstration()
            self.collection.review(self.integrity)
        return ui.review(self)

    def finish_aligned(self, history, timing):
        if self.state != "RECORDING":
            return self.stop()
        try:
            self.aligner.check_integrity(history, timing)
            self.metadata["action_context_stop"] = self.aligner.finish_context(history, timing)
            due = (timing["sample_monotonic_ns"] - self.deadline.started_ns) * self.fps // NANOSECONDS
            # One currently due release can be cancelled by the user's end
            # command. Two or more mean a full earlier grid slot was missed.
            if due - self.deadline.next_slot + 1 > 1:
                raise IntegrityError("unprocessed_deadlines_before_stop")
        except IntegrityError as exc:
            self.invalidate(exc.reason, timing["sample_monotonic_ns"], details=exc.details)
            return ui.invalid(self)
        return self.stop(timing["sample_monotonic_ns"])

    def _publish_timing(self, target_root, episode_index, outcome, repo_id):
        parent = target_root / "meta" / "recorder_timing"
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / f"episode-{episode_index:06d}"
        if destination.exists():
            raise FileExistsError(f"Timing metadata already exists: {destination}")
        temporary = Path(tempfile.mkdtemp(prefix=f".episode-{episode_index:06d}-", dir=parent))
        # Keep pending originals if publishing fails. Never overwrite a sidecar.
        with (temporary / "frames.jsonl").open("w", encoding="utf-8") as stream:
            for index in range(self.frames):
                row = json.loads((self.path / f"{index:08d}.timing.json").read_text(encoding="utf-8"))
                if row["frame_index"] != index:
                    raise ValueError("Pending timing/frame index mismatch")
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        _atomic_json(temporary / "episode.json", {
            **self.metadata, "status": "saved", "timing_summary": self.timing_summary(),
            "dataset_root": str(target_root), "dataset_repo_id": repo_id,
            "dataset_episode_index": episode_index, "outcome": outcome})
        os.rename(temporary, destination)
        return destination

    def save(self, outcome: str, target_root: Path):
        if self.save_failed:
            return "[저장 차단] 앞선 저장 오류를 대조한 뒤 재개하세요.\n복구 : " + ui.recovery_command(self.collection, self.pending_root)
        if not self.integrity["valid"]:
            return ui.invalid(self)
        if self.state != "REVIEW" or self.frames < 2:
            return "[저장 거부] REVIEW 상태이고 최소 2프레임이 있어야 합니다."
        expected_outcome = "correction" if self.demonstration_type == "correction" else "success"
        if outcome not in ("success", "correction") or outcome != expected_outcome:
            return "[저장 거부] 시연 유형과 Dataset이 다릅니다. 새 Failure 저장은 지원하지 않습니다."
        if outcome == "correction":
            try:
                rows = [json.loads((self.path / f"{i:08d}.timing.json").read_text()) for i in range(self.frames)]
                validate_correction(self.correction, self.frames, self.fps, rows)
            except (ValueError, OSError) as exc:
                return f"[저장 거부] 유효한 교정 구간이 없습니다: {exc}\nPending : {self.path}"
        dataset = None
        try:
            expected_index = None
            if self.collection:
                if target_root != self.collection.roots[outcome]:
                    raise ValueError("Collection Dataset destination mismatch")
                self.metadata["collection"] = self.collection.context()
                self._sync_demonstration()
                expected_index = self.collection.begin_save(outcome, self.frames)
            # Persistent marker also prevents an unsafe restart after partial writes.
            self._metadata(status="saving", outcome=outcome, target_root=str(target_root))
            dataset = self.dataset_factory(outcome)
            episode_index = dataset.meta.total_episodes
            if expected_index is not None and episode_index != expected_index:
                raise ValueError("Collection/Dataset episode index changed before save")
            self._metadata(dataset_episode_index=episode_index)
            for index in range(self.frames):
                with np.load(self.path / f"{index:08d}.npz", allow_pickle=False) as stored:
                    frame = {key: stored[key].copy() for key in stored.files}
                frame["task"] = str(frame["task"].item())
                dataset.add_frame(frame)
            # PNG writing is synchronous; no outstanding image queue or buffer swap.
            dataset.save_episode(parallel_encoding=False)
            dataset.finalize()
            count = dataset.meta.total_episodes
            if count != episode_index + 1:
                raise ValueError("Unexpected saved episode count")
            self._metadata(status="dataset_committed_timing_pending", dataset_episodes=count)
            timing_path = self._publish_timing(target_root, episode_index, outcome, getattr(dataset, "repo_id", None))
            self._metadata(status="saved", timing_path=str(timing_path))
            if self.collection:
                self.collection.commit()
        except BaseException as exc:
            self.save_failed = True
            try:
                self._metadata(status="save_failed", error=str(exc))
            except OSError:
                pass  # Keep the earlier 'saving' marker if the disk is unavailable.
            if dataset is not None:
                try:
                    dataset.finalize()
                except Exception:
                    pass
            return ui.save_error(self, exc)
        # Never report a successful Dataset commit as failed just because cleanup fails.
        saved_path = self.path
        condition_id = self.metadata.get("collection", {}).get("condition_id")
        self.last_saved = {"outcome": outcome, "dataset_episode_index": episode_index, "condition_id": condition_id}
        self.path = None
        self.state = "READY"
        self.frames = 0
        try:
            shutil.rmtree(saved_path)
        except OSError:
            return ui.saved(self, outcome, episode_index, condition_id, leftover=saved_path)
        return ui.saved(self, outcome, episode_index, condition_id)

    def discard(self):
        if self.save_failed:
            return "[폐기 차단] 저장 오류의 복구 파일을 보존해야 합니다."
        if self.path is None and not (self.collection and self.collection.current):
            return "[폐기 대상 없음] 현재 시도나 미저장 시연이 없습니다."
        attempt = self.collection.current if self.collection else None
        if attempt:
            attempt["discard_completed"] = False
        if self.collection:
            self.collection.finish("DISCARD")  # Journal before deleting any pending files.
        if self.path is not None:
            shutil.rmtree(self.path)
            from ai.record.collection import sync_directory
            sync_directory(self.pending_root)
        if attempt:
            attempt["discard_completed"] = True
            if attempt["status"] == "INVALID":
                attempt["pending_discarded"] = True
            self.collection._persist()
        self.path = None
        self.frames = 0
        self.state = "READY"
        self.action = None
        self.integrity = {"valid": True, "violations": []}
        self.aligner = None
        self._frame_refs = array('q')
        self._frame_system_refs = array('q')
        return "[폐기 완료]" + ("\n\n" + self.collection.display(title="재수집 조건", with_status=False) if self.collection else "")

    def request_quit(self):
        if self.save_failed:
            return True, f"[종료] 저장 오류 복구 파일 보존\nPending : {self.path}\n복구 : " + ui.recovery_command(self.collection, self.pending_root)
        if not self.integrity["valid"]:
            return True, ui.invalid(self)
        if self.state == "RECORDING":
            self.stop()
        if self.path is not None:
            return False, "[종료 보류] REVIEW 시연을 o로 저장하거나 d로 폐기한 뒤 q를 입력하세요."
        if self.collection:
            self.collection._persist()  # Only claim preserved after a successful durable snapshot.
        return True, ui.panel("Recorder 종료", [*ui.counts(self.collection), "",
                               "수집 기록 보존 완료" if self.collection else "미저장 시연 없음"])


def _validate_destination(root: Path, fps: int, resume: bool):
    if not root.exists():
        return
    if not resume:
        raise ValueError(f"Dataset path already exists: {root}; pass --resume or choose another root")
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    if info.get("total_episodes", 0) == 0:
        raise ValueError(f"Empty/incomplete Dataset needs inspection before reuse: {root}")
    if info.get("fps") != fps or info.get("robot_type") != "indy7":
        raise ValueError(f"Dataset FPS/robot mismatch: {root}")
    custom = {key: value for key, value in info["features"].items() if key in _features()}
    if set(info["features"]) != set(_features()) | set(DEFAULT_FEATURES):
        raise ValueError(f"Dataset feature keys mismatch: {root}")
    for key, feature in _features().items():
        actual = custom.get(key, {})
        if (actual.get("dtype") != feature["dtype"] or tuple(actual.get("shape", ())) != feature["shape"]
                or actual.get("names") != feature["names"]):
            raise ValueError(f"Dataset feature mismatch: {root}: {key}")


def _check_pending(pending_root: Path, roots, collection=None, *, emit=print):
    for manifest in pending_root.glob("take-*/episode.json"):
        info = json.loads(manifest.read_text(encoding="utf-8"))
        if collection:
            identity = info.get("collection", {}).get("attempt_id")
            resolved = next((a for a in collection.state["attempts"] if a["attempt_id"] == identity and
                             (a["status"] in ("SUCCESS", "CORRECTION", "FAILURE") or
                              a.get("recovery") == "destination_unchanged_no_episode_added")), None)
            if resolved:
                emit(f"[복구 기록] 대조가 끝난 staging이 남아 있습니다 (재저장 금지): {manifest.parent}")
                continue
        if info.get("status") in ("saving", "save_failed", "dataset_committed_timing_pending") and info.get("target_root") in roots:
            raise ValueError(f"Unresolved partial save: {manifest}; inspect before collecting again")
        if info.get("status") != "saved":
            emit(f"[미저장 시연] 자동으로 이어 녹화하지 않습니다. Pending : {manifest.parent}")


class CommandInput(str):
    """stdin line receipt, NOT keydown time or physical failure time."""
    def __new__(cls, value, received_monotonic_ns=None, received_system_time_ns=None):
        instance = super().__new__(cls, value)
        instance.received_monotonic_ns = received_monotonic_ns
        instance.received_system_time_ns = received_system_time_ns
        return instance


def _commands() -> queue.Queue[str]:
    commands: queue.Queue[str] = queue.Queue()

    def read_commands():
        while True:
            line = sys.stdin.readline()
            received_mono = time.monotonic_ns()
            received_system = time.time_ns()
            if not line:
                commands.put("__eof__")
                return
            command = line.strip()  # Preserve case in operator metadata/notes.
            commands.put(CommandInput(command, received_mono, received_system))

    threading.Thread(target=read_commands, daemon=True).start()
    return commands


def configure_terminal_logging():
    # Process-local APIs, including libav after upstream restores its native callback.
    # Do not redirect stderr: codec/ROS warnings and errors must remain visible.
    import logging
    import av
    from datasets.utils.tqdm import disable_progress_bars
    disable_progress_bars()
    logging.getLogger("lerobot").setLevel(logging.WARNING)
    av.logging.set_libav_level(av.logging.WARNING)


def main(argv=None, *, frontend=None) -> int:
    # CLI keeps stdin/print; the desktop frontend supplies only input and status.
    emit = print if frontend is None else frontend.message
    configure_terminal_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", default="local/indy7_act")
    parser.add_argument("--root", type=Path, default=Path("datasets/indy7_act"))
    parser.add_argument("--failure-root", type=Path, default=Path("datasets/indy7_act_failures"))
    parser.add_argument("--correction-root", type=Path, default=Path("datasets/indy7_act_corrections"))
    parser.add_argument("--correction-repo-id", default="local/indy7_act_corrections")
    parser.add_argument("--failure-repo-id", default="local/indy7_act_failures")
    parser.add_argument("--pending-root", type=Path, default=Path("datasets/.indy7_act_pending"))
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--task", default="Grasp basket handle from shelf and place on table")
    parser.add_argument("--camera-317-topic", default="/camera_317222074298/camera/color/image_raw")
    parser.add_argument("--camera-844-topic", default="/camera_844212071939/camera/color/image_raw")
    parser.add_argument("--state-topic", default="/joint_states")
    parser.add_argument("--home-profile",type=Path,default=os.environ.get("SIRLAB258_HOME_PROFILE"),
                        help="measured collection-only Home qdes profile; starts a read-only source")
    parser.add_argument("--no-home-capture",action="store_true",help="use the original QP-only recording source")
    parser.add_argument("--action-topic", default="/capstone/teleop_action")
    parser.add_argument("--require-home-complete", action="store_true",
                        help="require a verified Home endpoint before saving; automatic with a measured Home profile")
    parser.add_argument("--gripper-topic", default="/capstone/gripper_state")
    parser.add_argument("--resume", action="store_true", help="append to an existing compatible dataset")
    parser.add_argument("--collection-dir", type=Path, help="enable fixed-plan formal Core-108 collection in this directory")
    for name, default in asdict(SyncOptions()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=int if name == "history_size" else float,
                            default=default)
    args = parser.parse_args(argv)
    home_contract = None
    if args.home_profile and not args.no_home_capture:
        home_contract = LiveHomeContract.load(args.home_profile)

    collection = None
    if args.collection_dir:
        from ai.record.collection import Collection
        if (args.root != Path("datasets/indy7_act") or args.failure_root != Path("datasets/indy7_act_failures") or
                args.pending_root != Path("datasets/.indy7_act_pending") or args.correction_root != Path("datasets/indy7_act_corrections")):
            parser.error("--collection-dir 사용 시 Success/Correction/Failure/Pending 경로 옵션을 생략하세요")
        try:
            collection = Collection(args.collection_dir)
        except (ValueError, OSError, KeyError, IndexError) as exc:
            directory = args.collection_dir.expanduser().resolve()
            emit(ui.panel("수집 차단 - Collection Blocked", [f"오류 : {exc}", f"Pending : {directory / 'pending'}",
                "복구 : .venv-act/bin/python -m ai.record.collection reconcile --collection-dir " + shlex.quote(str(directory))]))
            return 1
        if "definition" in collection.state:
            args.task = collection.state["definition"]["action"]["description"]
        args.root, args.failure_root = collection.roots["success"], collection.roots["failure"]
        args.pending_root = collection.pending_root
        args.correction_root = collection.roots["correction"]
        args.resume = True

    if args.fps != 20:
        parser.error("Bounded-history recording uses the fixed 20Hz Dataset grid (--fps 20)")
    try:
        sync_options = SyncOptions(**{name: getattr(args, name) for name in asdict(SyncOptions())})
    except ValueError as exc:
        parser.error(str(exc))
    roots = {"success": args.root.expanduser().resolve(), "correction": args.correction_root.expanduser().resolve(),
             "failure": args.failure_root.expanduser().resolve()}
    pending_root = args.pending_root.expanduser().resolve()
    paths = [*roots.values(), pending_root]
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:]):
        parser.error("Success/Correction/과거 Failure/Pending 경로는 서로 포함되지 않는 별도 디렉터리여야 합니다")
    try:
        for root in roots.values():
            _validate_destination(root, args.fps, args.resume)
        if collection:
            _check_pending(pending_root, {str(root) for root in roots.values()}, collection, emit=emit)
        else:
            _check_pending(pending_root, {str(root) for root in roots.values()}, emit=emit)
    except (ValueError, OSError) as exc:
        if collection:
            collection.close()
        if frontend:
            emit(ui.panel("시작 차단", [f"오류 : {exc}", f"Pending : {pending_root}",
                "복구 : " + ui.recovery_command(collection, pending_root)]))
        parser.error(str(exc))

    def open_dataset(outcome):
        if outcome not in ("success", "correction"):
            raise ValueError("과거 Failure Dataset은 읽기 전용입니다")
        root = roots[outcome]
        repo_id = args.repo_id if outcome == "success" else args.correction_repo_id
        # Reopen after each committed episode. Resumed recording writes new data/
        # video files instead of concatenating this take into previous files.
        if root.exists():
            _validate_destination(root, args.fps, True)
            return LeRobotDataset(repo_id=repo_id, root=root, vcodec="h264")
        return LeRobotDataset.create(
            repo_id=repo_id, root=root, robot_type="indy7", fps=args.fps,
            features=_features(), use_videos=True, image_writer_threads=0, vcodec="h264",
        )

    try:
        if frontend is None:
            rclpy.init()
        else:
            from rclpy.signals import SignalHandlerOptions
            rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
        node = Indy7Observations(
            args.camera_317_topic,
            args.camera_844_topic,
            args.state_topic,
            args.action_topic,
            args.gripper_topic,
            sync_options,
            **({"context_topic":LIVE_CONTEXT_TOPIC} if home_contract else {}),
        )
        node.home_contract = home_contract
        node.require_home_complete = args.require_home_complete or home_contract is not None
    except BaseException:
        try:
            if rclpy.ok():
                rclpy.shutdown()
        finally:
            if collection:
                collection.close()
        raise
    commands = _commands() if frontend is None else frontend.commands
    home_reader = None
    if home_contract:
        from ai.record.home_source import ReaderProcess
        home_reader=ReaderProcess(args.home_profile)
        home_reader.start()
    session = EpisodeSession(pending_root, open_dataset, args.fps, args.task, collection=collection) if collection else EpisodeSession(pending_root, open_dataset, args.fps, args.task)
    starter = PendingStart(sync_options)
    quit_requested = False
    period = 1.0 / args.fps

    emit(ui.startup(collection))
    try:
        if frontend:
            frontend.update(session, starter, node, force=True)
        while rclpy.ok() and not quit_requested:
            try:
                timeout = 0.01
                if session.state == "RECORDING":
                    timeout = min(timeout, max(0.0, (session.deadline.next_ns - time.monotonic_ns()) / NANOSECONDS))
                elif starter.active:
                    timeout = min(timeout, max(0.0, (starter.release_ns - time.monotonic_ns()) / NANOSECONDS))
                rclpy.spin_once(node, timeout_sec=timeout)
            except KeyboardInterrupt:
                if starter.active:
                    emit(starter.cancel("user_interrupt"))
                if session.state == "RECORDING":
                    timing, history = node.history_snapshot()
                    emit(session.finish_aligned(history, timing))
                    history = None
                if session.path is None:
                    break
                emit(f"[중단] Pending : {session.path}\no : 저장, d : 폐기, q : 종료")
                continue
            while not commands.empty() or (frontend and frontend.abort.is_set()):
                raw_command = "__eof__" if frontend and frontend.abort.is_set() else commands.get()
                scope = (frontend.command_scope(raw_command, session, starter, node)
                         if frontend and raw_command != "__eof__" else nullcontext(True))
                with scope as accepted:
                    if not accepted:
                        continue
                    verb = raw_command.split(maxsplit=1)[0].lower() if raw_command.strip() else ""
                    if verb in ("help", "guide"):
                        emit(ui.help_text() if verb == "help" else ui.guide())
                        continue
                    if collection:
                        try:
                            reply = collection.command(raw_command)
                            if reply is not None:
                                emit(reply)
                                continue
                        except (ValueError, OSError, KeyError, IndexError) as exc:
                            emit(f"[수집 안내 오류] {exc}")
                            continue
                    command = _normalize_command(raw_command)
                    if command == "start":
                        if starter.active:
                            emit("[시작 검사] 이미 확인 중입니다. 기존 시작 기준은 유지합니다. r/d : 취소")
                            continue
                        if session.state != "READY":
                            emit("[시작 거부] 현재 시연을 먼저 저장하거나 폐기하세요.")
                            continue
                        if collection:
                            try:
                                collection.prepare()
                            except (ValueError, OSError) as exc:
                                emit(f"[시작 거부] {exc}")
                                continue
                        timing, history = node.history_snapshot()
                        try:
                            emit(starter.request(timing, history))
                        except IntegrityError as exc:
                            emit(f"[시작 검사] 실패: {exc.reason}: {exc.details}")
                        finally:
                            history = None  # Do not retain an RGB history snapshot in READY/REVIEW.
                    elif command == "stop":
                        if starter.active:
                            emit(starter.cancel("user_stop"))
                        elif session.state == "RECORDING":
                            timing, history = node.history_snapshot()
                            emit(session.finish_aligned(history, timing))
                            history = None
                        else:
                            emit(session.stop())
                    elif command == "correction":
                        emit(session.mark_correction(raw_command))
                    elif command == "success":
                        outcome = "correction" if session.demonstration_type == "correction" else "success"
                        if frontend:
                            frontend.update(session, starter, node, phase="SAVING", force=True)
                        emit(session.save(outcome, roots[outcome]))
                    elif command == "discard":
                        if starter.active:
                            emit(starter.cancel("user_discard"))
                        emit(session.discard())
                    elif command == "quit":
                        if starter.active:
                            emit(starter.cancel("user_quit"))
                        if session.state == "RECORDING":
                            timing, history = node.history_snapshot()
                            emit(session.finish_aligned(history, timing))
                            history = None
                        quit_requested, message = session.request_quit()
                        emit(message)
                        if quit_requested:
                            break
                    elif raw_command == "__eof__":
                        if starter.active:
                            emit(starter.cancel("input_eof"))
                        quit_requested = True
                        if session.path is not None:
                            emit(f"[입력 종료] Pending : {session.path}")
                        break
                    elif raw_command:
                        emit("[명령 오류] 알 수 없는 명령입니다. help로 명령어를 확인하세요.")

            if quit_requested:
                break

            if starter.active:
                timing, history = node.history_snapshot()
                message = starter.poll(session, history, timing)
                history = None
                if message:
                    emit(message)

            now_ns = time.monotonic_ns()
            schedule = session.deadline.consume(now_ns) if session.state == "RECORDING" else None
            if schedule is not None:
                timing, history = node.history_snapshot()
                session.record_aligned(history, timing, schedule)
                history = None
                if not session.integrity["valid"]:
                    emit(ui.invalid(session))
            else:
                delay = min(period / 4, 0.01)
                if home_contract:
                    delay = min(delay,0.001)  # Drain 60 RGB + 20 paired-source callbacks/s.
                if session.state == "RECORDING":
                    delay = min(delay, max(0.0, (session.deadline.next_ns - time.monotonic_ns()) / NANOSECONDS))
                elif starter.active:
                    delay = min(delay, max(0.0, (starter.release_ns - time.monotonic_ns()) / NANOSECONDS))
                time.sleep(delay)
            if frontend:
                frontend.update(session, starter, node)
    except KeyboardInterrupt:
        emit("[중단] 녹화를 중단했습니다.")
    except (ValueError, OSError) as exc:
        if not collection:
            raise
        session.save_failed = True
        emit(ui.panel("수집 차단 - Collection Blocked", [f"오류 : {exc}", f"Pending : {session.path or pending_root}",
                    "복구 : " + ui.recovery_command(collection, pending_root)]))
    finally:
        if starter.active:
            emit(starter.cancel("recorder_exit"))
        if session.path is not None and not quit_requested:
            emit(f"Pending : {session.path}")
        try:
            node.destroy_node()
        finally:
            rclpy.shutdown()
            if home_reader:home_reader.close()
            if collection:
                collection.close()
    return 1 if session.save_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
