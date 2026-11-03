# 기존 Recorder 호환성과 Home qdes 관찰 절차

2026-11-02. 아래 검증 결과는 실물 실행 전 단계의 기록이다.
**후속 사용자 확인: J6 문제는 완전히 해결됐고, Home 포함 수집 준비 진행 및 현장 안전을 확인했다.**
J6 미해결을 전제로 한 이전 연결 금지는 현재 기준이 아니다. qdes 검증/활성화 결과와
현재 실행 범위는 PROJECT_CONTEXT를 따른다. 아래 템플릿은 필요한 조건을 설명하는 절차다.

## 현재 실물 후속 상태

장비별 qdes 중간 목표/degree 대응과20Hz를 실측하고 Home 수집 프로필을 활성화했다.
현재 `act record`는 **Home 포함**, 완료 후 r 방식이다. 기존 Home 없는 터미널 수집은
`--no-home-capture`로 유지한다. 실측/프로필/남은 실제 Episode 및 ACT replay 검증은
[Home 수집](ACT_HOME_RECORDING.md)을 따른다. 아래 비활성/비연결 결과는 이전 단계 이력이다.
진단 최대 duration은 현재120초다. 실제 RTDE의 생략된 application response는 payload 검증과
함께 unavailable로 표시하고 문자열 code="0"도 허용한다. 비정상 명시 code는 거부한다.

## 이전 Core-108 오프라인 수집 판단

- 같은 버전의 QP가 fresh `/capstone/action_context` v1을 보내면 Home 없는 기존
  Success/Correction 수집이 가능하도록 구현돼 있다. 실제 구독 콜백 → 정렬 → 저장 →
  Collection/Receipt 재개를 Fake ROS로 검증했고, 기존 경로와 저장 Action이 동일했다.
- Core-108 조건/순서/JSON/Collection 스냅샷과 기존 Dataset은 바꾸지 않는다.
  두 RGB·20FPS·7D·그리퍼 성공 명령 상태·기존 시간 제한을 유지한다.
- Home reader는 비활성 상태이고 `--require-home-complete` 기본값은 꺼져 있다.
  기존 수집에 qdes나 Home 완료를 요구하지 않는다. QP 중립 입력에서 Action이 새로
  오지 않아도 기존 initial/last target hold는 허용한다. Home 전에는 계속 r로 끝낸다.
- 새로 추가된 의존성은 context의 형식·프로세스 일관성·연속성·75ms freshness다.
  QP RPC가 executor를 오래 막거나 메시지가 누락되면 기존 센서가 정상이어도 시작 거부/
  INVALID가 가능하다. 실제 여유는 실물 검증 대상이다. 이를 피하려고 검사를 제거하지 않는다.

현재 루트에 `./act-record`는 없다. 이는 이번 작업에서 삭제된 파일이 아니다.
지원 진입점은 다음과 같다. **장비/ROS 사용 승인을 받은 후에만** 실행한다.

```bash
# GUI의 기존 JSON 데이터셋 선택
./scripts/bin/act record
# 기존 Collection의 터미널 Recorder
./scripts/bin/act record --collection-dir outputs/preserved/datasets/act1_108_001
# 같은 터미널 경로의 내부 호환 wrapper
./scripts/bin/act-record --collection-dir outputs/preserved/datasets/act1_108_001
```

`act`가 이 checkout에 설치돼 있으면 `act record`도 같다. 인자 없는 호출은 GUI 선택,
`--collection-dir` 호출은 기존 터미널 경로다. GUI에서 특정 Collection을 바로 열려면
기존 환경을 활성화한 뒤 `python -m ai.record.gui --collection-dir <경로>`를 사용한다.
이번 검증에서는 wrapper도 Fake Python/ROS로만 검사했다.

## QP와 Recorder의 실행 버전 맞추기

아래 두 명령은 지금도 장비/ROS/SDK에 연결하지 않는 정적 점검이다.

```bash
.venv-act/bin/python -B scripts/check_recorder_compatibility.py
.venv-act/bin/python -B scripts/observe_indy7_home_readonly.py --describe
```

