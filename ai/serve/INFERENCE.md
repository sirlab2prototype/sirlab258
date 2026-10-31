# Indy7 ACT two-camera inference and log-only Dry Run

For the hardware-off preflight and the separately approved ROS observation
procedure, see [ROS integration stage 1](../../docs/ACT_ROS_STAGE1.md).
Driver startup/shutdown is not observation-only. The inference subscriber uses
RELIABLE/VOLATILE/depth=10 and does not request retained gripper events. The
current QP publisher and Recorder subscriber use TRANSIENT_LOCAL/depth=1; this
does not create physical feedback or an initial event before any command.
Unknown initial gripper blocks
inference by default. For ROS Dry Run only, an operator can explicitly supply
an assumed initial state as described below.

From the project root, after collecting data and explicitly training:

```bash
./act infer --checkpoint outputs/my_act_run --source synthetic --dry-run
./act infer --checkpoint outputs/my_act_run --source ros --dry-run
```

Optional transient camera timing recovery is restricted to ROS log-only Dry Run:
`--ros-observation-recovery`. Defaults remain fail-fast. It requires three
consecutive valid delayed-reference observations before inference, pauses and
invalidates executable tails on eligible camera errors, and resumes only with
fresh inference. It never relaxes age/skew limits or applies to execution.
See [recovery behavior and 10s/60s commands](../../docs/ACT_ROS_RECOVERY.md).

Optional **ROS Dry Run only** initial assumption:

```bash
./act infer --checkpoint <pretrained_model> --source ros --dry-run \
  --operator-initial-gripper 0
```

`0` means Open, `1` means Grasp. This is **operator_assumed**, neither sensor
feedback nor evidence of a successful command. No ROS state is published, no
Gripper service is called, and the assumption is not counted as a callback or
inserted into the received message History. Omitting the option preserves the
unknown-initial-state gate. Synthetic/Mock execution rejects the option; Real
execution remains locked and Recorder/Dataset collection has no such option.

The assumption applies only until actual valid Gripper events become causal
for the selected observation reference. Real messages then follow the existing
receipt-time selection, holding and validation rules. Invalid real messages
still stop inference; they do not restore the assumed state. Age/skew/clock
limits are unchanged.

Provenance is recorded in launcher `result.json`, client diagnostics
`gripper_initialization`, and each observation's `sources.gripper.origin`
(`operator_assumed` or `ros_message`). Assumptions have explicit local
`assumed_at_*` times, null callback times, and `command_success_evidence=false`.
Do not use these Dry Run inputs as measured observations for dataset collection.

`--dry-run` is mandatory; there is **no real-execution mode**. The explicit CLI
invocation starts one session after checkpoint verification and observation
readiness. Setup does not start training, teleop or inference. The ROS command
is implemented but must be run only when robot/camera operation is authorized
separately; this work's tests never start real ROS nodes or devices.

`./act infer` owns a localhost server process in `.venv-act` and an observation
client process. The ROS client uses `.venv/bin/python` after sourcing ROS Jazzy
and the existing workspace environment; it does not import Torch or LeRobot.
No camera/driver/teleop/gripper launch command is executed. Synthetic mode uses
no ROS imports. The launcher terminates only its own two processes, including
on errors/interruption. Existing datasets/checkpoints/output files are never
overwritten. A new `outputs/act_infer_<id>` directory is the default output.

The existing client also writes bounded callback diagnostics approximately once
per second and in its final summary: per-topic receipt FPS, header/callback
delay, duplicate/gapped headers, silence age, message format and gripper command
state. `observation_not_ready` distinguishes startup readiness polls from frame
failures; active integrity failures emit `observation_invalid`. ROS startup
excludes headers generated before subscription creation, with a diagnostic
counter; the shared Recorder aligner still enforces age/skew and active faults.
These metrics do not prove exposure timestamps or hardware safety. Log-only
Dry Run reports `safety_validation.evaluated=false, violations=null` because no
verified controller status/reviewed real safety profile is available.

Useful implemented options:

```bash
./act infer --checkpoint outputs/my_act_run --source synthetic --dry-run \
  --device cpu --duration 10 --rpc-timeout-ms 250 --prefetch-steps 3
```

