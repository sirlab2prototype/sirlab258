# Local Indy7 ACT training and checkpoint pipeline

Only an explicit training invocation starts optimization. No setup or robot
launch is added. From the repository root:

```bash
act train
# Equivalent backward-compatible entry point:
act train
```

The user-local `act` launcher selects the project ACT environment. Shared environments and official sources are unchanged.

Defaults: successful `datasets/indy7_act`, identity `local/indy7_act`, a new
`outputs/act_<UTC-time>-<random-id>` run, 100000 steps, batch 64, LR 1e-4,
seed 0, chunk 40, n_action_steps 10, save every 20000, CUDA, workers 4,
RAM cache **off**. At 20FPS chunk 40 predicts 2 seconds and n_action_steps 10
represents 0.5 seconds of actions. This pipeline does not execute those actions.
The legacy 12 single-camera experiment YAMLs are descriptive artifacts, not
active Indy7 training configurations.

For formal Core-108, use `datasets/indy7_act_core108/success`, not the Collection
root. The default `datasets/indy7_act` is the independent Recorder path and is
not an alias for Core-108. Keep the original repo id (default `local/indy7_act`):

```bash
act validate --dataset-dir datasets/indy7_act_core108/success \
  --dataset-repo-id local/indy7_act --report outputs/core108_validation.json
act train --dataset-dir datasets/indy7_act_core108/success \
  --dataset-repo-id local/indy7_act --output-dir outputs/core108_plan \
  --device cpu --no-use-amp --dry-run
```

The report parent must already exist; report/output paths must be new. Validator
reads the Dataset without constructing a LeRobotDataset or changing its files.
Training `--dry-run` performs preflight and writes a plan, Train statistics and
run metadata, but does not optimize. Omit `--dry-run` only to explicitly start
training with the intended device/steps. Correction/Failure outcomes are rejected
by this success-only preflight; correction-aware training is not implemented.

```bash
act train --dataset-dir datasets/indy7_act --output-dir outputs/my_act_run \
  --steps 100000 --batch-size 32 --learning-rate 0.0001 --seed 42 \
  --chunk-size 40 --n-action-steps 10 --save-freq 20000 \
  --validation-fraction 0.2 --no-ram-cache

# Full preflight and recorded plan, without optimization:
act train --dry-run

# Deliberate small CPU functional run (use test data for software smoke tests):
act train --device cpu --no-use-amp --dev-small --steps 2 \
  --batch-size 1 --num-workers 0 --chunk-size 2 --n-action-steps 1 --save-freq 1
```

The model is the official LeRobot ACT + ResNet18. Initial backbone weights are
not downloaded: `pretrained_backbone_weights=null` initializes locally. This is
recorded in the actual train config. `--dev-small` changes Transformer sizes
for functional tests; it retains the real two VGA RGB inputs and ResNet18.
CPU mode disables AMP. Synthetic smoke accuracy is not a model-quality claim.

## 고정된 Snapshot 입력

수집을 계속하는 원본 대신 `act snapshot`으로 독립 Dataset을 만든 뒤 그 루트를
`--dataset-dir`에 지정할 수 있다. 단일·다중 입력, repo id와 출처는
[Snapshot 사용법](SNAPSHOTS.md)을 따른다. 기존 학습 preflight와 실행 방식은 동일하다.

## Preflight and split

The existing read-only Validator runs before Dataset loading or optimization.
It sequentially decodes **every frame of every referenced video**, converts each
to RGB, and checks dimensions, PTS and count against episode intervals. It does
not accumulate videos in memory. Reports state the scope, decoded frame count,
`video_validation_s`, and total validation time. Tests include a later corrupt
packet with a still-decodable first frame, an interior PTS gap, and a missing
last frame. See `VALIDATION.md` for remaining checks.

Train/Validation IDs are sorted, deterministically shuffled with a local seed,
and split by whole episode. Validation count is rounded from the requested
fraction and clamped to at least one and at most N-1 for N>=2. A one-episode
Dataset automatically uses pilot-only training; `--pilot-only` explicitly
selects this for larger Datasets. Fewer than 108 successes is not a rejection.
No held-out data means no validation metric; generalization is never certified.