현재 설치의 egg-link → build symlink는 이 프로젝트 소스를 가리키며,
qp_teleop.py/action_context.py가 일치한다. QP에는 context publisher와50ms timer가 있다.
그러나 **파일 일치는 이미 실행 중인 프로세스가 읽어 둔 코드의 일치 증거가 아니다.**
정적 도구는 항상 running_qp_version을 unknown으로 표시한다.

실물 사용 승인 후에는 다음 순서로 확인한다.

1. 운영자가 실행 checkout·환경·ROS domain과 기존 장비 프로세스 소유자를 확인한다.
2. 구형 QP이면 승인된 정지/재시작 절차로 같은 checkout의 QP/Recorder를 맞춘다.
   Driver/QP 종료는 제어 명령을 보낼 수 있고, 시작에는 자동 ARM이 있으므로 임의 수행하지 않는다.
   정적 경로가 맞는다는 이유로 현재 프로세스를 자동 재시작하지 않는다.
3. 승인된 읽기 전용 ROS 관측으로 context publisher가 하나인지, v1의 stream_id가
   일정하고 sequence가 연속인지, 현재 QP_TELEOP인지, stamp가 현재 host epoch와 맞는지
   확인한다. Header freshness와 수신 빈도는 별도 확인한다. Topic 존재만으로 충분하지 않다.
4. 녹화 시작 검사를 통과한 새 격리 Pilot에서 s→r→o와 s→x→r→o를 확인한다.
   실제 작업 성공이 없는 점검 시연은 정식 Dataset에 저장하지 않는다.

QP context QoS는 RELIABLE/TRANSIENT_LOCAL/depth32이며 Recorder도 같다.
기존 Action topic은 변경하지 않았다. 구형 QP의 topic 부재는 missing_action_context,
잘못된 버전은 action_context_protocol_mismatch, 지연/누락/프로세스 변경은 각 오류로
차단한다. GUI는 한국어 원인과 실행 버전 확인 안내를 제공하고 원본 진단은 상세에 남긴다.

## 진단 도구의 경계

- 기본 실행은 인자 오류로 종료하며 SDK를 import/생성하지 않는다. `--help`, `--describe`,
  `--analyze <기존 JSONL>`도 비연결이다. offline 모드와 연결 옵션을 함께 쓰면 거부한다.
- 연결은 `--connect-readonly`, 검증된 literal IP, 비어 있지 않은 `--approval-record`,
  새 `--output`이 모두 필요하다. 이 문자열은 실제 승인을 만들어 주지 않는다.
  기존 출력·잘못된 주소·0/NaN/무한대/120초 초과 duration은 SDK 생성 전에 거부한다.
- getter는 get_control_state, get_control_data, get_motion_data, get_servo_data뿐이다.
  q/qdes는 같은 get_control_state 응답에서 읽는다. 모든 필드는 원문으로 보존한다.
  q를 qdes 대신 쓰거나 각도 단위를 자동 변환하지 않는다.
- RPC마다 Unix/monotonic 요청·응답 시각과 경과 시간을 남긴다. 센서 sample timestamp는
  없으며 controller_sample_timestamp=null, qdes_unit=unverified를 유지한다.
- SDK 예외·비정상/미확인 response code·q/qdes 형식·비유한 값·clock bracket 오류에서
  추가 RPC 없이 관찰을 종료한다. NaN/Inf는 오류 표시와 함께 문자열 원문으로 보존한다.
- 순차4 RPC+처리의 50ms 주기를 넘으면 overrun으로 종료하고 burst catch-up하지 않는다.
  getter별100ms timeout은 안전한 명령 정지 시간이 아니라 관측 RPC deadline이다.
  Ctrl+C/오류 시 자체 채널을 정리하며 stop/movej/teleop/Recover/Servo write를 호출하지 않는다.
  기존 Driver/QP 프로세스·lease·명령권을 변경하지 않는다. 추가 client의 부하는 미검증이다.

각 파일에는 수동 label, raw sample, 종료 요약이 있다. 요약은 getter별 latency min/p95/max,
순차 cycle 시간, 실제 응답 Hz/간격,75ms 초과 간격·slot gap·오류 수를 제공한다.
SDK motion flag에 따라 전/중/후 raw qdes 범위와 max|qdes-q|도 비교한다.
**motion RPC는 순차 조회이므로 구간은 근사 분류이며 실제 Home 시작/도착 판정이 아니다.**
운영자의 Home 입력/도착 확인 시각과 현장 메모를 별도 근거로 남긴다.

