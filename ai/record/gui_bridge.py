"""Small thread-safe frontend for the existing Recorder loop; no ROS/Tk imports.

Only the engine thread reads Session/Collection/observation objects. The GUI
receives scalar snapshots and submits at most one timestamped command at a time.
No frames, image references, Dataset writers or control publishers cross here.
"""
from collections import deque
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import queue
import threading
import time

CAMERAS = ("camera_317222074298", "camera_844212071939")


class GuiCommand(str):
    def __new__(cls, value, token):
        obj = super().__new__(cls, value)
        obj.received_monotonic_ns = time.monotonic_ns()
        obj.received_system_time_ns = time.time_ns()
        obj.boundary_source = "gui_button_received"
        obj.expected_token = token
        return obj


def _token(session, starter):
    collection = session.collection
    condition = collection.condition_view() if collection else None
    current = collection.current if collection else None
    return (session.state, starter.active, str(session.path), session.demonstration_type,
            bool(session.save_failed), bool(session.integrity["valid"]),
            current["attempt_id"] if current else None,
            (condition["condition_id"], condition["attempt_number"]) if condition else None)


def snapshot(session, starter, node, *, phase=None, now_ns=None):
    """Metadata only. Camera freshness describes callbacks, not sync readiness."""
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    collection = session.collection
    condition = collection.condition_view() if collection else None
    state = ("ERROR" if session.save_failed or (collection and collection.blocked) else
             "INVALID" if not session.integrity["valid"] else
             "PRESTART" if starter.active else session.state)
    allowed = {"q"}
    if state == "READY" and (collection is None or condition):
        allowed.add("s")
    if state in ("RECORDING", "PRESTART"):
        allowed.add("r")
    if state == "RECORDING" and session.correction is None:
        allowed.add("x")
    if state in ("READY", "PRESTART", "REVIEW", "INVALID") and (
            session.path is not None or (collection and collection.current)):
        allowed.add("d")
    marker_index = (session.correction or {}).get("start_frame_index")
    if (state == "REVIEW" and session.frames >= 2 and
            (session.demonstration_type == "normal" or
             (type(marker_index) is int and 0 <= marker_index < session.frames))):
        allowed.add("o")
    if state not in ("RECORDING", "PRESTART", "ERROR"):
        allowed.update(("help", "guide"))
        if collection:
            allowed.update(("progress", "history"))
            if state == "READY" and collection.current is None:
                allowed.update(("condition", "placement"))
            if state in ("REVIEW", "INVALID"):
                allowed.add("result")
    if phase:
        state = phase
        allowed.clear()
    # Read only the small callback metadata, never images/history or disk files.
    with getattr(node, "lock", nullcontext()):
        sources = {name: dict(getattr(node, "source_timing", {}).get(name, {})) for name in CAMERAS}
    cameras = {}
    for name, source in sources.items():
        stamp = source.get("callback_monotonic_ns")
        age = None if stamp is None else (now_ns - stamp) / 1e6
        cameras[name] = {"age_ms": age, "receiving": age is not None and 0 <= age <= 1000}
    active_take = session.path is not None
    end_ns = session.stopped_ns if session.stopped_ns is not None else now_ns
    duration = max(0, end_ns - session.started_ns) / 1e9 if active_take else 0.0
    return {"state": state, "snapshot_monotonic_ns": now_ns,
            "token": _token(session, starter), "allowed": sorted(allowed),
            "stats": collection.stats() if collection else None, "condition": condition,
            "total": collection.total if collection else 0,
            "custom_plan": collection.custom if collection else False,
            "dataset_id": collection.directory.name if collection else None,
            "action": deepcopy(collection.state.get("definition", {}).get("action")) if collection else None,
            "frames": session.frames, "elapsed_s": duration,
            "actual_sample_fps": session.timing_summary()["actual_sample_fps"] if active_take else None,
            "demonstration_type": session.demonstration_type,
            "correction": deepcopy(session.correction) if active_take else None,
            "pending": str(session.path) if session.path else None,
            "integrity": deepcopy(session.integrity), "last_saved": deepcopy(session.last_saved),
            "cameras": cameras, "running": True}


class RecorderBridge:
    def __init__(self):
        self.commands = queue.Queue(maxsize=1)
        self.abort = threading.Event()
        self._lock = threading.Lock()
        self._messages = deque(maxlen=200)
        self._view = {"state": "INITIALIZING", "running": True, "busy": False, "allowed": []}
        self._busy = False
        self._next_update_ns = 0

    def message(self, message):
        if message:
            with self._lock:
                self._messages.append(str(message))
            try:
                print(message, flush=True)  # Also keep diagnostics in the terminal.
            except (OSError, ValueError):
                # A closed SSH stdout must not turn a committed save into an error.
                pass

    def read(self):
        with self._lock:
            view = deepcopy(self._view)
            messages = list(self._messages)
            self._messages.clear()
        return view, messages

    def submit(self, command):
        verb = command.split(maxsplit=1)[0].lower() if command.strip() else ""
        with self._lock:
            if self._busy or not self._view["running"] or verb not in self._view["allowed"]:
                return False
            item = GuiCommand(command, self._view["token"])
            self.commands.put_nowait(item)
            self._busy = True
            self._view["busy"] = True
            return True

    def update(self, session, starter, node, *, phase=None, force=False):
        now = time.monotonic_ns()
        if not force and now < self._next_update_ns:
            return
        self._next_update_ns = now + 200_000_000  # At most 5 scalar updates/s.
        view = snapshot(session, starter, node, phase=phase, now_ns=now)
        with self._lock:
            view["busy"] = self._busy
            self._view = view

    @contextmanager
    def command_scope(self, command, session, starter, node):
        current = snapshot(session, starter, node)
        verb = command.split(maxsplit=1)[0].lower() if command.strip() else ""
        accepted = (command.expected_token == current["token"] and verb in current["allowed"])
        if not accepted:
            self.message("[입력 거부] 상태가 변경되었습니다. 현재 상태를 확인한 뒤 다시 선택하세요.")
        try:
            yield accepted
        finally:
            # Publish the result before allowing the next click.
            view = snapshot(session, starter, node)
            with self._lock:
                self._busy = False
                view["busy"] = False
                self._view = view

    def finished(self, code):
        with self._lock:
            self._busy = False
            self._view.update(running=False, busy=False, allowed=[], exit_code=code,
                              state="CLOSED" if code == 0 else "ERROR")


def run_engine(bridge, argv, runner=None):
    """Worker entry: import the existing engine once, never run a second engine."""
    code = 1
    try:
        if runner is None:
            from ai.record.indy7_act_record import main
            runner = main
        code = 0 if bridge.abort.is_set() else runner(argv, frontend=bridge)
    except BaseException as exc:
        bridge.message(f"[Recorder 오류] {type(exc).__name__}: {exc}\n"
                       "Pending과 Collection을 보존하고 오류·reconcile 안내를 확인하세요.")
    finally:
        bridge.finished(code)
