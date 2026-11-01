# Indy7 / Mark7 guarded execution (Mock verified; Real CLI locked)

Task 1 orchestration, explicit user completion and separate Mock project-Home
return are available through `act task`; see [TASK_EXECUTOR.md](TASK_EXECUTOR.md).
Real Task/Home activation remains locked. The APIs below remain the ACT phase
controller used by that orchestrator.

## Commands and boundaries

```bash
# Explicitly approve Mock arming/start; never hardware:
act execute --mode mock --checkpoint outputs/my_act_run \
  --approve-mock "operator acknowledgement for software test" --device cpu --duration 10

# Existing log-only modes remain unchanged:
act infer --checkpoint outputs/my_act_run --source synthetic --dry-run
act infer --checkpoint outputs/my_act_run --source ros --dry-run
```

No approval flag means no Mock arming. `act execute --mode real` is rejected
before creating devices/nodes. Setup is not an execution command. The normal
Dry Run path never instantiates an execution controller or command adapter.
Standalone Mock execution starts at zero joints by default. For a Checkpoint
whose demonstrations start at project Home, supply `--mock-initial-joints` with
six absolute radians matching that fixture. Values must be finite and inside
the configured limits; no clipping occurs. This only initializes the software
plant before observations begin. Task Mock already uses the project Home and
does not accept this independent-pose override. No hardware state is set/moved.
The optional inference-runner bridge accepts Mock only; no Real adapter can be
selected through `act infer --dry-run`. Internal Scheduler `dry_run=true` and
`robot_commands_sent=0` mean hardware output is disabled; a Mock run separately
reports `execution_mode=mock` and submitted/acknowledged Mock commands.

The server/client launcher is reused. `mock_client.py` constructs only software
Indy7/Mark7, links synthetic joint/gripper observations to this Mock plant, and
passes the existing scheduled records to `ExecutionController`. It does not
create another Chunk queue. The Mock plant applies commands immediately after
its Future completes; it is not a calibrated dynamics/servo model.

## Structure and contracts

Scheduler -> `validate_action()` -> `SafetyGuard` -> `ExecutionController` ->
Indy7 absolute adapter / hysteretic `GripperAdapter` -> command transport.

An action record must have finite numeric 7D values, current session/checkpoint
IDs, request ID, action index and contiguous slot/deadline metadata. Joints are
**absolute radians**, never QP-relative offsets. Invalid targets are rejected,
not clipped. Guards cover position limits, target-current error, command step,
command velocity/acceleration, measured velocity/acceleration, observation and
joint/status freshness, external readiness/ownership, tracking persistence and
expired/missed deadlines. Command derivatives use the smaller of the nominal
50ms period and the actual interval since the last dispatch; a late-then-on-time
pair must not evade speed limits by assuming a full 50ms between sends.

`SafetyConfig` is loaded from JSON. `config/mock.json` contains **test-only**
limits and explicitly says it is not reviewed for hardware. The
`indy7_review_required.json` template has null joint limits and cannot load
until actual values are supplied/reviewed. Legacy QP constants are references,
not hardware certification: `qp_core.py` has +/-175 degrees (J6 +/-215), and
`config/indy7.yaml` has 45deg/s, soft margin 15deg, and the team Home pose.
The QP's Cartesian acceleration is not a measured joint acceleration limit.
No QP/Home algorithm or config is modified/imported into the execution loop.

Observation selection remains the existing bounded-history implementation.
`ObservationHealth.from_timing()` consumes its validated reference/age/skew
metadata; no new clock conversion or forced sensor synchronization is added.
Robot feedback must be cached, nonblocking and independently acquired. Camera
health cannot substitute for fresh robot/controller feedback.

## Indy7 official ROS interface and limitations

`RosIndy7Adapter` wires the unchanged driver:

- `/indy_srv`, `IndyService.data=6`: request joint-absolute teleoperation.
- `/joint_trajectory_controller/joint_trajectory`: exactly one point, six
  positions in rad with joint0..joint5 ordering.
- `/indy_srv`, `data=8`: software stop request.