`--device auto` chooses CUDA when available, otherwise CPU. Explicit CUDA fails
if unavailable. `--torch-threads` defaults to 2. `--startup-timeout` (60 seconds)
limits server verification/loading; `--observation-startup-timeout` (5 seconds)
limits initial observation readiness. `--max-lateness-ms` (20, strictly below
50) is a scheduler operational limit, not a sensor clock tolerance. All
SyncOptions are exposed using the same names as the Recorder, e.g.
`--receive-wait-ms`, `--camera-max-age-ms`, `--state-max-age-ms`,
`--camera-skew-ms`, `--observation-skew-ms`, `--history-size`,
`--history-seconds`, `--clock-tolerance-ms`.

## Server / protocol

The existing `zmq_act_server.py` now serves `indy7_act_rgb2_v2` via ZMQ REP.
Only literal loopback TCP addresses are allowed; external interfaces, DNS
names, and remote clock domains are rejected. Localhost processes share the
same OS monotonic clock. This protocol does not support remote hosts.

The server verifies the portable training checkpoint, loads the existing
`RGBActBackend`/`LeRobotActBackend`, checks the two-camera 7D contract, and uses
the saved official pre/postprocessors. No source Dataset is needed. RGB inputs
are exact uint8 HWC 480x640x3 arrays; state is finite float32[7], gripper 0/1.
No resize or dimension guessing occurs. Outputs are finite float32
`[checkpoint.chunk_size,7]` physical-unit chunks. Predicted gripper is a policy
output, not a sensor reading and not an issued open/grasp command.

Requests include version, command, Session ID, Request ID, paired observation
reference clocks and host-monotonic domain. Reset records a monotonically
ordered session start; obsolete sessions, duplicate prediction IDs, malformed
buffers, wrong features and stale/future observation references are rejected.
Default reference age at server receipt is capped at 250ms, separately from
camera/joint alignment ages. Error responses explicitly set `ok=false` and
`output_valid=false`; they never contain usable actions. Replies echo IDs,
checkpoint SHA-256, chunk shape/config, server receive/complete times and wall
inference duration, including saved pre/postprocessing and CUDA-to-CPU sync.
Per-session request IDs are bounded to 4096; start a new session beyond that.
The client checks checkpoint/config identity on reset, prediction and end, and
checks the echoed observation reference before accepting a chunk. Summaries
distinguish server-generated valid chunks from accepted scheduler chunks;
the former is unknown if the final end acknowledgement cannot be obtained.

For separate-process operation without the launcher:

```bash
.venv-act/bin/python -m ai.serve.zmq_act_server \
  --checkpoint outputs/my_act_run --endpoint tcp://127.0.0.1:5557 \
  --ready-file tmp/act-server-ready.json --log tmp/act-server-events.jsonl
# In an appropriately sourced ROS environment for source=ros:
.venv/bin/python -m ai.serve.dry_run --source ros --dry-run \
  --endpoint tcp://127.0.0.1:5557 --metadata tmp/act-server-ready.json \
  --output-dir tmp/act-client-new
```

Paths must be new; create/use only a scoped test/output directory. The old
single-camera pool and wire helpers were removed after preservation in Git
`78794cf`. Only the v2 server/client documented here is supported; no pool or
blind-model selection is advertised as compatible with this chunk client.

## One action owner

Only the client `DryRunScheduler` owns chunk consumption. The server calls
`predict_action_chunk()` and never consumes official `select_action()` queues.
`chunk_size` and `n_action_steps` come from the checkpoint, not hardcoded 40/10.
Temporal-ensemble checkpoints are rejected rather than silently implementing a
different consumption strategy.

A prediction row i is scheduled on `observation_reference + i * 50ms`. This is
an explicit nominal replay convention, not proof of actual SDK execution time.
Rows already past when the response arrives are discarded. At most
`n_action_steps` usable rows of one prediction are consumed; a second bounded
prediction can be prefetched ahead of the handover. A response must be ready
before its first scheduled output. There is one pending request and at most
two bounded prediction arrays; no unbounded request or action queues exist.

