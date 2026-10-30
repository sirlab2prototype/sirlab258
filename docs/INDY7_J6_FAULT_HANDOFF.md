# Indy7 J6 Fault 및 ACT/LeRobot 작업 인수인계

작성 기준일: 2026-10-09 (Asia/Seoul)  
대상 작업공간: `/home/sirlab2/sirlab258`

> [!WARNING]
> 현재 Indy7에는 motion, teleop, Home, Zero, Recover, servo on/off, brake 조작,
> `reset_servo`, `set_robot_zero_as_current`, calibration, encoder reset 또는 SDO/EtherCAT
> write를 실행하지 않는다. 제조사 공식 복구 절차를 받기 전까지 모든 진단은 read-only로 제한한다.

## 표기 규칙

- **FACT**: 실제 명령 출력, 저장된 로그, 코드 또는 diff로 확인한 사실.
- **INFERENCE**: FACT들을 함께 설명하는 가장 타당한 추론.
- **NOT CONFIRMED**: 아직 자료나 안전한 시험으로 확인하지 못한 사항.

ACT/LeRobot 준비 시점과 최초 fault 시점은 겹치지만, 이것만으로 ACT/LeRobot이 J6를
손상시켰다고 결론 내릴 수 없다.

## 1. Executive Summary

- **FACT** — Indy7 J6가 현재 비정상 position을 보고한다.
- **FACT** — 전원 완전 재인가 후 CONty의 실제 J6 joint position은 약 `-52677 deg`이다.
- **FACT** — IndyDCP3의 `q[5]`와 `qdes[5]`는 약 `-52678.1 deg`이다.
- **FACT** — J6 safety limit은 `-215 ~ +215 deg`이다.
- **FACT** — EtherCAT J6 `actualPosition = 969130066`, `targetPosition = 969130066`이다.
- **FACT** — 같은 snapshot의 J1~J5 raw actualPosition은 대략 `-15905 ~ 68096`이다.
- **FACT** — J6 raw 값은 다른 축보다 압도적으로 크며 controller power cycle 뒤에도 유지됐다.
- **FACT** — neuromeka SDK `3.3.2.3`과 `3.4.2.2` 모두 같은 J6 q/qdes를 반환했다.
- **FACT** — 두 SDK의 `ControlData2.q/qdes` protobuf field number/type/layout은 같다.
- **INFERENCE** — Python/protobuf decoding regression은 사실상 배제됐다.
- **INFERENCE** — CORE는 비정상 raw position을 정상적인 scale/sign/offset 관계로 변환해 약 `-52678 deg`를 만드는 것으로 보인다.
- **INFERENCE** — 가장 유력한 계층은 J6 absolute encoder, multi-turn 값 또는 servo drive의 persistent position feedback이다.
- **FACT** — 공개 SDK/protobuf에서 명시적인 absolute encoder multi-turn reset RPC는 찾지 못했다.
- **NOT CONFIRMED** — LiveUSB, CORE service tool 또는 servo-specific SDO 중 어떤 공식 복구 방법이 필요한지는 확인되지 않았다. Neuromeka 절차가 필요하다.
- **FACT** — 현재 실제 robot motion은 금지 상태이며 복원된 robot stack도 fault 확인 이후 실제 로봇에서 실행하지 않았다.

쉬운 설명: **J6가 실제로 수만 도 돌아간 것이 아니라, J6 encoder/servo가 말도 안 되는 raw
위치값을 보내고 controller가 그 값을 정상적으로 믿고 계산해서 joint-limit fault가 나는
상태로 보인다.**

## 2. 관련 프로젝트와 디렉터리

| 경로 | 역할 | 분석에서의 의미 |
|---|---|---|
| `/home/sirlab2/sirlab258` | 현재 capstone teleop 및 ACT/LeRobot 구축 작업공간 | 현재 코드, working tree, recorder와 진단 스크립트의 기준 |
| `/home/sirlab2/minseo/capstone` | 이전 capstone teleop 개발 workspace | ACT publisher를 제외한 capstone control logic 복원 기준 |
| `/home/sirlab2/pipet-physical-ai` | 과거 physical Indy7 teleop 프로젝트 | **실제 Indy7에서 정상 동작했던 known-good physical reference** |

`minseo/capstone`은 현재 capstone 구현과 가까운 코드 기준이고,
`pipet-physical-ai`는 실제 물리 로봇 정상 동작 이력이 있는 architecture 기준이다.

## 3. 시스템과 로봇 환경

- **FACT** — OS: Ubuntu 24.04; ROS: ROS 2 Jazzy.
- **FACT** — Robot: Neuromeka Indy7, IP `192.168.1.114`.
- **FACT** — `get_control_info()`: controller `3.3.2-GA3 [2025.01.23]`, model `Indy7`.
- **FACT** — system/user-site SDK: `neuromeka 3.4.2.2`.
- **FACT** — import 경로: `/home/sirlab2/.local/lib/python3.12/site-packages/neuromeka`.
- **FACT** — 격리 SDK 3.3.2.3 venv: `/tmp/indy_sdk_3_3_2_3`.

### `get_device_info()` 원문 핵심

```text
num_joints: 6
robot_serial: PD47I7E0D002
payload: 7.0
io_board_fw_ver: 0.02C
core_board_fw_vers: [9.12C, 9.12C, 1.12C, 1.12C, 1.12C, 1.12C]
endtool_board_fw_ver: 1.00C
controller_ver: 3.3.2
controller_detail: GA3
controller_date: 2025.01.23
teleop_loaded: true
```

**NOT CONFIRMED** — `core_board_fw_vers` 배열의 어느 원소가 J6에 대응하는지는 확인하지
않았다. SDK 3.4의 추가 default-valued fields(`robot_dof: 0`, 빈 문자열, `calibrated:
false`)도 controller 전송값인지 proto3 default인지 구별하지 않는다.

## 4. ACT/LeRobot 데이터 수집 환경 구축 내역

- **FACT** — LeRobot `0.5.1`은 `.venv-act`에 설치됐고 vendored source는 `ai/lerobot_source/lerobot`이다.
- **FACT** — robot/ROS 환경은 `.venv`, ML 환경은 `.venv-act`로 분리했다.
- **FACT** — activation: `scripts/activate_act.sh`; entry points: `./act-record`, `./act-train`.
- **FACT** — recorder: `ai/record/indy7_act_record.py`.
- **FACT** — 기본 dataset root/repo id: `datasets/indy7_act`, `local/indy7_act`.
- **FACT** — pilot root: `datasets/indy7_pilot`.
- **FACT** — trainer 기본 repo id는 `sirlab/grip_all`이며 CLI로 변경 가능하다.

Recorder는 20 FPS로 다음을 구독한다.

```text
/joint_states
/camera_317222074298/camera/color/image_raw
/camera_844212071939/camera/color/image_raw
/capstone/teleop_action
/capstone/gripper_state
```

State/action은 Indy joint 6개와 binary gripper 1개, 총 7차원이다. Open은 `0.0`,
grasp는 `1.0`이다. Action이 아직 없으면 측정 state를 hold target으로 사용한다.
카메라는 D435 `317222074298`, D435 `844212071939`이다. 후자는 이전 점검에서 USB2
enumeration이 관찰됐다. OMY D405 serial은 `335122271033`이지만, **NOT CONFIRMED** —
현재 Indy recorder/config에는 포함되지 않고 OMY 쪽 참고 장치로만 기록한다.

Recorder 키는 `s/a/r/d/q`이며 `start/save/retry/discard/quit`도 호환된다.

## 5. Teleop에 ACT recording 기능을 붙이며 변경된 내용

### 현재 유지한 publish-only 기능

- **FACT** — `/capstone/teleop_action`은 `candidate_joint`에서
  `np.radians(candidate_joint).tolist()`를 만들어 새 `JointState.position`에 넣는다.
- **FACT** — Python list로 materialize된 detached 값이며 controller target을 수정하지 않는다.
- **FACT** — `movetelej_rel()`에는 기존 `candidate_relative.tolist()`가 먼저 전달된다.
- **FACT** — name은 `joint0 ... joint5`; J6는 index 5이다. Radian 변환은 publisher에만 있다.
- **FACT** — `/capstone/gripper_state`는 기존 grasp/open Trigger service 성공 후에만 publish한다.
- **FACT** — grasp는 `1.0`, open은 `0.0`; QP, candidate arrays, ARM/DISARM 또는 gripper command를 바꾸지 않는다.

### 한때 함께 존재했던 control 변경

ACT publisher 추가 시점에는 publisher 외에도 MotionProfile 제거,
`CartesianInputFilter/AngularInputFilter` 사용, joystick shaping, translation,
yaw/rotation, acceleration, neutral 판정, reset/shutdown 변경이 함께 존재했다. 이후 이를
`/home/sirlab2/minseo/capstone`의 known-good capstone control path로 복원하고 두
publish-only 기능만 유지했다.

## 6. Teleop control path 복원 내역

대상:

