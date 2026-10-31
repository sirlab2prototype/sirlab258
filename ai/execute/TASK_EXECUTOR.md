# Task 1: Mock Task Executor and separate project Home

```bash
./act task --task indy7_basket_to_table1 --mode mock \
  --checkpoint <checkpoint-or-training-run> \
  --approve-mock "operator acknowledgement" --approve-home-mock \
  --confirm-home-path-mock "software fixture path only" --device cpu
```

The three approval arguments explicitly authorize Mock ACT start and subsequent
Mock Home. No motion starts from setup. `--mode real` is locked before any device
creation; the Task API also rejects Real preparation. Existing `act execute` and
`act infer --dry-run` retain their behavior. No real ROS node/SDK/serial transport
is constructed in the Mock command. All output directories must be new, as in
the existing inference launcher.

The launcher prints the output directory. `client/events.jsonl` includes the
current Session ID. To declare that ACT placement, release and safe retreat are
finished, append a newline-terminated command to `client/task_commands.jsonl`:

```json
{"command":"complete","session_id":"<current-session>","operator":"operator name","note":"placement/release/retreat checked"}
```

Other commands are `abort` and `emergency_stop`, also requiring the current
Session ID. They work during ACT, stop verification and Home. Stale-session,
malformed or oversized commands fail closed. The inbox is bounded per read and
each task has its own new inbox; it never replays an old Task's command file.
Commands are processed by the client; physical E-stop remains separate.

For an explicitly simulated user in offline tests only:

```bash
./act task --task indy7_basket_to_table1 --mode mock \
  --checkpoint <checkpoint-or-training-run> \
  --approve-mock "offline tester" --approve-home-mock \
  --confirm-home-path-mock "Mock-only path" \
  --mock-complete-after-actions 25 --device cpu --duration 5
```

This option logs `source=mock_test_user`, not a perception-derived success. It
cannot enable Real execution. Without a completion command, time limits abort;
`--duration` is an upper bound on the shared ACT loop, never a success trigger.

## Reuse and state transitions

`TaskExecutor` reuses a supplied `ExecutionController`, its robot/gripper
adapters, Safety Guard, independent watchdog and `CommandLease`. There is no
second Robot Controller, SDK client or action Chunk queue. The shared
observation/RPC/Scheduler loop is gated until the Task is ready for actions.
The existing bounded-history alignment and action expiry rules stay in force.
Task/Home logs and main-loop logs share a locked JSONL sink.

| Task state | Entry / commands / timeout |
|---|---|
| IDLE | New Task; `prepare()` only; no motor command |
| PREPARING | Check fresh observations and stable stopped project Home; prepare timeout 5s |
| READY | Await explicit bound approval; recheck Home before arm and before ACT begin; Controller mode timeout applies |
| RUNNING_ACT | Existing Controller/Scheduler actions; `complete()`, abort, emergency; ACT maximum 120s |
| STOPPING_ACT | Revoke ACT permission, cancel Scheduler, request stop through Controller |
| VERIFYING_STOP | Confirm stopped Controller, no fault/E-stop, fresh **post-stop** joint feedback and empty Scheduler; Controller stop timeout 1s plus 100ms verification margin |
| RETURNING_HOME | Only after normal user completion and all Home conditions; Home timeout 60s; abort/emergency still active |
| COMPLETED | ACT completed by user, stop verified, Home position and stationary stability verified; terminal |
| ABORTED | User abort, ACT timeout or release unconfirmed; no Home retry; terminal |
| FAULT | Execution/communication/safety/authority/Home failure; no automatic restart/rearm/Home; terminal |

Task numeric settings live in `config/task1.json`. Robot readiness/stop/command
thresholds remain in the existing SafetyConfig. Mock limits are not certified
hardware limits. Task watchdog polling continues stop/Home checks if the main
inference loop stalls; Python/GIL/process failure is not a safety controller.

`CompletionDecision` is a separate extension boundary, currently accepting only
explicit user decisions (or explicitly labelled Mock test users). Open gripper,
stillness and elapsed time do not auto-complete a Task. Completion after the ACT
time limit is rejected. A user completion still needs a confirmed logical Open
command and settled gripper service before Home; that is not physical basket
release verification.

## Home target and interface choice

`TaskConfig.load()` reads Home targets and SDK ratios directly from unchanged
`ros2_ws/src/capstone_teleop/config/indy7.yaml`:

