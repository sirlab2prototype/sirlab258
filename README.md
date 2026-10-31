# Capstone

Neuromeka Indy7 + Mark7 기반 캡스톤 시스템.

운영 문서: [Recorder·Core-108 수집](ai/record/COLLECTION.md) · [ACT 환경](ACT_SETUP.md) · [108개 수집 계획](docs/ACT_Data_Collection_Plan.md)

기존 시스템의 필요한 기능과 동작만 참고하고,
현재 캡스톤에 필요한 기능을 단순하고 설명 가능한 구조로 새로 구현한다.

현재 우선순위:

- 실제 Indy7 안정 동작
- 간단한 Xbox 수동 조작
- Mark7 gripper 제어
- demonstration 수집
- ACT 학습 및 평가

---

## Environment

현재 검증 환경:

- Ubuntu 24.04
- ROS 2 Jazzy
- Python 3.12
- Neuromeka Indy7
- Mand.ro Mark7
- Xbox Controller
- ROBOTIS OMY (official Docker-based ROS 2 Jazzy stack)

Python dependencies:

- `neuromeka==3.4.2.2`
- `numpy==1.26.4`
- `empy==3.3.4`

Official Neuromeka ROS 2 driver:

- Repository: `neuromeka-robotics/indy-ros2`
- Branch: `jazzy-indyDCP3`
- Commit: `ca0353f077620c877c1edff2e26c8cd976773f27`
- Git submodule로 관리

Official ROBOTIS OMY driver:

- Repository: `ROBOTIS-GIT/open_manipulator`
- Branch: `jazzy`
- Git submodule로 관리
- 프로젝트의 기존 ROS 패키지와 의존성을 분리하기 위해 공식 Docker 환경 사용

Official driver source는 capstone 기능을 위해 직접 수정하지 않는다.

---

## Project structure

```text
capstone/
├── README.md
├── requirements.txt
├── scripts/
│   ├── bootstrap.sh
│   ├── install_commands.sh
│   └── bin/
│       ├── capi
│       ├── capg
│       ├── capx
│       └── caps
│
└── ros2_ws/
    └── src/
        ├── capstone_teleop/
        │   ├── config/
        │   │   └── indy7.yaml
        │   ├── launch/
        │   └── capstone_teleop/
        │
        ├── capstone_mark7/
        │   ├── config/
        │   │   └── mark7.yaml
        │   └── capstone_mark7/
        │
        └── external/
            └── indy-ros2/
```

Generated files are not version-controlled:

```text
.venv/
ros2_ws/build/
ros2_ws/install/
ros2_ws/log/
datasets/
checkpoints/
recordings/
```

---

## Setup on a new PC

Required beforehand:

- Ubuntu 24.04
- ROS 2 Jazzy
- Python 3.12
- Git
- colcon
- Python tkinter support

Clone:

```bash
git clone --recurse-submodules <repository-url>
cd capstone
```

Setup:

```bash
./scripts/bootstrap.sh
```

`bootstrap.sh` performs:

1. prerequisite checks
2. Git submodule initialization
3. project-local `.venv` creation
4. Python dependency installation
5. tkinter availability check
6. ROS 2 workspace build
7. project-local command installation

It does not install apt packages or modify system-wide configuration.

---

## Build

Normal build:

```bash
cd ros2_ws
colcon build
```

Do not use:

```bash
colcon build --symlink-install
```

with the currently pinned official Indy ROS 2 source.

After moving or cloning the project, do not reuse:

```text
.venv/
ros2_ws/build/
ros2_ws/install/
ros2_ws/log/
```

Recreate the environment and rebuild instead.

---

## Commands

Activate the project environment:

```bash
source .venv/bin/activate
```

Convenience commands:

```text
capi    Indy7 driver
capg    Mark7 gripper
capx    Xbox + QP teleop + GUI
caps    Indy7 + Mark7 + Xbox full manual setup
omy     ROBOTIS OMY environment manager
```

