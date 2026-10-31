# ACT evaluation and failure analysis

```bash
./act evaluate --runs outputs/ --report-dir outputs/evaluation_report
./act evaluate --runs outputs/ --records outputs/act_evaluations \
  --checkpoint <checkpoint-content-id> --condition region=L \
  --condition training_coverage=unseen --report-dir outputs/evaluation_left
```

Inputs are read-only. Output directories must be new and separate from original
Task/training runs, Dataset and Checkpoint directories. There is no device,
inference, training or network initialization in this command. Paths must remain
inside this project. Existing Real execution locks are unchanged.

Outputs: `report.md`, `report.json`, `cases.csv`. Reports include input integrity
issues, stage/bin numerators and denominators, included Task IDs, per-case
evidence, Checkpoint comparisons and improvement hypotheses. CSV strings that
could be spreadsheet formulas are escaped.

## Three different questions

1. **Offline policy error** reuses `run.json`/`result.json`'s existing
   `offline_validation` output from `ai/train/evaluate_overfit.py`. That code
   already uses saved official processors and unnormalized ACT chunk outputs,
   masks padding, and measures six-joint MAE, gripper MAE/accuracy, Hold Baseline
   and per-episode errors. This report does not run that evaluator or retrain a
   model. It verifies Train/Validation disjointness and exact held-out Episode
   membership before including recorded errors. Missing metrics remain unknown;
   pilot-only runs have no held-out evaluation. Condition filters apply to Task
   assessments, not to Dataset episodes without pose annotations. A low offline
   error does not establish generalization or robot success.

   Gripper accuracy uses the existing 0.5 threshold. Numerator/denominator are
   checked against `valid_chunk_positions` for total and per-episode metrics.
   Overlapping Action Chunk positions are not independent robot attempts; their
   accuracy is not given a robot-trial binomial confidence interval.
2. **Execution reliability** uses actual client `prediction_response`, `action`,
   observation, Controller/Task state, stop and Home events. Inference duration
   comes from the server's already-computed `response.inference_ns`; RPC elapsed
   time uses the client's own sent/received monotonic timestamps. Action period
   uses `actual_output_monotonic_ns`, so it describes the Scheduler, not SDK or
   physical servo timing. No different clocks are subtracted. Stop/Home software
   verification is not a physical success label.
3. **Operator task assessment** is a separate immutable record. Neither Task
   `COMPLETED`/`success`, `act_policy_success_user_reported`, gripper command ACK,
   nor Home software state automatically populates physical outcomes. Without
   an assessment, every physical stage is `unknown` even for a completed Task.

Mock, Synthetic, Real and unknown environments are grouped separately. Current
Task files declare Mock through `execution.mock=true`; `false` identifies the
non-Mock execution context, not proof that hardware moved or the task succeeded.
A record cannot relabel a Mock or unknown source as Real. No actual Real trials
have been performed by implementing/testing this evaluator. Synthetic tests with
Real-shaped metadata are fabricated fixtures, not experimental results.

## Operator record workflow

```bash
./act evaluate template --task-result outputs/<run>/client/task_result.json \
  --output outputs/ratings/trial1_judgment.json
# Operator edits the new judgment file, independently of the Task source.
./act evaluate record --task-result outputs/<run>/client/task_result.json \
  --judgment outputs/ratings/trial1_judgment.json --store outputs/act_evaluations
```

The template has blank evaluator, `validity=unknown`, unknown stage outcomes and
unknown conditions. The operator must explicitly choose validity and outcomes.
Validity is `valid`, `invalid` (reason mandatory), or `unknown`. Each of `grasp`,
`lift`, `transport`, `placement`, `release`, `retreat`, `act`, `home`, `task` is
`success`, `failure`, `unknown`, or `not_attempted`. Known contradictions such as
Task success alongside Home failure are rejected. Unknown substage details do
not invalidate a separately observed whole-Task assessment; they are not filled.

Every saved record includes Task/run/session/checkpoint IDs, canonical Task
payload SHA256, environment, evaluator and UTC evaluation time. Optional
`training_run_id`, Dataset fingerprint SHA256 as `dataset_id`, related Dataset
Episode IDs, actual pose/context, failure stage/causes, notes and log references
are retained. Missing provenance is not fabricated. Training coverage `seen`/
`unseen` is an operator annotation; matching a coarse bin does not prove a pose
was present in training.

To correct an assessment, edit a new judgment file and append a new record:

```bash
./act evaluate record --task-result outputs/<run>/client/task_result.json \
  --judgment outputs/ratings/trial1_corrected.json --store outputs/act_evaluations \
  --supersedes <current-evaluation-id>
```

`change_reason` must explain the correction. The old file is never overwritten.
Revisions bind the parent ID/hash and the same original Task hash. A writer file
lock and atomic, exclusive publication prevent accidental overwrite/branches.
This is an audit trail, not a cryptographic signature or tamper-proof database.
The latest unambiguous valid chain is selected. Branches/orphans/duplicate-ID
conflicts are reported, not silently resolved. An unreadable evaluation file
whose affected Task cannot be established withholds physical judgments until
the store is repaired externally. This tool never repairs/deletes source files.

## Conditions and original plan

Default `conditions.json` cites `docs/ACT_Data_Collection_Plan.md` sections 5, 6
and 11: **L/C/R × six Yaw bins × six successful demonstrations = 108**. The
candidate Yaw range is -40..40deg with boundaries -40,-27,-13,0,13,27,40. Bins
are [min,max), with the final upper endpoint included; ambiguous shared endpoints
in the prose plan are made deterministic only for reporting. The plan calls for
roughly **60 unseen-pose evaluations**, not 108 evaluation trials.