Official `indy_define.py` maps request 6 to SDK `TELE_JOINT_ABSOLUTE=10` and
`TELE_OP=17`. The driver converts incoming radians to degrees using
`rads2degs()` and calls `movetelej_abs()`. It is **not** `movetelej_rel()`.
Servo/Ready must already be established; this adapter has no Servo ON,
Recover, Zero, or Home operation. `FollowJointTrajectory` is not used because
the stream is already scheduled and the official Action path owns a waypoint
loop and transitions mode itself.

The topic callback ignores velocities, accelerations and time_from_start,
uses fixed SDK vel_ratio=0.8 / acc_ratio=7.0, processes only the first point,
and suppresses duplicate positions using a cached previous value. The state
timer is 20Hz; that alone does not prove 20Hz command execution. A publish
Future acknowledges only local ROS publish, not SDK receipt or physical
execution. The tracking guard monitors subsequent feedback, but cannot prove
per-command controller timing or collision safety.

The driver service checks entry into TELE_OP, but exposes no adequate public
read-only ROS status for **all** required Servo, exact teleop method and
external-exclusive ownership evidence. `/joint_states` is actual rad position
and qdot, not readiness/mode proof. QP `/capstone/teleop_status` is local state
and the QP has startup/Home auto-rearm paths. Thus DISARMED alone is insufficient.

A Real adapter requires all of:

1. An explicit `RealActivation(enable_hardware=True, operator, validation_record,
   expires_ns)` and reviewed hardware profile.
2. A separate session/checkpoint-bound `UserApproval(scope='real')`.
3. A cached `RobotFeedback` provider confirming fresh position/velocity, all
   required Servo/Ready conditions, no fault/E-stop, actual controller state 5
   when idle or state 17 + method 10 for absolute teleop, and independent QP/
   external-control exclusion evidence. Unknown values are rejected.
4. Local `CommandLease` ownership and an unambiguous ROS publisher graph.

There is no positive default readiness provider. Supplying true flags is a
trusted external-provider contract, not a way this code proves physical facts.
Real CLI remains locked until that provider and hardware safety validation are
integrated. The adapter accepts a caller-owned ROS node and never starts a
node/executor or creates a second direct SDK writer. All real ROS imports and
publishers/clients are deferred until the explicit activation gate passes.

ROS graph checks reject a QP node and other trajectory-topic publishers, but
cannot exclude direct SDK programs or queued MoveIt/action goals. Independent
exclusion must cover every controller input channel. A local file lock only
coordinates cooperating ACT instances. Physical safety interlocks/network
control ownership remain necessary; none are assumed from an empty graph.

## State machine / ownership / stop

| State | Entry / allowed transition |
|---|---|
| DISCONNECTED | New object; no command permission |
| READY | Stopped, fresh, known gripper and external authority verified |
| ARMED | Explicit approval + lease + successful mode request AND actual absolute-mode confirmation |
| RUNNING | Separate `begin()` with current approval and health |
| STOPPING | Immediate command revocation, Scheduler clear, stop request; no new targets |
| STOPPED | Stop response + fresh idle/velocity confirmation + settled gripper services; lease released |
| FAULT | Latched safety/communication failure; no automatic restart/rearm |

No approval is manufactured by startup. The Mock bridge may begin automatically
only after the operator explicitly supplied `--approve-mock`; the Controller
API still separates connect, arm and begin. No completed/open-gripper/stationary
heuristic declares task success. Duration limit is a normal technical stop but
`policy_normal_end=false`. `complete_by_user()` records explicit human completion.
No stop/fault path initiates Home.

A dispatch or unresolved command exceeding its <=50ms budget is rejected/
faulted. An independent watchdog thread monitors dispatch/pending-command,
scheduler, action stream, first-action and inference heartbeat deadlines. It
revokes permission and clears the Scheduler before requesting stop. A blocked
send does not hold the Controller lock. Cached feedback providers must not
block; Python thread/GIL/process freezes are not covered like a safety PLC.
A logger or Scheduler-cancel exception cannot suppress the stop request.
Adapters also recheck the Action expiry immediately before output; graph or
message-preparation delay must not turn an already-expired goal into a publish.

