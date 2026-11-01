# ACT ROS 통합 1단계: 관측 전용 준비와 검증 순서

이번 단계는 A(장비 없는 검사)만 실행했다. B 명령은 **사용자가 별도로 장비/관측 실행을 승인한 뒤** 실행한다. C는 이번 범위 밖이다. ACT Real/Task/Home 잠금은 유지한다.

## A. 장비가 꺼져 있어도 가능한 검사

프로젝트 루트에서 환경 import만 검사할 수 있다. 아래는 rclpy.init(), Node 생성, DDS discovery, SDK/serial 연결을 하지 않는다.

```bash
cd /home/sirlab2/sirlab258
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import rclpy, numpy, zmq
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import Float32
from ai.serve.observations import RosSource
assert not rclpy.ok()
print('ROS client imports OK; ROS not initialized')
PY
```

`./act infer --help`는 파서 도움말만 출력한다. 비구동 회귀는 다음과 같다. 전체 suite에는 기존 **Synthetic Dataset 생성 및 소수-step 학습**이 포함되며 실제 수집 Dataset은 사용하지 않는다.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv-act/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp tmp/act-ros-readiness ai/serve/test/test_ros_readiness.py
```

## 확인한 관측 계약

| 입력 | 토픽/메시지 | 추론 구독 QoS | 확인할 값 |
|---|---|---|---|
| Camera A | `/camera_317222074298/camera/color/image_raw`, sensor_msgs/Image | KEEP_LAST 5, BEST_EFFORT, VOLATILE | 640×480, rgb8/bgr8, 유효 step/data, 약30FPS |
| Camera B | `/camera_844212071939/camera/color/image_raw`, sensor_msgs/Image | 동일 | 동일 |
| Indy7 | `/joint_states`, sensor_msgs/JointState | 동일 | joint0~joint5를 이름으로 재정렬, 선택6D 유한값, rad |
| Gripper | `/capstone/gripper_state`, std_msgs/Float32 | KEEP_LAST 10, RELIABLE, VOLATILE | 0=Open, 1=Grasp 성공 명령 상태, Header 없음 |

- 최종 Observation은 이미지 두 개와 `[joint0..joint5, gripper]` float32[7]이다. BGR은 RGB로 변환하고 잘못된 크기·이름·값은 거부한다. JointState의 입력 순서가 다르더라도 이름으로 재정렬하며 수신 이름/position 개수도 진단에 남긴다.
- 공식 Driver는 `PUBLISH_RATE=20`, publisher RELIABLE depth10, JointState position/velocity는 SDK deg→rad 변환이다. Header는 `get_control_data()` **직전**의 ROS now로, 실제 엔코더 계측 시각을 보장하지 않는다.
- 카메라 Launch는 두 serial을 namespace에 매핑하고 직접 realsense2_camera_node를 시작한다. RGB 640x480x30, depth/IR off, initial_reset=false다. A exposure/gain=50/16, B=300/64, WB=4600/manual 설정이다. 실제 적용 여부/단위/노출 효과는 장비에서 확인한다.
- 팀 카메라 config에는 publisher QoS/global_time을 명시하지 않는다. 설치된 `rs_launch.py`의 기본값을 이 직접 Node Launch에 그대로 적용됐다고 간주하지 않는다. 실제 endpoint QoS와 timestamp 기준은 B에서 확인해야 한다. BEST_EFFORT 구독은 BEST_EFFORT/RELIABLE publisher를 모두 받을 수 있지만 DDS/RMW/네트워크 호환성은 실측이 필요하다.
- Recorder는 동일한 BoundedHistory/HistoryAligner/SyncOptions를 재사용한다. 단, 기존 Recorder 구독은 카메라 RELIABLE depth5, state/action RELIABLE depth20이므로 추론 구독과 QoS가 같지는 않다. 실제 카메라 publisher가 BEST_EFFORT라면 Recorder의 RELIABLE 요청은 호환되지 않을 수 있다. 이번에는 Recorder QoS를 변경하지 않았다.
- Gripper는 현재 작업 트리의 QP Publisher와 Recorder Subscriber가 모두
  RELIABLE/TRANSIENT_LOCAL/depth=1이다. 위 표의 추론 `RosSource` 구독은
  RELIABLE/VOLATILE/depth=10으로 별개다. 소스 설정을 대조한 것이며 실제 DDS
  endpoint 매칭·retained sample 수신을 검증한 결과는 아니다.

## Gripper 초기 상태의 현재 차단 조건

`mark7_node.py`는 서비스만 제공하고 `/capstone/gripper_state`를 발행하지 않는다.
QP의 `gripper_command_done()`이 Open/Grasp 서비스 성공 때에만 Float32를 발행한다.
QP 내부 초기값0은 ROS로 발행되지 않는다. 현재 QP/Recorder는 TRANSIENT_LOCAL이며,
살아 있는 QP Publisher가 이미 발행한 마지막 명령 상태를 Recorder에 보관 전달하도록
설정되어 있다. 과거 명령이 없거나 Publisher가 재시작했다면 확인된 초기값이 생기지 않는다.
추론의 VOLATILE 구독에는 같은 과거 메시지 전달을 가정하지 않는다.

따라서 **장비·Publisher를 새로 시작하고 명령을 한 번도 보내지 않은 상태에서 실제
Gripper 위치를 읽는 경로는 확인되지 않았다.** 명령 상태가 없으면 기본 Dry Run은
`unknown_initial_gripper`로 준비 실패하며 Action을 생성/소비하지 않는다. 임의의0/1
발행이나 Open/Grasp 호출로 우회하지 않는다. 현재 TRANSIENT_LOCAL 설정도 실제 센서
피드백을 만들지 않는다. QP 시작은 auto-arm 경로가 있으므로 상태 조회 해결책이 아니다.

Historical: 이전 조사에서 QP Publisher도 VOLATILE이었다는 설명이 있었다.
현재 소스와의 차이는 위와 같으며, [Mark7 피드백 조사](MARK7_STATE_FEEDBACK_INVESTIGATION.md)의
물리 상태 조회 미확인 결론은 그대로다. 이번 호환성 수정에서는 QoS 코드를 바꾸지 않는다.

ROS Dry Run에서는 작업자가 `--operator-initial-gripper 0`(Open) 또는 `1`(Grasp)을 명시해 초기 가정으로 사용할 수 있다. 이 값은 **operator_assumed**이며 센서 측정·명령 성공 이력이 아니다. ROS에 발행하거나 수신 History 이벤트로 삽입하지 않는다. 옵션을 생략하면 기존 unknown 차단을 유지한다. Synthetic/Mock/Real 실행과 Recorder/Dataset 수집에는 이 옵션을 사용할 수 없다.

실제 상태 메시지가 수신되면 기존 수신 시각 기반 인과적 선택·유지·검증 정책을 적용한다. 기준 시각 이후 도착한 이벤트를 과거 관측에 즉시 붙이지 않는다. 잘못된 실제 메시지는 가정값으로 숨기지 않고 기존처럼 중단한다. 시간 제한은 바꾸지 않는다. QP·Mark7 인터페이스는 변경하지 않았다.

## 시간 정렬·진단의 의미

- 고정20Hz 기준 시각, 수신 여유60ms, camera age50ms/state75ms, camera skew40ms/전체75ms, clock guard10ms, History32개/0.5초로 Recorder와 같은 선택기를 사용한다. 후보 기본값이며 허용값을 느슨하게 바꿔 실패를 숨기지 않는다.
- 이전 측정 A29.98FPS/33.2ms, B29.76FPS/36.2ms, state20Hz/약1.5ms는 **이전 기록**이다. 이번에 최신 실측하지 않았다. Header를 센서 노출 시각으로 단정하지 않는다.
- 최초 ROS 구독 생성 시 Unix 경계를 잡고, 시작 이전 Header는 초기 준비 중 폐기한다. 로그에 `pre_subscription_header_discarded`를 남긴다. 새로운 생성 시각의 데이터가 들어온 뒤 기존 age/skew 검사를 통과해야 시작한다. Header 없는 Gripper는 수신 시각을 사용한다. DDS 큐를 직접 flush하거나 외부 publisher를 재시작하지 않는다.
- 실행 중의 오래된 Header/역전은 이 초기 폐기 규칙으로 숨기지 않으며 공유 History 무결성 검사에서 거부한다. use_sim_time, ROS/system 불일치, system/monotonic 경과 불일치, future/reversed/zero Header는 정상 데이터로 취급하지 않는다. Header와 monotonic을 직접 빼지 않는다.
- 수신 중단은 last_receive_age_ms와 기존 causal sample의 age 검사를 통해 드러난다. Gripper는 이벤트이므로 메시지 빈도가 낮다는 이유만으로 만료시키지 않는다. 이 토픽만으로 Gripper 통신 생존/물리 상태를 증명할 수 없다.
- callback 진단은 이미지 사본을 보유하지 않고 각 수치 최근2048개만 유지한다. FPS는 **수신 callback rate**이며 Header 간격·중복·gap과 함께 봐야 한다. gap 건수는 실제 누락 수의 확정치가 아니다. clock 검사를 통과한 경우에만 `callback_system_minus_header_ms`를 집계한다.
- Header 시차는 추가로 History 보관 범위 안의 과거 Header인 경우만 집계한다. 상대적인 장치 clock·아주 오래된 backlog·미래 Header는 정상 전송 지연으로 표시하지 않고 `header_delay_unavailable`로 구분한다. 이것은 epoch 호환성 sanity 검사이며 같은 clock이라는 증명이나 clock 변환이 아니다. 실제 장치 timestamp 설정 확인은 여전히 필요하다.

## B. 별도 승인 후 실제 관측을 읽는 순서

### B0. 첫 명령: 기존 ROS graph 확인

제어기·카메라 전원을 켰다는 사실만으로 ROS publisher가 생기지는 않는다. 이번 준비는 Driver/카메라를 자동으로 시작하거나 종료하지 않는다. 아래 첫 조회는 장치/서비스 명령이 아니라 ROS discovery다.

```bash
cd /home/sirlab2/sirlab258
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
ros2 topic list -t --no-daemon --spin-time 2
```

토픽이 없으면 중단하고 **장치 Launch를 별도로 검토·승인**한다. `./caps`, setup.launch.py, xbox.launch.py를 관측용 시작 명령으로 사용하지 않는다. 이미 실행 중인 Driver도 이 절차에서 재시작/종료하지 않는다. ROS_DOMAIN_ID/RMW_IMPLEMENTATION은 기존 장치 환경과 일치하는지 확인하고 시스템 전역 설정을 바꾸지 않는다.

### B1. endpoint/QoS와 메시지 확인

```bash
ros2 topic info -v /camera_317222074298/camera/color/image_raw --no-daemon
ros2 topic info -v /camera_844212071939/camera/color/image_raw --no-daemon
ros2 topic info -v /joint_states --no-daemon
ros2 topic info -v /capstone/gripper_state --no-daemon