The commands are installed inside `.venv/bin`.

`caps` also starts both configured RealSense RGB cameras and opens one preview
window per camera. From the project root, the same setup can be started without
activating the virtual environment:

```bash
./caps
```

To keep the camera topics active without opening preview windows:

```bash
./caps show_camera:=false
```

Camera topics:

```text
/camera_317222074298/camera/color/image_raw
/camera_844212071939/camera/color/image_raw
```

No shell-global alias or `~/.bashrc` modification is required.

---

## ROBOTIS OMY

OMY는 ROBOTIS 공식 Ubuntu 24.04 / ROS 2 Jazzy Docker 환경에서 실행한다.
최초 설정 및 상태 확인:

```bash
git submodule update --init --recursive
./scripts/install_commands.sh
omy doctor
omy start
```

컨테이너에 들어가려면:

```bash
omy enter
```

OMY 리더–팔로워 텔레옵 실행:

```bash
omy launch
```

`launch` 명령은 follower 초기 자세 이동 후 L100 리더의 중력보상과 동기화를
시작하므로 실제 로봇이 즉시 움직인다. 작업 공간을 비우고 비상정지 버튼에 손이
닿는 상태에서 실행한다.

상태 확인 및 종료:

```bash
omy status
omy robot-stop
```

OMY 로봇 PC를 네트워크로 사용하는 경우 로봇 PC와 사용자 PC의
`ROS_DOMAIN_ID`를 동일하게 유지한다. `omy launch`는 OMY 로봇 PC에서 공식
`omy_ai.launch.py`를 domain `30`, `rmw_zenoh_cpp`로 실행한다. 현재 USB 직결 주소는
`10.42.0.101`이며 필요하면 `OMY_ROBOT_HOST` 환경 변수로 덮어쓸 수 있다.

---

## ACT 운영 순서 — 수집부터 평가까지

프로젝트 루트에서 실행한다. ROS/제어 환경은 `.venv`, LeRobot 0.5.1 학습·서버
환경은 `.venv-act`이며, `./act`가 필요한 Python을 선택한다. 외부 PATH 설정은
필요하지 않다. 설치/build인 `./scripts/bootstrap.sh`는 학습이나 Teleop을 실행하지
않으며 `./act setup`은 지원하지 않는다. `./caps`는 설치 명령이 아니라 실제
로봇·Mark7·카메라·QP를 시작하는 **별도의 수동 실행 명령**이다. QP에는 시작 시
자동 재무장 경로가 있으므로 검증된 수집 안전 절차 없이 실행하지 않는다.

현재 오프라인 경로와 Mock Task는 지원하지만 **Real ACT 실행과 Real Home은 잠겨
있다**. Dataset과 Checkpoint 확보만으로 이 잠금이 해제되지 않는다. 실제 상태
피드백/명령권 배제/프로젝트 Home 인터페이스와 경로 안전성 검증이 추가로 필요하다.
기존 QP 수집은 ACT 실행과 별도 운영이며 두 제어 주체를 동시에 사용하면 안 된다.

공통 계약은 20FPS, RGB 두 대(640×480×3), state/action 7D이다.
`joint0..joint5` state는 실제 관절 위치 rad, action은 **절대 목표 rad**, index 6은
Open=0/Grasp=1 명령 상태다. Gripper 서비스 성공은 물리적 파지 성공 증거가 아니다.
영상 키는 `camera_317222074298`, `camera_844212071939`이며 원본 Header clock과
monotonic clock을 임의로 혼용하지 않는다.

### 1. Demonstration 수집 — 실기 사전 검증 후에만

Indy/Mark7의 Ready·정지·E-stop, Home 자세와 경로, 독점 제어, 관절 제한, 두 카메라
USB 대역폭·timestamp·연속 수신, `/joint_states`, `/capstone/teleop_action`,
`/capstone/gripper_state`를 먼저 확인한다. 실제 workspace/yaw/depth는 pilot에서
확정한다. 필요한 장치를 수동으로 준비한 뒤 별도 터미널에서:

