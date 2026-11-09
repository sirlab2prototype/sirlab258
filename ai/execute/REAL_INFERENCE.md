# Indy7 감독형 5초 ACT Real Inference

2026-11-05 이후 `config/indy7_real_reviewed.json`은 캡스톤 확대 범위의 **미승인
오프라인 후보**다(`reviewed_for_real=false`). J1~5 ±174°·J6 ±214°와
`max_step_rad=2.25°`는 이전 실물 시험 승인 범위가 아니다. 해당 시험 설정의 원문은
`config/indy7_real_5sec_20261105_backup.json`에 보존했다. 아래 5초 실행 기록은
백업 설정의 과거 결과이며 새 후보의 실물 검증이 아니다. 이전 Home 자세와
목표 오차·속도·가속도·tracking·정지 제한은 새 후보에서도 유지된다.

캡스톤 후보의 오프라인 근거는 학습 Snapshot manifest의 SUCCESS10/8997프레임이다.
Episode 경계를 제외한8987개 차분에서 관절별 Action 변화 최대는
J1~6 `[2.507,2.976,4.444,2.719,2.776,2.599]°/50ms`, 실측 위치 차분에서
계산한 속도 최대는 `[24.513,29.391,44.320,36.569,33.376,34.297]°/s`였다.
QP 구간의 Action은 QP30Hz의 사후 SDK 목표를 Recorder20Hz로 표본화한 값이며
실측 속도가 아니다. 이전1.5° Step은 명목20Hz에서30°/s였으므로 기존45°/s 상한에 맞춰
2.25°로 조정했다. 원본 최대4.444°를 복사하지 않았다. 새 Step에서도
J1~6 `[2,41,151,28,45,12]`개 차분이2.25°를 넘고, 기존 목표-실측
1.5° 상한 초과 프레임은 `[1369,1615,2046,622,1658,743]`개다.
기존 Home ±2° 범위 밖 Action은8405/8997프레임, 새 제조사-1° 범위 밖은0개다.
기존 목표 오차·tracking·가속도 값은 QP 표본만으로 ACT 실물 추종 허용치로
재승인할 수 없어 유지했다. `SafetyGuard.bounded_target()`도 원본 목표 범위와
목표-현재 오차를 보정 전에 검사하므로 새 범위만으로 시연 재생이 보장되지 않는다.

2026-11-05 최종 5초 실물 Closed-loop 시험은
`outputs/temporary/inference/act_real_test_5sec_20261105T102020`의 `result.json`과
`client/summary.json`에서 `complete`/`duration_complete`로 끝났다. ACT Chunk 10개,
SDK ACK 96회, 측정 관절 변화 최대 0.0249755 rad, 실제 Action 19.9975 FPS,
송신 기한 누락 0회다. safety fault 없이 정지를 확인했고 lease는 격리되지 않았다.
이는 해당 5초 세션의 명령·관측·정지 검증이다. 로그의
`physical_execution_verified=false`와 `physical_grasp_verified=false`는 유지되며
물체 파지·작업 성공을 뜻하지 않는다. 추가 실물 실행은 하지 않았다.

2026-11-05 상태 시계 후속: Controller가 feedback 조회 전에 잡은 now로 조회 후 새로
교체된 캐시를 검사하면 음수 age를 stale_controller_status로 처리하는 경쟁 조건을
수정했다. 조회 후 시계를 읽어 검사하며100ms 한계와 미래 timestamp 차단은 유지한다.
상태 거부 로그에는 검사 now·실제 관절/상태 timestamp·signed age를 기록한다.
controller_safety_feedback도 로그 작성 시각 대신 실제 검사한 캐시 timestamp를 기록한다.
act_real_test_20261105T094912의3개 prediction은 각각 새로운 양쪽 RGB와 관절 표본이며
모두 응답 수락됐다. 당시 실패의 검사 now/캐시 timestamp가 없어 음수 age 발생 자체는
로그만으로 확정하지 않는다. Fake 경쟁 재현·만료·정지 집중17 passed/12 deselected.
실물 정상 종료까지의 재시험은 필요하며 장비/ROS/학습/커밋은 실행하지 않았다.

## act prepare 입력 장치 준비

기존 카메라 두 대와 Mark7 전용 노드를 함께 실행하는 장기 명령이다.

```bash
cd /home/sirlab2/sirlab258
scripts/bin/act prepare
# 포트가 다르면: scripts/bin/act prepare --device /dev/확인한포트
```

기존 `scripts/bin/camera show_camera:=false`와 Mark7 source를 실행한다.
Mark7에는 publish_command_state=true 및 private open/grasp remap을 전달한다.
카메라 wrapper의 기존 장치 잠금과 Mark7 시작 전 포트 점유 확인을 사용하며,
점유·시작 실패·자식 종료 시 오류를 출력하고 직접 시작한 프로세스만 정리한다.
이미 별도 카메라/Mark7가 실행 중이면 먼저 작업자가 정상 종료한 뒤 실행한다.
외부 프로세스를 자동 종료하거나 재사용하지 않는다. Ctrl+C는 소유한 두 process group만 정리한다.

`act prepare`는 실행 중 실제 Mark7 Open 동작을 예고한다. Mark7 Open 서비스 준비를
기다려 정확히 한 번 호출하고,
서비스 성공 응답과 `/capstone/gripper_state`의 `0.0` 수신을 확인한다. 실패·시간 초과는
원인을 출력하고 소유 프로세스를 정리한다. `0.0`은 성공한 명령 상태이며 실제 손가락
위치 측정이 아니다.
RGB/설정/ROS 그래프/GID/초기 명령 상태를 계속 감시하지 않으며 INPUTS_READY를 표시하지 않는다.
프로세스 실행 안내는 장치·로봇 Ready 판정이 아니다. Indy Driver·SDK·
ACT 서버·제어기는 시작하지 않는다. 기존 execute의 초기 명령 상태와 관측·명령권·Guard
검사는 그대로 유지된다.
별도 터미널에서 아래 기존 Preflight 및 승인된 execute 절차를 수행한다.
ACT 정지·종료 확인 후 prepare를 Ctrl+C로 종료한다.

