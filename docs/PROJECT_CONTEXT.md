# SIRLab258 Project Context

최초 작성 / 마지막 확인: **2026-10-09 (Asia/Seoul)**  
Canonical workspace: `/home/sirlab2/sirlab258`  
목적: 새 세션·다른 계정의 agent가 현재 상태와 결정사항을 5~10분 안에 파악하는 living context.

**작업 전·중·종료 시 관리 규칙 — 사용자 지정**

- 작업 시작: **이 파일 전체 → 관련 상세 handoff → git status 및 필요한 현재 파일** 순으로 읽는다. 이전 구조·결정을 새로 추측하지 않는다.
- 문서와 실제 상태가 다르면 근거를 확인하고 사용자에게 차이를 알린다. README의 과거 운용 지침보다 현재 안전 제한이 우선한다.
- 기능·architecture·중요 config·환경/version·실물 검증·원인/가설·blocker·제조사 답변·안전 제한·우선순위가 바뀌면 갱신한다. 사소한 수정은 생략한다.
- 의미 있는 작업 종료 전 git status/diff, 실제 변경, test/build/robot 검증 범위를 확인한 뒤 Current Snapshot과 Pending Tasks까지 최신화한다.
- **FACT**=코드/출력/로그/실물 관측으로 확인, **INFERENCE**=그에 근거한 해석, **UNKNOWN**=증거 부족. 출처·관측 시점을 구분한다.
- 판단이 바뀌면 `Previous assessment / Updated assessment / Reason`을 남긴다. 사용자 결정도 변경 이유 없이 삭제하지 않는다.
- Recent Changes는 의미 있는 최근 10~20건 정도만 유지하고 오래된 항목은 Changelog History로 압축한다. 긴 분석·raw log는 별도 문서에 두고 여기에는 결론과 링크를 남긴다.
- 안전 제한 해제 시 근거·날짜·검증·승인 범위를 **Resolved History**에 보존한다. 문서 갱신만을 이유로 source/config 변경 또는 commit/push하지 않는다.

## 1. Current Snapshot

- **최신 Recorder UI/교정 수집:** 사용자 `ready`를 제거하고 `s`에서 기존 배치/Attempt 확정을 수행한다. `x`는 RECORDING 중 교정 전환이며 `o`가 Success/Correction에 저장한다. 과거 Failure와 version 1 receipt는 보존하고 새 실패 Episode는 만들지 않는다. 교정 입력 수신/처리 시각과 첫 대응 기록 프레임을 별도 저장하며 교정은 Core-108 완료에 포함하지 않는다. 자세한 동작·호환 한계는 [Recorder 가이드](../ai/record/COLLECTION.md). 기존 제어·Topic·QoS·시간 정합성 검사는 유지했고 실제 장비는 실행하지 않았다.

- **2026-10-31 추가 확인 — 정식 수집 관리:** 기존 Recorder에 opt-in `--collection-dir` 기반 Core-108 조건/Attempt/실제 Episode 연결을 추가했다. 원본 Shuffle과 ID를 유지하며 각 Region × Yaw의 6 Depth 균형을 코드로 검증한다. 원자적 JSON 장부·fsync·파일 잠금·저장 의도/receipt 대조를 사용하고, 증거가 불완전한 부분 저장은 차단한다. 사용법은 [수집 관리](../ai/record/COLLECTION.md). 로봇·시리얼 접속 없이 합성 Dataset과 fake ROS로만 검증하며 기존 시간 정합성 코드는 변경하지 않는다. 아래 2026-10-09 장애·실물 제한의 해제 근거는 이번 작업에서 추가되지 않았다.
- **Pilot 구분:** 사용자 지정 `outputs/act_recorder_pilot_20261031`은 정식 진행률에 포함하지 않고 변경·삭제하지 않는다. 정식 기본 예시는 `datasets/indy7_act_core108/{success,correction,failure,pending}`이며 실제 성공 수집 완료를 의미하지 않는다.

- **현재 phase:** Indy7 + Mark7 수동 teleop 기반의 ACT demonstration 수집 준비. J6 장애로 실물 작업 중단.
- **Robot 사용:** Indy7 실제 motion/write/reset/calibration 금지. 복원된 control + ACT publisher 조합은 fault 이후 실물 재검증하지 않았다.
- **핵심 blocker:** J6 DCP q/qdes 약 `-52678.1°`, EtherCAT actual/target position `969130066`; 완전 power cycle 후에도 지속.
- **현재 판단:** encoder/CORE의 지속 위치 계층 이상이 strongly supported인 **INFERENCE**. 구체적 failure mechanism은 **UNKNOWN**.
- **복구 조사 진전:** 공식 LiveUSB + CORE Setting encoder service 경로는 확인. 현재 serial/FW의 J6 적용 절차와 robot-zero 복원 순서는 제조사 확인 필요. 문의 초안만 준비했으며 전송/답변 증거는 없다.
- **다음 우선 작업:** 제조사에 장비별 진단·복구·영점 절차와 호환 service package 요청. 병행 가능한 offline 작업은 ACT 데이터 계약 불일치 정리.
- **Git (2026-10-31 정리 시작):** `main`, Recorder/Core-108/교정/UI 스냅샷 `b3bbb3b`. 학습·추론·실행·평가 및 QP QoS 등의 별도 미커밋 작업은 보존했다. 파일 정리 범위와 검증 결과는 [정리 조사 기록](PROJECT_CLEANUP_AUDIT.md)을 참고한다. 아래 과거 진단·불일치 기록은 당시의 관측이며 이번 정리에서 실물 상태를 다시 확인하지 않았다.