```bash
./act-record --collection-dir datasets/indy7_act_core108
# 정식 Collection은 같은 명령으로 재개한다 (--resume 내부 적용).
```

Recorder는 READY/RECORDING/REVIEW 상태를 사용한다. `s` 시작, `r` 종료·REVIEW,
`x`는 녹화 중 교정 시연 전환, `o`는 REVIEW에서 유형별 저장, `d`는 미저장 take 폐기,
`q`는 종료다. 사용자 `ready` 없이 `s`에서 배치·시도를 확정한다. 일반 `q`는 정상 미저장 take를 조용히 폐기하지 않는다.
Ctrl+C·EOF나 저장/무결성 오류 시 남은 Pending은 디스크에 보존된다. 미해결
Pending을 발견하면 자동 재사용/삭제하지 않고 복구 판단이 필요하다.

학습 범위는 Home 시작 → 접근·파지·운반·배치 → Open → 안전한 후퇴까지다.
**Home 복귀 전에 `r`**로 기록을 끝낸다. Success/Correction, 과거 Failure Dataset, Pending과
`meta/recorder_timing` Sidecar를 분리해서 보존한다. Invalid take는 학습 Dataset에
저장할 수 없다. 108개는 성공 본수집 목표이며 작은 pilot 학습을 막는 최소 개수가
아니다. 교정 데이터 혼합 학습은 구현하지 않았으며 기존 학습기가 교정 마커를 자동 해석하지 않는다.
수집 UI·마커·저장 대조 사용법은 [Recorder 가이드](ai/record/COLLECTION.md)를 따른다.

### 2. Dataset 검증과 명시적인 학습

정식 Core-108에서 학습에 사용할 Dataset은 Collection 루트가 아닌
`datasets/indy7_act_core108/success`다. `correction/`, 과거 `failure/`, Pilot은
자동으로 합치지 않는다. 독립 Recorder 모드의 기본값 `datasets/indy7_act`는 별도
경로이며, 학습기가 Core-108 경로를 자동 선택하지 않는다.

아래 출력 경로는 예시이며 기존 경로가 있으면 새 이름을 선택한다. Dataset ID는
수집 때 사용한 원래 ID를 지정한다 (기본 `local/indy7_act`). Validator의 report
부모 디렉터리는 미리 존재해야 하며, 기존 report와 학습 출력은 덮어쓰지 않는다.

```bash
./act validate --dataset-dir datasets/indy7_act_core108/success \
  --dataset-repo-id local/indy7_act --report outputs/dataset_check.json
./act train --dataset-dir datasets/indy7_act_core108/success \
  --dataset-repo-id local/indy7_act --output-dir outputs/act_run \
  --device cuda --steps 100000 --batch-size 8
# 최적화 없는 검증·계획 저장 (이 명령도 새 출력 파일을 만든다):
./act train --dataset-dir datasets/indy7_act_core108/success \
  --dataset-repo-id local/indy7_act --output-dir outputs/act_plan \
  --device cpu --no-use-amp --dry-run
```

학습은 Validator 통과 후 Episode 단위 Train/Validation 분할, Train 전용 정규화
통계, 공식 LeRobot ACT 학습, Checkpoint 저장·재로딩·실제 추론 검증, held-out
오차 기록으로 이어진다. RAM cache 기본값은 꺼져 있다. 2카메라 비압축 RGB는
1분당 약 2.06GiB이므로 cache를 켤 때 추정치와 여유 메모리를 확인한다.
원격 Dataset을 자동 보충하지 않으며 기존 Dataset/Checkpoint를 덮어쓰지 않는다.

### 3. Checkpoint 선택과 비구동 확인

