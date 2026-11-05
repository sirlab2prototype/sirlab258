# 5초 시험 프로필의 현장 검토

두 *_reviewed.json은 이름과 달리 현재 **미승인 초안**이다. reviewed_for_real=false를 유지한다.
기존 Home/Recorder/Snapshot, GA12 호환 처리와 제어 전환을 재개발하지 않았다.

## 근거와 상태 판정

실측 제한은 degree J1..5 +/-175, J6 +/-215이며 모델 URDF와 일치한다. 실제 SafetyLimits는
시작 시 다시 조회한다. SDK/펌웨어/serial과 단위 근거는 hardware 초안에 작성했다.
initial_joints_rad는 normal_control_state_03의 과거 관측 후보이며 현재 자세를 증명하지 않는다.
SDK ratio .8/7은 기존 Driver 값이며 운영 속도/가속도의 승인을 뜻하지 않는다.

저장된 real_site 상태 자료에는 op5(IDLE)/17(TELE_OP), Servo active, stop=-1의 정상 상태만 있다.
과거 J6 문제는 해결된 이력이며 현재 E-stop 대응표로 전용하지 않는다. id0/state255의 의미를
정상 해제로 가정하지 않는다. 제어 불가 시 기존 Servo/Fault/Stop/mode/freshness 검사는
sticky trip으로 연결되지만, E-stop 자격 게이트를 대체할 전체 대응 근거는 없다.

기존 직접 SDK E-stop 매핑은 clear_before/pressed/clear_after 3구간 capture와 해당 FW 정의를
요구한다. 신규 코드 없이 사용 가능한 이 경로가 먼저 성립하는지 확인한다. 상태쌍이 변하지
않거나 E-stop 의미를 입증하지 못하면 이름만 바꿔 매핑하지 않는다. 다른 제어 불가 신호를
사용하려면 그 신호의 의미와 현재 게이트와의 동등성 검토가 필요하며 현재 완료로 간주하지 않는다.

## 현장 승인 항목 (기본값을 승인으로 취급하지 않음)

| 파일/필드 | 설정 방법 |
| --- | --- |
| safety joint_min/max_rad | 감독자가 승인한 빈 공간의 축별 운영 범위. 실제/제조사 범위 안에 설정. degree를 pi/180으로 변환. 하드웨어 최대를 그대로 복사하지 않음 |
| max_velocity_rad_s | 현재 제어 설정과 승인된 저속 운용 근거에 따른 6축 속도 상한. SDK SafetyLimits에는 관절 속도/가속도 배열이 없음 |
| max_acceleration_rad_s2 | 현재 장비/제어 설정과 검토된 운용 근거에 따른 6축 상한. MoveIt 계획용 가정을 가져오지 않음 |
| max_step_rad | 승인 vmax와 주기50ms로 vmax*0.05 이하를 검토. 실제 송신 간격이 짧으면 Guard가 추가 제한 |
| max_target_error_rad | 현재 측정 관절과 허용 목표의 최대 차이. 운영 영역/검토된 추종 능력으로 축별 지정 |
| tracking_error_rad, tracking_timeout_ms | 승인된 목표-실제 추종 오차와 허용 지속 시간. 정지 관측 오차만으로 이동 오차 한계를 확정하지 않음 |
| hardware initial_joints_rad | 시험 시작 자세를 선택. 제공 후보를 실제 시작 상태와 대조해 채택/수정 |
| initial_pose_is_project_home, initial_tolerance_rad | 해당 자세가 프로젝트 Home인지 확인하고 축별 실측 변동/허용 위치 오차를 바탕으로 허용값(rad) 지정. 자동 Home 없음 |
| stopped_velocity_rad_s | 정지 qdot 잡음과 허용 잔여 운동으로 검토. 기존 .01은 미승인 템플릿 값 |
| stop_timeout_ms, stop_stable_ms | 기존 정상 소프트웨어 정지의 실제 소요 시간과 연속 fresh idle/low-qdot 안정 구간 근거. 물리 E-stop 성능 기록과 혼동하지 않음. stable < timeout |
| rpc_timeout_ms, interlock_max_age_ms | 실제 getter 지연과 제어 budget으로 검토. rpc <= command_timeout_ms, age <= status_stale_ms |
| 나머지 timing/그리퍼 값 | 원본 템플릿 값이며 자동 승인 아님. 기존 실측/운용 근거와 대조 |
| direct_link_interface | 로봇 IP에 대한 ip -j route get 결과의 실제 직접 유선 dev. gateway/무선/bridge는 불허 |
| stop_validation_record, external_exclusion_record | 기존 소프트웨어 정지/명령 중단 동작의 근거와 직접 케이블/다른 Writer·Conty 종료/감독 조건 기록 |
| sdk_estop | 검증된 필드 정의와 capture 연결. 미확정 255를 입력해 통과시키지 않음 |
| review_record, reviewed_for_real | 위 근거를 작성하고 승인 권한자가 검토 완료한 뒤에만 true. null을 없애기 위한 임의 숫자 입력 금지 |

