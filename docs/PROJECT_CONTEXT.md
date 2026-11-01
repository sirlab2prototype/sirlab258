# sirlab258 — 현재 상태와 다음 작업

소프트웨어 확인: 2026-11-01. 실물 정보는 과거 기록이며 이번 정리에서는 장비에 접속하지 않았다.
작업 규칙은 [AGENTS](../AGENTS.md), 운영 진입점은 [README](../README.md)를 따른다.

## 구현 기준과 Git

- 검증 구현 보존 기준: `f7d03b93d3044d136fb14d960b38e3e2373398d4`.
- Recorder/Core-108/Correction 기준: `b3bbb3b01b015aa60f769604c80db17fd723fac5`.
- 제거한 Pool의 마지막 미커밋 원문은 `78794cfdebddf7d08d7f3c671027d6bc064509b6`에 먼저 보존했다.
- `main`을 사용한다. 문서·정리 커밋으로 HEAD는 달라질 수 있으므로 매번 status/diff/log를 확인한다.
- 이번 정리는 구형 코드·생성물·문서 정리다. 제어·Topic/QoS·데이터 형식·기록 시간 계약은 변경하지 않는다.

## 목적·환경·구조

선반 칸1의 가변 위치/회전 바구니를 동일 Home에서 파지해 고정 table drop zone으로 옮기는
시연을 수집하고 ACT로 학습·평가하는 팀 캡스톤이다. feasible workspace는 실물 검증 없이 만들지 않는다.

- Ubuntu24.04 / Python3.12 / ROS2 Jazzy, Indy7·Mand.ro Mark7·Xbox·D435 두 대.
- ROS/제어 `.venv`와 ACT `.venv-act` 분리. 로봇 NumPy1.26.4와 ACT NumPy2.2.6을 합치지 않는다.
- vendored LeRobot0.5.1, 과거 환경 기록은 Torch2.10.0+cu130/torchvision0.25.0+cu130이다.
  CUDA 검증 범위와 현재 사용 가능 여부는 별개다. 현재 의존성 원본은 requirements와 vendored pyproject다.
- 카메라 serial: `317222074298`, `844212071939`; 설정은 RGB640×480·30FPS이다.
- Mark7 설정 기본값은 `/dev/ttyACM0`,115200이다. 실제 포트·펌웨어·손가락 위치 피드백 지원은 미확인이다.
- `192.168.1.114`는 launch의 접속 대상 설정이다. 현재 그 주소의 장비 신원을 재확인한 결과가 아니다.
- OMY는 별도 ROBOTIS 경로이며 현재 Indy7 수집과 통합 검증된 장비로 취급하지 않는다.

| 코드 | 현재 역할 |
| --- | --- |
| `capstone_teleop`·`capstone_mark7`, `scripts/bin/*` | 기존 QP·장비 launch·명령 wrapper |
| `ai/record/indy7_act_record.py`, `collection.py` | 수집·조건/Attempt·정상/교정 분리·장부/receipt/reconcile |
| `ai/observation_timing.py`, `dataset_contract.py` | 인과적 관측 선택·20FPS 시간/7D/두 영상 계약 |
| `ai/train/{validate_dataset,run_train,checkpoint}.py` | 검증→Episode 분할→Train 전용 통계→공식 학습→재로딩 검증 |
| `ai/serve/{infer,dry_run,zmq_act_server}.py` | localhost v2 추론 서버·관측·RPC·스케줄러 |
| `ai/execute/{controller,task_executor}.py` | Mock 명령/정지·명령권·Task/Home 상태 관리 |
| `ai/evaluate/main.py` | 오차·실행 신뢰성·별도 작업자 평가 보고서 |

## 수집에서 평가까지

실물 운용 승인 및 장비 상태 확인 → 카메라/상태/QP 목표 → Recorder/Core-108 → Success Dataset →
Validator → ACT 학습/Checkpoint → Synthetic 또는 승인된 ROS 관측 Dry Run → Mock 실행/Task → 평가.
이는 실물 모터까지 ACT로 연결해 검증했다는 뜻이 아니다.