## 송신 기한·정지 안정 구간 수정 (2026-11-05)

`act_real_closed_loop_20261104_03`의 첫 Action은 Scheduler 지연4.02ms였지만
Adapter의20ms 기한을 넘겨 관절 명령0개에서 중단됐다. 기존 로그에는 세부 host 검사별
시간이 없어 당시 지연 전체를 특정 함수 하나에 귀속할 수 없다. 코드에서는 Guard 전
feedback을 poll 직후 다시 조회하고, 각 조회가 TCP/TCP6 전체와 Writer 파일·ROS 그래프를
반복 조회했다. 정상 Mark7 서비스 목록도 feedback 한 번에4회 읽었다.

수정은 이 중복과 정지 버그에 한정한다. `consume()`는 방금 poll한 feedback을 Guard에서
재검사하고 Adapter/Driver의 송신 직전 검사는 유지한다. Mark7 서비스 존재/금지 검사는
한 번의 목록 snapshot을 공유한다. 직접 링크 route/carrier·외부 TCP 검사는 **기존 Driver
20Hz monitor**에서 갱신하고, 송신/Watchdog의 read는 매번 Writer inode/owner와 증거 나이를
검사한다. 증거 나이는 scan 시작부터 기존 `interlock_max_age_ms=100` 이하여야 하며,
갱신 실패·만료·시계 역행은 sticky revoke다. 비싼 갱신 중에도 read는 대기하지 않고
만료를 차단한다. TCP 검사는 이제 feedback 호출마다 실행하는 대신 monitor 주기로
탐지한다. 감독자가 다른 client 실행을 막는 기존 direct-link 운용 조건을 유지한다.
별도 Supervisor·자동 복구·시간/관절 제한 완화는 없다.

`_poll_stop()`는 idle/Ready/fresh 검사 실패 또는 정지 요청 전 표본에서 안정 시작을
초기화한다. 복귀 후 fresh idle/무이동/낮은 qdot의250ms를 새로 세며, 기존 Fault latch와
자동 재개 금지·미확정 quarantine을 유지한다.

시간 진단은 모두 host monotonic ns이며 SDK 센서시각이 아니다.

- `execution_dispatch_timing`: schedule, lock 대기를 포함한 poll 완료, Guard 완료,
  transport 진입/완료와 종료 시각. Adapter에는 feedback 완료, ROS graph 완료,
  message 준비, publish 시각을 담는다. RealSource provider에는 authority/cache 완료,
  publisher graph 완료, service 목록 완료를 담는다. 실패해도 도달한 단계까지 기록한다.
- `direct_link_refresh_timing`: monitor의 route/carrier 및 TCP 조회 완료 시각.
- `real_driver_dispatch_timing`: 같은 `expires_ns`로 Adapter와 연결한다. ROS 수신,
  feedback 확인, SDK lock 요청/획득, RPC 진입/ACK를 구분한다.20ms 만료 검사는
  Adapter와 SDK 호출 직전에 그대로 적용한다.
- `controller_submitted_actions`: Controller transport 제출 수.
  `sdk_command_attempts`: 실제 SDK MoveTeleJ 호출 시도 수.
  `sdk_acknowledged_actions`: SDK 응답까지 성공 검사한 수.
  Real의 `robot_commands_sent`는 마지막 ACK 수이며, 수집 못 하면 null이다.
  ROS publish Future의 `acknowledged_actions`와 구분한다. SDK ACK도 물리 이동 증거는 아니다.

CPU/Fake 검증·수정 전 원문·로컬 IO 측정 근거는
`tmp/act_dispatch_fix_5sg5slgr/`에 보존한다. TEST-NET/Fake route/고정 clock으로 로컬
파일 조회만 비교한30표본 중앙값은 수정 전2.2988ms, 수정 후0.00665ms다.
최종 CPU/Fake 회귀169 passed/학습·모델 추론 fixture2 deselected(2.19초),
그중 신규 집중21개다. 수정 전 정지 코드에서는8개 중5개 실패/3개 통과를 확인했다.
비구동 Preflight는 profile_valid:true/errors0/hardware_connected:false였다.
로봇 연결·실제 ROS·SDK·모델 추론·학습·Git commit은 하지 않았다.

### 작업자의 다음 재시험