Depth is continuously sampled inside the feasible range and depends on yaw.
Front/Front-Middle/Middle/Middle-Back/Back/Random are coverage labels; the plan
does not define universal numeric depth bins. Defaults therefore contain no cm
thresholds. Record actual `x_cm`, `y_cm`, `depth_cm`, `yaw_deg`, region and depth
condition; absent actual values stay unknown/unbinned. Use `--conditions <json>`
to change regions, yaw bins or numeric `depth_bins_cm` after pilot measurements.
These are reporting bins, not certified motion/safety limits or collection gates.

## Denominators and uncertainty

- Physical rate per stage = successes / (successes + failures) from the latest
  compatible operator record with `validity=valid`. Unknown, not_attempted,
  unassessed, invalid and validity_unknown are separately counted exclusions.
- Aborted/Fault/Timeout attempts remain visible. They enter a physical metric
  only if explicitly and validly assessed for that stage. They are not silently
  turned into physical failures or removed as successful-only selection.
- Stage rates are conditional on that stage's assessed outcomes; stage
  not_attempted is not retroactively called failure. Task, ACT and Home rates
  have separate denominators and separate included Task lists.
- Every rate includes numerator/denominator and Wilson two-sided 95% interval.
  n=0 gives `null`/unknown, never 0% or 100%. Broad small-sample intervals are
  shown; there is no 108-trial minimum or performance threshold.
- Reliability attempts include invalid experiments as system diagnostics, in
  separate environment groups. Logged safety-stop means explicit Guard reject,
  E-stop request or safety-condition signal. Generic `safety_fault=true` is a
  fault latch also used for communication/logging errors and is not proof of a
  Guard intervention. Negative logged-safety assessment needs complete client
  log coverage; positive explicit signals remain visible in partial logs.
- Software stop/arrival rates are labelled separately from physical Home success.

## Input identity, relocation and incomplete logs

The loader reads actual Task payloads, `client.task`/`task` wrappers and `task_end`
or `session_end` events. Identical copies are one Task attempt; conflicting Task
IDs/session IDs/payloads are withheld. Exact duplicate event files are detected
by streamed content hash. For multiple partial logs of one session, one most
complete client log is selected; partial files are not concatenated into an
apparently continuous timeline. Missing/corrupt logs remain issues with partial
coverage. Sessions without a Task result retain separate diagnostic metrics and
never become fabricated physical attempts.

Source absolute paths may become stale. Task/evaluation joins use IDs and the
canonical Task payload hash, not those paths. Log evidence points to the current
discovered source path and line. The existing inference bundle content-ID hash
was moved unchanged to `ai/checkpoint_identity.py`. Future checkpoint verification
also records that ID, enabling portable joins to training manifests. Older
bundles can be hashed in their discovered run directory (streamed, no model
loading). Without a verified ID, a named operator training link is explicitly
unverified. Missing training provenance remains unknown; no model is downloaded.

When a launcher `result.json` includes its child's Task and a parent failure
reason, the reason is retained as separate `origin=launcher` evidence joined by
the child's IDs. For example, server disconnection and the client's starvation
or timeout can both be reported without rewriting the original Task payload or
guessing a physical manipulation cause.

JSON metadata is limited to 16MiB; JSONL is read sequentially with a 4Mi-character
line limit (bounded decoded text, up to four bytes per character).
No video/RGB/tensor is loaded. Numeric metrics retain at most 10,000 values per
metric/session and per pooled group. Median/P95/P99 are exact for retained
values; a truncated chronological prefix is explicitly labelled as such, not
reported as an exact full-log percentile. Metadata and report size scale with
attempt count; failure evidence is bounded to five examples per code/log.

## Failure analysis and Checkpoint comparisons

Failure codes separate policy/manipulation, system/control, safety/operator, and
unknown/availability. Multiple labels may apply to one attempt. Log token rules
produce **reported event signals**, not inferred manipulation causes. Operator
causes retain `observed` versus `suspected` and their evidence; suggestions never
promote a suspected cause to an established causal explanation.

Checkpoint reports show Dataset version, successful demo count when known,
Train/Validation Episode IDs, ACT settings/steps, recorded offline errors, timing,
per-environment stage/condition rates, safety stops and sample counts. Exact
pose/protocol/workspace/camera/object distributions and Dataset/split differences
are flagged. No automatic winner/ranking is declared from unmatched conditions.

Recommendations link metric denominators and Task/run IDs. Manipulation-stage
failures can support position/yaw/depth or Grasp/Place/Retreat data hypotheses;
Home-only failure is not used to recommend additional grasp demonstrations.
Observation/transport/Guard/tracking signals instead suggest system diagnosis.
Corrective recovery is a separate experiment, never automatic failure-data mixing
into normal behavior cloning. Offline failure to beat Hold motivates controlled
step/chunk comparisons, not a claim about robot success. Nothing changes data,
hyperparameters, safety limits or robot control automatically.

## Tests

`ai/evaluate/test/test_evaluation.py` uses project-local temporary metadata and
operator fixtures, plus retained actual earlier Synthetic ACT/Mock Task E2E logs
in `outputs/act_task_verification_c3740dc278`. It does not train or run a policy.
Retained source bytes are checked unchanged. Training-bearing legacy tests must
be excluded when the current work prohibits learning; report their exclusion
explicitly instead of claiming a fresh training/checkpoint E2E run.