- 일반 `s → r → o`, 교정 `s → x → r → o`; x는 녹화를 계속하고 첫 마커를 유지한다.
- [Recorder GUI](../ai/record/GUI.md)는 Tk 버튼을 동일 엔진에 전달한다. 기존 CLI/wrapper·저장 계약을 유지하며 영상 미리보기·로봇 제어는 없다. GUI 실물/화면 통합 검증은 미완료다.
- 목표는 L/C/R × A1..A6 × 여섯 Depth의 정상 성공108개다. 원본 ID와 Shuffle은 유지한다.
- 조건표는 `docs/ACT_108_Episode_Execution_Log_Template.md`를 코드가 직접 읽는다. 이동·수정 금지.
- `ready`는 제거됐다. s에서 조건·Attempt·선택적 좌표를 확정하고 60ms 시작 검사 후 기록한다.
- 미측정 좌표는 null이다. Home 전에 r로 끝내며 Home 동작을 기록 Action에 자동 추가하지 않는다.
- `datasets/indy7_act_core108/{success,correction,failure,pending}`와 `collection.json`, `receipts/`를 함께 보존한다.
- 정상만 Core 완료에 포함한다. Correction은 미완료를 유지하고 동일 조건의 다음 Attempt를 안내한다.
- 신규 Failure 저장은 없으며 과거 Failure 이력은 읽는다. INVALID 저장 차단·Pending·reconcile을 유지한다.
- 학습 입력은 `datasets/indy7_act_core108/success`다. 독립 모드 기본 `datasets/indy7_act`와 다르다.
- Correction 전체 Episode·교정 시작 프레임/시각은 저장하지만 Correction ACT 학습·혼합·실패 구간 제외는 미구현이다.
- 관절 action은 절대 목표 rad이며 gripper0/1은 성공 응답을 받은 명령 상태다. 실제 파지 센서값이 아니다.

## 검증 범위

| 항목 | 확인 범위 / 근거 | 확대하면 안 되는 범위 |
| --- | --- | --- |
| 과거 수동 제어 | 2026-10-02 등의 실물 운용 이력. [Teleop](TELEOP.md), [J6 인계](INDY7_J6_FAULT_HANDOFF.md) | 최신 전체 stack의 실물 인증 |
| 기존 Recorder 실물 입력 Pilot | 사용자 실측과 `outputs/act_recorder_pilot_20261031/success` 저장 기록: 5 Episode/3264 frames/20FPS. sidecar는 saved·success·integrity valid | 정식108개 완료·최신 Core/Correction·물리적 파지 성공. 이번에는 영상을 재검증하지 않음 |
| 최신 Core-108/Correction | 정리 후 CPU 회귀의120개, 원본108개 합성 저장·중단/부분 저장·reconcile·교정 마커 포함 | 최신 실물 통합 검증은 미완료 |
| ACT CPU 연결 | 2026-11-01 GUI 디자인 개편 후 `547 passed / 0 failed / CUDA2 deselected` (기존482+GUI65), 공식 소형 ACT3-step·Checkpoint·추론·Mock·평가 | 최종 모델 수렴·실물 ACT 성능 |
| Recorder GUI | 실험용 네 영역 UI·기술 정보 팝업, GUI65개 검증. 실제 Tk에서 Fake 상태로 렌더링·최소860×640/기본960×660·긴 Depth/오류 경계 확인. 엔진·bridge 유지 | ROS 실수신 부하·실물 운용 미검증 |
| 과거 CUDA 합성 연결 | 별도 `outputs/act_final_offline_20261031T025706Z_09a8f399/verification.json`의 CPU/CUDA3-step 기록 | 최신482개에서 CUDA 재검증한 결과가 아님 |
| 실제 ROS 입력 ACT 추론 | CLI 구현·fake ROS 검증 존재; 별도 실물 관측 추론 실행 완료 근거는 미확인 | Recorder Pilot을 추론 검증으로 대체하지 않음 |
| ACT 실제 모터 실행·Real Home | 미검증·CLI/API 잠금 유지 | Mock Task/Home 성공으로 해제하지 않음 |

직전482개 증거는 `tmp/act_compat_pe61fnjr/{verification.json,results.xml,suite.log}`에 보존했다.
구형 합성 Dataset·Checkpoint·캐시는 제거하고 실행/평가 메타데이터·로그는 `evidence/`에 남겼다.
이번 정리 후 결과는 `tmp/act_cleanup_1jj8_97d/{verification.json,results.xml,suite.log}`에 보존했다.
이후 GUI 추가 전체 회귀510개와 최종 GUI30개 기록은 `tmp/act_gui_djcd2anu/{verification.json,final_results.xml,final_suite.log,gui_final_release.xml}`에 보존한다. 고유 테스트는512개다.
이후 디자인 개편 최종547개 회귀·보호 대조·Fake Tk 화면은 `tmp/act_gui_design_eten3gqc/`에 보존한다. 상세 사용법은 GUI 문서가 기준이다.
CPU 전체 회귀 결과와 복구 커밋은 [정리 기록](PROJECT_CLEANUP_AUDIT.md)의 최신 항목을 따른다.
벤치마크는 일반 pytest와 별개다. 가짜 ROS QoS 설정과 임시 경로를 보완해 3초 합성 실행으로 58 frames·시간 무결성 valid를 확인했다.

