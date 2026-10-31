"""Bounded, observation-only ROS/synthetic adapters. No publishers or services."""
from dataclasses import asdict
import threading
import time

import numpy as np
from ai import dataset_contract as contract
from ai.observation_timing import BoundedHistory, HistoryAligner, SyncOptions, IntegrityError, _check_clock, _header_ns
from ai.serve.observation_diagnostics import ReceptionDiagnostics

NS = 1_000_000_000


class HistorySource:
    def __init__(self, options=None, receiver_clock=None):
        self.options = options or SyncOptions()
        self.history = BoundedHistory(self.options)
        self.receiver_clock = receiver_clock
        self.lock = threading.Lock()
        self.sequences = {}
        self.error = None
        self.aligner = None
        self.previous_sources = {}
        self.reception = ReceptionDiagnostics(epoch_window_ns=round(self.options.history_seconds*NS))
        self.subscription_started_system_ns = None

    def timing(self):
        mono, system = time.monotonic_ns(), time.time_ns()
        receiver = self.receiver_clock() if self.receiver_clock else {
            "ros_time_ns": system, "ros_clock_type": "synthetic_system_time", "use_sim_time": False}
        return {"sample_monotonic_ns": mono, "sample_system_time_ns": system, "receiver_clock": receiver,
                "clock_read_span_ns": time.monotonic_ns() - mono}

    def append(self, name, value, stamp, timing=None, message_format=None):
        timing = timing or self.timing()
        with self.lock:
            sequence = self.sequences.get(name, 0) + 1
            self.sequences[name] = sequence
            source = {"header_stamp": stamp, "callback_monotonic_ns": timing["sample_monotonic_ns"],
                      "callback_system_time_ns": timing["sample_system_time_ns"],
                      "receiver_clock": timing["receiver_clock"], "clock_read_span_ns": timing["clock_read_span_ns"],
                      "receipt_sequence": sequence}
            # ROS startup barrier: let new sensor generations arrive rather than
            # selecting middleware backlog. Headerless gripper events remain
            # receipt-based. Never compare an epoch Header to monotonic time.
            if self.aligner is None and self.subscription_started_system_ns is not None and stamp is not None:
                try:
                    _check_clock(timing, self.history.last_clock, self.options)
                    valid_stamp = stamp["sec"] >= 0 and 0 <= stamp["nanosec"] < NS
                    header_ns = stamp["sec"]*NS+stamp["nanosec"]
                    if valid_stamp and 0 < header_ns < self.subscription_started_system_ns:
                        self.reception.record(name, source, value, rejected="pre_subscription_header_discarded",
                                              message_format=message_format)
                        return
                except IntegrityError:
                    pass  # Shared History must retain/latch clock faults.
            self.history.append(name, value, source)
            self.reception.record(name, source, value, message_format=message_format)

    def ingest(self, name, message):
        timing = self.timing()  # callback entry, before RGB conversion
        try:
            stamp = getattr(getattr(message, "header", None), "stamp", None)
            stamp = None if stamp is None else {"sec": int(stamp.sec), "nanosec": int(stamp.nanosec)}
            if name in contract.CAMERA_NAMES:
                message_format = {"width": message.width, "height": message.height,
                                  "encoding": message.encoding, "step": message.step}
                if (message.height, message.width, 3) != contract.IMAGE_SHAPE or message.encoding not in ("rgb8", "bgr8"):
                    raise ValueError("Unexpected RGB size/encoding")
                if message.step < message.width * 3 or len(message.data) != message.height * message.step:
                    raise ValueError("Malformed image row buffer")
                rows = np.frombuffer(message.data, dtype=np.uint8).reshape(message.height, message.step)
                value = rows[:, :message.width * 3].reshape(contract.IMAGE_SHAPE)
                value = (value[:, :, ::-1] if message.encoding == "bgr8" else value).copy()
            elif name == "state":
                message_format = {"received_joint_names": list(message.name), "selected_order": list(contract.JOINT_NAMES),
                                  "position_count": len(message.position), "unit": "rad per publisher contract"}
                if len(message.name) != len(message.position) or len(set(message.name)) != len(message.name):
                    raise ValueError("Malformed joint names/positions")
                positions = dict(zip(message.name, message.position))
                value = np.asarray([positions[key] for key in contract.JOINT_NAMES], dtype=np.float32)
                if not np.isfinite(value).all():
                    raise ValueError("Nonfinite joints")
            elif name == "gripper":
                message_format = {"message": "std_msgs/Float32", "header": False}
                value = float(message.data)
                stamp = None
                if value not in (0, 1):
                    raise ValueError("Unknown gripper command state")
            else:
                raise ValueError("Unsupported observation topic")
            self.append(name, value, stamp, timing, message_format)
        except Exception as exc:
            with self.lock:
                self.error = f"malformed_{name}: {exc}"
                self.reception.record(name, {"callback_monotonic_ns": timing["sample_monotonic_ns"],
                    "callback_system_time_ns": timing["sample_system_time_ns"]}, rejected=f"malformed_{name}")

    def diagnostics(self):
        with self.lock:
            result = self.reception.snapshot(time.monotonic_ns())
            result.update(source_error=self.error, subscription_started_system_ns=self.subscription_started_system_ns,
                          sync_options=asdict(self.options))
            return result

    def snapshot(self):
        if self.error:
            raise IntegrityError(self.error)
        with self.lock:
            timing = self.timing()
            history = self.history.snapshot(timing["sample_monotonic_ns"])
            timing["history_fault_overflows"] = history["fault_overflows"]
            timing["sources"] = {name: values[-1][1] for name, values in history["items"].items() if values}
        return timing, history

    def begin(self):
        timing, history = self.snapshot()
        self.aligner = HistoryAligner.prepare(self.options, history, timing)
        self.previous_sources = {}
        return timing

    def sample(self, reference_ns):
        timing, history = self.snapshot()
        reference_system = self.aligner.start_system + reference_ns - self.aligner.start_mono
        selected, alignment, _ = self.aligner.select(history, timing, reference_ns, reference_system)
        # Reuse selection/clock guards, not the Recorder's QP-action metadata.
        # Actual ACT predictions are logged separately by the scheduler.
        alignment = {key: value for key, value in alignment.items() if key not in (
            "recorded_action", "ignored_pre_episode_actions", "last_ignored_action_source")}
        selected = {name: (value, {**source,
                    "reused_callback_sample": name in self.previous_sources and source["receipt_sequence"] == self.previous_sources[name]["receipt_sequence"],
                    "same_header_as_previous_observation": name in self.previous_sources and source["header_stamp"] is not None and source["header_stamp"] == self.previous_sources[name]["header_stamp"],
                    "receipt_age_ns": timing["sample_monotonic_ns"] - source["callback_monotonic_ns"]})
                    for name, (value, source) in selected.items()}
        self.previous_sources = {name: item[1] for name, item in selected.items()}
        observation = {f"observation.images.{name}": selected[name][0] for name in contract.CAMERA_NAMES}
        observation["observation.state"] = np.concatenate((selected["state"][0], [selected["gripper"][0]])).astype(np.float32)
        metadata = {"observation_monotonic_ns": reference_ns, "observation_system_time_ns": reference_system,
                    "clock_domain": "same_host_monotonic", "sample_created_monotonic_ns": timing["sample_monotonic_ns"],
                    "sources": {name: item[1] for name, item in selected.items()}, "alignment": alignment}
        return observation, metadata

    def health(self):
        # Check at every loop, even while a GPU request is pending.
        timing, history = self.snapshot()
        if self.aligner:
            self.aligner.check_integrity(history, timing)

    def update(self):
        pass

    def close(self):
        pass