```text
ros2_ws/src/capstone_teleop/capstone_teleop/qp_teleop.py
ros2_ws/src/capstone_teleop/capstone_teleop/qp_core.py
ros2_ws/src/capstone_teleop/capstone_teleop/input_profile.py
ros2_ws/src/capstone_teleop/config/indy7.yaml
ros2_ws/src/capstone_teleop/test/test_qp_core.py
ros2_ws/src/capstone_teleop/test/test_input_profile.py
```

목표는 `known-good control + publish-only teleop_action + publish-only gripper_state`였다.
당시 기록된 결과는 control-path normalization PASS, candidate_relative path PASS,
publisher/J1~J6 mapping PASS, Python compile PASS, pytest `12 passed`, colcon test
`12 tests / 0 errors / 0 failures`, build 성공이다.

**FACT (현재 정적 재검증)** — 2026-10-09 현재 `qp_core.py`, `input_profile.py`,
`indy7.yaml`은 minseo 기준과 diff가 없다. `qp_teleop.py`의 기준 대비 차이는
`Float32` import, 두 publisher 및 그 publish state뿐이다.

**FACT** — 복원 후 실제 robot stack을 실행하지 않은 상태에서도 read-only J6는
`-52678.1`이었다. 따라서 persistent position 이상이 현재 qp_teleop 계산 때문에
유지되는 것은 아니다.

## 7. 최초 증상과 중요한 정정

보고된 대표 console 흐름:

```text
AUTO ARM startup
QP TELEOP ARMING
MSG_TELE_JOINT_RLT
TIMEOUT WHEN TRYING TO START TELEOP
```

이후 CONty joint-limit/fault가 발생했다. 완전한 원본 runtime log는 현재 보존 파일에서
찾지 못했으므로 이 순서는 사용자 관찰 기록이다.

> **중요 정정 — FACT:** 처음 약 `40.2`를 CONty J6 joint position으로 이해했으나,
> 이는 J6 servo temperature(`40.202637°C` 또는 `40.283203°C`)였다. 실제 CONty
> joint position은 약 `-52677 deg`이다.

## 8. Read-only IndyDCP3 진단

스크립트: `scripts/diagnose_indy7_j6_readonly.py`  
로그: `/tmp/indy7_j6_readonly_diagnostic.txt`

```text
q:    [0.019929744, 1.3808271, 3.5115392, 0.89489627, 17.821184, -52678.1]
qdes: [0.019929744, 1.3808271, 3.5115392, 0.89489627, 17.821184, -52678.1]
J6 safety: -215.0 / +215.0 deg
servo_actives: [false, false, false, false, false, false]
brake_actives: [true, true, true, true, true, true]
voltages: [48.0, 48.0, 48.0, 48.0, 48.0, 48.0]
violation code/id: 0 / 0; message empty; queue empty
```

SDK `enums.py`의 실제 mask는 `(status & 0x004f)==0x0008 -> FAULT`,
`(status & 0x006f)==0x0021 -> READY_SWITCH`이다. 따라서 `0x0208 -> FAULT`,
`0x0221 -> READY_SWITCH`. Snapshot 사이 여러 축에서도 변화가 있어 status 전환만을
J6-only 현상으로 단정하지 않는다.

- **FACT** — `get_joint_limit_config()`는 controller에서 gRPC `UNIMPLEMENTED`.
- **FACT** — SDK 3.4의 `is_robot_connected=False`.
- **INFERENCE** — 이 field는 SDK 3.4에서 추가됐고 구 controller가 보내지 않아 default
  False일 가능성이 높다. 실제 disconnect의 신뢰 가능한 증거로 쓰지 않는다.

## 9. SDK version compatibility 검증

| 항목 | 3.3.2.3 | 3.4.2.2 |
|---|---|---|
| 환경 | `/tmp/indy_sdk_3_3_2_3` | user-site |
| J6 q/qdes | `-52678.1 / -52678.1` | `-52678.1 / -52678.1` |
| J1~J5 q | 사실상 동일 | 사실상 동일 |
| is_robot_connected | field 없음 | False(default 가능) |
| joint-limit getter | method 없음 | controller UNIMPLEMENTED |

보존 파일은 `/tmp/indy_sdk_3_4_2_2.json`, `/tmp/indy_sdk_3_3_2_3.json`,
`/tmp/proto_3_4_2_2.json`, `/tmp/proto_3_3_2_3.json`이다.

**FACT** — 두 `ControlData2`에서 q는 field 1/repeated float, qdes는 field 4/repeated
float로 동일하다. 3.4는 기존 field를 바꾸지 않고 새 fields를 추가했다.
**INFERENCE** — q decoding regression은 배제된다.

## 10. EtherCAT raw 진단

| Joint | statusWord | actualPosition | get_servo_rx 원문 | targetPosition | zero count |
|---|---:|---:|---|---:|---:|
| J1 | 545 / 0x0221 | 32169 | `[0,10,32167,0,-11]` | 32167 | 0/default |
| J2 | 545 / 0x0221 | -15904 | `[0,10,-15904,0,32]` | -15904 | 0/default |
| J3 | 545 / 0x0221 | 68096 | `[0,10,68094,0,-71]` | 68094 | 0/default |
| J4 | 520 / 0x0208 | 15401 | `[0,10,15400,0,-13]` | 15400 | 0/default |
| J5 | 520 / 0x0208 | 53359 | `[0,10,53360,0,64]` | 53360 | 0/default |
| J6 | 545 / 0x0221 | **969130066** | `[0,10,969130066,0,2]` | **969130066** | 0/default |

J6 errorcode는 `0`, 이후 snapshot temperature는 `31.0986328125°C`, voltage는
`48.0 V`였다. 초기 DCP snapshot의 약 40.2°C와는 다른 시점이다.

`RobotZeroCount.count`는 proto3 int32 scalar이고 모든 축이 빈 serialized payload/default
0이었다. Endpoint 성공만 확인되며 실제 zero=0, 미지원 placeholder, 값 미기입을 구별할 수 없다.

- **FACT** — 이상값은 DCP/ROS보다 아래 EtherCAT PDO에 이미 존재한다.
- **NOT CONFIRMED** — target==actual만으로 teleop이 그 수를 명령했다고 단정하면 안 된다.
  Servo inactive/brake active에서 current position을 hold target으로 복사했을 수도 있다.

## 11. raw count에서 q로의 변환 분석

```text
Servo encoder/PDO actualPosition
 -> Controller CORE internal conversion
 -> IndyDCP3 q(degree)
 -> indy-ros2 degs2rads()
 -> ROS JointState(radian)
```

CORE 내부 구현은 로컬에 없다. 관측값은 일반형

```text
joint_deg = direction * (raw-zero) * 360
            / (encoder_counts_per_motor_rev * gear_ratio) + joint_offset
```

과 일치한다.

**NOT CONFIRMED** — `65536 counts/rev`와 ratio 후보
`[121,121,101,81,81,101]`은 공식 local config가 아니라 관측값 역산 후보다.

J6에 반전 방향, ratio 101을 가정하면 counts/degree는 `18386.4888889`, 역산 zero는
`564765.662...`이다. 정수 zero=564766이면:

```text
(564766 - 969130066) / 18386.4888889 = -52678.0999816 deg
```

DCP와 약 `0.0000184 deg` 차이다. J1~J5도 같은 scale/sign/zero 형태로 설명됐다.
**INFERENCE** — q 변환보다 raw 자체 이상을 가리키는 매우 강한 수학적 정황이지만 공식
resolution/ratio 확인은 아니다.

## 12. Power cycle 결과

- **FACT** — Indy7/controller를 완전히 OFF/ON했다.
- **FACT** — 이후 CONty 약 `-52677 deg`, DCP 약 `-52678.1 deg`가 유지됐다.
- **INFERENCE** — 단순 RAM state/transient TELE_OP latch만으로 설명하기 어렵고 persistent
  encoder/drive/controller state 가능성이 높다.

## 13. ACT 작업이 원인인지 조사한 결과

두 프로젝트 source, git log/reflog, 가능한 shell history와
`/tmp/indy_keyword_audit.txt`를 검색했다.

**FACT** — ACT 과정에서 `set_robot_zero_as_current`, encoder zero/offset write,
multi-turn reset/write, calibration, SDO write, `reset_servo`, manual brake write,
servo on/off 또는 EtherCAT direct target write를 실행한 흔적은 발견되지 않았다.

SDK에 `set_robot_zero_as_current/reset_servo/set_servo/set_servo_rx/set_brake`가
존재하지만 **API 존재는 실행 증거가 아니다**. External indy-ros2의 `set_servo_rx`
subscriber는 upstream 기존 기능이고 submodule commit 변경 및 ACT publisher 연결,
shell-history 실행 흔적은 없다.

- **FACT** — ACT 준비와 최초 fault 시점은 겹친다.
- **INFERENCE** — persistent raw 이상을 recorder/ROS/SDK가 직접 만들었다는 증거는 없다.
- **NOT CONFIRMED** — 당시 teleop/mode transition이 최초 fault를 촉발했을 가능성은 완전히 배제되지 않았다.

## 14. reset_servo / robot-zero API 의미