`outputs/act_run/run.json`의 `status=complete`, `eligible_for_inference=true`,
`checkpoint_validation.valid=true`를 확인한다. 이는 기술적 유효성으로 실기 안전
승인이 아니다. 해당 run 디렉터리 또는 `checkpoint`의 `pretrained_model` 경로를
아래 명령에 그대로 전달한다.

```bash
./act infer --checkpoint outputs/act_run --source synthetic --dry-run --device cuda
./act task --task indy7_basket_to_table1 --mode mock --checkpoint outputs/act_run \
  --approve-mock "작업자" --approve-home-mock \
  --confirm-home-path-mock "Mock fixture 경로" --device cuda
```

Task는 팀 Home에서 시작하는 Mock 초기 조건을 사용한다. 독립적인
`./act execute --mode mock`은 기본 영점 자세를 유지한다. Home 정책을 그 명령으로
시험할 때는 `--mock-initial-joints <6개 rad>`로 초기 조건을 명시해야 한다. 이 옵션은
가짜 로봇의 초기 조건만 정하며 하드웨어를 움직이거나 Guard를 완화하지 않는다.

Task CLI가 출력한 `client/task_commands.jsonl`에 현재 Session ID를 포함해 사용자
완료/중단 명령을 추가한다. 예:

```json
{"command":"complete","session_id":"현재-session-id","operator":"작업자"}
```

`abort`, `emergency_stop`도 지원한다. Task 시간 상한은 성공이 아니라 Timeout이다.
오프라인 자동 시험에서만 `--mock-complete-after-actions 25`로 완료 입력을 모사할
수 있다. 정상 ACT 완료·Scheduler 비움·정지 확인·명령권 재획득 후에만 별도 Mock
Home이 실행된다. Fault/중단/정지 미확인에서는 자동 Home을 시작하지 않는다.

실제 토픽을 읽는 단계는 별도 실기 승인 후
`./act infer --checkpoint outputs/act_run --source ros --dry-run`으로 확인할 수 있다.
이 명령도 로봇 제어 명령은 전송하지 않는다. 현재 `--mode real`은 거부된다.

### 4. 별도 평가 기록과 보고서

```bash
# 아래 값을 Task CLI가 출력한 실제 디렉터리로 바꾼다.
TASK_RUN="outputs/Task-CLI가-출력한-실제-디렉터리"
./act evaluate template --task-result "$TASK_RUN/client/task_result.json" \
  --output outputs/ratings/trial1.json
# 작업자가 trial1.json에 평가자·조건·유효성·단계 결과를 작성한다.
./act evaluate record --task-result "$TASK_RUN/client/task_result.json" \
  --judgment outputs/ratings/trial1.json --store outputs/act_evaluations
./act evaluate --runs outputs/act_run "$TASK_RUN" --records outputs/act_evaluations \
  --report-dir outputs/evaluation_report
```

원본 Task를 수정하지 않으며 평가 수정은 `record --supersedes <평가ID>`와 변경
근거를 가진 새 파일로 남긴다. Offline Action 오차, 실행 신뢰성, 작업자가 평가한
물리 성공률은 서로 다르다. `COMPLETED`·Open·Mock Home 성공만으로 실제 성공을
채우지 않는다. 미평가는 unknown, Mock/Real은 별도 집계다. Markdown/JSON/CSV에
분자·분모·신뢰구간·조건별/Checkpoint별 한계와 개선 후보 근거가 기록된다.

### 5. 전체 오프라인 연결 재검증

명시적으로 아래 테스트를 실행하면 프로젝트 내부 임시 Dataset 생성과 **실제
소수-step CPU/CUDA 학습**이 시작된다. 로봇·카메라·ROS 장치는 시작하지 않는다.

```bash
CUDA_VISIBLE_DEVICES="" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv-act/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp tmp/act-connected-test \
  --deselect=ai/test/test_offline_end_to_end.py::test_connected_pipeline_cuda \
  ai/test/test_offline_end_to_end.py
```