## 2. Project Goal

- 최종 목적: Indy7 + Mark7로 demonstration을 수집하고 ACT를 학습·평가하여 선반 바구니를 table로 옮기는 작업 자동화.
- 1차 task: 동일 Home에서 시작해 선반 **칸1**의 가변 `(x, y, yaw)` 바구니 손잡이를 잡아 고정 drop zone에 놓는다. Home·카메라·기본 전략은 고정하는 계획이다.
- 계획 순서: **안전한 로봇 복구 → 현재 teleop 실물 검증 → 5~10 pilot → 108 successful core demonstrations → ACT v0 → 약 60 unseen-pose 평가**. 이후 실패 영역 보강.
- 108개는 L/C/R 3영역 × yaw 6 bin × 각 6개. ±40° yaw·약 3 cm lift는 후보 조건이며 실제 feasible workspace는 pilot에서 확정해야 한다.
- 상세: [ACT 수집 계획](ACT_Data_Collection_Plan.md). 계획 문서의 동작 순서는 현재 실행 허가가 아니다.

## 3. Environment

아래 local 환경은 2026-10-09 metadata/source로 확인했다. 로봇 정보는 기존 read-only 진단 기록이며 이번 문서 작성에서 재접속하지 않았다. 버전은 실제 변경 시 갱신한다.

| 구분 | 현재 상태 / 근거 범위 |
|---|---|
| OS / Python | Ubuntu 24.04.4 LTS, kernel `6.8.0-142-generic`; 두 venv Python 3.12.3 |
| ROS | ROS 2 Jazzy, `/opt/ros/jazzy` |
| Robot 환경 | `.venv`, system-site-packages 허용. `neuromeka 3.4.2.2`, `numpy 1.26.4`, `empy 3.3.4`; [requirements](../requirements.txt)의 pin과 일치 |
| User-site SDK | `/home/sirlab2/.local/lib/python3.12/site-packages/neuromeka`, 3.4.2.2 |
| ACT 환경 | `.venv-act`, system-site-packages 비허용. LeRobot 0.5.1, torch `2.10.0+cu130`, torchvision `0.25.0+cu130`, NumPy 2.2.6, torchcodec `0.10.0+cu130` |
| ACT 추가 dependency | datasets 4.8.5, huggingface_hub 1.33.0, av 15.1.0. CUDA runtime/GPU 사용 가능 여부는 이번에 확인하지 않음 |
| LeRobot source | editable `.pth`와 `find_spec`는 **현재 repo** `ai/lerobot_source/lerobot/src`를 가리킴. `direct_url.json`에만 오래된 pipet 경로가 남아 있음 |
| ROS peripheral packages | realsense2_camera 4.57.7, cv_bridge 4.1.0, joy 3.3.0, image_tools 0.33.10 |
| Indy7 | IP `192.168.1.114`, serial `PD47I7E0D002`, payload 7 kg; controller `3.3.2-GA3 [2025.01.23]` |
| CORE firmware | `[9.12C, 9.12C, 1.12C, 1.12C, 1.12C, 1.12C]`; 배열→축/세부 hardware 매핑은 미확인 |
| Mark7 / 입력 | Mand.ro Mark7, 기본 `/dev/ttyACM0`, 115200 baud; Xbox controller |
| RGB 카메라 | D435 serial `317222074298`, `844212071939`. 후자는 과거 USB2 연결 관측. 현재 연결·프레임률 재조회 안 함 |
| OMY 별도 경로 | ROBOTIS 공식 Docker 구성, image 설정 `robotis/open-manipulator:5.1.3`; 실행 중 container/실물 검증은 미확인 |

SDK 3.3.2.3 비교 환경은 `/tmp/indy_sdk_3_3_2_3`에 있다. 최신 3.5.0.10은 앞선 복구 조사에서 **소스만 검토**했으며 설치 SDK가 바뀐 것이 아니다. `/tmp`는 임시 자료이며 다음 세션에서 존재를 확인한다.

## 4. Directory Map

