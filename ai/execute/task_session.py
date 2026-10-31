"""Mock Task bridge; reuse the existing observation/RPC/Scheduler runner."""
import json
from pathlib import Path
import time

from ai.execute.controller import UserApproval
from ai.execute.mock_session import create_mock_session, MockSession
from ai.execute.safety import SafetyError
from ai.execute.task_config import TaskConfig
from ai.execute.task_executor import TaskExecutor, TaskApproval, CompletionDecision, TERMINAL, render_task_summary


class MockTaskSession(MockSession):
    def __init__(self, source, controller, approval_text, config, output, path_evidence, complete_after_actions=None):
        super().__init__(source, controller, approval_text)
        self.task = TaskExecutor(controller, config, scheduler_empty=lambda: False,
                                 home_path_check=lambda: bool(path_evidence.strip()))
        self.path_evidence, self.complete_after_actions = path_evidence, complete_after_actions
        self.output = Path(output)
        self.command_path = self.output / "task_commands.jsonl"
        self.command_path.open("x").close()
        self.commands = self.command_path.open("r")
        self.task_result = None

    @property
    def ready_for_actions(self): return self.task.state == "RUNNING_ACT"

    @property
    def stop_requested(self): return self.task.state in ("STOPPING_ACT", "VERIFYING_STOP", "RETURNING_HOME", *TERMINAL)

    def prepare(self, session_id, model, cancel_scheduler):
        if not self.prepared:
            scheduler = getattr(cancel_scheduler, "__self__", None)
            if scheduler is None or not hasattr(scheduler, "is_empty_and_stopped"): raise SafetyError("task_scheduler_quiescence_interface_missing")
            self.controller.cancel_scheduler = cancel_scheduler
            self.task.scheduler_empty = lambda: scheduler.is_empty_and_stopped
            self.task.prepare(session_id, model["checkpoint_id"], model["checkpoint"], self.controller.health)
            self.prepared = True
            self.task.start_watchdog()
        self.task.poll()
        if self.task.state == "READY" and not self.task.arming:
            now = self.controller.clock()
            approval = TaskApproval(UserApproval(session_id, model["checkpoint_id"], self.approval_text, "mock", now,
                        now + round((self.task.config.max_act_seconds + self.task.config.home_timeout_seconds + 30) * 1e9)),
                        self.task.config.task_id, self.task.config.identity, True, True, self.path_evidence)
            self.task.start(approval)

    def _commands(self):
        for _ in range(32):
            offset = self.commands.tell()
            line = self.commands.readline(4097)
            if not line: return
            if len(line) > 4096: raise SafetyError("task_command_too_large")
            if not line.endswith("\n"):
                self.commands.seek(offset)
                return  # atomic append may be in progress
            request = json.loads(line)
            if request.get("session_id") != self.task.session_id: raise SafetyError("obsolete_task_command_session")
            command = request.get("command")
            if command == "complete":
                self.task.complete(CompletionDecision(self.task.session_id, request.get("operator", ""), note=request.get("note", "")))
            elif command == "abort": self.task.abort("user_abort")
            elif command == "emergency_stop": self.task.abort("user_emergency_stop", emergency=True)
            else: raise SafetyError("unknown_task_command")

    def poll(self, inference=False):
        if not self.prepared: return
        self.controller.heartbeat(inference=inference)
        try:
            self._commands()
            self.task.poll()
            if (self.complete_after_actions is not None and self.task.state == "RUNNING_ACT"
                    and self.controller.submitted >= self.complete_after_actions):
                self.task.complete(CompletionDecision(self.task.session_id, self.approval_text, "mock_test_user",
                    "Explicit MOCK test option simulated a user completion decision; no task perception."))
        except Exception as exc:
            self.task.abort(f"task_control_failed: {exc}", fault=True)
            raise

    def consume(self, record): self.task.consume(record)

    def finish(self, reason, fault=False):
        try:
            if fault and self.task.state not in TERMINAL:
                self.task.abort(reason, fault=True)
            elif self.task.state in ("IDLE", "PREPARING", "READY", "RUNNING_ACT"):
                self.task.abort("act_timeout" if reason == "duration_complete" else reason)
            deadline = time.monotonic() + self.task.config.home_timeout_seconds + 2 * self.controller.config.stop_timeout_ms / 1000 + 1
            while self.task.state not in TERMINAL and time.monotonic() < deadline:
                self._commands()  # Abort/E-stop must also work during Home.
                self.task.poll()
                time.sleep(.001)
            if self.task.state not in TERMINAL: raise SafetyError("task_cleanup_timeout")
        except BaseException as exc:
            self.task.abort("user_interrupt" if isinstance(exc, KeyboardInterrupt) else f"task_cleanup_failed: {exc}",
                            fault=not isinstance(exc, KeyboardInterrupt))
            stop_deadline = time.monotonic() + self.controller.config.stop_timeout_ms / 1000 + .2
            while self.task.state not in TERMINAL and time.monotonic() < stop_deadline:
                self.task.poll()
                time.sleep(.001)
        finally:
            self.task.close()
            self.commands.close()
        self.task_result = self.task.snapshot()
        with (self.output / "task_result.json").open("x") as stream: json.dump(self.task_result, stream, indent=2)
        with (self.output / "task_summary.txt").open("x") as stream: stream.write(render_task_summary(self.task_result))
        self.controller.log("task_end", result=self.task_result)
        return self.controller.snapshot()


def create_task_session(options, log, model, output, config_path, approval_text, task_config_path,
                        path_evidence, complete_after_actions=None):
    source, execution = create_mock_session(options, log, model, output, config_path, approval_text)
    config = TaskConfig.load(task_config_path)
    # Initial Mock state is a declared software fixture, never a Home move.
    execution.controller.indy.joints = config.home_rad.copy()
    return source, MockTaskSession(source, execution.controller, approval_text, config, output, path_evidence, complete_after_actions)
