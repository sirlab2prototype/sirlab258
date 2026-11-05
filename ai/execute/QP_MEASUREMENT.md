# QP 계측 (ROS 구독만)

scripts/log_qp_readonly.py는 SDK/서비스/명령 Publisher 없이 기존 토픽을 원래 발행 주기로
구독한다. 파일 I/O는 별도 worker, bounded queue의 put_nowait이며 overflow는 drops에
기록한다. 파일 오류는 logger 자체에만 보고한다. drops/write_error 또는 end 누락이면
완전한 계측으로 사용하지 않는다. 다른 ROS 프로세스는 종료하지 않는다.

기존 Driver joint_states는 q/qdot rad와 tau를 발행한다. header는 센서 sample 시각을
보증하지 않으므로 callback receipt monotonic도 별도 기록한다. QP teleop_action은
movetelej_rel 호출 반환 뒤 발행한 절대 관절 목표 rad이며 Controller 내부 qdes와 다르고,
발행시각은 정확한 SDK 송신 시각/수락 증명이 아니다. action_context/teleop_status와 ROS
publisher graph도 기록하지만 graph는 명령권 보증이 아니다. Servo/Operation/Fault의
완전한 상태와 정확한 stop request/ACK는 현재 이 토픽 집합에 없다. ROS 서비스 요청은
일반 topic 구독으로 관측되지 않는다. 추가 SDK client나 원래 Driver 변경을 하지 않았다.

따라서 이 로거는 속도/표본 가속도/수신기준 Tracking 탐색용으로 준비됐다. 정확한 SDK
송신/정지 소요시간/전체 Servo·Fault 계측의 최종 승인 용도로는 불충분하다. 기존 Driver/QP
내부 stop event가 없는 경우 stop latency는 null로 남는다. 기존 SDK_RPC_timing은 ACT
전용 Driver 계측이며 현재 QP Driver에도 있다고 주장하지 않는다.

## 운영자 실행

기존 ROS 환경을 사용하는 터미널에서 실행한다. 로거는 제어 프로그램을 시작하지 않는다.

```bash
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
.venv/bin/python scripts/log_qp_readonly.py --output tmp/real_site/qp_measurement_01/raw.jsonl
```

초당 queue/drops/write_error 출력이 정상인지 확인한다. 다른 터미널에서 기존 평소 절차로
QP를 실행/사용한다. 로거를 위해 중복 Driver를 launch하지 않는다. 기존에 검증된 동작만
운영자가 조작한다. 정상 정지 후 **로거 터미널에서만 Ctrl+C**로 종료한다.

```bash
.venv-act/bin/python scripts/log_qp_readonly.py \
 --analyze tmp/real_site/qp_measurement_01/raw.jsonl \
 > tmp/real_site/qp_measurement_01/analysis.json
```

reported qdot 최대값, 위치 차분 속도, 가속도 분위수/최대, 최근 수신 명령과의 오차 분위수,
간격 min/max, 명령 age, 누락/end 오류를 본다. 차분 가속도는 관측 노이즈와 timestamp
jitter에 민감하고 센서 peak의 증거가 아니다. Tracking에는 지연 및 정지 후 command hold가
섞이므로 command age와 원문을 함께 검토한다. 이 통계로 Safety Profile을 자동 승인하지 않는다.

Mock 분석1개 통과. ROS 실수신/부하는 아직 검증하지 않았다. 이번 작업에서 ROS/장비는
실행하지 않았다. 전체 정지 계측을 하려면 별도 범위로 기존 QP/Driver의 SDK 호출 지점에
비동기 이벤트 hook을 넣어야 하며 이 구현에는 포함하지 않았다.