| 경로 | 역할 |
|---|---|
| `ros2_ws/src/capstone_teleop` | QP teleop, input profile, GUI, launch와 robot/camera config |
| `ros2_ws/src/capstone_mark7` | Mark7 serial node, config, protocol test |
| `ros2_ws/src/external/indy-ros2` | 공식 Indy submodule; `jazzy-indyDCP3`, `ca0353f077620c877c1edff2e26c8cd976773f27`, clean |
| `external/open_manipulator` | OMY 공식 submodule; 설정 branch `jazzy`, `9f84095404d3e596267cd520e90963011059ebf6`, clean. gitlink는 staged 추가 |
| `ai/record` | Indy7 두 RGB 카메라 / 7D state-action recorder |
| `ai/train`, `ai/serve` | ACT training/evaluation 및 serving 코드. Indy 데이터 계약과 맞지 않는 부분이 남아 있음 |
| `ai/lerobot_source/lerobot` | LeRobot 0.5.1 vendored source; provenance는 `UPSTREAM.md` 참고 |
| `scripts`, root wrappers | 환경 활성화·편의 실행·진단. `caps`, `act-record`, `act-train` 등 |
| `docs` | 이 canonical context, J6 상세 handoff, ACT 수집 계획·실행기록 template |
| `.venv`, `.venv-act`, `.hf_cache` | 로컬 환경/캐시. Git 제외 |
| `datasets`, `outputs`, `checkpoints`, `recordings` | 생성 산출물 예정 경로, Git 제외. **현재 root에서 이 디렉터리들은 없음** |
| `ros2_ws/build`, `install`, `log` | 생성 빌드/검증 산출물. source와 구분하고 다른 PC로 재사용하지 않음 |

Reference project는 `/home/sirlab2/minseo/capstone`(control 복원 비교 기준), `/home/sirlab2/pipet-physical-ai`(과거 정상 물리 teleop reference). Reference의 기능·검증 결과를 현재 repo에 적용된 것으로 기록하지 않는다.

## 5. Current Architecture

**현재 구현된 실행 연결 — 아래 설명은 실행 지침이 아니다.**

- `./caps` → `scripts/bin/caps` → `setup.launch.py` → 두 RGB camera + Indy driver + Mark7 + Xbox/QP/GUI. `caps`는 카메라 전용 명령이 아니다.
- `capi` → `indy.launch.py` → 공식 `indy_bringup.launch.py` → `indy_driver` + `robot_state_publisher`; 현 launch의 `ros2_control_node`는 주석 처리되어 있다.
- Driver는 IndyDCP3/EtherCAT client를 보유하고 `/joint_states`를 20 Hz로 발행한다. `joint0…joint5`, radian 단위. `/indy_srv`, trajectory/action 및 `/set_servo_rx` write 경로가 존재한다.
- `capx`의 `qp_teleop`도 **별도 IndyDCP3 client**를 갖는다. `/joy` + `/joint_states` → `MotionProfile` → `qp_core.qp_step` → 상대 joint 명령 `movetelej_rel`, 30 Hz. Mode 전환은 `/indy_srv`와 연계한다.
- Startup AUTO ARM은 fresh state·입력·neutral을 기다린다. Home 이후 re-arm, Recover/Zero 이후 disarm 유지. 이 구현은 현재 안전한 실행을 의미하지 않는다.
- GUI는 `/capstone/teleop_speed_mm`, `/capstone/teleop_status` 구독 프로세스이며 robot control의 owner가 아니다.

| Recording 연결 | 현재 의미 |
|---|---|
| `/capstone/teleop_action` | 명령 호출 성공 후 absolute `candidate_joint`를 radian list로 복제한 `JointState`; control array를 수정하지 않는 publish-only |
| `/gripper/open`, `/gripper/grasp` | Mark7 `Trigger` service. 성공 후 `/capstone/gripper_state`에 open=0 / grasp=1 발행 |
| Gripper state 주의 | **명령 전송 성공 기반 상태**, 실제 위치·grasp 성공 센서 측정값이 아님 |
| 두 RGB topic | `/camera_317222074298/camera/color/image_raw`, `/camera_844212071939/camera/color/image_raw` |
| Recorder | `act-record` → ROS/workspace + ACT 환경 → `ai/record/indy7_act_record.py`; robot driver 시작/정지 없이 구독만 수행 |
| Dataset | 기본 20 FPS, `local/indy7_act`, `datasets/indy7_act`; 두 RGB video + state/action 각각 7D (`joint0…5` rad + gripper) |
| Sampling | 최신 수신값과 monotonic 수신 시각 사용. State/image freshness <0.25s 및 gripper 최초 수신 요구. Header timestamp 동기화가 아니며 action/gripper age 검사는 없음 |
| Action fallback | 최초 action 수신 전 measured state 사용; 수신된 action은 이후 유지됨 |
| Episode 조작 | `s/a/r/d/q` 및 full commands; save 최소 2 frames. discard는 이미 저장된 episode를 삭제하지 않음 |

`act-train` → `.venv-act` → `ai.train.run_train` → LeRobot trainer. 기본 repo id `sirlab/grip_all`은 recorder와 다르므로 실제 데이터에 맞춰 지정해야 한다. 기본 CUDA/AMP, 100k steps, batch 64, workers 14, chunk 40/action steps 10, hub push 없음. **현재 Indy7 autonomous inference로 연결된 완료 시스템은 아니다.**

주요 config의 원본은 [indy7.yaml](../ros2_ws/src/capstone_teleop/config/indy7.yaml), [mark7.yaml](../ros2_ws/src/capstone_mark7/config/mark7.yaml), [camera config](../ros2_ws/src/capstone_teleop/config/camera_rgb_only.yaml), [serial registry](../ros2_ws/src/capstone_teleop/config/rgb_cameras.yaml).