timeout 10 ros2 topic echo /joint_states sensor_msgs/msg/JointState \
  --once --qos-reliability best_effort --no-daemon
timeout 10 ros2 topic echo /camera_317222074298/camera/color/image_raw sensor_msgs/msg/Image \
  --once --no-arr --qos-reliability best_effort --no-daemon
timeout 10 ros2 topic echo /camera_844212071939/camera/color/image_raw sensor_msgs/msg/Image \
  --once --no-arr --qos-reliability best_effort --no-daemon
timeout 10 ros2 topic echo /capstone/gripper_state std_msgs/msg/Float32 \
  --once --qos-reliability reliable --no-daemon
```

마지막 명령이 timeout이어도 0/1을 발행해 보충하지 않는다.
위 echo와 추론의 VOLATILE 구독은 과거 이벤트 수신을 가정하지 않는다.
Recorder의 TRANSIENT_LOCAL 구독과 구분하고, 확인된 발행이 없으면 unknown을 유지한다.

FPS 보조 확인은 아래를 한 명령씩 실행한다. timeout 종료코드124는 측정 구간 종료다. 고해상도 영상의 CLI 구독 자체도 부하가 되므로 동시에 실행하지 않는다.

```bash
timeout 10 ros2 topic hz /camera_317222074298/camera/color/image_raw --window 100 --wall-time
timeout 10 ros2 topic hz /camera_844212071939/camera/color/image_raw --window 100 --wall-time
timeout 10 ros2 topic hz /joint_states --window 100 --wall-time
```

### B2. 기존 ACT ROS Dry Run

첫 인터페이스 확인에는 보존된 Synthetic Checkpoint를 사용할 수 있다. hold/open 시험용이며 물체 조작 능력이 있다고 판단하지 않는다. 운영 평가에서는 실제 학습 Checkpoint로 바꾼다.

```bash
ACT_ROS_CHECKPOINT=outputs/temporary/verification/act_final_offline_20261031T025706Z_09a8f399/integration_final/train_cuda/training/checkpoints/000003/pretrained_model
./act infer --checkpoint "$ACT_ROS_CHECKPOINT" --source ros --dry-run \
  --device auto --duration 10 --observation-startup-timeout 10
