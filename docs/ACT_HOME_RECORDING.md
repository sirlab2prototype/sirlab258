# Home 포함 ACT 수집

2026-11-02. **방법 A: 기존 movej 유지 + 읽기 전용 qdes 수집을 활성화했다.**
J6 완전 해결과 현장 안전은 사용자가 확인했다. 복구 작업을 에이전트가 수행했다는 뜻은 아니다.
ACT 모터 실행과 qdes 궤적의 Teleop 재생은 여전히 미검증이며 Real 실행 잠금을 유지한다.

## 확인된 실물 근거

- Indy7 serial `PD47I7E0D002`, IP `192.168.1.114`, controller `3.3.2-GA12 [2026.09.15]`, SDK `3.4.2.2`.
- 운영자가 실행한 기존 Home에서 120초/2400표본, 이동 중69표본을 관찰했다.
  qdes는 최종값 반복이 아니라 중간 목표로 변했다. 축별 범위(deg)는
  `[18.754,63.890,59.830,11.864,59.663,0.844]`였다.
- SDK Home degree 목표와6축 최종 qdes 대응, qdotdes와 시간 미분의 일치를 근거로
  이 장비/버전의 원시 단위를 degree로 묶었다. 모든 제조사/FW에 대한 일반 보증은 아니다.
  이동 중 max|qdes-q|는 약0.02101도였다. q를 Action으로 복사하지 않는다.
- 실측20.0006Hz, control_state RPC 최대4.91ms, 순차4 getter 최대17.20ms,
  최대 응답 간격53.83ms, 오류/75ms 초과 간격0이었다. qdes의 controller sample 시각은 없다.
- 원문/신원/승인 기록은 `outputs/preserved/validation/home_qdes_20261102_0rx_dvsw/`에 보존한다.
  활성 프로필은 `ai/record/config/home_capture.json`이며 원문SHA-256과 장비/버전을 검증한다.
  프로필 scope는 `collection_only`; `actuator_replay_verified=false`다.

## 지금 사용하는 순서

1. 기존 Indy/QP/Mark7와 두 D435를 정상 운용한다. 중복 launch를 하지 않는다.
2. `act record`로 기존 JSON 데이터셋을 선택한다. wrapper가 활성 프로필을 읽고,
   녹화 엔진이 독립 읽기 전용 source를 시작/종료한다. GUI에 **Home 포함**이 표시된다.
   직접 Python 실행은 먼저 `source scripts/activate_act.sh`로 같은 환경을 사용한다.
3. 실제 성공한 Open/Grasp 명령 상태가 수신돼야 한다. 초기 gripper 값을 추정하지 않는다.
4. Home에서 s → QP 작업 → 물체 내려놓기/개방/충분한 후퇴 → 운영자의 View+Y Home.
5. GUI **Home 도착 확인** 이후 r. 실제 작업 성공을 작업자가 확인하고 o로 저장한다.
   Correction은 기존 x 마커를 사용한다. Home 도착만으로 작업 성공을 자동 저장하지 않는다.
6. 첫 실수집은 격리된 기술 Pilot에서 영상·7D·Timing Sidecar·Validator를 확인한다.
   qdes 실측 성공과 실제7D/RGB 이동 Episode의 저장 성공은 별개다.

Home 없는 종전 터미널 수집은 `act record --collection-dir <기존 경로> --no-home-capture`다.
이 모드에서는 Home 전에 r로 끝낸다. context 검사와 기존 시간/무결성 검사는 유지된다.
Core-108 JSON·이름·순서, 기존 Collection/Receipt/데이터는 변환하지 않는다.
Home 없는 과거 시연과 전체 복귀 시연은 출처/수집 범위를 확인해 사용한다.

## 제어와 데이터 흐름

기존 Home은 QP `request_capstone_home` → Driver TELE_STOP → QP `movej` → 기존 도착 검사
→ 재ARM이다. 이 이동/소유권/속도·가속도/키맵은 변경하지 않았다. 방법 B는 구현하지 않았다.

```text
QP movetelej_rel → 기존 teleop_action (절대 목표 rad, 원래 명령 stamp)
QP lifecycle → 기존 action_context
독립 home_source → 순차 RTDE getter → q(관측), qdes(Home 목표), 상태/시간 검증
                ↓ /capstone/recording_context
Recorder → 기존 BoundedHistory/HistoryAligner → 20FPS/7D/두 RGB/Sidecar/Receipt
```

source는 getter만 호출하며 SDK 객체를 QP와 공유하지 않는다. QP 동기 SDK 호출이 콜백을
막아도 독립 q/qdes 관측을 유지한다. 이미 관찰한 Home에서만 fresh RTDE/상태가 QP heartbeat
일시 공백을 보완한다. QP 구간은 fresh 원본 context가 필요하다. 오래된 qdes를 재사용하지 않는다.

QP Action은 SDK에 전달한 절대 목표다. SDK 반환 후 발행하지만 기존 QP는 response.code를
검사하지 않으므로 controller 수락/물리 실행 보증은 아니다. Home Action은 실측으로 확인한
controller의 순간 desired 관절 위치다. 측정 q는 observation.state에만 들어간다.
공통 저장 계약은 `[joint0..joint5 절대 목표 rad, 성공 응답 그리퍼 명령0/1]`이다.
두 목표의 생성 단계/필터 차이는 sidecar 출처로 구분한다.

