# ROS Dry Run 관측 안정화와 제한적 복구

이 기능은 `./act infer --source ros --dry-run --ros-observation-recovery`에서만
명시적으로 활성화한다. 기본값은 기존 Fail-fast다. Synthetic/Mock/Real 실행 및
Recorder/Dataset 수집에는 적용하지 않는다. 로봇·Gripper 명령은 보내지 않는다.

## 기존 구조와 변경 범위

- RosSource는 카메라2개·Joint·Gripper를 BoundedHistory에 보관하고 공유
  HistoryAligner로 인과적 관측을 고른다. 원본 Header와 callback 시각을 구분한다.
- 기존 begin()은 현재 시각으로 즉시 관측을 검증한 후, run_session이20Hz 격자와
  수신 여유60ms를 사용한다. 실행 중 IntegrityError는 모두 세션을 종료했다.
- 옵션을 켜면 RosSource.begin_stabilization()은 실제 Joint와 알려진/명시적 가정
  Gripper를 확인해 **준비용 기준 격자만** 만든다. 이는 유효 관측/추론 준비 완료
  판정이 아니다. 이후 sample()로 실제20Hz 기준 시각의 관측을 지연 정렬·검증한다.
  카메라를 wall-now로 먼저 판단해 수신 지연/위상 차이로 시작을 놓치는 것을 피한다.
- ACT backend는 predict_action_chunk()를 사용한다. Chunk 소비 주체는 기존
  DryRunScheduler 하나이며 서버의 select_action Queue를 중복 사용하지 않는다.
  복구용 Action Queue를 추가하지 않았다.

## 초기 안정화

기본 조건은 **연속3개**의20Hz 기준 관측 검증 통과다. 첫 기준은 격자 시작50ms 뒤이며,
각 기준 이후 기존 receive_wait_ms=60ms를 두고 선택한다. 이상적인 입력에서도 준비용
격자 이후 약210ms가 필요하다. 노드 초기화·필수 입력 도착·실제 지터 대기는 별도다.

안정화 전체 최대 대기는 기존 `--observation-startup-timeout`(기본5초)이다.
준비 시간은 초기 RPC reset 완료 뒤 안정화 루프 진입부터 측정한다.
`--ros-stable-observations 3`으로 필요한 연속 개수를 명시할 수 있다. 최초 Joint와
Gripper가 준비되지 않거나 카메라 프레임이 아직 없는 경우는 readiness 대기로
기록하며 active 오류/프레임 누락으로 세지 않는다. 이미 시도한 기준에서 age/skew가
잘못되면 안정화 streak를0으로 돌리고 별도 startup 폐기 수를 남긴다.

초기3개가 통과하기 전에는 추론 요청과 Action 출력이 없다. 마지막으로 검증한
안정화 관측을 첫 추론에 사용할 수 있다. 초기 Header/Clock/형식 오류는 즉시 종료한다.
기본 Fail-fast의 초기 준비 경로는 변경하지 않았다.

## 실행 중 복구 대상

| 오류 | 처리 |
|---|---|
| camera_skew | 복구 시도 |
| observation_too_old + topic=Camera A/B | 복구 시도 |
| missing_causal_observation + topic=Camera A/B | 복구 시도 |
| Joint의 stale/부재, observation_skew, Gripper 오류 | 즉시 종료 |
| Header 역전/0/비정상/future, use_sim_time, Clock 불일치/점프, History overflow | 즉시 종료 |
| 처리/Scheduler deadline 누락, 현재 추론 timeout/잘못된 출력/통신 오류 | 기존대로 종료 |

복구 대상 오류가 난 **해당 기준 관측은 ACT에 보내지 않는다**. 즉시 active/prefetched
미실행 Action을 비우고 Scheduler를 STOPPED로 둔다. 요청 중이었다면 그 request ID를
obsolete로 표시한다. worker/socket을 계속 만들거나 Action Queue를 중복 생성하지
않고 기존 한 worker가 응답/timeout을 마칠 때까지 결과를 버린다. 이 대기는 ROS
callback을 막지 않으며 Action을 출력하지 않는다.

계속 같은 관측 격자를 검증하고, 오류 이후의 새 정상 관측과 RPC 준비가 확보되면
기존 Scheduler를 WARMUP으로 전환해 새 request ID로 요청한다. 이전 응답은 pending
요청과 분리해 버리며, 새로운 결과를 받아 첫 Action을 출력할 때 복구 성공을 기록한다.
Session/기준 격자/누적 지표를 유지하며 clock 검사 anchor를 새로 잡아 오류를 숨기지
않는다. 재개 때 의도적으로 건너뛴 Action slot은 복구 pause로 표시한다. 실행 중 실제
deadline 누락은 여전히 종료 대상이다.

## 복구 한계와 변경하지 않은 조건

- 연속 복구 대상 오류 **3회째** 종료
- 최근10초의 복구 대상 오류 **6회째** 종료
- 실행 단계에서 정상 관측이 **1.0초** 이상 없으면 종료
  (`--ros-no-valid-timeout 1.0`으로 설정 가능)
- 동일20Hz reference는 한 번만 오류로 센다.1ms polling 횟수를 더하지 않는다.
- 정상 관측은 연속 오류 수를 초기화하지만 rolling10초 이력은 유지한다.
- duration 종료 시 아직 복구 중이면 `recovery_incomplete_at_session_end`로 실패한다.

이 값은 **비구동 시험 초기값**이며 실제 로봇 안전 기준이 아니다. Joint/Gripper/clock
오류를 일시적 카메라 문제로 분류하지 않는다. 아래 기본 설정은 그대로다.