- joint degrees: `[0.005221, 40.003174, -129.996640, 90.000870, 0.000435, 90.507]`
- `vel_ratio=18`, `acc_ratio=100`
- tolerance: 0.5deg (the existing QP arrival threshold), stationary stability
  window: 250ms. The additional window/timeouts are configurable software defaults,
  not hardware-certified stopping criteria.

Source evidence: QP `request_capstone_home()` stops teleop;
`capstone_home_after_stop()` calls its existing SDK `movej()` with those settings;
the main loop checks actual position error <=0.5deg before automatic rearming.
Only settings/behavior were read: no QP node, Xbox handling, SDK connection or
automatic rearm code is imported into Task execution.

The official driver's `/indy_srv` Home (`data=2`) uses
`get_home_pos()['jpos']`, not this configured project target. Its service cannot
carry a target or Home velocity/acceleration ratios. Its trajectory topic calls
`movetelej_abs()` in teleop mode with fixed ratios and ignores trajectory timing
and derivatives. Neither proves equivalence to the project's deterministic
`movej()` Home. Consequently **Real Home is not implemented/activated** through
those paths. `HomeReturn.start()` fails closed on a Real adapter; no second SDK
client is introduced. Future Real enablement needs a single command owner with
a verified project-target/speed/acceleration move interface, actual mode/Servo
readiness feedback, motion stop feedback and external-controller exclusion.

Mock Home uses the **same** Mock Indy7 object via `request_project_home()` with
absolute radians and the unchanged SDK ratio values. It models completion after
a delay, not joint dynamics, stopping distances or collision avoidance. A Future
success is insufficient: fresh joint position within tolerance, idle/stationary
state and distinct feedback timestamps spanning the stability window are needed.

ACT stop releases the existing lease only after Controller confirmation. Task
then independently rechecks post-stop feedback, scheduler emptiness, no active
ACT permission, known Release, approved config identity, unexpired Home approval,
path condition and external authority evidence. Home reacquires the **same lease
file** with `<session>:home`; a racing owner blocks Home without stopping that
owner. Home completion releases it, and uncertain Home stop quarantines it.
Session/checkpoint IDs remain attached throughout. Local locks/ROS graph cannot
prove exclusion of CONTY, MoveIt or direct SDK programs.

During Home the ExecutionController's STOPPED state describes the finished ACT
phase; `Task.state=RETURNING_HOME` and `HomeReturn.state` describe the separate
motion. ACT permission remains revoked throughout Home. New ACT arm attempts
cannot obtain the held Home lease.

## Results and evaluation API

`TaskExecutor.prepare/start/consume/complete/abort/poll/snapshot/close` form the
orchestration API. `abort(..., emergency=True)` requests software stop; no path
initiates Home after faults. New executions require a new Task instance and
new Session, with fresh approval. A faulted Controller remains unusable.

`snapshot()` exposes Task/run/session/checkpoint IDs, wall-clock and monotonic
start/end times, ACT elapsed time/action count, bounded gripper command history,
completion source/operator/note, ACT stop reason/confirmation, Home start/end and
final per-joint errors, final state/success, failure stage/reason, and embedded
Controller status. Full authority/command/gripper transitions remain in JSONL.
`client/task_result.json` is structured output; `client/task_summary.txt` is the
human-readable summary; launcher `result.json` also includes the Task result.

`act_policy_success_user_reported` and Task `success` are independent: a user may
report normal ACT completion and Home can subsequently fail. Neither is a measured
robot task success rate. `physical_task_success_verified` is always false here.

## Offline verification and remaining real work

Virtual-clock tests cover normal/abnormal Task transitions, no Home on ACT
failure/timeout/abort, fresh post-stop state, service ACK versus actual arrival,
stable stopping, Home error/timeout/command failure, authority races, emergency
stop, session isolation and no automatic rearm. Actual Synthetic LeRobot ACT
training/reload/server/Scheduler/Guard/Mock plant/Home tests exercise both the
explicit Mock test completion option and the JSONL user command route.

The learned smoke fixture uses constant safe Home-relative hold/open targets;
it proves software wiring and does not learn grasp/transport/retreat. Synthetic
action tests separately exercise Grasp/Open command transitions. Real camera,
robot, gripper, basket handling, Home collision path and physical stopping are
not verified. Hardware activation remains locked until these interfaces and
conditions are validated; no official driver or QP source was modified.