## 최소 현장 관측

로봇을 움직이지 않는다. 정상 상태 자료는 이미 있으므로 반복하지 않아도 된다. 기존 운용에서
정지/수동 복구 대기 상태가 발생했을 때 getter capture를 저장한다. 새 충돌/이동/정지 성능 시험은
요구하지 않는다. 필요 시 제조사 절차에 따라 **이미 정지한 로봇**에서 작업자가 상태 변화만
만들고 읽는다. 도구는 상태 변경을 하지 않는다. pressed는 라벨이지 E-stop의 증명이 아니다.

```bash
.venv/bin/python scripts/inspect_indy7_real_readonly.py \
 --connect-readonly --approve-readonly "현장 승인: 정지·복구 대기 상태 getter 관측만" \
 --ip 192.168.1.114 --phase pressed --output tmp/real_site/control_unavailable_01.json
```

CONTY 복구는 작업자가 기존 절차로 수행한다. 필요하면 복구 후 clear_after를 별도 새 경로에
저장한다. ACT 세션 자동 재개는 없다. 정상↔제어 불가 차이뿐 아니라 제조사 상태 정의/실제
CONTY 표시로 신호 의미를 연결해야 한다. 대응 신호가 없으면 실행은 계속 보류한다.

## 파일 완성과 오프라인 검사

```bash
nano ai/execute/config/indy7_real_reviewed.json
nano ai/execute/config/real_hardware_reviewed.json
act execute --mode real --preflight-only \
 --config ai/execute/config/indy7_real_reviewed.json \
 --hardware-profile ai/execute/config/real_hardware_reviewed.json
```

preflight는 모델/SDK/ROS를 시작하지 않는다. 현재 초안은 exit2가 정상이다. 검토 완료 후
exit0/profile_valid:true를 확인한다. 이 결과는 라이브 검사의 대체나 실물 안전 인증이 아니다.
실제 학습 Checkpoint가 준비된 후 같은 두 검토본과 --approve-real, --duration 5를 사용한다.
현재 상태 의미의 자격 확인이 완료됐다고 가정해 이동 명령을 실행하지 않는다.

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


## Safety Profile 최종 소프트웨어 검토 (후속)

범위 A~H를 기존 Guard/Controller/Adapter/Authority에서 확인했다. 제조사/URDF와 실제
SafetyLimits는 일치하며 운영 범위의 승인 근거는 아니다. 기존 QP 45deg/s 및 SDK ratio는
기존 운용 설정이지 ACT의 속도/가속도/추종 한계 검증값이 아니다. 관절 action은 7D의 첫6축
absolute rad, joint0..5 순서이며 Driver에서 한 번 deg로 변환해 movetelej_abs로 전달한다.
20Hz Guard는 실제 송신 간격과 vmax/amax/step/현재오차/지속 tracking을 검사한다.
초기 자세는 cached feedback의 finite 검증 후 Controller.connect에서 freshness/limits를
검사하며 임의 Home/자동 복구는 없다. 모드 전환은 기존 요청 ACK뿐 아니라 fresh mode10
확인을 기다리며 mode11/unknown/timeout이면 진행하지 않는다.

### 이번 실제 수정

- DeadlineStub가 group/RPC/요청·응답 monotonic/경과시간/deadline/status를 events.jsonl에
  SDK_RPC_timing으로 남긴다. payload 전체나 센서 시각을 만들어 내지 않는다. 코드 수준
  재시도는 없고 SDK unary 호출에 명시적 deadline을 전달한다. grpc 라이브러리 내부의
  transparent retry/네트워크 스케줄링까지 없다고 주장하지 않는다. timeout은 기존 trip으로
  이어진다. 이 계측의 실물 지연 결과는 아직 없다.
- 직접 연결 5초 mode는 기존 Controller.poll/Watchdog에서 RUNNING 기준 wall-time cap을
  검사한다. Scheduler 진행이 없어도 Action/Chunk를 폐기하고 기존 정지 경로를 호출한다.
  같은 deadline을 Driver에 전달해 SDK 송신 직전 만료 목표를 거부한다. 이미 송신한 목표의
  실제 정지 완료 시간/거리를 5초로 보장하는 기능은 아니다. OS freeze/네트워크 단절도
  일반 Python Watchdog로 보장하지 않는다.
- Preflight가 운영값 미승인과 이미 완료된 SDK E-stop 자격을 분리해 표시한다.
  E-stop의 기존 hash/신원/복합 상태 검사는 유지했다. 프로필 숫자/승인 플래그는 바꾸지 않았다.

### 현장 확인을 끝내는 방법과 합격 기준