소형 합성 hold/open 모델은 바구니 조작 능력이나 최종 모델 실시간 성능을 검증하지
않는다. Grasp/Open 서비스 전환·실패는 기존 Mock 테스트에서 별도 검증한다.
세부 API와 한계: [학습](ai/train/TRAINING.md), [추론](ai/serve/INFERENCE.md),
[실행 계층](ai/execute/EXECUTION.md), [Task/Home](ai/execute/TASK_EXECUTOR.md),
[평가](ai/evaluate/EVALUATION.md), [환경](ACT_SETUP.md),
[실기 전 준비도·차단 조건](docs/ACT_OFFLINE_READINESS.md).

실제 장비 관측을 연결하기 전에는 [ROS 통합 1단계 절차](docs/ACT_ROS_STAGE1.md)의
A(장비 없는 검사)와 B(별도 승인 후 구독/Dry Run)를 구분한다. Driver 종료도 SDK
정지 명령을 호출하며, 기존 전체 Launch는 QP 자동 재무장 경로를 포함한다.
Gripper 초기 명령 상태를 새 구독에서 알 수 없으면 기본적으로 unknown으로 차단한다.
ROS Dry Run에 한해 `--operator-initial-gripper 0`(Open) 또는 `1`(Grasp)을 명시할 수
있으며, `operator_assumed`로 기록하고 실제 측정/명령 성공으로 취급하지 않는다.
상태를 얻기 위해 QP나 Gripper 명령을 자동 실행하지 않는다. 이 옵션은 Recorder나
Dataset 수집 및 실제 실행에 사용할 수 없다.

일시적인 카메라 시간 오류를 조사하는 ROS Dry Run에서는 선택적으로
`--ros-observation-recovery`를 켤 수 있다. 초기 연속 관측 검증, 오류 시 Action 폐기와
새 추론 재개, 엄격한 복구 한계를 적용하며 기존 age/skew 값을 완화하지 않는다.
기본은 Fail-fast이며 실행 계층/Recorder에는 적용하지 않는다.
[동작·로그·10초/60초 명령](docs/ACT_ROS_RECOVERY.md)을 따른다.

---

# Xbox teleoperation

## Translation

```text
Left Stick ↑↓      X
Left Stick ←→      Y
RT / LT            Z
```

The left stick uses the original Pipet radial response curve, low-pass filter,
and acceleration limiter, with axis signs adapted for the ROS `joy_node` used
by this project. Diagonal input therefore follows the stick direction
continuously instead of being clipped into axis-aligned steps.

## Rotation

```text
Right Stick ←→     Rz / Yaw
D-pad ←→           Rx
D-pad ↑↓           Ry
```

Right Stick vertical input is intentionally unused.

The commonly used manipulation controls are therefore:

```text
X / Y / Z / Yaw
```

while Rx/Ry require explicit D-pad input.

---

## Gripper

```text
A                  Grasp
B                  Open
```

---

## System controls

```text
Share              ARM / DISARM

View               modifier only
View + A            Recover
View + B            Zero
View + Y            Capstone Home

View + LB           Speed -0.25 mm/tick
View + RB           Speed +0.25 mm/tick
```

Pressing View by itself does not DISARM.

---

## ARM behavior

At startup:

```text
fresh /joint_states
+ live Xbox input
+ neutral Xbox controls
        ↓
automatic ARM
```

The robot is not automatically armed while a stick, trigger,
D-pad direction, or button is being held.

Manual control:

```text
Share
→ ARM / DISARM
```

Capstone Home:

```text
View + Y
→ leave TELE_OP
→ move to Capstone Home
→ verify motion finished
→ verify Home pose
→ wait for neutral Xbox input
→ automatic ARM
```

Recover and Zero:

```text
View + A / View + B
→ DISARM
→ stop TELE_OP
→ verify robot stopped
→ execute command
```