- `EtherCAT.reset_servo(servo_idx)` docstring: **“Reset servo error”**.
  RPC는 `ResetServo(ServoIndex)->Empty`. Servo fault/error reset이며 encoder/multi-turn
  reset 근거가 없다. 실행하지 않았다.
- `EtherCAT.set_robot_zero_as_current(servo_idx)` docstring:
  **“Set robot zero as current”**. `GetRobotZeroCount/SetRobotZeroAsCurrent`와 짝을 이룬다.
  Controller zero/calibration write로 취급해야 하며 raw multi-turn memory reset 근거가 없다.
  실행하지 않았다.

## 15. Public SDK multi-turn reset 조사

발견 RPC: `Get/SetServoRx`, `GetServoTx`, `SetServoOn/Off`,
`GetServoTemperature`, `GetServoErrorCode`, `ResetServo`,
`SetCOREManualBrake`, `GetRobotZeroCount`, `SetRobotZeroAsCurrent`.

찾지 못한 것: `ResetEncoder`, `EncoderReset`, `ClearMultiturn`,
`ResetAbsolutePosition`, `GetMultiTurnCount`, `SetHomeOffset`.

공개 SDO API는 max torque와 profile velocity/acceleration/deceleration 전용뿐이며 범용
index/subindex write나 공식 reset index/value는 없다. 최종 분류는 **C: 공개 자료
부족으로 제조사 절차 필요**. LiveUSB/CORE service tool/servo SDO 가능성은 추측하지 않는다.

## 16. pipet-physical-ai 비교

Known-good physical reference는 teleop node가 `/indy/teleop_joint`를 publish하고 driver가
TELE_OP start/stop, `movetelej_rel`, validation/clamping, rejection/fault latch를 담당한다.
Motion input 시 TELE_OP를 확보하고 startup AUTO ARM은 없다.

Capstone은 역사적으로 driver와 qp_teleop에 각각 DCP client가 있고, mode lifecycle/target
stream을 나누며 startup AUTO ARM과 `set_teleop_params`를 썼다. **FACT (사용자 확인)** —
이 dual-client 구조는 의도적이며 fault 전 실제 정상 동작했다. 직접 원인으로 결론 내리지
않는다. Single-owner redesign은 설계만 검토했고 **적용하지 않았다**.

## 17. 현재 수정된 코드 상태

문서 작성 직전 tracked 상태:

```text
 M .gitignore
M  .gitmodules
 M README.md
A  external/open_manipulator
 M ros2_ws/src/capstone_teleop/capstone_teleop/qp_teleop.py
 M ros2_ws/src/capstone_teleop/config/indy7.yaml
 M ros2_ws/src/capstone_teleop/launch/setup.launch.py
 M ros2_ws/src/capstone_teleop/package.xml
 M scripts/install_commands.sh
```

주요 untracked:

```text
ACT_SETUP.md, act-record, act-train, ai/, caps, docs/, pytest.ini
ros2_ws/src/capstone_teleop/capstone_teleop/input_profile.py
ros2_ws/src/capstone_teleop/config/{camera_rgb_only,rgb_cameras}.yaml
ros2_ws/src/capstone_teleop/launch/cameras.launch.py
ros2_ws/src/capstone_teleop/test/test_input_profile.py
scripts/activate_act.sh, scripts/bin/act-record, scripts/bin/omy
scripts/diagnose_indy7_j6_readonly.py, scripts/rotate_ros_image.py
```

External Indy submodule은 clean, commit
`ca0353f077620c877c1edff2e26c8cd976773f27` (`ca0353f add nuri50`)이다.
이 문서 작성에서는 기존 파일을 수정하지 않았다.

## 18. 현재 원인 판단

1. **J6 absolute encoder/multi-turn position 이상**: raw `969130066`, 다른 축보다 수
   orders 큼, CONty/DCP/EtherCAT 일치, power cycle 뒤 지속, SDK 독립.
2. **J6 servo/CORE 내부 persistent absolute-position/feedback 문제**.
3. **J6 persistent zero/offset/config corruption**.

Zero getter의 0은 proto3 empty/default라 실제 zero=0으로 단정하지 않는다. 가능성이 내려간
것은 recorder, 두 ACT publishers, protobuf decoding, 현재 qp_teleop이 계속 이상값을
만든다는 가설이다. **NOT CONFIRMED** — ACT 당시 transition이 최초 fault를 촉발했는지 여부.

## 19. WARNING — 현재 금지 작업

> [!CAUTION]
> actual teleop, Home, Zero, Recover 반복, `set_robot_zero_as_current(5)`,
> encoder reset 목적의 `reset_servo(5)`, calibration, 추측 기반 multi-turn reset,
> 임의 SDO/EtherCAT write, servo on/off, brake release/manual motion, J6 강제 이동,
> pipet actual motion test를 하지 않는다.

Controller가 J6 위치 자체를 비정상 인식하므로 잘못된 reference에서 motion을 허용하면
예상하지 못한 동작이나 추가 손상이 가능하다.

## 20. 제조사 문의용 support summary

```text
Robot: Indy7
Controller: 3.3.2-GA3 [2025.01.23]

J6:
- CONty position ~= -52677 deg
- IndyDCP3 q/qdes ~= -52678.1 deg
- safety limit = -215 ~ +215 deg
- EtherCAT actualPosition = 969130066
- EtherCAT targetPosition = 969130066
- servo errorcode = 0
- temperature ~= 31.1 C in later EtherCAT snapshot
  (earlier get_servo_data snapshot ~= 40.2 C)
- unchanged after complete controller/robot power cycle

J1-J5:
- raw actualPosition roughly -15905 ~ 68096

SDK:
- 3.3.2.3 and 3.4.2.2 produce the same J6 q/qdes
- ControlData2 q/qdes protobuf layout is identical

Need:
- official J6 absolute encoder / multi-turn recovery procedure
- whether CORE/encoder reset is required
- whether LiveUSB/service tool is required
- whether one-axis reset is possible
- zero/reference calibration procedure afterward
- encoder battery/CORE hardware check
- whether SetRobotZeroAsCurrent must NOT be used in this state
```

## 21. 다음 단계

1. 제조사 공식 J6 absolute encoder/multi-turn recovery procedure 확보.
2. 절차 전 write/reset/calibration 금지.
3. 복구 후 CONty joint position 확인.
4. DCP read-only q/qdes 확인.
5. EtherCAT raw actualPosition 확인.
6. 모두 정상일 때만 actual teleop 재검증.
7. 첫 physical validation은 승인된 조건에서 known-good reference와 비교해 저속/작은 motion.
8. 이후 ACT pilot collection 재개.

## 22. 명령과 진단 로그 Appendix

기존 read-only 진단 형식:

```bash
python scripts/diagnose_indy7_j6_readonly.py --ip 192.168.1.114
```

사용 getter:

```text
get_control_info, get_device_info, get_control_state, get_robot_data
get_servo_data, get_safety_limits, get_violation_data
get_violation_message_queue
get_servo_tx_raw, get_servo_rx, get_servo_errorcode
get_servo_temperature, get_robot_zero_count
```

로그:

```text
/tmp/indy7_j6_readonly_diagnostic.txt
/tmp/indy_sdk_3_4_2_2.json
/tmp/indy_sdk_3_3_2_3.json
/tmp/proto_3_4_2_2.json
/tmp/proto_3_3_2_3.json
/tmp/indy_keyword_audit.txt
```

### DO NOT RUN

```text
move*, teleop*, Home, Zero, Recover
reset_servo, set_robot_zero_as_current, set_servo, set_servo_rx, set_brake
SDO/EtherCAT write, calibration, encoder reset
```

## 23. 최종 검증 상태

- **FACT** — 실행한 getter와 source에서 존재만 확인한 write API를 구분했다.
- **FACT** — Single-owner redesign이 적용됐다고 쓰지 않았다.
- **FACT** — ACT/LeRobot을 원인으로 단정하지 않았다.
- **INFERENCE** — 현재 증거는 J6 persistent raw absolute/multi-turn 계층을 가장 강하게 가리킨다.
- **NOT CONFIRMED** — 최초 corruption 사건과 공식 복구 방법.
- **현재 상태** — 제조사 공식 절차가 필요하며 그 전에는 state-changing 작업을 하지 않는다.

## 24. 2026-10-11 추가 조사 — 공개 SDK, CORE 문서, read-only 재검증

> [!CAUTION]
> 이 섹션의 복구 기능은 **존재와 의미만 정적 조사**했다. `reset_overflow_count`,
> `reset_servo`, `set_robot_zero_as_current`, LiveUSB/CORE Setting의 Clear PVL/Sync/
> Reconfiguration/Calibration은 모두 실행하지 않았다.

### 24.1 조사 범위와 근거