```

작업자가 초기 Open 상태를 **가정**하는 이번 진단에서는 위 명령에
`--operator-initial-gripper 0`을 추가한다(Grasp 가정은1). 이 선택은 로봇/Gripper에
명령을 보내지 않으며 실제 상태를 입증하지 않는다. 실제 Gripper 상태가 있어야만
하는 Recorder/수집/Real 동작의 준비 조건을 충족시키지 않는다.

기본으로 새 outputs/act_infer_<ID>를 만든다. 지정할 때는 미존재 --output-dir를 사용한다. server 시작/Checkpoint 검증은 .venv-act, ROS 구독 client는 ROS 환경을 source한 .venv의 별도 프로세스다. GPU 대기/통신은 ROS callback thread를 막지 않는다.

실제 상태와 작업자 가정 모두 없으면 Gripper 초기 상태가 unknown이므로 준비는 실패한다. 가정 옵션 사용 시 Camera/Joint는 실제 수신, Gripper는 작업자 가정임을 구분해 보고한다. 기존 aligner→processor→ACT→chunk→20Hz log-only Scheduler를 사용하며 입력 Stale/clock 이상/timeout/deadline 누락에서는 Queue를 폐기하고 중단한다.

진단 파일:

- `client/summary.json`: reception_diagnostics(topic별 FPS, Header 지연 분포, 중복/gap, 마지막 수신 경과, 수신 순서/이미지 형식, Gripper 명령 상태), observation_validation, 추론 P50/P95, Action FPS/deadline, robot_commands_sent=0.
- `client/events.jsonl`: 약1초 주기 reception_diagnostics, observation_not_ready, observation_invalid, 선택한 observation의 age/skew/reuse, prediction/Action/중단 사유.
- `server_ready.json`/`server_events.jsonl`: Checkpoint ID/7D chunk/모델 규격, 출력 검증·추론 오류.
- `safety_validation.evaluated=false / violations=null`: 이 Dry Run은 실기 Safety Guard를 실행하지 않는다. 미검증 상태/실기 제한값을 가정해 위반0으로 표시하지 않는다. 7D shape/finite/기한 검증과 관절·추종·충돌 안전 검증은 별개다.

## Driver/Launch 시작·종료 위험(정적 확인)

- `indy.launch.py` → 공식 `indy_bringup.launch.py` → `indy_driver.py`와 robot_state_publisher(RViz false). Driver context enter→connect는 IndyDCP3/EtherCAT 클라이언트를 생성한다. 단순 ROS 구독이 아닌 SDK 연결이며 이번에는 생성자도 실행하지 않았다.
- 현재 설치된 SDK 생성자 소스는 gRPC channel/stub 생성이며 즉시 Servo ON/Home/teleop 시작 호출은 보이지 않는다. Driver timer는 이후 get_control_data/get_control_state를 호출한다. 이 정적 확인을 실제 접속의 무부작용 보장으로 확대하지 않는다.
- Driver context exit→disconnect는 **stop_teleop()→1초 대기→stop_motion()**을 실행한다. 관측을 끝내려 Driver를 종료해도 제어 상태에 영향을 준다. 관측 클라이언트만 종료한다.
- Driver는 trajectory Topic/Action, indy_srv, set_servo_rx 제어 경로도 공개한다. ROS 구독의 존재만으로 다른 writer가 없음을 증명할 수 없다.
- `setup.launch.py`/`./caps`는 Camera+Indy+Mark7+Xbox/QP를 시작한다. QP는 startup/Home 뒤 auto_arm_pending을 가지며 중립 Joy·신선한 state 등이 확보되면 ARM한다. 관측 준비용으로 실행하지 않는다.
- Mark7 Launch는 serial open/termios 설정 후 서비스를 만든다. 시작 시 Open/Grasp preset 전송은 소스상 보이지 않지만 단순 ROS 구독과 다르므로 이번에는 시작하지 않는다. 종료 시 fd를 닫는다. 성공 응답은 물리 파지 확인이 아니다.

## C. 후속 실동작 검증

Servo/ARM/모드 전환/절대 목표 명령, Mark7 Open/Grasp, 정지 거리, 추종, 하중·선반/바구니 충돌, 프로젝트 Home 경로는 별도 실기 승인 단계다. 이번에는 이 명령을 제공·실행하지 않는다.

`docs/ACT_OFFLINE_READINESS.md`의 상태 provider, 외부 SDK/CONTY를 포함한 독점 명령권, reviewed hardware safety profile, 프로젝트 Home interface, 물리 E-stop/Gripper 확인은 미해결이다. 이번 관측 진단으로 이 조건을 충족했다고 판단하지 않으며 Real 잠금을 유지한다.