class SyntheticSource(HistorySource):
    """30Hz cameras with 33.2/36.2ms delivery, 20Hz joints with 1.5ms delay."""
    def __init__(self, options=None):
        super().__init__(options)
        self.origin = self.timing()
        self.generated = {**{name: 0 for name in contract.CAMERA_NAMES}, "state": 0}
        self.append("gripper", 0., None)

    def update(self):
        now = time.monotonic_ns()
        elapsed = now - self.origin["sample_monotonic_ns"]
        for name, hz, delay in [(contract.CAMERA_NAMES[0], 30, 33.2), (contract.CAMERA_NAMES[1], 30, 36.2), ("state", 20, 1.5)]:
            while True:
                index = self.generated[name]
                generated = round(index * NS / hz)
                if generated + round(delay * 1e6) > elapsed:
                    break
                if elapsed - generated > round(self.options.history_seconds * NS):
                    raise IntegrityError("synthetic_delivery_stall")
                stamp = self.origin["sample_system_time_ns"] + generated
                header = {"sec": stamp // NS, "nanosec": stamp % NS}
                value = np.full(contract.IMAGE_SHAPE, index % 255, dtype=np.uint8) if name in contract.CAMERA_NAMES else np.full(6, np.sin(index / 20) * .01, dtype=np.float32)
                self.append(name, value, header)
                self.generated[name] += 1


class RosSource(HistorySource):
    def __init__(self, options=None, *, operator_initial_gripper=None):
        if operator_initial_gripper is not None and (
                type(operator_initial_gripper) is not int or operator_initial_gripper not in (0, 1)):
            raise ValueError("Operator initial gripper must be exactly 0 or 1 (ROS Dry Run only)")
        # Lazy imports: synthetic mode and server never initialize ROS.
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Image, JointState
        from std_msgs.msg import Float32
        rclpy.init()
        self.rclpy = rclpy
        self.node = Node("indy7_act_dry_run_observations")
        def clock():
            ros_clock = self.node.get_clock()
            return {"ros_time_ns": int(ros_clock.now().nanoseconds), "ros_clock_type": str(ros_clock.clock_type),
                    "use_sim_time": bool(self.node.get_parameter("use_sim_time").value)}
        super().__init__(options, clock)
        initialized = self.timing()
        self.subscription_started_system_ns = initialized["sample_system_time_ns"]
        self.operator_gripper = None
        if operator_initial_gripper is not None:
            # Snapshot-only fallback, not a ROS event or an entry in History.
            # Sequence zero precedes the first real ROS event (sequence one).
            # The shared aligner uses the local event-time boundary internally;
            # public metadata explicitly distinguishes it from a callback.
            self.operator_gripper = (float(operator_initial_gripper), {
                "origin": "operator_assumed", "header_stamp": None,
                "sensor_measurement": False, "command_success_evidence": False,
                "assumed_at_monotonic_ns": initialized["sample_monotonic_ns"],
                "assumed_at_system_time_ns": initialized["sample_system_time_ns"],
                "callback_monotonic_ns": initialized["sample_monotonic_ns"],
                "callback_system_time_ns": initialized["sample_system_time_ns"],
                "receiver_clock": initialized["receiver_clock"],
                "clock_read_span_ns": initialized["clock_read_span_ns"],
                "receipt_sequence": 0, "history_error": None})
        for name in contract.CAMERA_NAMES:
            self.node.create_subscription(Image, f"/{name}/camera/color/image_raw", lambda msg, key=name: self.ingest(key, msg), qos_profile_sensor_data)
        self.node.create_subscription(JointState, "/joint_states", lambda msg: self.ingest("state", msg), qos_profile_sensor_data)
        self.node.create_subscription(Float32, "/capstone/gripper_state", lambda msg: self.ingest("gripper", msg), 10)
        self.stopping = threading.Event()
        def spin():
            try:
                while not self.stopping.is_set():
                    rclpy.spin_once(self.node, timeout_sec=.01)
            except Exception as exc:
                self.error = f"ros_spin_failed: {exc}"
        self.thread = threading.Thread(target=spin, name="act-observation-callbacks", daemon=True)
        self.thread.start()

    def begin_stabilization(self):
        """Anchor only; NOT ready for inference until delayed samples validate.

        prepare() validates cameras against wall-now, before the receive wait.
        Recovery startup instead validates the actual reference grid via sample().
        Fresh measured joints and known/explicitly assumed gripper are still
        required here; no camera observation is invented or marked valid.
        """
        timing, history = self.snapshot()
        system = timing["sample_system_time_ns"]
        states = [(value, source) for value, source in history["items"].get("state", ())
                  if source.get("history_error") is None and
                  0 <= system-_header_ns(source) <= self.options.ns("state_max_age_ms")]
        if not states:
            raise IntegrityError("no_initial_state")
        value, source = max(states, key=lambda item: _header_ns(item[1]))
        timing["sources"]["state"] = source
        self.aligner = HistoryAligner(self.options, timing, value, history["gripper_carry"])
        self.previous_sources = {}
        return timing

    @staticmethod
    def _gripper_metadata(source):
        source = dict(source)
        if source.get("origin") == "operator_assumed":
            source.update(callback_monotonic_ns=None, callback_system_time_ns=None,
                          timestamp_semantics="local_operator_assumption_initialization_not_ROS_receipt")
            if "receipt_age_ns" in source:
                source["assumption_age_ns"] = source.pop("receipt_age_ns")
                source["reused_callback_sample"] = None
        return source

    def snapshot(self):
        timing, history = super().snapshot()  # Invalid real messages still fail closed.
        def mark(item):
            value, source = item
            return value, {**source, "origin": "ros_message", "sensor_measurement": False,
                           "command_success_evidence": "reported_by_publisher_not_verified_by_client"}
        carry = history["gripper_carry"]
        if carry is not None:
            history["gripper_carry"] = mark(carry)
            history["items"]["gripper"] = tuple(mark(item) for item in history["items"].get("gripper", ()))
        elif self.operator_gripper is not None:
            history["gripper_carry"] = self.operator_gripper
        if history["gripper_carry"] is not None:
            timing["sources"]["gripper"] = self._gripper_metadata(history["gripper_carry"][1])
        return timing, history

    def sample(self, reference_ns):
        observation, metadata = super().sample(reference_ns)
        metadata["sources"]["gripper"] = self._gripper_metadata(metadata["sources"]["gripper"])
        return observation, metadata

    def diagnostics(self):
        result = super().diagnostics()
        with self.lock:
            real = self.history.gripper_carry
            effective = real if real is not None else self.operator_gripper
            result["gripper_initialization"] = {
                "configured_operator_assumption": None if self.operator_gripper is None else self.operator_gripper[0],
                "latest_available_origin": "ros_message" if real is not None else "operator_assumed" if effective else "unknown",
                "latest_available_value": None if effective is None else effective[0],
                "sensor_measurement": False,
                "command_success_evidence": "reported_by_publisher_not_verified_by_client" if real is not None else False}
        return result

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("ROS callback thread did not stop")
        self.node.destroy_node()
        self.rclpy.shutdown()