Stop is best-effort. A service success alone is insufficient. Unconfirmed stop,
communication failure or unknown gripper completion quarantines the lease.
Persistent lease markers prevent a restarted process from silently taking over
an unresolved owner. Quarantine requires manual reconciliation after independent
verification; there is no auto-reset API. FAULT is terminal for that Controller
instance. Local lease release on confirmed stop does not certify external
network ownership.

`emergency_stop()` is a software stop-request path, **not physical E-stop**.
Controller safety functions and a physical E-stop remain independently required.
No program can guarantee delivery of a stop message after communications loss.

## Mark7 command-state handling

Continuous policy output uses configurable open/grasp thresholds with a dead
band, stable dwell and minimum interval. Current command state is retained in
the dead band; duplicate commands and overlapping requests are prevented. An
initial confirmed 0/1 command state is mandatory. Nothing auto-opens/grips on
startup or stop. An already-issued serial command cannot be recalled safely.

`RosMark7Transport` calls `/gripper/open` and `/gripper/grasp` asynchronously.
Only after successful response does it publish `/capstone/gripper_state` so
subsequent ACT observations see its own command changes while QP is excluded.
The original Mark7 server reports completed serial writes, without a physical
motion/grasp ACK. Failure/timeout leaves state unknown and blocks reuse; no
blind retry or release of a possibly held basket occurs.

Snapshot `stop_confirmed` / `indy_stop_confirmed` mean verified Indy stop plus
no unresolved gripper service, **not physical Mark7 motion stop**. The separate
`mark7_physical_stop_confirmed` is unknown and `physical_grasp_verified=false`.
A later Task Executor must preserve that distinction.

## Task Executor interface and diagnostics

`connect()`, `arm(UserApproval)`, `begin()`, `observe()`, `heartbeat()`,
`consume(scheduled_record)`, `poll()`, `stop()`, `emergency_stop()`,
`complete_by_user()` and `snapshot()` are the execution APIs. Snapshot exposes
start time, state, session/checkpoint, submitted/acknowledged counts, last joints,
gripper state, normal completion vs user abort vs safety fault, stop confirmation,
and lease quarantine. `executed_actions` is a Mock-only applied count; Real is
unknown because ROS publish is not an SDK execution ACK.

Logs include profile/readiness proof, mode request, joint target and send timing,
ACK level, rejection with raw goal text, gripper requests/results, state changes,
stop requests/confirmation and final status. Existing run/session/checkpoint IDs
link them to camera/observation, inference and Scheduler records. No action Chunk
queue is duplicated by this layer.

Joint guards are not collision avoidance. Workspace, basket/shelf/obstacles,
load, stopping distance, feedback/communication latency and 20Hz suitability
require supervised hardware validation before any Real activation.

## Historical: resolved normalization defect

The earlier team training entry passed the standalone directory
`train_metadata/stats.json` to official `load_stats()`, which instead searches
`root/meta/stats.json`; it returned None. The previous shape/finite smoke tests
missed empty normalizers. Actual Guard integration caught raw, unnormalized
large goals BEFORE the first Mock command.

The team entry now explicitly reads/casts the standalone stats file using
public LeRobot JSON/stat conversion utilities. Processor state is verified
before training and after reload; known normalized-zero values must map to
saved quantile midpoints. Missing referenced state files or empty required
statistics block checkpoint use. Existing checkpoints are not modified or
silently patched: models trained under the old path need retraining/explicit
review. No official LeRobot source is changed.

Offline tests use virtual-clock Mock devices, fake ROS classes and actual
Synthetic LeRobot ACT checkpoints. The successful wiring fixture learns small
constant hold/grasp labels; it proves software connectivity, not basket-task
competence or real motor performance. Synthetic Dataset/model/cache files are scoped to a unique project tmp. After
verification, retain logs/results/provenance and remove only confirmed synthetic
artifacts. Existing production data and Checkpoints are never cleanup targets.
