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
./act train \
  --dataset-dir /absolute/path/to/lerobot_dataset \
  --dataset-repo-id local/indy7_act \
  --output-dir outputs/act_run \
  --steps 100000
```

Use `--dry-run` to validate the dataset and print the resulting LeRobot
training command without starting optimization. Training defaults to CUDA;
pass `--device cpu --no-use-amp` only for a small functional check.

For formal Core-108 collection, pass `--dataset-dir datasets/indy7_act_core108/success`
and the original Dataset repo id. The Collection root itself is not a Dataset;
Correction/Failure/Pilot are not merged into training. `./act validate` performs
read-only Dataset preflight; its optional `--report` writes a new file outside
the Dataset. `./act train --dry-run` also writes a new plan/output directory.
See [the training guide](ai/train/TRAINING.md) for complete commands.

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

Recorder의 실행·키 조작·선택적 좌표 입력·Core-108 진행 관리·저장 복구는
[Recorder / Core-108 운영 가이드](ai/record/COLLECTION.md)를 기준으로 한다.
정식 수집과 Pilot의 경로를 분리하고, 기존 Dataset을 재개할 때는 가이드의
동일 repo id 및 `--resume` 규칙을 따른다.

장비 실행은 환경 설치와 별도다. `./caps`는 실제 장비를 시작하며 QP 자동 ARM
경로가 있다. 실물 사전 검증 없이 실행하지 않는다. Home 복귀 전에는 `r`로
녹화를 종료한다. 미확인 Gripper 상태를 임의 값으로 채우지 않는다.

저장된 Dataset·Timing Sidecar·Episode Index·장부·receipt는 함께 보존한다.
일반 Dataset 편집기로 일부만 수정하거나 삭제하지 않는다. 오류가 있으면
운영 가이드의 Pending 보존·reconcile 절차를 따른다.