- QP: 기본/max 5 mm/tick, min/step 0.25 mm/tick, angular 0.3 rad/s, joint 45°/s, soft margin 15°. MotionProfile은 radial response + acceleration 제한(600 mm/s², yaw 1.5 rad/s²).
- TELE_OP: smooth 0.5 / cutoff 8.0 / error gain 10.0. Home 목표 degree `[0.005221,40.003174,-129.996640,90.000870,0.000435,90.507]`, ratio 18/100. **현재 이동 금지**.
- Mark7: open `[-10,-10,-10,-10,-10,0]`, grasp `[0,350,350,350,350,0]`, speed/current raw 50/100.
- Cameras: RGB only 640×480×30, initial_reset=false; manual exposure/gain 기본 50/16, serial 844…은 300/64; white balance 4600. Serial별 shelf/table 역할은 실물 mount 확인 전 미확정.
- OMY는 별도 wrapper/Docker/remote robot 경로. host 설정 `10.42.0.101`, domain 30, `rmw_zenoh_cpp`, `omy_ai.launch.py`; 현재 Indy recorder와 통합 검증되지 않았다.
- **설계만 검토:** single-owner DCP redesign. 현재 적용하지 않았으며 dual-client 구조를 유지한다.

## 6. Implemented / Verified

`CODE VERIFIED`는 정적 확인인지 실행 test인지 명시한다. 과거 실물 정상 동작은 현재 변경된 전체 stack의 안전성을 인증하지 않는다.

| 항목 | 확인 수준 / 시점 / 범위 |
|---|---|
| 과거 manual stack | **REAL ROBOT VERIFIED — 과거 기록**: README와 2026-10-02 이력의 driver, XYZ/RxRyRz QP, ARM/AUTO ARM, Home/re-arm, Mark7, speed/GUI/shutdown. 이번 재실행 없음 |
| 복원 control + ACT publishers | **CODE VERIFIED — 2026-10-09 정적 비교**: minseo 대비 `qp_core.py`, `input_profile.py`, `indy7.yaml` 동일. `qp_teleop.py` 차이는 publish-only 추가. **현재 HEAD 대비 diff 전체가 publish-only라는 뜻은 아님** |
| Offline QP/input tests | **CODE VERIFIED — 저장 결과**: `ros2_ws/build/capstone_teleop/pytest.xml`, 2026-10-09 01:24:12, 12 tests / errors 0 / failures 0 / skipped 0. 이번 재실행 없음 |
| Teleop package build | **BUILD VERIFIED — 저장 결과**: `ros2_ws/log/build_2026-10-09_01-23-44/logger_all.log`, capstone_teleop 한 패키지 성공. 최신 전체 workspace build로 확대하지 않음 |
| Recorder / camera / training wrappers | **CODE VERIFIED — 정적 소스 확인**. 실제 Indy dataset 저장·학습·배포의 end-to-end 검증은 미확인 |
| 두 camera 동시 영상 | 과거 ACT_SETUP의 image 수신 관측. 안정적인 FPS/no-frame-drop/synchronization 검증 완료가 아님 |
| J6 read-only 진단 | **REAL ROBOT VERIFIED — handoff의 기존 관측**: DCP/PDO/CONty 및 power-cycle 결과. 이는 복구 성공·motion 검증이 아님 |
| 현 문서 작성 세션 | 로컬 read-only 확인 및 문서 구조/링크/변경 범위 검증만 수행. Test/build/로봇 호출 없음 |

저장된 최근 package build는 `--symlink-install`을 사용했지만 이는 capstone_teleop 한정 과거 명령이다. 공식 Indy를 포함한 빌드는 README/bootstrap의 일반 `colcon build` 원칙을 따른다.

## 7. Current Known Issues

1. **P0 — J6 persistent abnormal position:** motion 불가. 상세 [INDY7_J6_FAULT_HANDOFF.md](INDY7_J6_FAULT_HANDOFF.md), 최신 추가 증거는 §11.
2. **현재 복원 stack 실물 재검증 미완료:** 코드/과거 build 통과를 실제 로봇 정상화로 취급하지 않는다.
3. **수집·학습 산출물 미확인:** 현재 workspace에 dataset/output/checkpoint 경로가 없고 108행 수행표의 목표/실측/결과 칸은 비어 있다. 다른 경로·과거 수행 여부까지 부정하는 것은 아니다.
4. **ACT 데이터 계약 불일치:** `rgb_act_backend.py`는 camera 1개만 허용; 현재 recorder는 2개. `run_train.py` baseline은 joint들을 0으로 예측하고 gripper만 복사하며 XYZ delta용 설명을 유지. `evaluate_overfit.py`는 gripper index 3, 현재 데이터는 6. Pool server에는 10D warmup/없는 model registry 가정이 남아 있다. 학습 지표·실기 배포 전에 별도 수정/검증 필요.
5. **Camera/timing:** USB2 관측 카메라의 delivery 안정성, 실제 역할/고정 mount, latest-value sampling의 시간차·action freshness를 pilot 전에 검증해야 한다.
6. **문서 차이:** README의 recorder 추후 구현 문구는 오래됨. Low-pass 설명도 현재 MotionProfile 정적 구현과 다름. ACT_SETUP/README의 정상 운용 launch 안내는 §9 제한에 종속된다.
7. **재현성 차이:** `ai/train/configs/README.md`가 가리키는 `docs/experiment_design.md`, `scripts/train/run_main_matrix.sh`가 없다. 12개 config가 현재 Indy 실험 완료를 의미하지 않는다. `UPSTREAM.md`의 committed 표현과 달리 현재 parent repo의 `ai/`는 untracked; editable direct_url metadata의 reference 경로도 오래됨(실제 import는 현재 repo).