Recover and Zero do not automatically re-arm.

---

# Xbox GUI

`capx` and `caps` automatically start the Xbox information GUI.

The GUI displays:

```text
ARMED
ARMING
DISARMED
TELEOP OFFLINE

current mm/tick speed
Xbox key mapping
```

If QP teleop status stops arriving, the GUI changes to
`TELEOP OFFLINE` instead of leaving an old ARMED indication on screen.

The GUI is a separate ROS 2 process.

Closing or failing the GUI does not own or stop robot control.

---

# Teleoperation configuration

Main Indy7 teleoperation configuration:

```text
ros2_ws/src/capstone_teleop/config/indy7.yaml
```

It contains:

```text
control frequency
translation speed
rotation speed
joint speed
joint soft margin
QP weights
Xbox deadzones
Xbox input shaping
Indy TELE_OP parameters
Capstone Home pose
Capstone Home velocity / acceleration
```

Current maximum translation step:

```text
5.00 mm/tick
```

At 30 Hz this corresponds to approximately:

```text
150 mm/s
```

before considering robot/controller behavior.

Speed can be adjusted in:

```text
0.25 mm/tick
```

increments down to:

```text
0.25 mm/tick
```

Rotation command magnitude scales with the selected translation speed.

---

# Capstone Home

Current target joint pose in degrees:

```text
[
  0.005221,
  40.003174,
  -129.996640,
  90.000870,
  0.000435,
  90.507
]
```

Current verified Home settings:

```text
velocity ratio      18.0
acceleration ratio 100.0
```

Canonical values are stored in:

```text
ros2_ws/src/capstone_teleop/config/indy7.yaml
```

---

# Mark7

Mark7 configuration:

```text
ros2_ws/src/capstone_mark7/config/mark7.yaml
```

Default device:

```text
/dev/ttyACM0
```

Launch-time override example:

```bash
ros2 launch capstone_teleop grip.launch.py \
  mark7_device:=/dev/ttyACM1
```

Axis order:

```text
0  thumb flexion
1  index
2  middle
3  ring
4  little
5  thumb rotation
```

Current presets:

```text
Open:
[-10, -10, -10, -10, -10, 0]

Grasp:
[0, 350, 350, 350, 350, 0]
```

Current verified settings:

```text
speed_raw   50
current_raw 100
```

`speed_raw` and `current_raw` must remain within the protocol byte
range `0..255`.

---

# Safety

Before moving the real robot:

- E-stop must be accessible.
- Clear people and obstacles from the workspace.
- Check the current robot pose.
- Check gripper state.
- Start with low teleoperation speed after control changes.
- Verify each Cartesian direction after changing mappings or signs.
- Check the motion path before Home, Zero, or other preset motion.

Current QP manual teleoperation provides joint-limit protection,
but does not provide external obstacle collision avoidance.

Validation order for new control behavior:

```text
offline check
→ command/log inspection
→ low-speed real robot test
→ restricted workspace test
→ normal operation
```

---

# Current verified manual stack

The following behavior has been physically checked on the Indy7:

```text
Indy7 ROS 2 driver
/joint_states
QP Cartesian teleoperation
X / Y / Z
Rx / Ry / Rz
Xbox ARM / DISARM
startup automatic ARM
Capstone Home
Home automatic re-arm
Mark7 open / grasp
runtime speed adjustment
Xbox status GUI
safe node shutdown
```

---

# Data collection

Large generated research outputs are intentionally excluded from Git:

```text
datasets/
recordings/
checkpoints/
```

Recorder와 ACT Dataset/학습/비구동 Task/평가 파이프라인은 구현되어 있다.
운영 순서와 Real 활성화 전 차단 조건은 위 **ACT 운영 순서**를 따른다.

---

# License

A project license has not been selected yet.

Choose the intended license before public distribution or GitHub release.