## 실물 검증 전 선행 조건

1. 2026-11-02 사용자가 J6 문제의 완전 해결을 확인했다. 이 복구 검증을 반복하지 않는다.
   과거 Conty 33°나 fault 수치를 현재 상태로 사용하지 않는다.
   [J6 인계](INDY7_J6_FAULT_HANDOFF.md)와 [Context 제한](PROJECT_CONTEXT.md)을 따른다.
2. 실제 robot serial/IP/controller FW/SDK와 현재 관절 제한·zero 기준을 확인한다.
   이번 정적 점검에 사용한 SDK3.4.2.2와 해당 controller의 호환성을 확인한다.
3. **읽기 연결도 별도 승인**한다. 추가 SDK client/getter가 현재 writer와 공존 가능한지
   제조사/운영 근거가 필요하다. 이미 getter라고 알려진 API라는 사실만으로 승인하지 않는다.
4. 독립 E-stop과 담당자를 정하고, 통신 장애 시 controller/물리 정지 방법을 확인한다.
   진단 종료나 채널 close가 로봇 정지가 아님을 공유한다.
5. 단일 writer를 확인한다. Conty/MoveIt/ACT/다른 SDK·자동 ARM 경로와 충돌하지 않아야 한다.
   바구니/그리퍼 상태, 선반·책상·OMY·사람과 Home 경로의 충돌 여유를 확인한다.
6. Home 이동 승인은 읽기 승인과 별개다. 낮은 속도/가속도와 안전 경로는 운영자가 사전
   검토해야 하며 이번 작업은 기존18/100이나 어떤 제어 설정도 변경하지 않았다.

## A. 승인 후 정지 상태 관찰

현재 단계에서는 실행하지 않는다. 선행 조건과 읽기 승인 후, 기존 스택이 승인된 정지
상태이고 Xbox 입력이 중립임을 운영자가 확인한다. 진단 때문에 스택을 새로 시작하지 않는다.
APPROVED_INDY_IP/READ_APPROVAL_ID/NEW_OBSERVATION_JSONL은 승인된 값과 **새 파일**로 정한다.

```bash
# 현재 금지: 선행 조건과 읽기 연결 승인 이후에만 사용하는 템플릿
.venv/bin/python -B scripts/observe_indy7_home_readonly.py \
  --connect-readonly --ip "$APPROVED_INDY_IP" --approval-record "$READ_APPROVAL_ID" \
  --output "$NEW_OBSERVATION_JSONL" --seconds 3 --label stationary
```

약60개를 요청한다. 오류/overrun이면 부분 근거로 보존하고 추가 실행을 자동 반복하지 않는다.
q/qdes의 안정성,6축 범위·차이, response code, motion/servo 상태와 지연을 확인한다.
정지 상태에서 qdes≈q라는 사실은 중간 궤적·단위·fresh controller frame의 증거가 아니다.
비정상 J6·상태 반복·통신 장애를 보면 연결 단계를 종료하고 승인된 대응 절차로 돌아간다.

## B. 추가 승인 후 기존 Home 관찰

읽기 A가 문제가 없고 이동 승인이 별도로 있을 때만 진행한다. 새 JSONL에
`--seconds 30 --label home_observation`으로 관찰을 시작한다. 진단은 Home을 실행하지 않는다.
운영자가 몇 초의 정지 구간 후 승인된 기존 View+Y Home을 한 번 실행하고, 완료 뒤 정지
구간도 남긴다. 자동 재ARM을 포함한 기존 Home 동작과 통신 장애 대응을 담당자가 감시한다.
수집용 Recorder는 Home 전에 r로 끝낸 상태여야 하며 실물 Home을 Dataset에 섞지 않는다.
30초 안에 전·중·후가 확보되지 않으면 불완전한 관찰로 표시한다. 진단 시간에 맞추려고
Home 속도를 올리거나 Home을 자동 반복하지 않는다.

