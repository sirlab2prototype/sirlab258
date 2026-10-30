# Capstone

Neuromeka Indy7 + Mark7 기반 캡스톤 시스템.

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

## LeRobot ACT

ACT training uses a separate environment so its PyTorch and NumPy versions do
not interfere with the ROS environment:

```bash
source scripts/activate_act.sh
```

See [`ACT_SETUP.md`](ACT_SETUP.md) for tutorial and training commands.

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

The episode recorder and ACT dataset pipeline will be added separately.

They should not be coupled directly to the robot-control implementation.

---

# License

A project license has not been selected yet.

Choose the intended license before public distribution or GitHub release.