QP_TELEOP → HOME_REQUESTED → HOME_MOVING → HOME_SETTLING → HOME_COMPLETE를 관리한다.
Home 중 s, 완료 전 r, 누락/노후화/느린RPC/실패/취소/미도달/조기 QP 명령은 차단한다.
Home 도착은 fresh 측정 q/속도/servo·fault/motion 상태와0.5도/0.01rad/s/250ms 안정 구간으로
확인한다. 60초 timeout을 사용하며 기존 QP 재ARM 조건을 바꾸지 않는다.
Home 완료 후 중립 재ARM은 완료 상태를 유지하며 새 QP 목표가 오면 QP로 돌아간다.

기존50ms 격자/60ms receive wait와 영상·state 제한을 유지한다. context/sample75ms,
Home RPC25ms/state skew75ms를 검사한다. 요청·응답 Unix/monotonic 시각, 실제 수신,
4 getter bracket, 관측/Action 시간 차이, 전환·시작·완료를 sidecar에 남긴다.
RPC 응답 시각은 사용 가능 시각이며 센서 측정 시각이 아니다. 순차 getter도 원자적이지 않다.
source의 놓친 poll은 계수하고 격자 재동기화하되 가짜 표본/밀린 burst를 만들지 않는다.
Recorder 자체20FPS/누락 검사는 완화하지 않는다.

## 추론 범위와 남은 검증

현재 RosIndy7Adapter는 절대 rad → trajectory topic → Driver `movetelej_abs` 경로다.
Driver의 ratio/보간은 movej와 다르다. 같은7D 절대 목표를 실행할 수 있는 형식이지만,
기록한 qdes의 속도·가속도·추종·정지를 동일하게 재현한다는 실물 증거는 아직 없다.
ACT 학습/추론은 이번에 실행하지 않았으며 Real 실행 잠금도 해제하지 않았다.
별도 Mock Home Task를 learned-return 경로로 사용하지 않는다.

TASK_SUCCESS는 별도 물체 운반 성공 근거가 필요하다. HOME_COMPLETE/정지/명령권 반환과
독립 task 성공이 함께 있어야 READY_FOR_NEXT_TASK가 된다. LLM/Task Allocator는 구현하지 않았다.
ACT1/ACT2는 기존 JSON/별도 Dataset·Checkpoint로 분리하며 Core-108 정의는 보존한다.

## 진단과 재검증

`scripts/observe_indy7_home_readonly.py`는 기본 비연결이고 명시적 연결 옵션이 필요하다.
최대120초,20Hz,100ms RPC deadline이며 Home 실행 기능이 없다. raw 단위 표기는 계속
unverified로 남기며 장비별 해석은 별도 프로필에만 둔다. FW/SDK/장비 변경 시 프로필을
무작정 재사용하지 않는다. RTDE application response가 생략된 정상 gRPC 응답은 payload를
검증하고 application_status=unavailable로 표시한다. 수락 ACK를 만들어내지 않는다.

실제 이동은 운영자/기존 QP만 수행한다. E-stop/정지 담당, 빈 Home 경로와 후퇴, 하중/그리퍼,
통신 단절 시 정지 방법과 단일 writer를 유지한다. reader 종료는 로봇 정지가 아니다.
J6 복구 검증은 사용자 확인에 따라 반복하지 않았다.

## 이번 검증

관련 오프라인 회귀424개 통과, 마지막 변경 집중110개 통과. 근거
`tmp/home_live_0rx_dvsw/{final_regression,endpoint_checks}.xml`.
실물 qdes/시간/두 카메라·context 수신을 확인했다. 2026-11-03 그리퍼 개폐 정상의 사용자
확인 후 실제 성공 명령 상태0.0을 수신하고 Recorder 시작·무결성 검사를 통과했다(fault0).
근거는 보존 디렉터리의 `gripper_start_20261103.json`이다. 임의 초기값이나 검사 완화는 없다.
전체 실제7D/RGB 이동 Episode 저장은 첫 기술 Pilot에서 별도로 확인해야 한다. 기존 Dataset/Receipt/Collection을 변경하지 않는다.

## 이전 오프라인 단계 이력

- 최종 **480 passed, 5 deselected**, 120.03초. Recorder 전체·Collection/Receipt/재개·GUI·
  기존 QP·Dataset Validator·Mock Execution/Task를 함께 실행했다. 5개 제외는 실제 ACT
  학습/추론 fixture를 실행하는 테스트이며 이번 사용자 제한에 따라 수행하지 않았다.
- 새 Home 집중 테스트49개도 통과했다. QP→Home→완료→새 QP 목표, 정상 합성 저장,
  누락/노후화/조회 실패/느린RPC/단위·차원·순서/중도 시작·종료/취소/미도달/소스 충돌,
  필수 Home 종료 gate, Success·Correction Receipt 저장/재개와 FakeSDK 변환을 포함한다.
- 기존 테스트 fixture는 String 메시지만 추가했고, CLI 옵션 전달은 기존 관측 노드 생성자
  계약을 유지했다. 회귀 중 발견한 fixture 마커 출처/생성자 호환 문제를 수정한 최종 결과다.
- 결과: `tmp/home_action_final_20261102b.xml`, 집중 `tmp/home_action_source_audit_20261102a.xml`.
  정적 근거: `tmp/home_action_control_audit_20261102.json`.
  control_loop/arm/arm_done/disarm/_set_disarmed_state/shutdown AST와 QP SDK 호출은 HEAD와 동일하다.
- `git diff --check` 통과. 새 외부 의존성·빌드·ROS 노드·SDK 객체·장비 연결·학습/추론·
  Git 커밋은 없었다. 실물 Dataset/Collection/Receipt를 변환하거나 이동하지 않았다.
- Torchvision의 기존 video API deprecation 경고1건이 있었다. 실물20FPS·충돌/정지 안전·
  실제 작업/파지/Home 재현은 검증하지 않았다.
