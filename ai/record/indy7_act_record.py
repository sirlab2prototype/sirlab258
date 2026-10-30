#!/usr/bin/env python3
"""Record Indy7 teleoperation demonstrations as a LeRobot dataset.

Run after sourcing ROS Jazzy, this repository's ROS workspace, and
scripts/activate_act.sh. The collector intentionally does not start or stop
robot drivers; it only subscribes to their observation/action topics.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np
import rclpy
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from rclpy.node import Node
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import Float32


JOINT_NAMES = [f"joint{i}" for i in range(6)]
IMAGE_SHAPE = (480, 640, 3)
COMMAND_ALIASES = {
    "s": "start",
    "a": "save",
    "d": "discard",
    "r": "retry",
    "q": "quit",
    "start": "start",
    "save": "save",
    "discard": "discard",
    "delete": "discard",
    "retry": "retry",
    "quit": "quit",
}


def _normalize_command(command: str) -> str | None:
    return COMMAND_ALIASES.get(command.strip().lower())


def _print_controls() -> None:
    print("[Recorder Controls]")
    print("s : start")
    print("a : save")
    print("r : retry")
    print("d : discard")
    print("q : quit")


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


class Indy7Observations(Node):
    def __init__(
        self,
        camera_317_topic: str,
        camera_844_topic: str,
        state_topic: str,
        action_topic: str,
        gripper_topic: str,
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
        self.create_subscription(Image, camera_317_topic, lambda msg: self._image("camera_317222074298", msg), 5)
        self.create_subscription(Image, camera_844_topic, lambda msg: self._image("camera_844212071939", msg), 5)
        self.create_subscription(JointState, state_topic, self._state, 20)
        self.create_subscription(JointState, action_topic, self._action, 20)
        self.create_subscription(Float32, gripper_topic, self._gripper, 10)

    def _image(self, name: str, msg: Image) -> None:
        try:
            image = _image_to_rgb(msg)
        except ValueError as exc:
            self.get_logger().error(str(exc))
            return
        with self.lock:
            self.images[name] = (image, time.monotonic())

    @staticmethod
    def _joint_vector(msg: JointState) -> np.ndarray | None:
        positions = dict(zip(msg.name, msg.position))
        if not all(name in positions for name in JOINT_NAMES):
            return None
        return np.asarray([positions[name] for name in JOINT_NAMES], dtype=np.float32)

    def _state(self, msg: JointState) -> None:
        vector = self._joint_vector(msg)
        if vector is not None:
            with self.lock:
                self.state = (vector, time.monotonic())

    def _action(self, msg: JointState) -> None:
        vector = self._joint_vector(msg)
        if vector is not None:
            with self.lock:
                self.action = (vector, time.monotonic())

    def _gripper(self, msg: Float32) -> None:
        with self.lock:
            self.gripper = (float(msg.data), time.monotonic())

    def snapshot(self):
        with self.lock:
            return self.images.copy(), self.state, self.action, self.gripper


def _features() -> dict:
    return {
        "observation.images.camera_317222074298": {
            "dtype": "video", "shape": list(IMAGE_SHAPE), "names": ["height", "width", "channels"],
        },
        "observation.images.camera_844212071939": {
            "dtype": "video", "shape": list(IMAGE_SHAPE), "names": ["height", "width", "channels"],
        },
        "observation.state": {
            "dtype": "float32", "shape": [7], "names": JOINT_NAMES + ["gripper"],
        },
        "action": {
            "dtype": "float32", "shape": [7], "names": JOINT_NAMES + ["gripper"],
        },
    }


def _commands() -> queue.Queue[str]:
    commands: queue.Queue[str] = queue.Queue()

    def read_commands():
        while True:
            line = sys.stdin.readline()
            if not line:
                commands.put("quit")
                return
            command = line.strip().lower()
            commands.put(command)
            if _normalize_command(command) == "quit":
                return

    threading.Thread(target=read_commands, daemon=True).start()
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", default="local/indy7_act")
    parser.add_argument("--root", type=Path, default=Path("datasets/indy7_act"))
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--task", default="Grasp basket handle from shelf and place on table")
    parser.add_argument("--camera-317-topic", default="/camera_317222074298/camera/color/image_raw")
    parser.add_argument("--camera-844-topic", default="/camera_844212071939/camera/color/image_raw")
    parser.add_argument("--state-topic", default="/joint_states")
    parser.add_argument("--action-topic", default="/capstone/teleop_action")
    parser.add_argument("--gripper-topic", default="/capstone/gripper_state")
    parser.add_argument("--resume", action="store_true", help="append to an existing compatible dataset")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if root.exists() and any(root.iterdir()) and not args.resume:
        parser.error(f"dataset path is not empty: {root}; choose another --root or pass --resume")
    if args.resume:
        if not (root / "meta" / "info.json").is_file():
            parser.error(f"--resume requested but no LeRobot dataset exists at {root}")
        dataset = LeRobotDataset(repo_id=args.repo_id, root=root)
    else:
        dataset = LeRobotDataset.create(
            repo_id=args.repo_id,
            root=root,
            robot_type="indy7",
            fps=args.fps,
            features=_features(),
            use_videos=True,
            image_writer_threads=2,
            vcodec="libx264",
        )

    rclpy.init()
    node = Indy7Observations(
        args.camera_317_topic,
        args.camera_844_topic,
        args.state_topic,
        args.action_topic,
        args.gripper_topic,
    )
    commands = _commands()
    recording = False
    quit_requested = False
    frames = 0
    last_warning = 0.0
    period = 1.0 / args.fps
    last_tick = time.monotonic()

    print(f"Dataset: {root}\nWaiting for both cameras and /joint_states. Press s when ready.")
    _print_controls()
    print("[READY]")
    try:
        while rclpy.ok() and not quit_requested:
            rclpy.spin_once(node, timeout_sec=0.01)
            while not commands.empty():
                raw_command = commands.get()
                command = _normalize_command(raw_command)
                if command == "start":
                    images, state, _, gripper = node.snapshot()
                    stamp = time.monotonic()
                    ready = (
                        state is not None and stamp - state[1] < 0.25
                        and gripper is not None
                        and all(images[key] is not None and stamp - images[key][1] < 0.25 for key in images)
                    )
                    if ready:
                        recording = True
                        frames = 0
                        last_tick = stamp
                        print("[RECORDING] Move the robot through the planned demonstration.")
                    else:
                        print("Not ready: wait until both camera streams and /joint_states are fresh.")
                elif command == "save":
                    if not recording or frames < 2:
                        print(f"Nothing saved: active episode has {frames} frames (minimum 2).")
                    else:
                        dataset.save_episode()
                        recording = False
                        print(f"[SAVED] Dataset now has {dataset.meta.total_episodes} episodes.")
                elif command == "discard":
                    dataset.clear_episode_buffer()
                    recording = False
                    frames = 0
                    print("[DISCARDED] Current unsaved episode discarded; saved episodes are unchanged.")
                elif command == "retry":
                    dataset.clear_episode_buffer()
                    recording = True
                    frames = 0
                    print("[RECORDING] Current take discarded. New take is recording now.")
                elif command == "quit":
                    quit_requested = True
                    break
                elif raw_command:
                    print("Unknown command. Use s/start, a/save, r/retry, d/discard, or q/quit.")

            now = time.monotonic()
            if recording and now - last_tick >= period:
                last_tick = now
                images, state, action, gripper = node.snapshot()
                fresh = (
                    state is not None and now - state[1] < 0.25
                    and gripper is not None
                    and all(images[key] is not None and now - images[key][1] < 0.25 for key in images)
                )
                if not fresh:
                    if now - last_warning > 2.0:
                        node.get_logger().warning("Skipping frame: robot state or camera image is stale")
                        last_warning = now
                else:
                    # On the first frame (or before the first teleop command), use
                    # the measured hold position as the target action.
                    action_vec = state[0] if action is None else action[0]
                    state_vec = np.concatenate((state[0], np.asarray([gripper[0]], dtype=np.float32)))
                    action_vec = np.concatenate((action_vec, np.asarray([gripper[0]], dtype=np.float32)))
                    dataset.add_frame({
                        "observation.images.camera_317222074298": images["camera_317222074298"][0],
                        "observation.images.camera_844212071939": images["camera_844212071939"][0],
                        "observation.state": state_vec,
                        "action": action_vec,
                        "task": args.task,
                    })
                    frames += 1
            else:
                time.sleep(min(period / 4, 0.01))
    except KeyboardInterrupt:
        print("Interrupted; unsaved frames will be discarded.")
    finally:
        if recording:
            dataset.clear_episode_buffer()
        dataset.finalize()
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