## 기술적 제약·안전 제한

- 최신 장비 상태·접속 대상·위치 정확성·실제 QoS/clock·카메라 mount/USB 대역폭은 이번에 확인하지 않았다.
- 과거 J6 큰 음수/FAULT 반복 관측과 후속33.47° 관측은 [인계](INDY7_J6_FAULT_HANDOFF.md)의 날짜별 기록이다.
  현재 같은 값이라고 단정하지 않는다. 복구 버튼 조작·Pilot 존재만으로 복구/기준 위치 검증 완료를 단정하지 않는다.
- **기존 J6 복구 절차·기준 위치 검증 전 motion/write 제한은 유지한다.** 제한 해제의 날짜·근거·승인 기록은 확인되지 않았다.
- teleop/move/Home/Zero/Recover/calibration, encoder·offset·multi-turn reset, Servo/brake/SDO/PDO write를 임의 실행하지 않는다.
  `reset_servo`, `reset_overflow_count`, `set_robot_zero_as_current`, `set_servo`/`set_servo_rx`,
  `set_brake`와 brake release/수동 강제 이동도 안전한 조회·복구로 취급하지 않는다.
- `caps/capi/capx`와 Driver 시작/종료는 관측 전용이 아니다. 기존 장비 노드·시리얼을 임의 종료하지 않는다.
- 실제 getter·ROS 구독도 목적과 API를 설명하고 현재 사용자 승인 범위에서만 수행한다.
- Gripper 무명령 실제 초기 상태 조회는 명세·펌웨어 근거 미확인이다. 임의0/1이나 Open/Grasp로 우회하지 않는다.
  QP/Recorder는 RELIABLE/TRANSIENT_LOCAL/depth1, 추론 RosSource는 RELIABLE/VOLATILE/depth10이다.
- ROS Dry Run의 operator-initial-gripper는 명시적 가정이며 Recorder/실제 실행에서는 사용할 수 없다.
- Real ACT에는 독립 readiness/state provider·외부 writer 배제·실물 제한값·프로젝트 Home 검증이 추가로 필요하다.
  Mock 제한값을 실물 값으로 복사하지 않는다. QP는 외부 장애물 충돌 회피를 제공하지 않는다.
- dual DCP 구조는 기존 설계다. 장애 원인으로 단정하거나 single-owner 재설계를 적용한 것처럼 쓰지 않는다.

## 해결 이력·보류·다음 작업

- 해결된 소프트웨어 문제: 구형 SAVED 문자열 의존 테스트, Train-only 통계 로딩/역정규화,
  두 카메라·7D 학습/추론 연결. 구형 잘못된 정규화 Checkpoint는 자동 수정하지 않는다.
- 보류: `scripts/rotate_ros_image.py`는 과거 wrist 독립 도구이며 수동 외부 호출 여부가 미확인이라 유지했다.
- 구형 Pool·단일 카메라 YAML은 Git 원문과 설계 메모를 남기고 제거했다. 현행 v2 경로로 사용하지 않는다.
- 다음 순서: 장비 복구/제한 해제 근거 확인 → 승인된 수동 stack/최신 Recorder 실물 통합 확인 →
  실제 feasible workspace·카메라 역할 검증 → Pilot/정상108개 → 실제 성공 데이터 학습 →
  승인된 ROS 관측 추론·최종 모델 부하 검증 → 별도 실물 실행 안전 검증·미관측 pose 평가.
- 문서·코드 정리는 이 실물 작업의 승인이 아니다. 제조사 문의는 초안·과거 조사와 실제 송신/답변을 구분한다.
- 상세 공식 복구 자료·미확인 원인·제조사 요청은 J6 인계, Mark7 자료는 [피드백 조사](MARK7_STATE_FEEDBACK_INVESTIGATION.md)를 따른다.
