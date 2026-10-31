# ACT 운영 준비도와 실기 차단 조건

이 문서는 소프트웨어 연결 검증과 실제 장비 검증을 분리한다. 최신 실행 ID·검증 수치는
[PROJECT_CONTEXT](PROJECT_CONTEXT.md#검증-범위)를 기준으로 한다. 본문의 CPU/CUDA 설명은 과거 합성 검증 범위이며 최신 CPU 회귀에서 CUDA를 재실행했다는 뜻이 아니다. 오프라인 성공은
실제 Indy7·Mark7 동작 성공이 아니다. 실행 순서는 루트 README의 ACT 운영 순서를
따른다. 설치/build는 장치 실행이 아니며, `./caps`는 실제 장치와 QP를 시작하는
별도의 명시적 명령이다. 이번 검증에서는 실행하지 않는다.

| 영역 | 오프라인 검증 | 남은 확인 |
|---|---|---|
| Recorder | 실제 BoundedHistory/HistoryAligner/EpisodeSession 저장·재로딩, Sidecar, 연속 20Hz 가상 시간축 | 실제 ROS QoS·clock·프레임 수신·장시간 Pending 디스크 I/O |
| Validator | 전체 영상 디코딩·state/action·시간축·success/integrity 검사 | 실제 수집 데이터와 센서 특성 |
| Train/Checkpoint | 실제 LeRobot 0.5.1 소형 ACT 학습·분할·통계·저장·재로딩·추론 | 실제 성공 Demonstration 수집·최종 설정 학습·일반화 |
| 추론/Scheduler | 실제 localhost 서버/클라이언트, CPU/CUDA 2카메라 7D 입력, 20Hz Chunk 소비 | ROS 입력 연결·최종 모델 지연·네트워크/부하 worst case |
| Safety/명령권 | Mock 제한 검사·중단·Queue 폐기·lease/Session 격리 | 실제 제한값·Servo/Ready·정확한 제어 모드·외부 writer 배제 |
| Indy7 | Mock 절대 rad 전달, 공식 Driver 단위 변환 경로 확인 | 실제 SDK 수신/실행 주기·추종·정지 거리·정지 ACK |
| Mark7 | Mock Open/Grasp·중복 억제·비동기 실패/Timeout | 실제 초기 상태·동작·파지/Release·배선/서비스 지연 |
| Task/Home | 사용자 완료·ACT 정지·lease 전환·별도 Mock Home·오류 시 Home 차단 | 프로젝트 Home 이동 인터페이스와 실제 경로/장애물/하중 안전 |
| Evaluation | 기록 연결·held-out 오차·진단·분모/신뢰구간·조건/Checkpoint 비교 | 실제 위치/각도/깊이, 작업자 단계 판정, Real 시행 수 |

## Real ACT/Task/Home는 잠금 상태다

현재 잠금을 설정 몇 개만 바꿔 풀 수 있는 준비 상태로 해석하면 안 된다.
다음 항목은 추가 연동과 독립적인 검증 증거가 필요하다.

1. **신선한 읽기 전용 실제 상태 provider**: 현재 공식 Driver의 `/joint_states`는
   q/qdot이며 모든 Servo/Ready/Fault/E-stop/teleop method/독점 소유권을 증명하지
   않는다. 팀 어댑터가 요구하는 idle OP=5, absolute teleop OP=17/method=10과
   실제 readiness를 확인하는 cached provider는 아직 제공되지 않는다. true
   상수나 오래된 서비스 성공값을 넣는 방식은 증거가 아니다.
2. **모든 제어 채널의 독점권**: QP 시작/Home 후 자동 재무장, 다른 ROS publisher,
   FollowJointTrajectory/MoveIt goal, 직접 SDK, CONTY 등을 배제해야 한다. ROS graph와
   협조적 프로세스의 파일 lock만으로 외부 writer 배제를 보장하지 않는다. Real
   통합은 공유된 하나의 명령권과 한 writer를 가져야 한다.
3. **검증된 안전 profile**: `ai/execute/config/indy7_review_required.json`의 null
   제한값은 의도적으로 실행 불가다. Mock 숫자를 실기 제한값으로 복사하지 않는다.
   관절/속도/가속도/추종/age/deadline/stop 설정은 실제 장비·부하로 검증해야 한다.
   관절 제한 검사는 선반·바구니·책상 충돌 회피나 정지 거리 보장이 아니다.
4. **프로젝트 Home의 단일 제어 경로**: 팀 Home은
   `ros2_ws/src/capstone_teleop/config/indy7.yaml`의 관절 목표와 ratio 18/100이다.
   공식 `/indy_srv data=2`는 컨트롤러 등록 Home을 사용하며 같은 값이라고 입증되지
   않았다. 기존 trajectory 경로는 고정 SDK ratio로 teleop 목표를 처리하고 trajectory
   시간/속도/가속도를 무시한다. 현재 Real 프로젝트 Home은 구현/활성화되지 않았다.
   별도 SDK writer를 추가하지 않고 필요한 목표/ratio/모드/정지 확인 인터페이스를
   제공할 설계와 실기 경로 승인이 필요하다.
5. **실제 Gripper 확인**: Float32 명령 상태와 서비스 성공은 물리 센서 피드백이
   아니다. 초기 상태 불명은 실행 차단 조건이다. 통신 중단 시 이미 전송된 직렬
   명령을 취소하거나 물리 E-stop이 전달됐다고 가정할 수 없다.

`./act execute --mode real`, `./act task --mode real`과 Task/Home API의 잠금은 유지한다.
정상 학습 Checkpoint도 기술적 유효성만 확인하며 로봇 안전/작업 능력을 승인하지 않는다.

## 실제 데이터 수집 전 순서

1. 장비별 수동 복구·정지·E-stop, 작업자 접근 구역, Home 자세/경로를 확인한다.
   QP/ROS/SDK 단일 제어 주체를 확인한 후 수집용 수동 스택을 명시적으로 시작한다.
2. 카메라 두 serial/topic/RGB 640×480, USB3 대역폭, 약 30FPS 수신, 실제 Header
   clock/global_time, `/joint_states` 약 20Hz와 이름/단위를 재확인한다. 카메라 B의
   과거 USB2 연결 이력이 있으므로 실제 연결을 점검한다. Header는 검증 없이 센서
   노출 시각으로 부르지 않는다. use_sim_time/시계 변경은 현 Recorder가 거부한다.
3. `/capstone/teleop_action`이 SDK 정상 반환 후 발행되는 절대 rad 목표임을 확인하고,
   Header 생성과 실제 명령 시작 시점의 차이를 기록한다. 이전 에피소드에서 시작한
   SDK 호출이 다음 시작 후 반환되는 경우를 Header만으로 완전히 구별할 수 없다.
   Recorder는 QP/Home 상태를 제어하지 않는다. 초기화 중 명령/외부 이동을 배제하고
   정지·대기 후 `s`, 안전 후퇴 후 **Home 복귀 전에 `r`**를 지킨다.
4. Gripper Open/Grasp 성공 응답 뒤 명령 상태 수신을 확인하고 소수 pilot을 수집한다.
   정상 시간축/영상/목표값/실제 관절/파지 상태를 사람이 검토한 뒤 `o`로 분류한다.
   Invalid 시간축은 작업 실패가 아니며 성공/실패 Dataset 모두 저장하지 않는다.
5. 파일을 보존한 상태로 Validator 전체 검사를 실행한다. 실제 I/O 부하에서 연속
   프레임·deadline·중복 영상·영상/관절 age/skew와 최종 저장/인코딩을 확인한다.
   이번 합성 데이터의 가상 20Hz Sidecar는 실제 수집 처리 성능 측정이 아니다.
6. feasible 위치/yaw/depth를 pilot에서 확정하고 108 **성공** Demonstration 본수집을
   진행한다. 실패는 별도 보존한다. 학습 후 synthetic/ROS Dry Run, 제어권/정지/충돌
   안전을 포함한 단계적 실기 승인 순서가 필요하다. 실제 작업 평가는 별도 작업자
   기록으로 수행하며 소프트웨어 COMPLETED를 물리 성공으로 대체하지 않는다.

## 오프라인 증거 해석

`ai/test/test_offline_end_to_end.py`는 실제 Recorder 저장 경로로 네 Episode의 합성
Dataset과 Bounded Sidecar를 만들고 공식 ACT 소수 step을 학습한다. Sidecar를 직접
작성하거나 다른 Dataset에서 복사하지 않는다. 정상 정책 fixture는 Home hold/open이며
Grasp/운반/배치/후퇴 능력을 학습한 정책이 아니다. 서비스 전환/고장·누락·정지는 별도
Mock 장애 주입으로 검증한다. CPU/CUDA 수치는 작은 모델·짧은 시행의 측정치다.
최종 크기 모델·장시간·ROS/하드웨어 20Hz 성능으로 일반화하지 않는다.

과거 검증 기록은 `outputs/act_final_offline_*`, 최신 회귀 기록은 Context가 가리키는 격리 tmp에서 확인한다.
합성 원본을 정리한 경우에도 작은 결과·로그·실행 metadata는 보존하며 기존 outputs는 수정하지 않는다. 테스트용 Dataset과
Checkpoint는 `synthetic_dataset`, `train_cpu`, `train_cuda`로 명확히 구분하며 실제
수집/운영 Dataset과 섞지 않는다. 실제 장비 성공률은 작업자 평가 전까지 unknown이다.