The schedule uses fixed monotonic deadlines. It emits at most one row per poll,
preserves row order, and fails on missed slots, excessive lateness, expired
predictions or starvation. It never repeats the last action to hide slow
inference and never fills skipped intervals. A short horizon (for example the
previous 2-row training smoke checkpoint) may expire before alignment/transport
complete; that is a safe failure, not a guarantee that any checkpoint can run
at 20Hz. The inference E2E tests train a 12-row/4-action smoke model; production
defaults remain 40/10.

Communication is in one worker, with a fresh REQ socket per call and a bounded
timeout. A late reply to a timed-out socket cannot be reused for a new request.
Session/request checks reject mismatches; the scheduler ignores obsolete
session/request results. A detected disconnect/timeout stops outputs and clears
pending actions. An idle server failure cannot be known instantly: detection
occurs on the next bounded prefetch/RPC or action handover. Before detection,
only already accepted finite forecast rows may be logged. This is **not a
real-robot communications watchdog**.

## Observation alignment / gripper

`ai/observation_timing.py` contains the unchanged pure FixedDeadline,
SyncOptions, BoundedHistory and HistoryAligner code moved from the Recorder.
The Recorder re-exports these names; recording and Dataset saving are not
imported by the client. RGB history is bounded by count and retention time,
with immutable array references (no extra history image copies). ROS image
conversion makes the necessary owned RGB copy once.

The client subscribes only to:

- `/camera_317222074298/camera/color/image_raw`
- `/camera_844212071939/camera/color/image_raw`
- `/joint_states`
- `/capstone/gripper_state`

The same causal header-at-or-before-reference selection is used. Defaults are
60ms receive wait, camera age <=50ms, state age <=75ms, camera skew <=40ms,
observation skew <=75ms, history 32 samples / 0.5s, clock guard 10ms. These are
the existing Recorder limits, not new measured sensor guarantees. ROS headers
are used under the operator-confirmed Unix-epoch deployment assumption;
use_sim_time, clock jumps, header reversals, missing/stale observations and
history faults stop the session. Header time is never subtracted from
monotonic time. Paired start clocks map the reference grid by elapsed duration.

The headerless gripper uses local callback monotonic time and carries its last
confirmed 0/1 command state. QP publishes this after a successful service
response; the existing volatile publisher does not guarantee late subscribers
receive initial state. The client never invents state, calls a gripper service
or queries a physical sensor. It waits and fails readiness if no command state
has been observed. Long-lived command state is not falsely treated as expired
sensor feedback. Actual hardware operation must establish state separately.

ROS callbacks run in a dedicated thread, while RPC and scheduling are separate.
Callbacks do no GPU inference, network waits or Dataset writing. The synthetic
source models 30Hz cameras with 33.2/36.2ms delivery and 20Hz state with 1.5ms
delivery; its gripper value is explicitly synthetic.

## Logs / diagnostics / future execution boundary

`result.json`, `server_ready.json`, `server_events.jsonl`, `server.log`,
`client.log`, `client/events.jsonl` and `client/summary.json` link run/session,
checkpoint ID, original headers, callback clocks, observation references,
age/skew, request/response times, chunk IDs, consumed indices and seven output
values, deadline lateness/misses, resets and failures. Actions are **only JSONL
log events**. No publishers/services/SDK calls send them anywhere. The summary
reports actual action FPS, interval range and inference p50/p95/max. Summary
metric samples are bounded to 100000 with an explicit truncation flag; all
per-action records remain on disk. A real-time capability claim requires
measured device/model-specific results, not the existence of this scheduler.

The next safety layer can consume a validated scheduled record (session,
request, checkpoint, reference, target deadline, seven physical-unit values)
and source-health metadata. It must add command ownership/mode handover,
Indy7 joint target limits/speed/tracking/watchdog, Mark7 event policy and task
lifecycle. No actuator plug-in is active in this implementation; a future layer
must explicitly change that boundary. Model technical validity is not robot
safety, task success or collision clearance.
Owned-server crash handling: the local launcher reports server loss to its own
client with SIGUSR1 (Ubuntu/POSIX). The client treats it as a connection Fault,
clears scheduled actions and runs stop cleanup, rather than labelling it an
operator Ctrl+C abort. Ordinary SIGTERM/Ctrl+C retain operator-stop semantics.
Abnormal launcher results retain the child's summary/Task and the parent's
failure reason separately. This is software fault handling, not a physical
stop-delivery guarantee.