Only Train episode IDs go to official `dataset.episodes`. The official Dataset
clips action chunk indices to the current episode and returns `action_is_pad`;
training and evaluation mask padded positions. No frames cross the split.
A small hook in the **team-owned** `lerobot_entry.py` uses official
`aggregate_stats()` on Train episode stats, replacing the loaded dataset's
in-memory stats before policy/processors are built. It never writes the source
Dataset. Validation therefore does not influence normalization. Official
quantiles are approximations aggregated by LeRobot, not recomputed exact
quantiles of all raw values. No official source file is edited.

Dataset fingerprints hash relative paths, sizes and full file bytes of
`meta`, `data`, and `videos`, including timing sidecars. They are independent of
absolute location/mtime. They are compared before/after training and evaluation.
Do not collect or modify this Dataset during a run. This detects changes but
does not provide filesystem locking or a transactional snapshot. HF offline
flags prohibit the training subprocess from filling missing data remotely.

## Cache and output

Before training, the Train-only RGB estimate is displayed and recorded:
`frames * 2 * 640 * 480 * 3`, approximately **2.06 GiB/minute** at 20FPS.
This excludes action chunk tensors, batches, workers, decoder state, model and
optimizer. `--ram-cache` explicitly enables the already-existing vendored cache
extension. An RGB estimate exceeding half available host pages is refused.
This host estimate is not a GPU/cgroup memory guarantee; leave caching off
unless available memory has been checked. Images are never all cached by the
Validator or evaluator.

Each invocation gets a new run root, even on resume. Existing output paths and
paths nested in the Dataset are refused. Artifacts:

```text
run.json / result.json          identity, settings, split, versions, Git state,
                               warnings/errors, completion and eligibility
 dataset_validation.json       full Validator report
 train_metadata/stats.json     Train-only normalizer source
 training.log                  official trainer stdout/stderr including losses
 training/checkpoints/<step>/  official pretrained_model + training_state
 training/checkpoints/last     official last-checkpoint link
 checkpoint_validation.json    reload, contract, actual inference and reset
 validation_metrics.json       held-out physical-unit action metrics
```

The `pretrained_model` directory additionally gets `indy7_contract.json` and
`training_provenance.json`. All official model, processor config and processor
state files must be retained. The saved processors contain normalization
statistics, so **no Dataset metadata files or source Dataset are required for
portable inference**. `train_config.json` retains the original Dataset path as
provenance; it is not accessed by the portable loader.

The team entry reads the standalone `train_metadata/stats.json` explicitly.
The official `load_stats(root)` expects `root/meta/stats.json` and must not be
given that standalone directory. Training and checkpoint reload now reject
missing/empty saved normalization state and verify known-value unnormalization.
Checkpoints produced with the earlier empty-statistics path are not patched;
they require retraining or explicit review before use. See
`../execute/EXECUTION.md` for the integration finding.

## Resume

```bash
# Last available checkpoint of a prior pipeline run:
act train --resume outputs/prior_run --steps 100000
# Or a particular checkpoint:
act train --resume outputs/prior_run/training/checkpoints/020000 --steps 100000
```

The Dataset identity/content hash, split, model/optimizer settings and saved
policy/train config are checked. Required optimizer/RNG/scheduler files,
training step and scheduler epoch are checked. A run must have this pipeline's
`run.json` provenance; arbitrary old checkpoints are not silently adopted.
Unspecified options inherit the previous run; incompatible explicit overrides
fail. Device, workers and caching can change; total steps must exceed the saved
step. The selected checkpoint is **copied into the new run**, and official
LeRobot resume reloads its weights/processors, optimizer, scheduler, RNG and
step. The original run is never overwritten.

Completed-run training configurations are also compared against their recorded
SHA-256. Optimizer update steps must be finite, positive and no greater than the
global step (AMP may skip updates), and the RNG state must be readable.