| 구분 | 필요한 확인 / 비구동 확인 방법 / 통과 기준 |
| --- | --- |
| 실제 장비 측정 | 정지 상태의 기존 getter-only capture에서 q/qdot 변동과 각 RPC request/response 지연을 분석. 현재 초기 후보와 실제 q의 최대 차이를 산출. 모든6축 finite, 동일 신원, motion=false와 관측 시간 정합성을 확인. 후보 허용 오차는 책임자가 승인 |
| 실제 장비 측정 | 기존 정상 소프트웨어 stop 기록의 요청~fresh idle/low-qdot 안정 구간을 분석. 움직이는 로봇의 정지 시간/거리는 비구동 측정으로 새로 알아낼 수 없음. 기존 기록 또는 별도 승인된 후속 시험이 필요하며 이번 작업에서는 실행하지 않음. E-stop 반복 시험으로 대체하지 않음 |
| 실제 네트워크 측정 | 기존 정지 capture의 순차 RPC cycle p95/max가 50ms 주기 안인지 확인. 실제 운전 monitor 및 command RPC budget은 향후 SDK_RPC_timing으로 확인. getter 시간만으로 command/stop latency를 승인하지 않음. 선택 timeout은 command_timeout<=50ms와 status stale 예산을 충족해야 함 |
| 제조사 확인 | SDK ratio→실제 속도/가속도/보간의 관계, MoveTeleJ 입력 중단·PC 종료·네트워크 장애의 FW 동작과 설정. 정지 상태 관찰이나 클라이언트 timeout만으로 보장할 수 없음. 해당 FW 문서/지원 답변 또는 검토된 실제 기록이 필요 |
| 안전 책임자 승인 | 장애물·하중·감독 조건에 맞는 운영 min/max/vmax/amax/step/tracking/초기 허용 오차 및 stop 안정 기준. 승인 범위가 실제 SafetyLimits/제조사 범위의 교집합 안이어야 함. 값별 근거와 승인자를 review_record에 기록 |
| 직접 연결/Writer | READ-ONLY `ip -j route get 192.168.1.114`의 dev를 현장 직접 유선 케이블과 대조. 다른 PC/태블릿 연결과 Writer를 작업자가 정상 종료. runtime의 물리 interface/carrier/route, flock inode/소유자, foreign TCP/ROS 충돌 검사를 모두 통과해야 함. 임의 비협조 SDK client를 차단하는 ACL은 아니므로 시험 중 새 Writer를 실행할 수 있는 환경에서는 사용 불가 |

이미 끝난 GA12 E-stop 관측·물리 E-stop 성능 시험은 반복하지 않는다. profile의 미확정
운영값을 자동 추정하지 않는다. 예를 들어 정지 qdes-q 차이가 작아도 동적 tracking 오차,
vmax/amax나 실제 정지 시간을 결정할 수 없다. 따라서 승인 flag는 아직 false가 맞다.

### 오프라인 검증 / 최종 명령

직접 영향 Fake/Mock118개 통과, 모델 생성/추론 통합2개 제외:
`tmp/profile_readiness_final.xml`. Preflight/추가 wall-time 테스트7개 통과:
`tmp/profile_readiness_preflight.xml` (앞 집합과 중복 있음, 고유119개).
테스트14항목은 기존 test_execution/test_real_execution/test_direct_link/test_ga12_qualification과
신규 test_profile_readiness에서 확인한다. 실제 장비/SDK/ROS 연결 및 GPU 실행은 없었다.

Checkpoint 없이도 프로필/세 capture 해시/E-stop 자격/CLI 차단/Mock 검증이 가능하다.
현재 오프라인 결과는 tmp/real_site/profile_readiness_preflight.json이며 expected fail-closed다.
Checkpoint의 실제 통계/모델 로딩/정규화·모델 Action 품질은 이 검증에서 확인하지 않았다.
실제 학습 모델이 준비된 후에는 다음 명령을 쓴다.

```bash
act execute --mode real --preflight-only --checkpoint "$CHECKPOINT" \
 --config ai/execute/config/indy7_real_reviewed.json \
 --hardware-profile ai/execute/config/real_hardware_reviewed.json
```

exit0/profile_valid는 파일 검사의 통과이며 모델 로딩 성공까지 뜻하지 않는다. 기존 실제
런처에서 checkpoint 로딩과 관측/라이브 안전 검사를 수행한다. 미승인/불명확 값은 계속 차단한다.

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

## 선택적 연속 목표 갱신 승인 항목
target_update_mode는 strict 또는 bounded_rate이며 기본strict다. 현재미승인hardware초안은
bounded_rate를명시하지만 실제승인flag/숫자는미확정이다. 원래firstAction검사유지, 원본목표의
관절범위와current오차min(max_target_error,tracking_error)검사뒤 연속command만기존step/v/a
안에서갱신한다. 최종Guard는완화하지않는다. 지연·방향반전·그리퍼타이밍차이는내부검토대상.
이모드를승인했다고큰초기정책오차를따라가도된다는뜻이아니다.5000step실제관측도first모두차단.