관찰 전/중/후 기존 QP의 입력 응답·상태/Action 발행과 Driver Joint 갱신·timeout/error를
승인된 외부 관측으로 비교한다. 정지 중 Action이 안 나오는 것은 정상 QP 정책이므로
그 자체를 장애로 판단하지 않는다. 지연/오류가 늘면 진단을 종료하고 운영 절차를 따른다.

## C. 판정 기준과 오프라인 분석

```bash
# 기존 관찰 파일만 읽는다. SDK/ROS 연결 없음
.venv-act/bin/python -B scripts/observe_indy7_home_readonly.py --analyze "$OBSERVATION_JSONL"
```

| 항목 | 필요한 근거와 보류 조건 |
| --- | --- |
| 중간 목표 | 실제 움직인 축의 qdes가 이동 구간에서 여러 중간값으로 갱신되어야 한다. 처음부터 최종 Home각만 고정되면 불충분하다. 정지 축의 고정값은 허용한다. 이것만으로 내부 desired 의미가 확정되지는 않는다. |
| q와 qdes 차이 | 같은 GetControlState 응답의 q/qdes/qdot/qdotdes/qddotdes를 비교한다. 추종 선행·지연·불연속이 해당 FW/제어 방식과 물리 이동에 타당한지 검토한다. 임의 수치 허용값을 만들지 않는다. |
| 순서·단위 | 제조사/FW 정의, 검증된 기준 자세·Home 설정·Conty 축 대응을 교차 확인한다. 측정 q의 단위 확인을 qdes에 자동 전이하지 않는다. 미확인 시 계속 unverified다. |
| 20Hz | 평균 Hz 외에 모든 RPC/순차 cycle p95·최대와 실제 응답 간격을 검사한다.50ms 초과 cycle은 도구가 중단하며75ms 초과 gap도 보류 근거다. 미래 reader의25ms RPC budget은 기존 합성 초기값이며 실측 후 검토해야 한다. |
| 누락·RPC 지연 | raw slot/시각/오류와 summary를 함께 본다. getter 실패·overrun·잘린 파일·시작/이동/완료 구간 누락을 정상 연속 관측으로 표시하지 않는다. timestamp/sequence가 없는 controller 내부 cache freshness는 추가 근거가 필요하다. |
| 기존 제어 영향 | observer off/on/off의 Joint/context 수신 간격, QP 입력 응답과 Driver/controller 오류를 비교한다. 추가 연결 허용과 부하가 미확인이라면 활성화하지 않는다. |

위 항목을 만족하더라도 qdes→현재 절대 Teleop 실행기 재현은 별도 과제다.
이 관찰만으로 live reader, ACT 추론, 실제 작업 성공 또는 Home의 충돌 안전을 승인하지 않는다.

## 이번 변경과 검증 근거

구독 콜백 기반 호환성 테스트, GUI의 context/Home 오류 설명, 설치 경로 정적 점검 도구와
진단 초기화/응답 오류/cleanup/시간·raw 비교 요약을 보강했다. 기존 QP/Driver 제어를 바꾸지 않았다.
근거 디렉터리는 `tmp/recorder_compat_jsjoocks/`이며 before.json에 기존 미커밋 파일과
기존 Collection/Receipt12개의 해시를 먼저 보존했다. 실제 장비 상태는 여전히 미확인이다.

- 관련 전체 회귀 **417 passed** /118.85초. Recorder/Core-108/GUI/Teleop/Validator와
  Home/관찰 Fake 테스트를 포함한다. 기존 Torchvision video API deprecation 경고1건이 있다.
- 지원 `act record` wrapper의 GUI/터미널 인자 전달 **2 passed**(Fake Python/ROS).
- 마지막 cleanup 오류 기록 보강 후 새 호환성/관찰 테스트 **33 passed**(전체 회귀의 부분집합).
- QP 제어 및 context emitter는 이번 단계 시작 시 해시와 동일하다. 기존 Collection/Receipt
  12개도 시작 전후 동일하다. 공식 SDK/Driver·기존 계획/데이터 계약은 수정하지 않았다.
- 근거: `regression.xml`, `cli.xml`, `final_focus.xml`, `install_check.json`, `protection.json`.
  실제 SDK 객체/RPC·ROS 노드·장비·ACT 학습/추론·빌드·커밋은 실행하지 않았다.
