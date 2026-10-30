# LeRobot ACT environment

This project keeps robot/ROS dependencies in `.venv` and machine-learning
dependencies in `.venv-act`. The ACT environment contains Hugging Face
LeRobot 0.5.1 and uses the vendored source at
`ai/lerobot_source/lerobot`.

## Activate

```bash
cd /home/sirlab2/sirlab258
source scripts/activate_act.sh
python -c "import torch, lerobot; print(torch.__version__, torch.cuda.is_available())"
```

Authenticate only when a Hugging Face dataset or model requires it:

```bash
hf auth login
```

Do not commit tokens. `HF_HOME` defaults to this project's `.hf_cache`.

## Official ACT tutorial

```bash
python ai/lerobot_source/lerobot/examples/tutorial/act/act_using_example.py
```

That example may download public artifacts from Hugging Face.

## Train the project RGB-only dataset

```bash
./act-train \
  --dataset-dir /absolute/path/to/lerobot_dataset \
  --dataset-repo-id your-user/your-dataset \
  --output-dir outputs/act_run \
  --steps 100000
```

Use `--dry-run` to validate the dataset and print the resulting LeRobot
training command without starting optimization. Training defaults to CUDA;
pass `--device cpu --no-use-amp` only for a small functional check.

## Camera observations

`./caps` starts the two RGB cameras used for collection. Their topics are:

```text
/camera_317222074298/camera/color/image_raw
/camera_844212071939/camera/color/image_raw
```

Both streams are configured for 640x480 at 30 FPS. Camera roles should be
assigned only after the physical mounts have been checked. During the first
simultaneous-stream check, image messages were observed but measured delivery
rates fluctuated substantially; treat the camera pair as connected, but run a
short pilot and verify stable frame delivery before collecting the 108 core
episodes. Camera 844212071939 was enumerated as USB2; use a USB3-capable direct
port/cable if available.

## Record Indy7 ACT demonstrations

Build the ROS workspace after changing the Indy teleoperation node so it
publishes commanded joint targets on `/capstone/teleop_action`. Start the full
robot stack in one terminal with `./caps`, then run `./act-record` in another.
The recorder samples `/joint_states`, both configured RGB topics, and the
teleoperation target at 20 FPS into `datasets/indy7_act`. State/action vectors
contain six Indy joints plus a binary gripper value (open `0`, grasp `1`).
Before the first episode, use the Xbox gripper-open button once and wait for
the recorder's readiness check; the gripper state topic must be observed
before `start` is accepted.

The recorder prints the following controls at startup:

```text
[Recorder Controls]
s : start
a : save
r : retry
d : discard
q : quit
```

In the recorder terminal, press `s` at the start of an episode, `a` to commit
it, `d` to drop the current unsaved take, `r` to discard and immediately record
the replacement, or `q` to exit. The original full commands `start`, `save`,
`discard`, `retry`, and `quit` remain supported. `Ctrl+C` also exits and
discards only the unsaved take. A saved episode is not erased by `d`/`discard`;
preserve the master execution log in `docs/ACT_108_Episode_Execution_Log_Template.md`
for target/actual basket coordinates, yaw, and outcome.

To delete a previously saved bad episode, stop the recorder first. Episode
indices are zero-based; for example, remove episode 3 (the fourth episode) with
this LeRobot edit command, which creates an in-place backup:

```bash
source scripts/activate_act.sh
lerobot-edit-dataset \
  --repo_id local/indy7_act \
  --root datasets/indy7_act \
  --operation.type delete_episodes \
  --operation.episode_indices "[3]"
```

Use `--root datasets/indy7_pilot` for a separate pilot. To append to an existing
dataset, pass both its original repo id and `--resume`; the root must contain a
LeRobot `meta/info.json`.
