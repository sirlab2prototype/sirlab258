# 수동 Teleop·장비 운영 참고

이 문서는 기존 README의 장비·Xbox 상세 설명을 옮긴 것이다. 실행 승인이 아니다.
현재 실물 제한과 검증 시점은 [PROJECT_CONTEXT](PROJECT_CONTEXT.md)를 먼저 확인한다.
`indy`/`indy driver`/`indy teleop`는 실제 장비 연결·자동 ARM 또는 종료 시 제어 명령 경로가 있다.
`indy`는 카메라를 실행하지 않는다. `show_camera`는 setup에서 호환 인자로만 남아 카메라 실행에 영향이 없다.
카메라 명령의 `show_camera:=false`는 영상 미리보기만 끄고 카메라 노드는 유지한다.
아래 키맵은 현재 코드의 기능 설명이며 Home/Zero/Recover 사용 허가가 아니다.
Gripper의 0/1과 서비스 성공은 물리 위치·파지 완료가 아닌 명령 상태다.

QP는 수집용 `/capstone/action_context` heartbeat와 미검증 Home 시각을 추가 발행한다.
movej·입력 매핑·기존 도착 검사·재ARM 제어는 유지하며 새 qdes 조회나 reader thread는 없다.
새 Recorder는 이 heartbeat를 요구하고 Home 중 녹화를 INVALID로 차단한다.
[Home 포함 ACT 수집의 조건부 구현](ACT_HOME_RECORDING.md)을 따른다.

## 실행 진입점

| 명령 | 연결 |
| --- | --- |
| `indy` | 카메라 없이 Indy·Mark7·Xbox/QP/GUI |
| `camera` | D435317222074298 +844212071939 |
| `cam1` | D435844212071939 한 대 |
| `cam2` | D435317222074298 한 대; `indy cam`은 제거 |
| `indy driver` | Indy driver |
| 내부 `scripts/bin/capg` | 기존 Mark7 개별 경로; 일반 운용은 `indy` |
| `indy teleop` | Xbox/QP/GUI |

카메라는 별도 터미널에서 독립 실행한다. 새 명령끼리만 같은 사용자 serial 잠금을 공유한다.
하나가 실행 중일 때 `camera`는 전체 요청을 거절하며 기존 카메라를 종료/재시작하지 않는다.
외부 프로세스·다른 사용자 점유는 확인할 수 없다. 자세한 제한은 README를 따른다.
실제 운용 승인을 받은 경우에만 사용한다. 환경은 프로젝트 `.venv`와
`ros2_ws/install/setup.bash`를 사용하며, 설정 원본은
`ros2_ws/src/capstone_teleop/config/`와 `capstone_mark7/config/mark7.yaml`이다.

# Xbox teleoperation

## Translation

```text
Left Stick ↑↓      X
Left Stick ←→      Y
RT / LT            Z
```

The left stick uses a radial response curve and acceleration limiter, with axis signs adapted for the ROS `joy_node` used
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

`indy teleop` and `indy` automatically start the Xbox information GUI.

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

Configured Home settings (current hardware not revalidated):

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

Configured settings:

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

# Historical manual stack verification (past records)

The following behavior was physically checked in past manual-stack records. This is not current hardware certification:

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


## ROBOTIS OMY — 별도 장비 경로

OMY는 현재 Indy 수집 경로와 별개다. 아래 실행·종료·원격 상태 조회도 별도 장비 승인이 필요하다.
주소는 설정값이며 현재 실물 연결 확인 결과가 아니다.


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
