"""Command-state hysteresis/debounce; service success is not grasp success."""
from ai.execute.safety import SafetyError


class GripperAdapter:
    def __init__(self, transport, config, log):
        self.transport, self.config, self.log = transport, config, log
        self.state = None
        self.pending = None
        self.uncertain = False
        self.stopped = False
        self.candidate = None
        self.last_completed_ns = None
        self.sent = 0

    def initialize(self, confirmed_state):
        if confirmed_state not in (0, 1) or self.pending or self.uncertain: raise SafetyError("unknown_initial_gripper")
        self.state, self.stopped = int(confirmed_state), False
        self.candidate = None
        self.sent = 0

    def request(self, value, now_ns):
        self.poll(now_ns)
        if self.stopped or self.pending is not None: return
        desired = 1 if value >= self.config.gripper_grasp_threshold else 0 if value <= self.config.gripper_open_threshold else self.state
        if desired == self.state:
            self.candidate = None
            return
        if self.candidate is None or self.candidate[0] != desired:
            self.candidate = (desired, now_ns)
        if now_ns - self.candidate[1] < self.config.gripper_dwell_ms * 1e6: return
        if self.last_completed_ns is not None and now_ns - self.last_completed_ns < self.config.gripper_min_interval_ms * 1e6: return
        try:
            future = self.transport.request(desired)
        except Exception as exc:
            self.uncertain, self.state, self.stopped = True, None, True
            raise SafetyError("gripper_dispatch_failed_state_unknown") from exc
        self.pending = (future, desired, now_ns)
        self.sent += 1
        self.log("gripper_requested", desired=desired)

    def poll(self, now_ns):
        if self.pending is None: return
        future, desired, started = self.pending
        if now_ns - started > self.config.gripper_timeout_ms * 1e6:
            self.uncertain, self.state, self.stopped = True, None, True
            # Retain the unresolved future: a late physical operation is possible.
            raise SafetyError("gripper_service_timeout_state_unknown")
        if not future.done(): return
        self.pending = None
        try:
            response = future.result()
            if getattr(response, "success", False) is not True: raise SafetyError("gripper_service_failed")
        except Exception as exc:
            self.uncertain, self.state, self.stopped = True, None, True
            raise SafetyError("gripper_service_failed_state_unknown") from exc
        self.state, self.candidate, self.last_completed_ns = desired, None, now_ns
        self.transport.confirm_state(desired)
        self.log("gripper_command_confirmed", state=desired, physical_grasp_verified=False)

    def stop(self):
        self.stopped = True
        self.candidate = None
        # Never open a held basket or cancel/pretend to cancel an issued serial command.

    @property
    def settled(self): return self.pending is None and not self.uncertain