현재 두 JSON의 승인 기록은 **최초 최대2초 시험**이다. CLI 상한5초와 구분한다.
기존10개 학습 Checkpoint와 프로필을 사용하고, 실제 Home 초기 오차≤0.5°,
두 D435 global time/실시간 clock, 성공한 Mark7 초기 명령 상태, 단독 Writer/직접
케이블·빈 영역·E-stop 접근을 현장에서 다시 확인한다. QP/기존 Driver/다른 SDK reader·
Conty는 기존 승인된 절차로 종료해야 한다. Writer와 ACT Lease의 과거 released 기록은
현재 장비 상태가 아니다. unresolved면 삭제/자동 초기화하지 말고
[기존 수동 복구](EXECUTION.md#unresolved-indy7-writer-recovery-2026-11-04)를 따른다.

아래 명령은 현장 작업자가 준비 확인과 별도 구동 승인을 한 뒤 실행한다. source Python을
쓰므로 이번 ai 변경에 ROS/submodule 재빌드는 필요 없다. 기존 프로세스를 자동 재시작하지 않는다.

```bash
cd /home/sirlab2/sirlab258
CHECKPOINT="$PWD/outputs/preserved/training_runs/act1_real10_executionfit_20261103_v1/training/checkpoints/008000/pretrained_model"
scripts/bin/act execute --mode real --preflight-only --checkpoint "$CHECKPOINT" \
  --config ai/execute/config/indy7_real_reviewed.json \
  --hardware-profile ai/execute/config/real_hardware_reviewed.json
# exit0/profile_valid:true 확인 후, 현장 구동 승인 범위에서 한 번 실행
RUN_DIR="$PWD/outputs/temporary/inference/act_real_dispatch_fix_$(date -u +%Y%m%dT%H%M%S)"
scripts/bin/act execute --mode real --checkpoint "$CHECKPOINT" \
  --config ai/execute/config/indy7_real_reviewed.json \
  --hardware-profile ai/execute/config/real_hardware_reviewed.json \
  --approve-real "현장 감독자: 직접유선/다른 Writer·Conty 종료/초기 자세/빈 영역/E-stop 확인" \
  --device cuda --duration 2 --output-dir "$RUN_DIR"
```

별도 터미널에서는 위 실제 RUN_DIR 경로로 `scripts/bin/act execute --stop-run "$RUN_DIR"`
또는 `--status-run "$RUN_DIR"`를 사용한다. Ctrl+C도 기존 정지 경로이며 물리 위험은
기존 E-stop으로 중단한다. 실패 시 자동 재시도·Home·Recover를 하지 않는다.

성공 판정은 `prediction_response` → `real_SDK_target_ack` → fresh measured 관절 변화 →
새 RGB/관절 관측의 후속 prediction → 추가 SDK ACK/관절 변화 → `stop_confirmed:true`와
정상 세션 종료 및 두 잠금 반환을 모두 대조한다. 후속 request의 observation 기준시각과
카메라 Header·관절 표본이 갱신됐는지 확인한다. ACK나 작은 측정 잡음만으로 이동을
판정하지 않는다. 바구니 파지·자동 Home 성공은 이번 완료 조건이 아니다.
범위/첫 raw 목표/Tracking/카메라/기한 중 하나라도 차단되면 원인을 로그로 확인한다.
2초 재시험과 정지 확인 후5초로 늘리려면 해당 시간에 대한 현장 승인 범위를 확인한다.

2026-11-03. 실제 로봇 구동은 현장 작업자가 별도로 수행한다. 이번 소프트웨어 작업은
장비 연결/SDK RPC/ROS 실행/학습/추론/이동 없이 Fake로 검증했다. Recorder/Home/Snapshot/
데이터/OMY/공식 SDK·Driver는 변경하지 않았다.

## 사용할 운용 방식

사용자는 Indy7 제어함이 이 PC에 연결돼 있고 Xbox/Conty 태블릿을 끊을 수 있다고 확인했다.
**제어함 전원은 켜 둔다.** 작업자가 로봇/E-stop을 감독하며, QP·기존 Driver·Conty·별도 SDK
및 Recorder의 독립 SDK reader를 정상 종료한 뒤 ACT 전용 guarded Driver만 사용한다.
사람/장애물과 분리된 빈 영역에서 진행하며 자동 Home/Recover/재개는 없다.

`authority_mode: supervised_direct_link`는 이 제한된 연구 시험용이며 최대5초다.
별도 Supervisor나 heartbeat 프로그램은 필요 없다. 아래의 실제 조건을 함께 사용한다.

- 현장: 제어함↔이 PC의 직접 유선 케이블을 확인하고 태블릿/무선/다른 PC의 제어 연결을 끊는다.
  시험 중 다른 SDK/제어 프로그램을 새로 실행하지 않는다. 공유망이면 이 모드를 사용하지 않는다.
- 소프트웨어: 해당 물리 유선 인터페이스/직접 route/gateway 없음/carrier를 확인한다.
  Wi-Fi/bridge/virtual interface와 다른 경로를 거부한다. 네트워크 설정을 변경하지 않는다.
- 기존 QP/ACT flock 보유·소유자/파일 identity, ROS Writer/명령 endpoint를 확인한다.
- Linux TCP/TCP6에서 컨트롤러에 연결된 다른 프로세스가 발견되면 getter client도 거부한다.
  운전 중 발견/점검 실패는 세션 폐기와 기존 정지 경로로 연결한다. 자동 재개하지 않는다.
- E-stop은 자격 확인된 SDK 입력을 매 표본 대조한다. Ready를 E-stop clear로 대용하지 않는다.

이것은 **통제된 PC에서의 운용상 명령권**이다. 임의의 직접 SDK client를 커널에서 막는 ACL이나
독립 안전 장치는 아니다. 비협조 프로그램이 새 연결 직후 명령을 보내면 탐지 전에 명령이
전달될 가능성까지 제거하지 않는다. 그 상황을 배제할 수 없는 환경에서는 direct mode를
사용하지 않고 실제 접근 통제/외부 interlock이 필요하다. JSON true나 승인 문자열만으로
안전 기능을 생성하지 않는다. 기존 `external_interlock` 모드도 그대로 유지했다.

## 1. 현장에서 딱 한 번 준비할 자료

| 확인 | 방법과 통과 기준 |
| --- | --- |
| E-stop 신호 | 정지 상태의 해제 전→누름→해제 후 capture. 같은 serial/FW/SDK, 안정된 clear 쌍, 구별되는 pressed 쌍. 해당 FW 필드 정의/제조사 확인으로 E-stop 의미를 확인해야 함. 단순 차이 관찰만으로 자격을 부여하지 않음 |
| 실제/운영 제한 | capture의 현재/원래 joint_pos_limit와 SafetyLimits를 Conty/제조사 정의와 대조해 단위 확인. 빈 영역의 운영 min/max·vmax·amax·오차·초기 허용 범위 검토. 하드웨어 최대치를 운영 기본값으로 복사하지 않음 |
| 정상 정지 | 현장 승인된 기존 저속 작은 이동에서 CAT2 stop_motion + stop_teleop의 실제 정지 시간/거리, qdot/이동 아님/안정 구간 확인. 정지 ACK만으로 통과하지 않음 |
| 명령 중단/통신 장애 | 해당 FW의 MoveTeleJ 입력 중단·네트워크 단절·호스트 종료 때 마지막 목표/timeout/정지 동작을 제조사 설정 또는 승인된 시험으로 확인. 현재 SDK 코드만으로 보장되지 않음 |
| 직접 연결/감독 | 로봇 제어함 전원 ON, 이 PC 직접 케이블, 다른 연결/Writer 종료, 감독자와 E-stop 접근, 빈 제한 영역. 매 시험마다 확인 |

SDK3.4.2.2의 JointLimitConfig 실제 wire schema는 `joint_pos_limit.{min,max}`,
robot_name, source_file이다. docstring과 달리 속도/가속도 배열은 없다. MoveIt YAML의
5rad/s²는 계획 가정이므로 사용하지 않는다. 설치 모델 참고값 J1..J5 ±175°, J6 ±215°,
최대 속도150/180°/s는 `config/indy7_manufacturer_reference.json`에 제조사 참고로만 보존했다.
이번 운영 제한은 별도 검토가 필요하며 미확정 null을 임의로 채우지 않았다.
제조사의 [일반 Indy 충돌 감지 설명](https://en.neuromeka.com/indy)은 특정 FW의 MoveTeleJ
입력 중단/통신 watchdog을 입증하지 않는다.

## 2. 무이동 getter capture와 하드웨어 draft

기본 명령은 설명만 출력하고 연결하지 않는다.

```bash
.venv/bin/python scripts/inspect_indy7_real_readonly.py
```

장비 앞 작업자가 별도로 다음을 실행한다. 명령 사이 E-stop 조작은 사람이 제조사 절차로
수행한다. 스크립트는 Home/정지/Servo/Recover/설정 변경 기능이 없다. 오류 시 추가 getter를
중단하고 연결을 닫는다. IP는 실제 장비와 대조한다. 기존 출력은 덮어쓰지 않는다.

```bash
.venv/bin/python scripts/inspect_indy7_real_readonly.py \
  --connect-readonly --approve-readonly "현장 작업자" --ip 192.168.1.114 \
  --phase clear_before --output tmp/real_site/clear_before.json
# 사람이 E-stop을 누르고 실제 정지 확인
.venv/bin/python scripts/inspect_indy7_real_readonly.py \
  --connect-readonly --approve-readonly "현장 작업자" --ip 192.168.1.114 \
  --phase pressed --output tmp/real_site/pressed.json
# 사람이 E-stop 해제/필요한 기존 수동 준비 절차 수행. 자동 복구 없음
.venv/bin/python scripts/inspect_indy7_real_readonly.py \
  --connect-readonly --approve-readonly "현장 작업자" --ip 192.168.1.114 \
  --phase clear_after --output tmp/real_site/clear_after.json
```

각 getter의 요청/응답 monotonic 시각과 지연을 보존한다. 센서 timestamp가 아니다.
연결 시에만 SDK를 import/생성하고 RPC deadline200ms를 적용한다. 이동/불명확한 상태는
stationary capture 실패다. 세 capture와 제조사 정의 확인 후 아래 설명을 실제 근거로 바꾼다.

```bash
.venv-act/bin/python scripts/inspect_indy7_real_readonly.py \
  --prepare-hardware ai/execute/config/real_hardware_review_required.json \
  --captures tmp/real_site/clear_before.json tmp/real_site/pressed.json tmp/real_site/clear_after.json \
  --definition-reference "해당 FW 제조사 필드 정의 문서/페이지" \
  --review-record "현장 E-stop 회로와 3구간 확인 기록" \
  --supervised-direct-link \
  --output ai/execute/config/real_hardware_reviewed.json
```

이는 장비 연결 없이 파일과 로컬 kernel route/device만 확인한다. 장비 신원, 원문 SHA-256,
E-stop 쌍, 직접 유선 interface를 결합한 **미승인 draft**다. 필드가 구별되지 않거나 의미가
확정되지 않으면 사용하지 않는다. 근거 파일 변경/FW 변경은 실행 전에 거부한다.

## 3. 기존 두 프로필만 완성

- `config/indy7_review_required.json`의 별도 검토본 `indy7_real_reviewed.json`:
  운영 min/max, vmax/amax, max_step, target-current/tracking 오차 및 기존 timing/stop 기준.
- 위 `real_hardware_reviewed.json`: SDK getter별 단위와 근거, 실제 초기 자세 rad/Project Home
  여부/허용 오차, RPC budget, 정지 안정 구간/검증 기록, 단일 PC 운용 근거인
  external_exclusion_record와 전체 검토 기록을 완성한다. 검토 완료 후에만 reviewed_for_real을 승인한다.

초기 자세는 실제 안전한 시작 자세다. SDK Home을 자동 실행하지 않는다. 기존 ratio0.8/7.0은
검토된 기존 Driver 값 그대로이고 임의 변경하지 않는다. RPC timeout은 command budget 이하,
정지 안정 구간은 stop timeout 미만, 직접 링크 재검사 간격은 status stale 이하여야 한다.
20Hz 유효 step은 min(max_step, vmax×0.05)이고 더 짧은 실제 송신 간격도 적용한다.
단위/속도/가속도·오차를 확인할 자료가 없으면 null을 유지한다.

direct mode는 interlock_file/interlock_issuer를 사용하지 않는다. 기존 external mode는
신뢰할 실제 source의 fresh/session-bound 신호가 필요하며 수동 heartbeat를 만들지 않는다.

## 4. 매 시험의 사전검사와 5초 실행

작업자가 수집 저장을 마친 뒤 기존 QP/Driver를 승인된 종료 절차로 닫는다. 에이전트는
운영 프로세스를 종료/재시작하지 않았다. 실제 두 카메라와 Mark7 상태는 준비해 둔다.
Mark7는 기존 선택 기능을 적용한 source를 다음처럼 별도 시작할 수 있다(운영 중 적용 금지).

```bash
.venv/bin/python ros2_ws/src/capstone_mark7/capstone_mark7/mark7_node.py \
  --ros-args -p publish_command_state:=true \
  -r /gripper/open:=/capstone/real_gripper/open \
  -r /gripper/grasp:=/capstone/real_gripper/grasp
```

실제 성공 응답을 받은 그리퍼 명령 상태가 필요하다. 초기0/1을 가정하지 않는다. 이는 손가락
위치/파지 센서가 아니다. 카메라 토픽·관절 순서·7D/rad/20Hz 계약은 유지했다.

```bash
# CHECKPOINT를 실제 10개 성공 시연으로 학습한 checkpoint 경로로 지정
act execute --mode real --preflight-only --checkpoint "$CHECKPOINT" \
  --config ai/execute/config/indy7_real_reviewed.json \
  --hardware-profile ai/execute/config/real_hardware_reviewed.json
```

exit0 + profile_valid:true는 파일 검사를 통과했다는 뜻이며 실행 허가는 아니다. exit2는
errors를 해결한다. 모델/SDK/ROS를 시작하지 않는다. 실제 실행에서는 장비 serial/FW/SDK,
controller 제한, E-stop/명령권, Servo/Ready/Fault/mode, 초기 자세, 관측 freshness를 다시 확인한다.

```bash
act execute --mode real --checkpoint "$CHECKPOINT" \
  --config ai/execute/config/indy7_real_reviewed.json \
  --hardware-profile ai/execute/config/real_hardware_reviewed.json \
  --approve-real "현장 감독자: 직접 케이블/다른 Writer·Conty 종료/빈 영역/E-stop 접근 확인" \
  --device cuda --duration 2 --output-dir outputs/temporary/inference/real_trial_001
```

실제 상태가 부적합하면 새 목표를 보내지 않는다. 실행 중 모든 이상은 기존 Controller/
Watchdog의 정지·Scheduler 폐기 경로를 사용한다. 무한 반복/자동 재개/자동 Home은 없다.

## 5. 정지와 결과 확인

```bash
act execute --stop-run outputs/temporary/inference/real_trial_001
act execute --status-run outputs/temporary/inference/real_trial_001
```

Ctrl+C도 같은 정지 경로다. 물리적 위험 시 기존 하드웨어 E-stop을 사용한다.
SDK CAT2 stop_motion + stop_teleop ACK와 실제 정지를 구분하며, fresh post-request 상태,
idle5/이동·queue·stopping 없음, qdot 및 안정 구간을 확인한다. 확인 불가면 FAULT/quarantine,
자동 reset은 없다. 단순 명령 발행 중단을 물리 정지로 주장하지 않는다.
Python Watchdog는 SIGKILL/OS freeze/네트워크 단절 때 stop 도달을 보장하지 않는다.

run 루트 result.json/server.log/client.log와 client/events.jsonl/execution_status.json/
summary.json을 본다. `stop_confirmed`, feedback_samples, measured_joint_change_rad,
real_feedback의 갱신 시각과 다음 prediction 반복을 확인한다. Action→관절 변화→새 관측의
반복이 목표이며 ACK·서비스 성공·파지/Task Success로 대체하지 않는다.

검증 근거는 `tmp/real_commission_20261103/`에 있다. 새 SDK E-stop/진단/preflight와 직접 링크/
외부 연결·잠금·5초 제한을 Fake로 검증했다. 실제 정지 거리·통신 장애·모델 움직임은 미검증이다.

RPC 진단 보강: 각 getter의 ok/grpc_code/error를 콘솔과 samples[].calls에 기록한다.
E-stop 관측의 필수 getter는 device/control identity, motion, safety_control_data다. 제한/Servo/boot 등
추가 진단 조회 실패는 보존하고 계속 관측한다(Real 실행에서 선택 사항이라는 뜻은 아님).
필수 실패는 capture_valid:false와 종료코드2로 저장한다. SafetyControlData 실패 시 공식 SDK의
GetSafetyFunctionState도 별도 조회하지만 E-stop 의미/펌웨어 지원은 미확인이므로 자동 대체하지 않는다.
기존 오류만으로 실패한 RPC는 특정할 수 없으며 재실행의 getter별 출력으로 확인한다.

## 2026-11-03 RPC 실측과 정지 연동 후속
사용자는 물리적 E-stop 정지 및 CONTY 수동 복구 전 명령 거부를 반복 운용으로 확인했다.
물리적 E-stop/복구 반복 시험을 요구하지 않는다. getter-only 지원 관측 원문은
`tmp/real_site/rpc_support_readonly_20261103.json`이다. 현재/원래 JointLimitConfig 두 RPC는
UNIMPLEMENTED, Boot20010은 UNAVAILABLE/connection refused였다. SafetyLimits/StopConfig/
collision policy/control/Servo/motion/stop/violation/safety_control/teleop 조회는 성공했다.
원래 스크립트의 첫 실패는 get_joint_limit_config였다. 설치 SDK에 메서드가 있다고
컨트롤러에서 서비스가 지원되는 것은 아니다. 실제 제한/boot를 정상으로 위장하지 않는다.
운전 monitor는 매 갱신 후 기존 feedback.require와 허용 mode를 검사하고, 불가/미확인/
통신 오류는 기존 sticky gateway trip/Controller/Watchdog 정지·세션 종료로 보낸다.
CONTY 복구 후에도 해당 ACT 세션은 재개하지 않는다.
현재 Real 시작은 미확정 운영 프로필/직접 E-stop 필드 의미 외에도 미지원 JointLimitConfig와
접속 불가 Boot의 기존 필수 검사에서 차단된다. 이 사실을 구현 완료/실행 준비 완료로 숨기지 않는다.
대체 정의 없이 필수 검사를 삭제하지 않았다. 다음 현장 자료는 기존 정상 운용의 정지/복구 전후
상태 capture면 충분하며, 새 충돌/이동/E-stop 성능 시험을 요구하지 않는다.

## GA12 호환 검사 후속
실측 SDK3.4.2.2 / controller3.3.2-GA12 [2026.09.15]에만 지원 API 집합을 선택한다.
미지원 두 JointLimitConfig는 호출하지 않고 live SafetyLimits와 제조사 참고 범위의 교집합으로
검토된 ACT 운영 범위를 검사한다. 단위 검토는 get_safety_limits에 필수이며 다른 FW는 기존
검사를 유지한다. Boot를 정상으로 위장하지 않고 boot_ready=None으로 보존한다. 이 경우
정확한 Servo OperationEnabled/active/brake 상태와 control의 connected=true, sim=false,
허용 op/method 및 기존 stop/violation 검사를 함께 요구한다. 관측된 connected=false는 계속 차단한다.
E-stop id0/state255는 여전히 해제 신호가 아니다. qualified E-stop/제어 불가 의미 근거 없이
직접 연결 모드의 관련 검사를 제거하지 않았다. 사용자 물리 E-stop/CONTY 운용 확인은 유지한다.
검토본 두 파일은 현재 없고 템플릿만 있다. 정식 act1_108_001에는 success metadata/receipt가 없고
10개 실제 Snapshot/학습 모델도 없다. 발견된 모델은 synthetic 3-step CPU/CUDA 검증용이다.
새 호환 분기 집중 Mock33개 통과: tmp/ga12_compat_tests.xml. 이번 작업에서 장비 명령은 없다.

## GA12 connected flag 후속
normal_control_state_03.json과 기존 실제 Home 운용에서도 is_robot_connected=false가 반복됐다.
설치 SDK는 bool과 기본값 포함 MessageToDict만 제공하며 해당 FW의 정확한 내부 의미는 미확인이다.
false를 실제 TCP 단절의 단독 증거로 사용하지 않는다. 정확한 GA12/SDK 조합에서 fresh successful
non-simulation SDK payload를 transport availability로 분리했다. raw flag는 로그에 보존한다.
Servo active/brake/OperationEnabled, op/method, stop/violation, freshness/authority/estop 검사는 유지한다.
op5=IDLE, op17=TELE_OP는 공식 Driver indy_define.py에 정의돼 있다. method11은 기존 QP의
relative 모드이고 ACT method10과 달라 현재 관측 상태 그대로 ACT 명령을 보낼 수는 없다.
safety_state id0/state255의 E-stop 의미는 확인되지 않았고 정상 해제로 사용하지 않는다.
SafetyLimits의 +/-175(J1..5), +/-215(J6)는 설치 URDF degree와 일치한다. 자동 근거 파일은
tmp/real_site/ga12_profile_evidence.json이며 운영 제한/프로필 승인이 아니다.
물리 E-stop 재시험은 요구하지 않는다. 실제 제어 불가 신호의 검증된 대응이 없으면 기존
E-stop 자격 게이트는 유지된다. 실제 Checkpoint/운영 프로필 미준비도 기존대로 차단된다.

## 5초 프로필 검토본 생성 후속
현재 config/indy7_real_reviewed.json, real_hardware_reviewed.json 두 파일을 미승인 초안으로
생성했다(reviewed_for_real=false). 과거 문서의 미존재 상태는 생성 전 이력이다. 장비 신원,
SafetyLimits degree 근거, 기존 SDK ratio, 과거 관측 초기 자세 후보만 채웠다. 운영 min/max,
속도/가속도/오차와 stop/RPC 승인값은 임의 확정하지 않았다. PROFILE_REVIEW.md가 각 필드의
근거/설정 방법과 최소 정지 상태 관측을 설명한다. 저장된 정상 자료만으로 E-stop/제어 불가
대응은 증명되지 않아 자격 게이트를 유지한다. 물리 E-stop 성능 재시험은 요구하지 않는다.
실제 모드 전환은 기존 request_absolute_mode→start_teleop(method10)→fresh 상태 확인이 있으며
이번 작업에서 실행하지 않았다. 초안 preflight는 예상대로 exit2, 장비 명령0이었다.

## GA12 E-stop 3구간 자격 확인 완료
normal_control_state_03 / control_unavailable_01 / control_recovered_01의 동일 장비 정지 관측을
검토했다. op17→2→5, Servo on→off→on, Brake off→on→off, Stop -1→0→-1,
Violation 0→262144(EMG Button Activated)→0이 대응한다. 공식 Driver는 op2=OP_VIOLATE,
op5=IDLE, op17=TELE_OP이며 Servo OperationEnabled는 SDK의 EtherCATStatus 정의를 사용한다.
설치 SDK에는 262144와 EMG 문자열의 독립 숫자 매핑이 확인되지 않았다. 다른 motor.py의
ISO_EMERGENCY_BUTTON 상수를 DCP3 violation code에 전용하지 않는다. 숫자/문구는 컨트롤러
자체 응답 근거다. safety_state255는 정상 해제로 해석하지 않는다.
기존 sdk_estop 게이트에 kind=ga12_control_availability를 추가해 복합 상태를 검사한다.
normal/recovered 모두 Servo/Brake/Stop/Violation/op/sim/motion 조건이 통과해야 한다.
pressed는 위 EMG 및 off/brake/stop/op 실측 signature까지 확인해 자격화했다. captures의
SHA256과 serial/FW/SDK를 기존 hardware 초안에 결합했다. 게이트 삭제/항상True가 아니다.
런타임은 한 번 불가/unknown이면 sticky revoke하고 복구 후에도 기존 세션을 재개하지 않는다.
기존 state-pair 모드도 유지하지만 이번 장비의 활성 후보는 복합 조건이다.
자격 관측은 완료됐으므로 추가 E-stop/복구 관측을 요구하지 않는다. 하드웨어 안전 회로의
인증을 새로 주장하거나 호스트/네트워크 장애시 software stop 도달을 보장하지 않는다.
실행 승인값(운영 범위/속도/가속도/step/오차/초기 허용/정지 안정·시간/RPC/직접 링크)과
reviewed_for_real은 아직 미승인이다. 기존 sdk ratio와 초기 후보도 자동 승인하지 않는다.
집중 Mock31개 통과(tmp/ga12_estop_qualified.xml): 제어 불가→Action 차단/Chunk 폐기/
Fake stop 호출, 복구 후 기존 세션 재개 거부. 실제 장비 연결·명령·재시험은 없었다.

## Safety Profile 소프트웨어 준비 후속
프로필의 운영값/승인 flag는 변경하지 않았다. SDK_RPC_timing 계측과 Controller/Watchdog의
5초 wall-time 종료 및 Driver 송신 직전 deadline 검사, Preflight의 완료된 E-stop 자격 표시를
보완했다. Fake/Mock118개와 후속7개(중복 포함, 고유119개) 통과, 모델 통합2개 제외.
정확한 필드별 승인/장비측정/제조사 확인은 PROFILE_REVIEW.md 후속 표를 따른다.
실제 SDK/ROS/시리얼/모델 실행·장비 제어 명령·커밋은 없었다. 운영 한계와 정지/통신 장애
근거는 현장 승인 전이며 현재 Real 프로필은 expected fail-closed다. E-stop 자격은 해결된
상태로 유지하고 같은 실물 시험을 반복하지 않는다.

## 기존 운용값 재사용 확정 (후속, 연구팀 내부 검토 기준)
제조사 관계자/지도교수의 별도 승인은 필수 절차가 아니다. 연구팀이 객관적 기존 운용 기록,
설정과 코드 적용 여부를 확인해 내부 승인한다. 제조사 기술 자료는 근거 선택지이며 별도
서명을 받아야만 진행한다는 뜻이 아니다. 미검증 동적 동작을 내부 승인만으로 검증 사실로
바꾸지도 않는다.

| 항목 | 기존 QP/Home | ACT 이전 | 최종 반영 | 판단 |
| --- | --- | --- | --- | --- |
| 관절 범위 | QP hard J1..5±175°,J6±215°; soft margin15°는 centering 기준 | null | null 유지 | 실제 SafetyLimits/URDF와 hard bound 일치. 장애물/작업 공간 범위는 증거 없음. margin을 안전 작업영역으로 위장하지 않음 |
| 속도 | QP45°/s,30Hz | null | 0.7853981634rad/s 각축 | Guard command/actual velocity 검사에 적용. 실물 ACT 추종 인증 아님 |
| 가속도 | QP ratio1/Home100; absolute Driver7 | null | null 유지; ratio7 그대로 | ratio를 rad/s²로 환산할 계약 없음. QP의 Cartesian600mm/s²도 관절 가속도가 아님 |
| Step | QP45/30=1.5°/tick | null | 0.02617993878rad 각축 | ACT20Hz에도1.5° 유지해 명령 변화율 상한30°/s. 더 느슨한2.25°로 확대하지 않음 |
| Tracking/target error | Home 도착0.5°,실측endpoint약0.012° | null/150ms template | null 유지 | 동적 추종 임계값과 도착 허용은 다른 의미. 150ms도 미승인 템플릿 |
| 초기 자세 | 기존 YAML Home,0.5° 도착 검사 | 과거 관측 후보/오차null | 정확한 Home rad,home=true,오차0.00872664626rad | 기존 최대6축 error 판정과 동일. 실제 현재 자세는 시작 전 fresh feedback으로 검사 |
| 정지 기준 | Home low-qdot0.01rad/s,stable250ms | low-qdot0.01/stablenull | 0.01rad/s,250ms | 동일 정지 안정 기준 재사용. 명령 후 정지 소요시간과 정지 거리는 별개이며 stop record 미확정 |
| RPC | Home max_rpc25ms,실측4-getter max17.20ms | null | 25ms | 보수적 fail-fast software budget. command/stop 지연 검증값이 아님; timeout은 trip/세션 폐기 |
| interface/Writer | QP lock,운영자 직접 PC 연결 | interface null | enp11s0 | 로컬 route/sysfs 확인. 여타 Writer 종료는 매 실행 필수, 로컬/remote 새 Writer를 kernel ACL로 막는다고 주장하지 않음 |

세부 출처/단위/실물 사용 범위/ACT 적용과 승인 상태는
config/indy7_real_value_sources.json에 기록했다. inherited software ceiling과 같은 의미의
Home 정지/도착 기준은 채택했지만 전체 reviewed_for_real은 false를 유지한다. Home/Recorder/
Snapshot 소스나 데이터는 변경하지 않았다.

실제 수정은 두 검토 프로필, 설정 출처 파일, 상속값 검증 테스트 및 기존 Preflight 테스트의
미확정 항목 기대값(velocity→acceleration)이다. 추가 안전 시스템/제어 알고리즘은 없다.
Mock20개 통과(tmp/profile_reuse_20261103.xml). 실제 Step을 기존 Controller에 넣어 통과/초과
차단을 확인했고, Home YAML과 초기 자세 일치/단위/더 엄격한20Hz 상한/미승인 유지도 검사했다.
기존 전체119개를 불필요하게 반복하지 않았다.

남은 숫자: 운영 min/max, amax, target-current 및 지속 tracking 오차, 전체 stop timeout의 실물
근거와 미승인 timing/통신 장애 처리 근거. 처음 세 항목은 현재 기록으로 확정 불가하며
정지 관찰만으로 동적 오차/정지 거리를 측정할 수 없다. 연구팀이 기존 기록/해당 설정 근거를
추가 제공하거나 범위를 정한 별도 현장 검증으로 정한다. 새 개발은 요구하지 않는다.
기존 소프트웨어 stop 동작이나 통신 장애의 안전성을 Mock/E-stop 기록으로 대체하지 않는다.
외부 승인 절차를 추가하지 않는다. 전체 승인 후 같은 두 파일에 review_record를 작성한다.

## ACT500→5000 추가 학습 및 목표 갱신 계약 후속
기존 독립 act train --resume으로 동일10개 Snapshot 총5000step완료. 원래scheduler horizon500
유지/LR1e-6,optimizer계속업데이트. run:outputs/preserved/training_runs/act1_real10_resume5000_20261103_v1.
Checkpoint3000/5000을 동일160관측으로 재평가: 첫target-state pooled평균4.876→4.840→4.856deg,
160개 모두1.5deg초과. 실제시작10개평균1.565→1.840→1.946deg,실패10/10유지. Loss감소가
실행가능성을 증명하지 않는다. 원래500step모델/데이터는보존했다.
기존Guard안에선택적 bounded_rate 연속targetupdate를추가. 첫Actionstrict유지, raw목표의
범위/현재오차(min target-error,tracking-error) 검사후에만 기존step/v/a교집합내 출력한다.
원래goal/실제출력을별도로로그, 새큐/만료목표replay없음. defaultstrict;hardware초안bounded선택
하나reviewed_for_real=false 및실물운영값null유지. 큰초기오차를잘라무조건따라가지않는다.
실제5000예측MockGuard양모드0명령/160차단. 직접영향78테스트와추가9개(중복제외81)통과,
bounded모드Scheduler/RealSession/새feedback루프·통신/운영자stop도Fake확인. 실제장비접속없음.
목표제한은시간지연/arm-gripper타이밍변화를만들어실물충돌/작업성공보장아님. 상세run의
RESUME_AND_CONTRACT_REPORT.md. 실제초기예측실패와운영profile승인은여전히실행차단조건.

## 2026-11-04 최초 실물 시험 시도
사용자의 현장 구동 승인으로 최대2초 Real Session을 한 번 실행했다. 시작 전 Home 오차
최대0.012°, idle, 두 D435 새 프레임, 그리퍼 명령 상태0.0, Writer released를 확인했다.
실행 중 카메라 시간차42.42ms가 설정40ms를 넘어 첫 Chunk/관절 명령 전에 중단됐다.
관절 명령0개, ACT 재추론0회, Mark7 요청0개다. 별도로 기본 rclpy Executor의
관측/Driver 동시 spin이 `Executor is already spinning`을 일으켰다. Driver의 전용
SingleThreadedExecutor 적용 후 장비 없는 동시 spin 검사를 통과했으며 실물 재시도는 없다.
StopMotion/StopTeleOp RPC는 성공했지만 세션의 정지 확정은 실패해 Writer가
`quarantined`다. 사후 getter는 idle5, motion=false, queue0이고 관절 변화는 측정
잡음 범위(최대0.00033°)였다. 다음 실물 실행에는 새 현장 정지/제어권 확인에 따른
공식 Writer 수동 복구와 별도 실행 승인 및 기존 Preflight가 필요하다. 안전 시간 제한은
변경하지 않았다. 실행 로그: `outputs/temporary/inference/act_real_closed_loop_20261104_01/`.

### 비구동 원인 수정 및 Writer 복구
실행 중 42.42ms는 두 D435의 최신 Header를 각각 고른 뒤 비교한 값이다. 한 스트림에
66.7ms 프레임 간격이 있었다. 두 카메라는 global time과 ROS system time을 사용하며
실측 Header는 같은 Unix epoch 범위였다. 물리적 노출 동기화까지 의미하지 않는다.
ACT 관측에만 기존 버퍼의 가장 가까운 인과적 프레임 쌍 선택을 적용했다. 40ms skew,
50ms 나이, 원래 Header/시계 검사는 그대로다. 유효 쌍이 없으면 실행을 차단한다.
Recorder의 기존 선택은 바꾸지 않았다. 실제 D435 비구동20Hz/60ms 관측100회에서
99회 유효, 최대 skew16.65ms/나이45.89ms, 1회 나이 조건 차단이었다.
분리된 Executor는 실제 ROS 카메라 관측 스레드와 동시에 비구동 spin해 오류가 없었다.
첫 실행의 정지 확정 실패는 Driver Executor가 이미 `Executor is already spinning`으로
종료되어 ROS stop service future가 처리되지 못한 채1초가 지난 것이 원인이다.
별도 SDK StopMotion/StopTeleOp ACK를 정지 확정으로 승격하지 않는다. 사용자의 사후
현장 확인과 새 SDK getter 3회/다른 TCP·Writer 부재를 근거로 공식 절차를 통해
Writer를 `released`로 복구했다. 확인자 이름은 제공되지 않았다. 프로필·Checkpoint·
GPU·안전 제한은 변경하지 않았고, 실물 재시험은 새 구동 승인 전 하지 않았다.
관측 근거: `tmp/real_site/camera_pair_exact_dry_20261104_01.json` 및
`tmp/real_site/act_writer_stop_20261104_2217.json`.

### 두 번째 실물 시도와 ACT Lease 복구
새 현장 안전·최대2초 구동 승인 후 Real Session을 한 번 실행했다. CUDA와 실제
Guarded Driver `/joint_states`는 정상으로 들어왔지만, 이전 시도의 ACT 명령 Lease
`quarantined`가 별도 파일에 남아 `previous_owner_unresolved_requires_manual_reconciliation`
으로 첫 명령 전에 차단됐다. Chunk/관절 명령/Mark7 요청0, Driver Executor trip0,
정지 SDK RPC0이다. Lease 미획득으로 세션의 정지 확정 경로는 이번에도 미검증이다.
종료 시 Writer는 기존 보수적 정책에 따라 격리됐다. 자동 재시도는 하지 않았다.
수동 복구 스크립트가 새 정지 getter·현장 확인·다른 TCP 부재를 검사한 후 동일한
증거로 ACT Lease와 Writer를 모두 inode 유지로 복구하도록 수정했다. 관련 집중
오프라인 테스트2개가 통과했다. 사용자의 두 번째 시도 후 현장 확인과 새 getter3회의
idle/motion=false/queue0/qdot 최대0.0038rad/s에 근거해 두 잠금을 `released`로
복구했다. 현장 확인자 이름은 제공되지 않았다. 재시험은 별도 새 구동 승인 전 금지다.
근거: `outputs/temporary/inference/act_real_closed_loop_20261104_02/` 및
`tmp/real_site/act_retrial_stop_20261104_02.json`.

### 세 번째 실물 시도: 첫 Action 송신 기한 차단
사용자의 새 현장 안전/최대2초 구동 승인으로 한 번 실행했다. 두 잠금 released,
idle/Home 오차0.012° 이내, 두 D435/Mark7 상태0.0, Preflight 통과를 확인했다.
CUDA 실제 관측으로 Chunk1개를 생성하고 Guarded Driver `/joint_states`를 수신했지만,
첫 Action의20ms 기한이 Adapter 최종 송신 검사 전에 지나 `adapter_action_deadline_expired`
로 중단됐다. 관절 명령0/Mark7 요청0/재추론0, 자동 재시도0이다. Guard 제한과
시간 제한은 유지했다. StopMotion/StopTeleOp ACK 후 fresh idle/qdot 안정 구간까지
확인해 세션 stop_confirmed=true, Writer와 ACT Lease 모두 released다. 사후 getter도
idle5/motion=false/queue0/qdot 최대0.00238rad/s다. 이 시험으로 실제 ACT 이동이나
새 관측 재추론을 검증했다고 주장하지 않는다. 근거:
`outputs/temporary/inference/act_real_closed_loop_20261104_03/`.