## 8. Important Decisions

- 공식 external Indy source는 capstone 기능을 위해 기본적으로 수정하지 않는다. 프로젝트 기능은 자체 패키지에 둔다. [README](../README.md)
- Reference project는 필요한 동작과 구조의 근거로 사용하며 임의로 전체 기능을 도입하지 않는다. **이미 수행된 예외적 사실:** minseo control path로 복원했고 ACT publisher만 추가한 비교 결과를 확인했다.
- Dual DCP 구조는 사용자 확인상 과거 실제 정상 teleop에서도 사용됐다. 구조 자체를 J6 fault 원인으로 단정하거나 single-owner 전환을 이미 적용한 것처럼 쓰지 않는다.
- ACT action/gripper publisher는 **publish-only**를 유지한다. Recorder가 robot control lifecycle을 소유하지 않는다.
- ROS `.venv`와 ML `.venv-act`를 분리한다. ROS NumPy 1.26.4를 ML 환경의 NumPy와 무조건 통일하지 않는다.
- 생성 데이터/모델/환경·토큰은 Git에 넣지 않는다. 문서 갱신은 commit/push 허가가 아니다.
- 실행했음 / 코드에 존재함 / reference에 있음 / 계획 중임을 구분한다. 장애 시 시간적 선후관계만으로 ACT를 원인으로 확정하지 않는다.
- **이번 사용자 요청 범위:** `PROJECT_CONTEXT.md`만 생성/수정. 발견한 source/config/기존 문서 문제는 수정하지 않고 Pending으로 남긴다.

## 9. Safety / Do Not Do

**J6 복구 절차 및 실제 기준 위치 검증 전 Indy7 motion/write 금지.**

- actual teleop/move, Home, Zero, Recover, calibration, encoder/multi-turn reset, position/offset write, controller configuration 변경.
- `set_robot_zero_as_current`, `reset_servo`, **`reset_overflow_count`**, `set_servo`, `set_servo_rx`, `set_brake`, servo on/off, 임의 SDO/PDO write, brake release/수동 강제 이동.
- CORE 도구의 factory reset·동기화·calibration·firmware update를 이름만 보고 시도하지 않는다. 타사/일반 CiA402 복구 recipe를 적용하지 않는다.
- **`caps`, `capx`, `capi` 실행 금지:** AUTO ARM 또는 driver write/종료 시 stop 경로가 있다. Driver만 띄우는 것도 read-only 진단이 아니다. `show_camera:=false`도 robot 실행을 막지 않는다.
- Read-only robot 조회가 필요하면 **먼저 API와 목적을 설명**하고 명백한 getter만 사용한다. 기존 진단 로그가 충분하면 재접속하지 않는다.
- OMY `launch`도 실제 초기 이동을 일으킨다. `start`/`robot-stop`/원격 `status`를 이번 프로젝트의 무해한 로컬 진단 명령으로 취급하지 않는다. Indy 장애 조사에 다른 실물 로봇 동작을 끼워 넣지 않는다.
- 현재 QP는 joint-limit 보호를 제공하지만 외부 장애물 충돌회피를 제공하지 않는다.

**Resolved History:** 현재 J6 제한 해제 기록 없음. 추후 해제 시 제조사 절차·실물 검증·사용자 승인 범위와 날짜를 여기에 남긴다.

## 10. Recent Changes

**2026-10-31 — Recorder 한국어 UI / 교정 시연 수집**
- 순차 출력 한국어 화면, `help/guide`, 현황과 요약 이력·세부 JSON 접근을 추가하고 정상 화면의 환경/상세 설정 로그를 제거했다.
- 사용자 `ready` 없이 `s` 준비·검증, `x` 교정 전환·녹화 지속, `r` REVIEW, `o` 유형별 저장으로 정리했다. 일반 성공만 Core 완료로 집계한다.
- Correction은 동일 저장 의도/fsync/파일·timing·receipt/SHA 검증과 reconcile을 사용한다. 과거 Failure 이력/파일/receipt 호환과 저장 실패 시 진행률 롤백을 검증했다. 학습 코드 변경은 없다.
- 최종 오프라인 검증 **120 passed** (108개 전체 합성 정상 저장, Success/Correction 각각의 강제 프로세스 종료 복구 포함). bash 문법·Python 문법·공백 검사 및 기존 관측/영상/데이터 계약 함수 AST 대조 통과. 정상 코덱 상세 로그만 제한하고 경고·오류는 유지한다. 실제 장비·Pilot·기존 수집 파일을 사용하거나 수정하지 않았다.