Extending total steps beyond the original scheduler's decay horizon is allowed
with a recorded warning. The original scheduler definition and state are
preserved, including its behavior after that horizon; warmup/decay are not
silently redesigned. For ordinary interrupted-run resume, keep the original
planned total step. Resume restores official RNG state but does not promise
bit-identical future minibatch order or CUDA numerical results.

## Checkpoint approval and offline metrics

A zero trainer exit is insufficient. The last checkpoint must reach the
requested step, load all required configs/weights/preprocessor/postprocessor,
match two CHW RGB features + state 7D + action 7D and the portable data contract,
execute real 640x480 two-camera inference, produce a finite unnormalized
`[chunk_size,7]` chunk and `[7]` action, and infer again after reset.
Verification or evaluation failure records `status=failed` and
`eligible_for_inference=false`. No global model registry is modified.

The evaluator uses saved official processors for normalization and
unnormalization; no manual q10/q90 reversal. It reports per-episode and aggregate
six-joint MAE (rad), per-joint errors, gripper MAE and threshold-0.5 accuracy,
and Hold Baseline comparisons. Hold means copying the current six measured
joint positions and current gripper index 6. Padding is masked. Metrics average
valid action chunk positions, including overlapping chunk targets; these are
not closed-loop rollout metrics or robot task success rates. Training losses
remain in the official log. Generalization and robot safety remain unverified.

Interfaces for the later inference server:

```python
from ai.train.checkpoint import verify_checkpoint
from ai.serve.lerobot_act_backend import LeRobotActBackend

report = verify_checkpoint("outputs/my_act_run", device="cpu")
backend = LeRobotActBackend("outputs/my_act_run", device="cuda")
action = backend.predict(observation)        # [7], official select_action queue
chunk = backend.predict_chunk(observation)  # [chunk_size,7], no queue consumption
backend.reset()
```

`observation` contains the exact two camera feature names with uint8 HWC arrays
and a float32 state vector of length 7. Inputs are not mutated by these methods.
Use only one action-consumption strategy per session. The current
`RGBActBackend` extends `LeRobotActBackend` with two-camera/7D contract checks,
and `act infer` uses `zmq_act_server.py` with `indy7_act_rgb2_v2`.
The incompatible single-camera/10D pool, selector and legacy wire helper were
removed. Their exact sources are preserved in commit `78794cf`; they are not a
supported Indy7 inference path. The active server/protocol above is unchanged.
Existing backend callers supplying a Dataset keep the compatibility path;
new portable bundles should omit Dataset arguments.

## Verification limits

CPU synthetic tests execute actual LeRobot ACT optimization, official saving,
reloading, held-out evaluation, reset, portability and optimizer resume. They
also exercise pilot-only training, split reproducibility, chunk padding,
Train-only stats, cache estimates, config mismatch and false trainer success.
Use a unique project-local temporary tree for data, HF/Torch caches and outputs;
retained test artifacts are not production Datasets or Checkpoints. CPU-only
verification hides CUDA and deselects CUDA cases explicitly. No real Dataset,
real checkpoint, robot/camera or CUDA/AMP training is used in that run.
Large-scale convergence, performance, safety and
physical deployment require later validation.

## 연구실 ACT 환경

현재 PC의 `.venv-act`를 사용한다. ROS/제어용 `.venv`와 NumPy/ABI 의존성이 다르므로 합치지 않는다.

```bash
source scripts/activate_act.sh
```

`HF_HOME`을 별도로 지정하지 않으면 프로젝트 `.hf_cache`를 사용하며 캐시는 필요 시 재생성된다.
HF 인증은 원격 자료가 필요한 경우에만 수행하고 토큰은 커밋하지 않는다.
공식 학습 예제 `ai/lerobot_source/lerobot/examples/tutorial/act/act_using_example.py`는
외부 다운로드를 할 수 있는 참고 자료이며 현재 Indy7 학습 진입점을 대체하지 않는다.
현재 원래 Dataset ID를 유지하고 출력 경로는 새로 지정한다. `--dry-run`도 검증·계획 파일을 쓴다.
장비 설정·실제 카메라 실행은 [Teleop 가이드](../../docs/TELEOP.md),
수집 운영은 [COLLECTION](../record/COLLECTION.md)을 따른다.
