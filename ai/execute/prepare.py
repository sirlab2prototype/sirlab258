"""Launch cameras and Mark7, then send one physical Open command."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def commands(device):
    return [
        ('cameras', [str(ROOT/'scripts/bin/camera'), 'show_camera:=false']),
        ('mark7', [str(ROOT/'.venv/bin/python'),
            str(ROOT/'ros2_ws/src/capstone_mark7/capstone_mark7/mark7_node.py'),
            '--ros-args', '-p', 'publish_command_state:=true', '-p', f'device:={device}',
            '-r', '/gripper/open:=/capstone/real_gripper/open',
            '-r', '/gripper/grasp:=/capstone/real_gripper/grasp']),
    ]


def serial_available(device):
    path = Path(device)
    if not path.is_char_device() or not os.access(path, os.R_OK | os.W_OK):
        raise RuntimeError(f'Mark7 serial unavailable or inaccessible: {device}')
    # Check ownership without opening the serial port or sending a command.
    result = subprocess.run(['fuser', str(path.resolve())], capture_output=True, timeout=2)
    if result.returncode != 1 or result.stderr.strip():
        raise RuntimeError(f'Mark7 serial busy or ownership check failed: {device}; {result.stdout.decode().strip()}')


class Children:
    def __init__(self): self.items = []
    def start(self, name, argv):
        process = subprocess.Popen(argv, cwd=ROOT, start_new_session=True)
        self.items.append((name, process))
    def failures(self):
        return [f'{name} child exited ({p.poll()})' for name, p in self.items if p.poll() is not None]
    def close(self):
        # Only process groups created by start_new_session above are signalled.
        for _, p in reversed(self.items):
            try: os.killpg(p.pid, signal.SIGINT)
            except ProcessLookupError: pass
        deadline = time.monotonic()+5
        for _, p in reversed(self.items):
            try: p.wait(timeout=max(.01, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                try: os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try: os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    p.wait(timeout=2)


def initialize_gripper(children):
    """Send one Open request and confirm the Mark7 command-state event."""
    import rclpy
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from std_msgs.msg import Float32
    from std_srvs.srv import Trigger

    rclpy.init()
    node = None
    try:
        node = rclpy.create_node('act_prepare_gripper_init')
        states = []
        node.create_subscription(
            Float32, '/capstone/gripper_state', lambda msg: states.append(msg.data),
            QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL))
        client = node.create_client(Trigger, '/capstone/real_gripper/open')

        def spin_until(predicate, timeout, failure):
            deadline = time.monotonic() + timeout
            while not predicate():
                failures = children.failures()
                if failures:
                    raise RuntimeError('; '.join(failures))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(failure() if callable(failure) else failure)
                rclpy.spin_once(node, timeout_sec=min(.1, remaining))

        spin_until(client.service_is_ready, 10, 'Mark7 Open service not ready within 10s')
        # The subscription exists before the only hardware command. The newly launched
        # Mark7 publisher retains its response even if discovery is still in progress.
        future = client.call_async(Trigger.Request())
        spin_until(future.done, 5, 'Mark7 Open service response timed out after one request')
        try:
            response = future.result()
        except Exception as exc:
            raise RuntimeError(f'Mark7 Open service request failed: {exc}') from exc
        if response is None:
            raise RuntimeError('Mark7 Open service returned no response')
        if not response.success:
            raise RuntimeError(f'Mark7 Open service failed: {response.message}')
        spin_until(lambda: 0.0 in states, 3,
                   lambda: ('Mark7 Open succeeded but /capstone/gripper_state 0.0 '
                            f'was not received; received={states}'))
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='/dev/ttyACM0', help='Existing Mark7 serial port')
    args = parser.parse_args(argv)
    args.device = str(Path(args.device).expanduser().resolve())
    directory = Path.home()/'.local/bin/.sirlab258-camera-locks'
    directory.mkdir(parents=True, exist_ok=True)
    children = Children()
    def interrupt(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    with (directory/'act-prepare.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('act prepare already running; nothing started', flush=True)
            return 2
        try:
            serial_available(args.device)
            for name, command in commands(args.device):
                children.start(name, command)
            print('Mark7 Open will physically move the gripper once when its service is ready.', flush=True)
            initialize_gripper(children)
            print('Mark7 initial gripper state registered: Open (0.0).', flush=True)
            print('Camera and Mark7 processes launched. Ctrl+C stops owned processes. '
                  'No ongoing input/robot readiness checks.', flush=True)
            while True:
                failures = children.failures()
                if failures: raise RuntimeError('; '.join(failures))
                time.sleep(.2)
        except KeyboardInterrupt:
            print('Stopping owned camera and Mark7 processes.', flush=True)
            return 0
        except Exception as exc:
            print(f'act prepare failed: {exc}', flush=True)
            return 1
        finally:
            children.close()


if __name__ == '__main__': raise SystemExit(main())
