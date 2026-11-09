# Indy7 ACT Dataset preflight

The shared contract is `ai/dataset_contract.py`: 20 FPS, two named VGA RGB
videos, measured joint0–joint5 in radians plus binary command gripper state,
and absolute joint0–joint5 targets in radians plus binary command gripper state.
The gripper is successful-command state, not grasp/contact sensor feedback.
The schema is unchanged from the Recorder; no existing Dataset migration is
required. The default training identity is now `local/indy7_act`, matching the
Recorder. Custom identities require `--dataset-repo-id`.

Run with the ACT environment, from the project root:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-act/bin/python -m ai.train.validate_dataset \
  --dataset-dir datasets/indy7_act \
  --dataset-repo-id local/indy7_act \
  --report /tmp/indy7-act-validation.json
```

The CLI prints a human-readable report and optionally writes JSON to a new
file outside the Dataset. The parent directory must already exist. An existing
report is never overwritten. Exit status is 0 for trainable, 1 for blocked,
and 2 for invalid CLI arguments. Source Dataset files are never repaired,
modified, downloaded, or deleted. Do not collect/modify the Dataset during
validation: this is a preflight, not an immutable snapshot or locking system.

Python integration:

```python
from ai.train.validate_dataset import validate_dataset, format_report

report = validate_dataset("datasets/indy7_act", repo_id="local/indy7_act")
print(format_report(report))
if not report["trainable"]:
    raise RuntimeError(report["error_counts"])
# Only now proceed to training.
```

`run_train.main()` calls this guard before constructing a Dataset or launching
the trainer, including in `--dry-run`. The local `act train` dispatcher uses
this pipeline. See `TRAINING.md` for splitting, checkpointing and evaluation.

Validation covers every episode and frame, including episodes sharing v3
Parquet/MP4 files. It uses official LeRobot local JSON/task loaders and version
validation, batched PyArrow reads, and sequential PyAV RGB decoding. It avoids
LeRobot Dataset/Metadata constructors because their missing-file fallback can
download data. Package version 0.5.1 uses Dataset **format v3.0**; these are
different version numbers. Other Dataset major formats require explicit review.

Training is blocked by missing/malformed files, incompatible features/order or
physical vector dtype, inconsistent episode/frame indices, invalid values,
nonbinary gripper, nominal/PTS gaps, missing sidecars, unconfirmed success or
integrity, nonzero missed deadlines/skips, and inconsistent stored statistics.
For the isolated forward-only ACT experiment, an Episode with
`outcome=derived_forward_approach` is accepted only with a `derived_from` record
that identifies a successful source, the first Grasp as its exclusive end, and
`home_return_required=false`. It is counted as **zero successful full-task
Episodes** in the report. This permits training the shorter task without
misrepresenting its outcome as a completed basket transfer.
The sidecar path is
`meta/recorder_timing/episode-NNNNNN/{episode.json,frames.jsonl}`.
The saved `dataset_root` is provenance and may differ after relocating a Dataset;
sidecar indices and repo identities must still agree. Older `offline_direct_frame`
sidecars with the existing timing/integrity fields remain supported. Missing
integrity/outcome fields are not implicitly approved.

Bounded-history sidecars additionally recheck the causal reference grid,
selection validity, source ages and skew against **their recorded SyncOptions**,
callback ages, gripper receipt causality and QP command epoch boundaries.
Header/system-epoch relations and monotonic relations are checked separately.
One currently due release cancelled by the Recorder's normal stop is allowed;
interior gaps and two or more unprocessed releases are blocked. No new sensor
clock conversion or synchronization tolerance is introduced. `1e-4` seconds is
the LeRobot nominal/video timestamp precision tolerance, not sensor alignment
tolerance. Stored numeric min/max/mean comparisons use float32 precision
tolerances (`rtol=1e-5`, `atol=1e-6`); stored quantiles are checked for finite shape
and ordering, not recomputed exactly (official aggregation is approximate).

The report includes error counts with up to 100 examples, episode manifest
(indices, lengths, global row ranges), measured numeric statistics, gripper
distribution, source reuse counts, nominal duration, actual recording duration,
and actual per-episode sampling FPS range. Fewer than 108 successful episodes,
constant joints or only one gripper state produce warnings, not automatic
rejection. Nominal duration is frame_count/20, not measured wall duration.
`trainable` means these contract checks passed, not task coverage, collision
safety, physically verified units, sensor synchronization or future model quality.

Memory is bounded by Parquet batches (256 data rows), a few decoded images and
the video decoder's internal buffers, plus O(episode-count) metadata/report.
Videos are decoded once per unique file, not accumulated in RAM. Runtime is
linear in frames/pixels and depends chiefly on storage and CPU video decoding.
No worker pool or all-video RAM cache is used by this Validator.

Offline tests live in `ai/train/test/test_validate_dataset.py`; pass that path
explicitly because the repository's default pytest paths cover ROS packages.
Use a unique project-local `--basetemp`, offline HF flags, a scoped HF cache,
`PYTHONDONTWRITEBYTECODE=1`, and remove only the generated temporary test tree.
The tests include actual Recorder commits, LeRobot encoding/loading, sequential
video validation, bounded-history sidecars, file sharing, fault injection,
read-only fingerprints and the training guard. No ROS nodes or optimization run.