**2026-10-31 — Core-108 수집 관리 추가 (오프라인 구현)**
- `ai/record/collection.py`, Recorder opt-in 통합, 수집 사용 가이드와 합성 저장/장애 복구 테스트를 추가했다.
- 원본 조건표: 108 unique IDs/orders, Region별 36개, 18 Region × Yaw 각각 6 Depth가 하나씩이다. 조건표 문서는 수정하지 않았다.
- 기존 성공/실패 저장 및 시간 정합성 경로를 재사용하며 Xbox/QP/Mark7 제어·QoS와 Pilot은 수정하지 않았다. 실제 장비 검증·전원 차단 실험은 수행하지 않았다.
- 최종 검증: 수집 관리+기존 Recorder/PendingStart **98 passed** (108개 합성 성공 저장 포함), 기존 Mark7/QP/Xbox 입력 **20 passed**, 문법·공백 검사 통과. 강제 프로세스 종료 후 재실행 복구는 합성 데이터로 검증했으며 실제 PC 전원 차단 시험은 아니다.

**2026-10-09 — Context 최초 작성 (이번 작업)**
- 기존 코드·config·git 상태·저장 검증 결과 및 앞선 공식 복구 조사 내용을 통합. 정적/과거 실물 검증과 계획을 분리.
- 검증: 필수 17개 section, local 문서 링크, source/config 변경 없음 확인. Test/build/로봇 실행 없음.

**2026-10-09 — J6 공식 복구 자료 조사 (앞선 작업)**
- LiveUSB/CORE Setting service 경로 확인, SDK의 미설명 overflow reset과 SDO 용도 차이 추가 발견.
- 검증: Neuromeka 공식 문서·공식 SDK 소스 읽기. 로봇 write/복구 실행 없음. 세부 handoff는 이 추가 조사 이전 상태이므로 §11의 갱신 기록을 함께 읽는다.

**2026-10-09 — ACT 준비·teleop 복원·J6 진단 (기존 handoff 기록)**
- Recorder/publish-only topics, 환경 분리, 두 camera launch/wrappers가 로컬에 구현됨. 수집 계획·108행 shuffle template 작성.
- Teleop은 minseo control 기준으로 복원. Offline 12 tests와 package build 기록, SDK 두 버전 및 PDO getter 진단 있음.
- 결과: J6 이상 지속, 복원 stack 실제 운용은 하지 않음. 각 변경의 정확한 구현 시각은 미확인; 이 날짜는 handoff/검증 기준일.

**2026-10-02 — 수동 stack 기준 commit**
- HEAD `a1b5e27` 및 앞선 `1bb6893`, `368a3a6`에 manual teleop/Mark7/GUI 정리 이력.
- 검증: README에 과거 실물 검증 기록. 현재 미커밋 변경 전체의 실물 인증으로 승격하지 않는다.

## 11. Current Investigation / Evidence

**FACT — 기존 실제 관측:** CONty J6 약 `-52677°`; DCP q/qdes 약 `-52678.1°`; safety ±215°; EtherCAT actual/target `969130066`; 다른 축 raw는 대략 수만 count. Power cycle 후 지속, SDK 3.3.2.3/3.4.2.2 일치. 당시 servo off / brake active, J6 errorcode=0. 상세·관측 시점 차이는 [handoff](INDY7_J6_FAULT_HANDOFF.md).

**INFERENCE:** encoder multi-turn/동기화 또는 CORE의 지속 위치 처리 이상이 가장 유력. **UNKNOWN:** 배터리 방전, corruption/overflow, configuration·통신 문제 중 실제 원인; 실제 encoder vendor/model; 물리 J6 slave 번호. 큰 raw 숫자는 특정 fault code가 아니다.

**인수인계 이후 판단 갱신 — 2026-10-09**