- 로컬 SDK 3.4.2.2: `/home/sirlab2/.local/lib/python3.12/site-packages/neuromeka/`
- 격리 SDK 3.3.2.3: `/tmp/indy_sdk_3_3_2_3`
- 공식 Python SDK: [neuromeka-package](https://github.com/neuromeka-robotics/neuromeka-package),
  조사에 사용한 [고정 commit의 ecat.py](https://github.com/neuromeka-robotics/neuromeka-package/blob/d33d027fd1b2b8378b3ffdf312f334a9657524f8/python/neuromeka/ecat.py)
- 공식 ROS 2 driver: [indy-ros2](https://github.com/neuromeka-robotics/indy-ros2), 로컬 submodule
  `ca0353f077620c877c1edff2e26c8cd976773f27`
- Neuromeka CORE 공식 문서: [EtherCAT/CiA 402](http://docs.neuromeka.com/3.5.0/en/CORE/section3/),
  [LiveUSB CORE Setting](http://docs.neuromeka.com/3.5.0/en/CORE/section5/),
  [Complete SDO List](http://docs.neuromeka.com/3.5.0/en/resources/CORE/Neuromeka_CompleteSDO_List.pdf)
- 다운로드 보존본: `/tmp/indy7_core_ethercat_official.html`,
  `/tmp/neuromeka-core-setting-3.2.html`, `/tmp/Neuromeka_CompleteSDO_List.pdf`

### 24.2 SDK/RPC에서 실제로 노출된 진단 경로

| 요구 정보 | 실제 API / RPC / 자료형 | 코드 근거 | read-only | controller 3.3.2 검증 | J6 유용성 |
|---|---|---|---|---|---|
| EtherCAT PDO actual position | `get_servo_tx_raw(i)` → `GetServoTx(ServoIndex)` → `ServoTx.actualPosition: int32` | `ecat.py:133-147`; `ethercat_msgs.proto` field 3; stub path `/Nrmk.IndyFramework.EtherCAT/GetServoTx` | **명확** | **성공**, J6 `969130066` | **매우 큼**; 이상이 controller 하위 PDO에 존재 |
| PDO target position | `get_servo_rx(i)` → `GetServoRx(ServoIndex)` → `ServoRx.targetPosition: int64` | `ecat.py:171-177`; proto field 3 | **명확** | **성공**, J6 `969130066` | 현재 hold target 비교; 과거 원인 증거는 아님 |
| Servo current error | `get_servo_errorcode(i)` → `GetServoErrorCode` → `ServoError.errorCode` | `ecat.py:216-221` | **명확** | **성공**, J1~J6 `0` | 현재 0x603F fault 부재; history는 아님 |
| Servo temperature | `get_servo_temperature(i)` → `GetServoTemperature` | `ecat.py:209-214` | **명확** | **성공** | 보조 정보; encoder battery와 다름 |
| Robot zero count | `get_robot_zero_count(i)` → `GetRobotZeroCount(ServoIndex)` → `RobotZeroCount.count: int32` | `ecat.py:463-467`; proto field 1 | **명확** | endpoint는 **성공**, 모든 축 empty/default 0 | default ambiguity로 실제 offset 판정 불가 |
| Joint q/qdes | `IndyDCP3.get_control_state()` → `RTDataExchange.GetControlState(Empty)` → `ControlData2` repeated float | `indydcp3.py:126-154`, `_rtde.py:35-59` | **명확** | **성공**, J6 약 `-52678.1°` | raw→CORE joint conversion 결과 비교 |
| Servo 현재 상태 | `get_servo_data()` → `GetServoData(Empty)` → `ServoData` | `indydcp3.py:179-195`, `_rtde.py:83-97` | **명확** | **성공** | status string, temperature, voltage, current, torque, servo/brake state만 제공 |
| EtherCAT bus 상태/구성 개수 | `GetMasterStatus`, `GetSlaveStatus`, `GetTxDomainStatus`, `GetRxDomainStatus`, `GetSlaveTypeNum` | `ecat.py:27-114`; `EtherCAT` service descriptor | **명확** | **2026-10-11 성공** | bus는 OP/complete; 단, `SlaveTypeNum`은 CORE model이 아닌 장치 개수만 준다 |
| 제한된 SDO read | `get_maxTorque`, `get_profileVel`, `get_profileAcc`, `get_profileDec` | `ecat.py:435-460`; 각 `Get*SDO(EcatIndex)->SDOIntVal` | 해당 getter는 **명확** | 미실행 | J6 encoder 진단에 직접 도움 없음 |
| 범용 SDO read | **공개 API/RPC 없음** | EtherCAT service 전체 descriptor에 arbitrary index/subindex request 없음 | — | **미지원** | 0x6063, encoder status/CRC 등을 SDK로 임의 조회 불가 |
| Absolute encoder position / multi-turn count / overflow count / battery status / encoder alarm history / servo alarm history / servo position offset / drive internal position | **각 항목 전용 public getter 없음** | `ecat.py`, `indydcp3.py`, EtherCAT/RTDE protobuf service/message 전체 검색 | — | **미지원** | LiveUSB service tool 또는 제조사 절차 필요 |

`GetSlaveTypeNum` 응답 message의 field는 `num_servo`, `num_ioboard`, `num_endtool`,
`num_dio`뿐이다. 함수명의 "type"을 CORE100/200/500 모델 조회로 해석하면 안 된다.

ROS 2 driver는 새 하위 정보를 만들지 않는다. `indy_driver.py:282-302`에서
SDK `get_servo_tx(i)`의 5개 값을 `ServoTx.msg` (`status_word`, `mode_op_disp`,
`actual_pos`, `actual_vel`, `actual_tor`)로 복사할 뿐이며, 기본 timer callback의 servo
Rx/Tx publish는 `indy_driver.py:305-308`에서 주석 처리되어 있다.

### 24.3 2026-10-11 read-only 실행 결과

raw gRPC getter에 각 2초 timeout을 설정해 1회 조회했다. 첫 sandbox 시도는
network permission으로 실패했고, 승인된 network 실행은 다음과 같다.

```text
GetMasterStatus   {status: 8}                         # OP
GetSlaveStatus    {status: [8,8,8,8,8,8,8,8]}       # 8 slaves all OP
GetTxDomainStatus {status: 2}                         # COMPLETE
GetRxDomainStatus {status: 2}                         # COMPLETE
GetSlaveTypeNum   {num_servo: 6, num_ioboard: 1, num_endtool: 1}
```

**FACT** — EtherCAT bus/domain이 조회 시점에 정상 OP/COMPLETE였다. 
**INFERENCE** — 전체 bus 연결 불량보다 J6가 보고하는 persistent position에 문제가
있다는 판단을 추가로 강화한다. **NOT CONFIRMED** — OP는 encoder 값의 정당성을
보증하지 않는다.

### 24.4 `actualPosition=969130066`의 정확한 계층과 수치 해석

**FACT** — Neuromeka CORE 공식 기본 TxPDO에서 `Drive TxPDO (0x1A00)`의
position entry는 `0x6064:0 position_actual_value`, 32-bit signed `DINT`이다. Complete SDO
List도 `0x6064` = `INT32`, RO, "Actual position (cnt)"로 정의한다. 따라서 SDK
`ServoTx.actualPosition` → J6 `969130066`은 **CiA 402 0x6064의 drive-processed actual
position count**이며, Python/ROS가 만든 값이 아니다.

**FACT** — 공식 CORE 환산표는 CORE100/200/500/1000의 single-turn encoder resolution을
모두 `65536 counts/motor-turn`으로 명시한다. Gear ratio는 CORE100 `101`,
CORE200/500/1000 `121`이다. 이는 이전의 65536 가정을 **CORE 제품군 수준에서
공식 근거로 강화**한다.

**NOT CONFIRMED** — J6가 CORE100인지, 101:1인지는 `GetSlaveTypeNum`으로 알 수
없고 local Indy7 BOM/config에도 없다. 따라서 J6 ratio=101은 아직 확정하지 않는다.

raw의 정수 분해는 다음과 같다.

```text
969130066 = 0x39C3C052
969130066 / 65536 = 14787 remainder 49234
CORE100(101:1) 환산만 가정: +52708.8163 deg before sign/robot-zero
CORE200/500/1000(121:1) 가정: +43996.6153 deg before sign/robot-zero
```

CORE100으로 가정했을 때 이전에 역산한 zero `564765.66 count`와 reverse sign을
적용하면 DCP `-52678.1°`와 매우 정밀하게 맞는다. 이는 **INFERENCE**이며
J6 module/ratio/zero가 확정된 것은 아니다.

**INFERENCE** — 공식 문서는 absolute encoder가 battery로 multi-turn data를 유지하며,
0x6064 환산식이 count를 연속 motor turns로 해석하므로 이 큰 값은 multi-turn
기여를 포함한 drive position으로 보는 것이 타당하다. 그러나 `0x39C3C052`를
encoder bit-field로 직접 분해해 multi-turn=14787이라고 확정할 공개 형식 정의는
없다. 특정 `2^N` wrap, signed int32 overflow, 또는 특정 알려진 corruption constant와
일치한다는 근거도 없다. 값은 int32 범위 내이므로 **현재 전송 필드의
int32 overflow 자체**는 아니다.

### 24.5 복구 기능의 실제 영향과 제한

| 기능 | 실제 인자/범위 | 확인된 영향 | 영구성 / 재보정 | J6에 안전한가 |
|---|---|---|---|---|
| `reset_overflow_count()` | `ResetOverflowCount(Empty)`, **축 인자 없음** | SDK docstring: "Reset and save overflow count" | **save를 명시하므로 persistent write로 취급**; 저장 위치/영점 영향 미공개 | **아님**. J6-only 불가, controller 3.3.2 지원도 미검증 |
| `reset_servo(servo_idx)` | 단일 servo index | docstring/RPC상 "Reset servo error"; CiA 402 fault reset에 해당할 가능성 | encoder/multi-turn/offset 변경 근거 없음; 통상적 fault clear는 영점 복구가 아님 | **아님**. J6 error code가 0이므로 진단적 근거도 약함 |
| `set_robot_zero_as_current(servo_idx)` | 단일 servo index | 현재 위치를 robot zero로 설정하는 write | persistent 여부는 공개 client에 없음; 기계적 기준자세/캘리브레이션 필요 | **절대 아님**. 잘못된 -52678°를 정상처럼 숨길 위험 |
| CORE Setting `Clear PVL` | 공식 GUI에서 선택한 CORE | multi-turn chip error clear | 48 V power reset 절차 포함; 위치 기준 복원 영향은 제조사 확인 필요 | 후보이지만 **현재 실행 금지** |
| CORE Setting `Sync` | 선택 CORE | battery 교체 후 single-turn/multi-turn chip 재동기화 | 48 V power reset 포함; physical/robot zero 후속 검증 필요 | 소프트웨어 복구 가능성의 **가장 직접적 공식 근거**, 단 제조사 절차 필수 |
| CORE Setting `Reconfiguration` | 선택 CORE | encoder configuration factory reset | 구성 소실/재설정 위험 | 승인된 backup/rollback 없이 불가 |
| CORE Setting Calibration | 선택 CORE | commutation data 재캘리브레이션 | 공식 문서상 brake release와 작은 회전 발생 | **현 조사 범위의 절대 금지 항목** |

Neuromeka 공식 LiveUSB 문서는 “wrong position detected”와 encoder battery 교체 후
encoder preset/fix 경로를 명시하고, read-only `Get Chip information`, `Get Status`,
`Get Sync state`를 제공한다. 이는 **엔코더 하드웨어 교체 없이 서비스
도구로 복구될 시나리오가 실제로 존재**함을 보여 준다. 그러나 현재
error code 0이 battery/PVL/sync 문제를 부정하는지, 설치된 J6와 도구 버전이
호환되는지, Sync 후 robot zero를 어떻게 복원할지는 미확인이다.

일반 CiA 402와 Neuromeka 절차를 혼동하지 않는다. CiA 402의 fault reset은
drive state-machine fault clear이지 absolute encoder multi-turn 재동기화 표준 절차가 아니다.
더군다나 Neuromeka Complete SDO List에서 표준적 이름의 `0x607C home_offset`은
**Driver Temperature C**, `0x6098 homing_method`는 **Calibration control**로 재사용된다.
타사 CiA 402 SDO recipe를 Indy7에 적용하면 안 되는 직접적 근거다.

### 24.6 가설 갱신과 현재 결론

- **강화** — J6 absolute encoder/multi-turn 또는 CORE persistent feedback 이상. 0x6064가
  실제 drive TxPDO임이 확정됐고, 공식 문서에 wrong-position/PVL/sync 복구 경로가 존재한다.
- **강화** — 전체 EtherCAT 통신 이상 가설은 낮아졌다. 8 slaves OP, Rx/Tx domain
  complete이고 J1~J5는 정상 규모 raw를 보고한다.
- **부분 강화, 아직 미확정** — 65536 count/rev는 CORE 공식 값이다. ratio 101은
  J6가 CORE100임이 확인되기 전까지 가정이다.
- **약화** — 단순 int32 전송 overflow. `969130066` 자체는 signed int32 범위 내이며
  특정 wrap boundary와 일치하지 않는다. 다만 controller의 별도 overflow accumulator 이상은
  `reset_overflow_count` 내부 구현이 비공개이므로 배제하지 못한다.
- **유지** — battery alarm 가설. 공식 error 0xFF10은 존재하고 battery <3.2 V면
  교체가 필요하지만, J6 현재 error code는 0이고 SDK에 battery getter/history가 없어
  증명도 반증도 못 한다.
- **유지** — robot-zero/offset corruption. Getter 0은 default ambiguity가 있고 0x6064 자체가
  이미 과도하게 크므로, zero만의 문제보다 encoder/CORE position 계층이 우선이다.

**핵심 결론** — 엔코더 하드웨어가 무조건 고장났다고 볼 수 없다. Neuromeka가
문서화한 `Clear PVL` + `Sync` 서비스 경로 때문에 **battery/multi-turn chip
state/synchronization이 원인이라면 소프트웨어적 복구 여지가 있다.** 반면 encoder
자체, battery/cable, CORE nonvolatile memory 또는 controller board가 손상됐거나 신뢰할
수 있는 physical zero 기준/백업이 없다면 부품 교체 또는 제조사 재캘리브레이션이
필요하다.

### 24.7 위험 없이 남은 조사와 제조사 질문

1. 제조사에 J6 CORE model/gear ratio, encoder maker/model/bit layout, slave mapping을 요청한다.
2. LiveUSB CORE Setting의 **Get Chip information / Get Status / Get Sync state만** 실행하는
   serial/FW 호환 절차와 출력 해석을 요청한다. 이 도구를 임의로 부팅/실행하지 않는다.
3. `ResetOverflowCount`의 overflow 정의, 저장 위치, 전축/J6-only 범위, robot zero
   영향, controller 3.3.2 지원 여부를 질문한다.
4. `GetRobotZeroCount` empty/default 0의 의미와 Indy7 지원 여부를 질문한다.
5. Clear PVL/Sync 전 backup 방법, 실패 시 rollback, 48 V reset 순서, brake/servo
   조건, J6 선택 보장, 후속 physical zero/robot-zero 검증 절차를 요청한다.
6. 외부 write 없는 추가 로컬 조사는 보존된 진단 로그, 로봇 label/BOM,
   이전 정상 시점의 q/PDO 로그 비교로 제한한다.

최종 권장은 지금도 동일하다. 제조사가 serial/FW에 맞는 J6-only 절차, backup,
물리 기준자세, 영점 복원 방법을 확정하기 전에는 어떤 reset/write도 하지
않는다. 답변을 받으면 먼저 read-only chip/status/sync-state를 백업한 뒤,
사용자 승인을 받아 별도 정비 절차로 진행한다.

## 25. 2026-10-11 CORE Setting 진단·복구 계획 정밀 조사

> [!CAUTION]
> 이 섹션은 공식 문서, 보존된 공식 GUI 그림, encoder chip 제조사
> datasheet를 이용한 **정적 조사와 계획**이다. CORE Setting 실행, LiveUSB
> 부팅, EtherCAT master 변경, 로봇/컨트롤러 재부팅, encoder 명령은 없었다.

### 25.1 CORE Setting의 공식 실행 환경

Neuromeka 3.2와 3.5 공식 문서/검색 index의 CORE section 5 본문은 이 부분에서
실질적으로 동일하다. 공식 절차는 다음 환경만 문서화한다.

| 항목 | 판정 | 근거와 제한 |
|---|---|---|
| OS | **FACT**: 사전 구성된 LiveUSB의 Ubuntu 16.04 | 부팅 후 desktop에 `MasterCtrl.sh`, `CORESetting.sh`가 있는 형태 |
| LiveUSB 필수 여부 | **FACT**: 공식으로 문서화된 유일 방법은 LiveUSB | **NOT CONFIRMED**: 앱을 일반 Ubuntu에 설치/이식하는 절차는 없음 |
| 일반 Ubuntu 24.04 실행 | **NOT CONFIRMED / 비권장** | binary, EtherCAT master, kernel/driver/runtime 의존성과 배포 package가 공개되지 않음 |
| 배포 경로 | **FACT**: CORE module 제공 시 Neuromeka로부터 preconfigured LiveUSB 또는 image를 받는다고 설명 | **FACT**: 공개 download URL은 section 5에 없음; 제조사/대리점 제공 필요 |
| PC | **FACT**: Ethernet port 1개 이상, USB boot 가능 | USB-Ethernet도 동작할 수 있지만 비권장; laptop보다 desktop PC 권장 |
| USB | **FACT**: 이미지를 받는 경우 10 GB 이상 빈 USB 3.0 | Windows Win32 Disk Imager, Linux `dd`, Clonezilla를 image writer로 언급 |
| 네트워크 연결 | **FACT**: LiveUSB PC의 Ethernet interface를 선택하고 CORE를 PC에 연결 | 문서는 단독 CORE 예시를 제시; 완성된 Indy7 bus의 구체적 service connector/배선은 미공개 |
| EtherCAT master | **FACT**: `MasterCtrl.sh`에서 NIC 선택 후 ENABLE, 그 뒤 `CORESetting.sh` 실행 | interface 변경 시 master를 먼저 DISABLE |
| CORE bus state | **FACT**: CORE Setting slave list에 CORE가 `PREOP`로 표시 | 현재 Indy controller master의 OP 상태와 다른 service 환경 |
| 컨트롤러 재부팅 | **NOT CONFIRMED** | 공식 section 5는 Indy controller reboot 절차를 설명하지 않음. 다만 외부 master가 로봇 EtherCAT bus를 소유하려면 controller master와의 충돌을 방지해야 함 |
| 기존 master 정지 | **INFERENCE, 사실상 필수** | 동일 EtherCAT segment/NIC에 master 두 개를 동시 운영하는 절차가 아님. 그러나 Indy에서 어떤 service mode/전원 순서로 기존 master를 중지할지는 제조사 확인 필요 |
| controller/FW 호환성 | **NOT CONFIRMED** | 문서 GUI 예시는 `CORE CALIBRATION Rev.1`, CORE SW 1.10; 현재 controller 3.3.2-GA3와 CORE FW 9.12C/1.12C의 호환 matrix는 없음 |

따라서 현재 PC의 Ubuntu 24.04에 임의로 CORE Setting을 설치하거나, Indy controller가
마스터인 상태에서 LiveUSB master를 추가로 ENABLE하는 계획은 세우지 않는다.

### 25.2 공식 GUI에서 확인된 표시와 진단 기능

보존 그림 `/tmp/neuromeka-LiveUSB-core-setting.png`(공식 Figure 10 중 encoder
control 화면)에서 다음 GUI 구성을 확인했다.

```text
Slave list:       0 : 0.0 PREOP + NRMK Drive
Select device:    0 : 0.0 PREOP + NRMK Drive     (0 in 1)
Chip version:     CORE#0: W1 (0x06)
A_max:            CORE#0: 3000.00
Encoder Status:   CORE#0: 0x00, 0x00, 0x00
Synchronization:  CORE#0: NOT SYNC (11 vs 12)
Other status:     CRC Error 1, Error Bit 1
Buttons:          Get Chip information, Reconfigure, Get Status,
                  Clear PVL, Get Sync State, Sync
```

이 그림은 **UI/output format의 예시**일 뿐이며, 정상 상태 예시가 아니다.
실제로 `NOT SYNC`, `CRC Error 1`, `Error Bit 1`을 표시하므로 이 값들을
정상 reference로 사용하면 안 된다.

| 기능 | Neuromeka가 명시한 반환/목적 | GUI에서 확인된 형태 | read-only 판정 | 공개 근거의 한계 |
|---|---|---|---|---|
| Get Chip information | encoder version과 internal setting | `Chip version`, `A_max`; 예: `W1 (0x06)`, `3000.00` | **의도상 read-only** | 모든 configuration field, 정상 범위, J6 기대값 미공개 |
| Get Status | encoder internal statuses | `Encoder Status` 3 bytes, 별도 `CRC Error`, `Error Bit` | **의도상 read-only** | 3 byte의 정확한 배치/bit decoder를 Neuromeka가 공개하지 않음 |
| Get Sync State | synchronization status | `SYNC`/`NOT SYNC (a vs b)` 형태 | **의도상 read-only** | `a`, `b`가 어떤 single-turn/multi-turn code인지 미공개 |

**FACT** — 이 세 버튼은 공식 문서에서 `Status checking functions`로 분류된다.
**INFERENCE** — 조회만 하는 read-only 기능으로 설계됐다.
**NOT CONFIRMED** — 클라이언트 구현 소스가 공개되지 않아, 각 버튼이 encoder
진단 mode 전환과 같은 일시적 내부 명령을 전혀 전송하지 않는지는 코드 수준에서
증명할 수 없다. 제조사의 실행 승인 전에는 실제 실행하지 않는다.

### 25.3 PVL chip 단서와 status 해석 수준

Neuromeka는 해당 encoder chip의 제조사/모델을 section 5에서 명시하지 않는다.
그러나 GUI의 `Clear PVL`, chip release `W1`, `A_max`, 3-bit synchronization 구조는
iC-Haus의 battery-buffered multiturn encoder chip **iC-PVL** 자료와 강하게 일치한다.

- **INFERENCE** — CORE Setting이 iC-PVL 또는 동일 register model을 다룬 가능성이 높다.
- **NOT CONFIRMED** — 실제 Indy7 J6 encoder에 iC-PVL이 있는지, chip release가 무엇인지,
  CORE FW가 status byte를 그대로 GUI에 노출하는지는 제조사/BOM 확인 필요.

이 전제하에 encoder chip 제조사의 [iC-PVL Rev H1 datasheet](https://www.ichaus.de/product/iC-PVL)는
주요 status byte를 다음과 같이 정의한다.

| bit | 이름 | 의미 | J6 진단 연관성 |
|---:|---|---|---|
| 0 | `STUP_ERR` | startup/EEPROM read/CRC 오류 | configuration 부팅 실패 확인 |
| 1 | `CFG_ERR` | RAM configuration과 CRC 불일치 | configuration corruption 가설 |
| 2 | `CTR_ERR` | internal multi-turn counter와 CRC 불일치 | **PVL/multi-turn corruption에 가장 직접적** |
| 3 | `POS_ERR` | 비정상 position jump, 과속/약한 자기장/자석 소실 | encoder hardware/magnet/signal 가설 |
| 4 | `BAT_ERR` | battery undervoltage, position invalid | battery 손실 가설 |
| 5 | `BAT_WRN` | battery early warning | 예방 교체 판단 |
| 6 | `PDR` | power-down reset detected | 과거 전원 상태 단서 |
| 7 | `PRESET` | preset/I2C reboot/sleep event detected | preset 이력 단서; 이 비트만으로 정확한 원인 구분 불가 |

datasheet는 추가 status byte에 `AMPL_ERR`, `MAG_ERR`, `NOMAG_L`, working-state bits도
정의한다. 따라서 GUI의 `0x00, 0x00, 0x00`은 status/configuration/extended
status 등 여러 register일 가능성이 있지만, **3 byte 순서를 임의로 확정하지
않는다.**

정상 판정은 최소한 다음과 같은 형태여야 할 것으로 **추정**된다.

```text
Get Status:     CRC Error=0, Error Bit=0, decoded active fault bits=0
Get Sync State: SYNC, single-turn/multi-turn comparison values agree
```

그러나 Neuromeka가 Indy7/J6용 normal output example와 bit decoder를 제공하기 전에는
이를 공식 pass criterion으로 확정하지 않는다.

### 25.4 세 read-only 진단으로 구분할 수 있는 것

| 질문 | 판정 방법 | `969130066` 연결 |
|---|---|---|
| PVL/multi-turn counter 오류인가 | Get Status의 counter/CRC/PVL fault를 해독; iC-PVL이 맞다면 `CTR_ERR` 중요 | 현재 raw 값을 특정 error code로 역산할 수는 없고 status로 독립 확인해야 함 |
| single/multi-turn 불동기인가 | Get Sync State가 `NOT SYNC (a vs b)`인지 확인 | NOT SYNC라면 비정상 raw와 연관된 강한 증거지만 count 자체의 정답은 아님 |
| battery 문제인가 | Get Status의 battery error/warning; CORE 0x603F `FF10` 함께 비교 | 현재 0x603F=0이어도 chip-latched status/history가 남았는지 확인 가치가 있음 |
| configuration mismatch인가 | Get Chip Information의 version/internal setting을 동일 정상 축과 비교; Get Status CRC/config fault | 정상 J1~J5 값이 유용한 비교군이지만 CORE model/FW가 다를 수 있음 |
| hardware fault인가 | position/magnet/amplitude/startup error가 clear 후 재발하는지; battery/cable 실물 검사 | read-only만으로 부품 고장 확정은 불가 |

Get Chip Information이 multi-turn counter 실값이나 preload/zero를 보여 준다는 공식
근거는 없다. 따라서 `969130066` raw와 chip counter를 정량 대조할 수 있다고
미리 가정하지 않는다.

### 25.5 Clear PVL, Sync, Reconfiguration의 확인된 영향과 미확인 영향

#### Clear PVL

- **FACT (Neuromeka)** — "clear multi-turn chip error" 기능이다.
- **FACT (Neuromeka)** — battery 교체 후 절차는 모든 정보/상태 조회 → Clear
  PVL → 48 V power reset → Sync → 48 V power reset → 상태 재확인 순서다.
- **INFERENCE (iC-PVL이 맞는 경우)** — GUI의 Clear PVL은 chip `SCLR` command와
  관련될 가능성이 높다. iC-PVL SCLR은 status register의 모든 status bit를
  clear하며, counter position이나 configuration을 reset한다고 정의되지 않는다.
- **FACT (chip datasheet)** — 실제 error condition이 계속되면 clear 후 바로 다시
  set될 수 있다. 따라서 Clear PVL 성공을 고장 해결로 즉시 해석하면 안 된다.
- **NOT CONFIRMED** — Neuromeka GUI가 정확히 SCLR만 쓰는지, counter CRC를
  재생성하는지, multi-turn 값/robot zero/mechanical zero를 보존하는지, 변경이
  nonvolatile인지는 구현 소스/제조사 답변 없이 확정 불가.

#### Sync

- **FACT (Neuromeka)** — battery 교체 후 single-turn chip과 multi-turn chip을
  re-synchronize하는 기능이다.
- **FACT** — 공식 절차에 48 V power reset이 후속되므로 state-changing service
  operation으로 취급해야 한다.
- **INFERENCE** — single-turn이 보고하는 현재 revolution phase와 PVL multi-turn
  counter의 sector/경계를 다시 맞추는 작업으로 보이므로 현 문제에 가장 직접적인
  복구 후보다.
- **NOT CONFIRMED** — Sync가 existing multi-turn count를 그대로 보존하는지,
  현재 physical pose를 기준으로 counter/preload를 쓰는지, mechanical/robot zero를
  유지하는지, EEPROM/비휘발성 메모리를 쓰는지, rollback이 가능한지는 미공개.

#### Reconfiguration

- **FACT (Neuromeka)** — "factory reset of encoder configuration"이다.
- **INFERENCE** — chip mode, direction, period count, bit width, battery monitoring, threshold,
  sync width 등을 건드릴 수 있는 고위험 작업이다.
- **NOT CONFIRMED** — Neuromeka factory default가 Indy7 J6 assembly-specific setting과 동일한지,
  counter preload가 초기화되는지, 자동으로 백업/복원할 수 있는지는 미공개.
- **결론** — 제조사가 J6용 configuration file, backup, restore, calibration 순서를
  제공하지 않으면 실행하지 않는다.

세 복구 버튼 모두 공개 문서는 mechanical zero/Indy robot zero 보존 여부를
설명하지 않는다. 엔코더 chip state가 복구되어도 controller의 robot-zero count와
물리 기준자세가 맞는지 독립적으로 검증해야 한다.

### 25.6 J6 단일 축 식별을 위한 index 구분

| 개념 | 현재 알려진 값 | 근거/위험 |
|---|---|---|
| Joint index | DCP/ROS/Python에서 J6는 zero-based `5` | 로봇 관절 배열 순서 |
| EtherCAT `ServoIndex` | 기존 getter에서 J6를 `servo_idx=5`로 조회해 이상 raw를 확인 | controller service의 logical servo index; physical slave position과 동일하다는 명시적 근거는 없음 |
| EtherCAT `EcatIndex` | **미확인** | SDK가 SDO getter에서 별도 `EcatIndex` message를 사용함. `ServoIndex`=physical slave index라고 가정 금지 |
| EtherCAT slave position/alias | 현 bus에 8 slaves | 6 servo+IO+endtool의 physical enumeration order, alias, serial은 미확인 |
| CORE Setting device ID | GUI 예시는 `0 : 0.0 PREOP + NRMK Drive`, `CORE#0`, `0 in 1` | 앞의 `0`, `0.0`, `CORE#0`의 정확한 alias/position 표기 정의는 미공개 |
| CORE firmware array | `[9.12C, 9.12C, 1.12C, 1.12C, 1.12C, 1.12C]` | joint order일 가능성은 있지만 J1~J6 mapping은 미확인 |

**FACT** — CORE Setting은 bus에 여러 CORE가 있을 때 `Select device` dropdown으로
하나를 선택할 수 있다. 따라서 tool 자체에는 single-CORE targeting 기능이 있다.

**NOT CONFIRMED** — 완성된 Indy7에서 J6가 dropdown의 몇 번 device인지는 현재
자료로 알 수 없다. 단순히 `J6 = ServoIndex 5 = slave 5 = CORE#5`라고 놓지
않는다.

실제 작업 전 J6 식별에는 최소 다음 교차 확인이 필요하다.

1. 제조사의 serial `PD47I7E0D002` 전용 slave topology/J1~J6 mapping.
2. CORE Setting 목록의 device position, alias, product code, serial/FW 식별 필드.
3. 현 controller의 J6 CORE FW와 GUI에 나오는 선택 device FW의 일치.
4. 가능하면 물리 J6 CORE/encoder label과 일치.
5. read-only Get Chip/Status/Sync만 먼저 실행하고 J1~J5 비교값과 교차 검증.

오선택하면 Clear PVL/Sync/Reconfiguration이 다른 관절에 적용될 수 있다.
Reconfiguration은 특히 assembly-specific 설정 손실 위험이 있다. GUI의 device
dropdown이 하나를 선택하더라도, 48 V reset과 master/bus 재열거는 전체 bus에
영향을 줄 수 있다.

### 25.7 원인별 복구 가능성

| 시나리오 | 필요한 진단 | 공식 복구 후보 | zero/calibration | hardware | 제조사 |
|---|---|---|---|---|---|
| A. PVL/counter status 오류 | Chip info, Status raw/decoded bits, CRC/Error Bit, power reset 전후 latch | `Clear PVL`; 원인이 지속되면 재발 가능 | 보존 여부 미확인; q/0x6064/physical pose 재검증 필수 | 반복 오류 시 chip/battery/EEPROM 검사 | **필수** |
| B. Single/multi-turn 불동기 | Get Sync State의 SYNC/NOT SYNC와 비교값, Status | `Sync` + 문서상 48 V reset | 보존 여부 미확인; 물리 기준자세 필요 가능성 큼 | 동기화 실패/재발 시 encoder 검사 | **필수** |
| C. Battery 정보/전원 손실 | BAT_ERR/BAT_WRN, FF10, battery 전압, PDR, 실제 위치 신뢰성 | battery 교체 후 `Clear PVL` → reset → `Sync` → reset이 공식 흐름 | multi-turn 손실 시 physical/robot zero 재설정 가능성 큼 | 배터리 교체 필요 가능 | **필수**, arm 분해/배터리 사양 확인 |
| D. Position offset/robot zero corruption | chip Status/Sync가 모두 정상인지, 0x6064, 신뢰할 physical zero, controller zero backup | CORE encoder 버튼으로 해결된다는 근거 없음 | **재캘리브레이션/영점 복원 필요 가능성 높음** | 보통 부품 교체는 아님 | **필수**, `set_robot_zero_as_current` 임의 사용 금지 |
| E. Encoder hardware/magnet/cable/EEPROM 고장 | POS/MAG/AMPL/startup/config error, battery/cable, clear/reboot 후 재발 | 지속 hardware fault에는 유효한 공개 SW 복구 없음 | 부품 교체 후 calibration/zero 필요 가능성 큼 | **필요 가능성 높음** | **필수** |

`969130066` 하나만으로 A~E를 구분할 수 없다. 가장 정보량이 큰 다음
증거는 선택한 **정확한 J6 CORE**의 Chip Information + Status + Sync State 원문이다.

### 25.8 실행 전 backup과 rollback 요구사항

공개 CORE Setting 문서에는 backup/export/restore/undo 기능이 설명되지 않는다.
따라서 아래 항목을 제조사가 백업하는 공식 방법을 제공하기 전에는 write
단계로 넘어가지 않는다.

1. 선택 device의 EtherCAT position/alias, product/revision/serial, CORE FW.
2. Get Chip Information 전체 출력과 encoder internal configuration.
3. Get Status raw bytes + decoded labels, Get Sync State의 두 비교값.
4. encoder multi-turn counter/current/preload/CRC를 조회·export하는 제조사 방법.
5. controller robot-zero count, factory joint offset/calibration data의 유효한 backup.
6. 현재 물리 J6 자세와 신뢰할 수 있는 mechanical witness/reference.
7. 정상 J1~J5의 비교 snapshot과 현 J6 DCP/PDO/status snapshot.
8. Clear PVL/Sync/Reconfiguration 각각의 실패 시 restore 명령과 전원 순서.

**NOT CONFIRMED** — 이 항목 전부가 CORE Setting UI로 export 가능한지는 알 수 없다.
특히 chip counter/preload와 robot-zero를 모두 복원할 수 있다는 증거가 없으므로
rollback 가능성을 가정하지 않는다.

### 25.9 승인 전 실행 가능한 다음 작업

현재 즉시 가능한 것은 로봇과 네트워크에 영향을 주지 않는 자료 준비뿐이다.

1. 제조사에 아래 **read-only 진단 허용범위**를 문서로 확인한다.
   - 이 robot serial/controller/CORE FW와 호환되는 LiveUSB image/tool revision
   - Indy service connector와 기존 master 중지/절연/전원 순서
   - J6에 해당하는 CORE Setting device ID의 확정적 mapping
   - Get Chip Information/Get Status/Get Sync State가 설정을 변경하지 않는지
   - 각 field/bit의 decoder와 J6 정상 예시
2. 제조사가 제공할 image/checksum/revision과 서비스 절차를 로컬 문서로만 준비한다.
3. robot label, 정비 기록, BOM, 이전 정상 q/PDO 로그에서 J6 hardware 식별자를 찾는다.
4. 후속 실행 계획은 두 gate로 나눈다.
   - Gate 1: J6 식별이 확정된 read-only Chip/Status/Sync snapshot만 받음.
   - Gate 2: 원문 해석과 backup/rollback/zero 복원 절차를 서면 확정한 후 사용자의
     별도 승인으로 Clear PVL/Sync 여부를 판단함.

**현재 판정** — Clear PVL은 PVL/counter status error가 실제로 확인될 때만
합리적인 후보이고, Sync는 `NOT SYNC`가 확인될 때만 합리적인 후보다.
현재의 비정상 position만 보고 두 명령을 순서대로 시행하는 것은 근거가
부족하다. Reconfiguration은 configuration fault와 J6-specific factory settings/restore package가
확인되기 전에는 후보에서 제외한다.

## 26. 2026-10-12 무동작 위치 점프 보고 및 J6 상태 반복 실측

**USER REPORT — 실측 로그와 구분:** 사용자가 이머전시 후 복구를 진행했을 때
Conty J6가 약 33°로 돌아왔으나 FAULT/READY_SWITCH 반복이 지속됐다.
이머전시 후 다시 복구하자 약 -52674°로 바뀌었다. 사용자는 두 변화 모두
실제 로봇 움직임이 없었으며 상태 반복은 추가 조작 없이도 발생한다고 확인했다.
33°인 순간의 DCP/PDO 값은 확보되지 않았다. 33°가 정확한 위치라는 검증도 없다.

**FACT — 새 read-only 실측:** 2026-10-12 20:29:51~20:30:10 KST,
약 0.5초 간격 40회 조회. GetControlState, GetServoData, GetServoTx(5),
GetServoErrorCode(5), GetSlaveStatus, GetTxDomainStatus만 사용했다.
네트워크 sandbox 차단 후 허용된 실행으로 조회했으며, 제어/설정 write는 없었다.
원본: `docs/diagnostics/indy_j6_observe_20261012_202951.jsonl`.

- J6 q/qdes: 모든 표본 -52674.984°.
- J6 actualPosition: 모든 표본 969072715; actualVelocity 0.
- J6 PDO statusWord: 520 (0x0208, FAULT) 20회,
  545 (0x0221, READY_SWITCH) 20회. DCP에서도 두 상태 각 20회.
- DCP J1~J5 status: 40회 모두 0x0221.
- 모든 축: servo_actives false, brake_actives true, 보고 전압 48.0 V.
- J6 GetServoErrorCode: 모든 표본 0. FAULT 상태의 원인이 없다는 뜻은 아니다.
- 8개 slave 모두 OP, Tx domain COMPLETE가 모든 표본에서 유지됐다.

**INFERENCE:** 이번 표본은 J6에 국한된 반복 상태 이상을 뒷받침한다.
상태가 바뀌어도 현재의 잘못된 위치값은 고정돼 있으므로 상태 전환마다
위치값이 점프한다는 결론은 틀리다. 사용자 보고의 복구 시 위치 점프는
고정된 zero offset만의 문제보다 위치 초기화/재로딩/피드백 처리 문제를
추가 후보로 만든다. 멀티턴/동기화 이상과 CORE firmware/controller 처리,
encoder/drive hardware 이상은 아직 구분되지 않았다.

**LIMITATIONS:** 순차 RPC이므로 원자적 동시 snapshot이 아니다.
0.5초 표본은 실제 전환 주기나 짧은 오류를 확정/배제하지 못한다.
OP/COMPLETE 및 보고 전압 48 V는 순간적인 통신/전원 이상을 완전히 배제하지 않는다.
33° 이벤트는 재현시키지 않았고, 복구/Sync/초기화는 실행하지 않았다.
추가 복구 반복으로 이벤트를 유도하지 말고, 제조사에 이 시간대 controller/drive
오류 이력 및 J6 상태 전환 원인과 위치 재초기화 경로 분석을 요청한다.

### 26.1 33.47° 상태에서 두 번째 실측 및 사용자 정정

사용자가 다시 Conty 33.47°와 FAULT/READY_SWITCH 반복을 보고하여 같은 getter로
2026-10-12 20:35:43~20:36:03 KST에 40회 추가 기록했다.
원본: `docs/diagnostics/indy_j6_observe_20261012_203543.jsonl`.

**USER CLARIFICATION:** 이번 33.47° 표시 전에도 이머전시를 눌렀다고 확인했다.
따라서 위치값이 조작 없이 자발적으로 정상 규모로 돌아온 사례로 기록하면 안 된다.
버튼 누름/해제/복구 각각의 정확한 시각과 현재 버튼 상태는 아직 미확인이다.
앞선 사용자 확인 중 '조작 없이 반복'은 FAULT/READY_SWITCH 상태 반복에 해당한다.

**FACT — 두 번째 실측:**
- J6 q 및 qdes: 33.474743~33.475014°.
- J6 actualPosition: -50756~-50751 counts. 이전 969072715와 현저히 다르다.
- J6 TxPDO status: READY_SWITCH 19회, FAULT 21회.
- DCP J6 status: READY_SWITCH 23회, FAULT 17회. 순차 조회이므로 두 횟수는
  동시 snapshot의 모순이 아니다.
- J1~J5: 전부 READY_SWITCH 유지. 모든 축 servo false / brake true / 48 V.
- J6 errorCode: 모두 0. 8 slaves OP, Tx domain COMPLETE 유지.
- TxPDO actualVelocity: 0이 35회, -3000이 2회, -4000이 2회, -6000이 1회.
  해당 필드의 단위/유효성은 이번 조사에서 검증하지 않았으므로 실제 동작 속도로
  해석하지 않는다. 사용자는 앞선 큰 위치 변화에 실제 움직임이 없었다고 보고했다.

**UPDATED INFERENCE:** 큰 음수와 정상 범위 각도에 대응하는 서보 보고 위치값
양쪽을 확보했다. Conty 화면만의 문제나 ROS 단위 변환만으로는 설명할 수 없다.
단, GetServoTx는 controller를 통한 drive-processed feedback 조회이므로 encoder
chip 원시값 자체의 corruption으로 확정하지 않는다. CORE/drive 위치 처리뿐 아니라
controller의 PDO 취급/초기화 경로도 후보로 유지한다.
각도가 한계 안에 있어도 FAULT 반복이 지속되므로 현재 각도의 한계 초과만으로
모든 증상을 설명할 수 없다. 이전 오류 latch, safety/recovery state 처리 또는
독립적인 drive/encoder fault가 남았을 가능성을 구분해야 한다.
33.47°의 실제 기준자세 정확성, 동기화 정상 여부 및 복구 성공은 미검증이다.
이번 에이전트 작업은 getter만 실행했고 이머전시/복구/Sync 조작을 하지 않았다.

### 26.2 복구 완료 조작 확인 및 현재 오류 메시지 조회

**USER CLARIFICATION:** 사용자는 이머전시를 해제하고 Conty 복구까지 누른
상태라고 확인했다. 따라서 단순히 이머전시 버튼을 계속 누른 상황으로 설명하지 않는다.
단, 복구 버튼 조작은 controller의 복구 성공을 증명하지 않는다.

20:37:36~20:37:38 KST에 GetControlData/GetServoData/GetMotionData/
GetViolationData/GetViolationMessageQueue를 1초 간격 3회 조회했다.
원본: `docs/diagnostics/indy_j6_fault_detail_20261012_203736.jsonl`.
J6 약 33.475°, servo false/brake true, motion is_in_motion=false,
has_motion=false, is_stopping=true가 기록됐다. violation_code/id는 0,
violation_str는 빈 문자열, message queue는 비어 있어 근본 원인 메시지를
확보하지 못했다. j_index=4294967295를 실제 관절 번호로 해석하지 않는다.
현 오류 getter 출력의 부재는 정상 또는 fault 부재의 증거가 아니다.
앞선 20초 기록에서 확인한 J6 상태 반복의 원인에는 controller/drive 오류
이력과 fault/reset 상태 전환 추적이 필요하다. Sync의 필요성은 여전히 미확인이다.

### 26.3 Conty 이력 및 Rx/Tx 조회

사용자는 Conty 이력에 이머전시 조작 후의 Connection lost만 보인다고 보고했다.
어떤 connection을 뜻하는지와 정확한 시각/코드는 미확인이며 J6 원인으로 단정하지 않는다.
20:39:41~20:39:51 KST에 0.25초 간격 40회 GetServoRx/GetServoTx/
GetServoErrorCode를 J1(index 0)과 J6(index 5)에 대해 읽었다.
원본: `docs/diagnostics/indy_j6_rx_tx_20261012_203941.jsonl`.
J1/J6 controlWord는 전 표본 0, errorCode도 전 표본 0이었다.
J1 statusWord는 545가 40회, J6는 520이 16회/545가 24회였다.
읽힌 controlWord에 fault-reset 요청이 반복되는 증거는 잡히지 않았다.
순차 getter/0.25초 표본 및 controller 제공 snapshot이므로 짧은 reset pulse나
내부 recovery 경로까지 배제하지 않는다. 상태 반복의 시작 원인/전환 주기를
확정하지 못했으며 추가 제어 쓰기 없이 진단 기록을 보존했다.

> IP update (2026-10-29): `192.168.1.114` is the current connection target assigned by DHCP; the device has not yet been confirmed as the Indy7 controller.