| 설정 | 값 |
|---|---:|
| camera_max_age_ms | 50 |
| camera_skew_ms | 40 |
| state_max_age_ms | 75 |
| observation_skew_ms | 75 |
| receive_wait_ms | 60 |

알려진 Gripper 상태가 없으면 기존처럼 차단한다. ROS Dry Run에 한한 작업자 가정은
별도 `--operator-initial-gripper {0,1}`로만 허용하며 계속 operator_assumed로 표시한다.
복구 옵션 자체는 Gripper 초기 상태를 만들거나 검증을 우회하지 않는다.

## 로그와 결과 해석

client/summary.json의 observation_recovery와 client/events.jsonl을 함께 본다.

| 필드/이벤트 | 의미 |
|---|---|
| initial_stabilization_ms / initial_stabilization_succeeded | 준비 소요 시간·성공 여부 |
| startup_readiness_waits | 입력 대기 횟수. 프레임 누락이 아니며 이유별 polling/준비 기준 대기를 구분해야 함 |
| startup_valid_observations / startup_discarded_observations | 초기 검증/age·skew 폐기 수 |
| valid_observations / discarded_observations | 실행 단계의 정상 관측/복구 대상 폐기 수 |
| total_valid_observations / total_discarded_observations | 초기와 실행 단계 합계 |
| error_counts / fatal_integrity_error_counts | 복구 대상 오류 / 즉시 종료 IntegrityError 종류별 수 |
| consecutive_errors / maximum_consecutive_errors / recent_10s_errors | 실행 단계 오류 제한 상태 |
| recovery_successes / recovery_pending / failure_reason | Action 재개 횟수·미완료·실패 이유 |
| invalidated_actions | Queue에 있던 미실행 **소비 예정** Action 수. 아직 응답이 없는 요청의 크기를 추정해 더하지 않음 |
| abandoned_inference_requests / ignored_obsolete_responses | 무효화한 요청/실제로 도착해 무시한 응답 수 |
| actual_output_actions / robot_commands_sent | 실제 log-only 출력/실제 로봇 명령(항상0) |
| had_recovery / quality | 정상 종료여도 복구가 있었다면 recovered. 실패는 recovery_failed |

주요 이벤트는 observation_stabilization_start, stabilization_observation,
observation_stabilized, observation_recovery_pause, obsolete_inference_finished,
observation_recovery_resumed다. 개별 reference·오류 details·request ID를 남긴다.
기존 observation의 age/skew/reuse 및 prediction/action 로그도 유지한다.

복구 중에는 Action 출력에 의도적인 공백이 생긴다. 전체 actual_action_fps와 interval
최댓값에는 이 공백이 포함되므로 무중단20Hz 실행과 같은 결과로 보고하지 않는다.
exit0/complete만으로 무오류라고 판정하지 말고 had_recovery와 폐기 수를 확인한다.
이 기능은 Dataset을 생성하지 않으며 Recorder의 연속20Hz/invalid 정책을 바꾸지 않는다.

## 실제 ROS Dry Run 명령 — 이번 개발 작업에서는 실행하지 않음

이미 실행 중인 Camera/Driver를 재시작하지 않는다. 아래 Checkpoint는 기존 Synthetic
hold/open 시험 모델이다. 실제 조작 성능 검증용이 아니며 경로는 운영 모델로 바꿀 수 있다.
초기 Open 가정0은 작업자가 명시적으로 선택하는 값이며 실제 상태 측정이 아니다.

```bash
cd /home/sirlab2/sirlab258
ACT_CHECKPOINT=outputs/temporary/verification/act_final_offline_20261031T025706Z_09a8f399/integration_final/train_cuda/training/checkpoints/000003/pretrained_model

# 초기 안정화 후 실행 단계10초
./act infer --checkpoint "$ACT_CHECKPOINT" --source ros --dry-run \
  --operator-initial-gripper 0 --ros-observation-recovery \
  --ros-stable-observations 3 --ros-no-valid-timeout 1.0 \
  --observation-startup-timeout 5 --device auto --duration 10

# 같은 조건으로 실행 단계60초
./act infer --checkpoint "$ACT_CHECKPOINT" --source ros --dry-run \
  --operator-initial-gripper 0 --ros-observation-recovery \
  --ros-stable-observations 3 --ros-no-valid-timeout 1.0 \
  --observation-startup-timeout 5 --device auto --duration 60
```

각 실행은 기본적으로 별도의 새 outputs/act_infer_<ID>를 만든다. 전체 wall time에는
Checkpoint 로딩·초기 안정화·종료 처리가 추가된다. Grasp 가정은1, 실제 메시지만 사용할
경우 operator 옵션을 생략한다. 복구 flag를 빼면 기본 Fail-fast로 돌아간다. 복구 tuning
옵션만 단독 지정하는 것은 거부한다. setup/실제 장비 Launch/실제 실행은 시작하지 않는다.

## 비구동 검증 범위

가짜 시계/RPC로 실제 History/Aligner/Scheduler를 실행해 정상/초기 부재/위상 차이,
Skew/Age/프레임 부재, 새 관측·새 추론, Chunk 폐기·지연 응답 무시, 오류 한계와
rolling window 만료, 센서 정지·무정상 관측 timeout, 심각 오류·deadline·기본 Fail-fast,
실행 계층 차단을 검증한다. 실제 localhost RPC worker/REP와 FakeBackend를 쓰는
추가 시험도 포함한다.10초/60초 가상 시간 시험은 실제 장비 성능 측정이 아니다.
실제 카메라·로봇 ROS 노드나 Indy7/Mark7를 구동하지 않는다.