- **Previous assessment:** handoff §1/§15에서 LiveUSB/CORE service 복구 경로 미확인.
- **Updated assessment:** 공식 [CORE Setting using LiveUSB](http://docs.neuromeka.com/3.5.0/en/CORE/section5/)에 잘못된 encoder 위치를 다루는 service 앱 존재. 도구는 `MasterCtrl.sh`, `CORESetting.sh`; 특정 CORE 선택 기능도 설명됨. 현재 serial/FW 호환성·J6만의 영향 범위·robot-zero 복원은 미확정.
- **Reason:** 공식 문서 3.2/3.5 본문과 GUI 확인. 배터리 교체 후 multi-turn chip 오류 해제 및 single/multi-turn 재동기화가 문서화됨. 이를 모든 잘못된 위치에 그대로 적용하는 처방으로 해석하지 않는다.

추가 사실:

- 공식 CORE 자료는 배터리 기반 multi-turn 유지를 설명한다. 오류표에는 배터리 부족 `FF10`, encoder error `FF11`, CRC/configuration `FF12`, 단선 `FF13`, 자동 동기화 후 power reset `FF14`가 있음. **현재 J6에서 관측한 코드가 아님**. 적용 FW 범위 확인 필요. [공식 오류표](http://docs.neuromeka.com/3.5.0/en/CORE/section3/#error_code-object-0x603f)
- SDK `reset_servo`는 servo error reset, zero setter는 현 위치 zero 지정. **새 발견** `reset_overflow_count()`는 축 인자 없이 overflow count를 reset/save한다고만 설명. 대상/범위 미확인, 전부 실행하지 않음. Zero getter 원본 주석 `Agri CamRobot`도 Indy7 적용 확인이 필요한 이유. [공식 SDK 고정 commit](https://github.com/neuromeka-robotics/neuromeka-package/blob/d33d027fd1b2b8378b3ffdf312f334a9657524f8/python/neuromeka/ecat.py)
- 공식 SDO 표에서 표준 `home_offset` 이름은 온도, `homing_method`는 calibration, 일부 object는 내부 제어용으로 재사용됨. 추측 SDO 금지의 직접 근거. [Complete SDO List](http://docs.neuromeka.com/3.5.0/en/resources/CORE/Neuromeka_CompleteSDO_List.pdf)
- **현재 terminal 결론 C:** 이 장비에 안전한 복구 명령과 영점 복원 전체 절차는 제조사 필요. 공식 도구를 확보하는 B 경로의 존재는 확인됐으나 바로 실행할 수 있다는 뜻은 아님.

Raw 보존 경로: `/tmp/indy7_j6_readonly_diagnostic.txt`, `/tmp/indy_sdk_3_3_2_3.json`, `/tmp/indy_sdk_3_4_2_2.json`, `/tmp/proto_3_3_2_3.json`, `/tmp/proto_3_4_2_2.json`, `/tmp/indy_keyword_audit.txt`. 전체 로그를 이 문서에 복사하지 않는다.

## 12. Ruled Out / Lower Priority Hypotheses

- Python/protobuf decoding regression: 두 SDK의 동일 q/qdes·field layout 및 PDO 관측으로 사실상 배제.
- ROS JointState 단위 변환: ROS 이전 raw PDO와 CONty에도 이상이 있어 원인 우선순위 낮음.
- LeRobot recorder, action/gripper publisher가 J6 target을 생성·변조했다는 가설: 구독/publish-only 코드와 기록에서 근거 없음.
- 현재 qp_teleop이 계속 이상을 만든다는 가설: 복원 stack을 실행하지 않은 상태에서도 이상 지속.
- ACT 과정의 encoder zero/calibration/SDO write: 실행 흔적을 찾지 못함. **API 존재는 실행 증거가 아님**, 흔적 부재가 모든 미관측 사건의 부재 증명은 아님.
- Dual DCP client 자체: 과거 정상 사용 사실이 있어 직접 원인으로 단정 금지.
- 단순 transient RAM/TELE_OP latch: 완전 power cycle 후 지속하여 우선순위 낮음.
- **남은 UNKNOWN:** 최초 teleop/mode transition이 fault를 촉발했는지. `target==actual`만으로 과거 teleop 명령이라고 단정하지 않음.
- Zero getter의 빈/default 0, SDK3.4 `is_robot_connected=false`/`calibrated=false` 등은 실제 zero·연결 단절·미보정 증거로 단정하지 않음. 약 40.2는 position이 아니라 과거 temperature 정정값.

## 13. Pending Tasks

**최신 Recorder 추가 확인:** 소프트웨어 검증과 실물 검증을 구분한다. SSH stdin 수신 시각은 키 누름/물리적 실패 시각이 아니다. 실제 입력·디스크 부하에서의 타이밍과 운영 흐름은 승인된 실물 조건에서 별도 확인해야 한다. 교정 전 구간까지 전체 Episode를 보존하며, 교정 마커를 이용한 학습/구간 처리·모델 변경은 이번 구현 범위 밖이다.

**2026-10-31 추가:** 정식 수집 관리의 소프트웨어 경로를 구현했다. [사용 가이드](../ai/record/COLLECTION.md)의 장부 대조/복구 절차를 사용한다. 실제 feasible workspace·yaw와 좌표계 검증, 기존 장비 상태/운용 제한 확인, 승인된 실제 20FPS 수집 부하·시작 검증은 여전히 별도 필요하다. Mark7의 실제 초기 상태 조회 미확인을 임의 초기값으로 우회하지 않는다. 기존 2026-10-09 TODO는 해당 시점의 상태이며 이번 구현은 실물 제한을 해제하지 않는다.

| 순위 / 상태 | 다음 작업 / 완료 조건 |
|---|---|
| P0 **TODO** | 사용자와 제조사 문의 진행: §14 자료 요청. 문의 초안은 존재하지만 이 대화에서 전송하지 않았음; 외부 전송은 명시적 요청 후 수행 |
| P0 **WAITING FOR VENDOR — 자료 필요, 접수 미확인** | Serial/FW별 service package, J6-only 절차, backup/rollback 및 physical-zero/robot-zero 복원 지침 확보 |
| P0 **BLOCKED** | 위 절차로 실제 정비·복구 후 physical pose/CONty/DCP/PDO 일치와 지속성 검증. 현재 조사 권한으로 실행하지 않음 |
| P1 **READY — 별도 source 변경 작업** | ACT absolute 7D·2-camera 계약에 맞춰 baseline/evaluation/serving 및 없는 실험 참조 정리. 현재 문서 작업에서는 수정하지 않음 |
| P1 **BLOCKED** | J6 제한 해제 근거 기록 후 restored teleop + publish-only 경로를 승인된 실물 조건으로 검증 |
| P1 **TODO / 물리 pilot는 BLOCKED** | Camera serial-role/mount, USB3 delivery, 시간차/action freshness 검증 계획 확정 |
| P2 **BLOCKED** | 5~10 pilot 및 dataset 무결성 확인 → feasible workspace/yaw 확정 → shuffle된 108 successful episodes 기록 |
| P2 **BLOCKED** | 실제 dataset으로 ACT 학습/평가, 약 60 unseen pose 및 실패 영역 보강. 모델·dataset의 출처/검증 범위 기록 |
| P3 **TODO — 별도 문서 작업** | README/ACT_SETUP/handoff의 오래된 표현과 editable metadata 차이 정리. 이번에는 이 context에 차이만 기록 |

## 14. External Dependencies / Waiting

**Neuromeka에 받아야 할 것:**

- Serial `PD47I7E0D002`의 J6 CORE/encoder 모델, board revision, 배터리 사양, slave 매핑 및 firmware별 오류표.
- 호환 LiveUSB/image와 CORE Setting tool 버전, 설치 로봇에서의 service 연결·master 전환·전원/브레이크 조건, 한 축만 처리하는 방법.
- Chip 정보/상태/동기화 및 error/CRC 이력을 확보하는 승인된 read-only 방법; `969130066` 관련 알려진 문제 유무.
- 상태별 encoder 처치, 부품 교체 판단, 변경 전 backup, 실패 시 rollback, 물리 기준 자세/지그와 robot-zero/factory calibration 복원 순서.
- `ResetOverflowCount`의 대상·저장 위치·축 범위, `ResetServo`의 encoder 영향, Indy7 zero API 지원 및 default 0 의미.
- CLI/SDO 복구를 권한다면 정확한 도구·버전·인자 또는 해당 FW의 공식 정의와 검증 조건.

**상태:** 제조사 문의 전송/접수/답변, service image 수령, 부품 주문·도착 모두 확인된 기록 없음. 실제 진행되면 날짜·요청/답변 근거·영향을 갱신한다. OMY/D405 관련 reference 정보는 현 Indy 복구 의존성으로 확정되지 않았다.

## 15. Useful Commands

작업 디렉터리에서 실행하는 **로컬 읽기 전용** 명령이다. 실제 robot getter, launch, build/설치, 데이터 삭제 명령은 자동 실행 목록에 넣지 않는다.

```bash
cd /home/sirlab2/sirlab258
cat docs/PROJECT_CONTEXT.md
cat docs/INDY7_J6_FAULT_HANDOFF.md
git status --short --branch
git diff --stat
git diff --cached --stat
git log -5 --oneline
git submodule status
cat requirements.txt
cat .venv/pyvenv.cfg .venv-act/pyvenv.cfg
cat ros2_ws/build/capstone_teleop/pytest.xml
```

파일 존재는 먼저 확인한다. 전체 diff는 관련 경로로 한정해 읽는다. `scripts/diagnose_indy7_j6_readonly.py`는 기존 getter 진단 코드지만 네트워크 조회이므로 §9의 사전 설명 규칙을 따른다. README/ACT_SETUP의 명령을 안전 제한 확인 없이 복사 실행하지 않는다.

## 16. Related Documents

- [J6 상세 장애 인수인계](INDY7_J6_FAULT_HANDOFF.md): 원 진단·복원·실행 여부·로그. **공식 LiveUSB 조사 이전 문구는 이 context §11이 갱신한다.**
- [ACT_SETUP](../ACT_SETUP.md): 환경/recorder/training 사용법. 현재 motion 금지 우선.
- [README](../README.md): 수동 stack·OMY·과거 물리 검증. §7의 오래된 내용에 주의.
- [ACT 수집 계획](ACT_Data_Collection_Plan.md): 목표/coverage/pilot/evaluation 계획.
- [108 Episode 수행표](ACT_108_Episode_Execution_Log_Template.md): shuffle된 계획, 완료 데이터가 아님.
- [Vendored upstream 기록](../ai/lerobot_source/UPSTREAM.md), [기존 training config 설명](../ai/train/configs/README.md): provenance/기존 가정 참고; 현 git 상태·Indy 계약과 구분.

## 17. Context Update History

- **2026-10-09 — 최초 생성:** 사용자 지정 17개 section과 시작/중간/종료 갱신 규칙 반영. 현재 repository·환경·저장 결과를 읽기 전용으로 확인하고 앞선 J6 공식 조사 추가 증거를 보존. README/handoff 차이, ACT 미완성 연결, 미전송 제조사 문의를 명시.
- **최초 작성 검증 범위:** 이 문서 외 기존 640개 non-ignored 파일의 내용 및 staged/unstaged diff를 전후 대조. Source/config/기존 docs 수정 없음; 새 test/build/실물 조회·동작 없음; commit/push 없음.

> IP update (2026-10-29): `192.168.1.114` is the current connection target assigned by DHCP; the device has not yet been confirmed as the Indy7 controller.
