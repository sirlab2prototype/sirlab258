# ACT 최초 2초 실물 시험: 연구팀 일괄 승인안 (2026-11-04)

**2026-11-04 12:30 UTC 연구팀 운영값 승인.** 위 최초 시험 후보를 두 실물 프로필에 반영하고 `reviewed_for_real=true`로 설정했다. 이는 현장 확인이나 실제 관절 구동 승인이 아니다. 이 문서의 Mock 결과는 실제 움직임·충돌 회피·정지 성능을 증명하지 않는다.

**J6 범위 정정 승인(2026-11-04 후속):** 연구팀은 Home ±2°가 실제 Capstone Home 기준임을 명확히 했다. J6 Home 90.507°를 중심으로 승인 범위를 기존 88°~92°에서 **88.507°~92.507°**로 정정했다. 나머지 다섯 축은 설정 중심과 실제 Home 차이가 최대 0.005221°라 기존 승인 범위를 유지한다. 속도·Step·가속도·Tracking은 변경하지 않았다. 이 정정도 실제 구동 승인은 아니다.

## 첫 명령 제어 계약

기존 `bounded_rate`의 첫 Action은 raw 그대로 Guard에 전달되어, 1.5° Step 안이어도 QP 측정 가속도 외피에서 차단됐다. 정지 상태에서 축별 가속도 제한 `[4,3,4,5,3,5] rad/s²`가 허용하는 50ms 이동은 `[0.573,0.430,0.573,0.716,0.430,0.716]°`다.

이제 `bounded_rate`에서 **첫 raw 목표의 축 범위, 현재 관절과의 거리, 기존 1.5° Step을 먼저 검사**한다. 허용된 목표에 한해서 현재 측정 속도에서 출발하는 가속도·속도·Step 교집합으로 **이번 출력만** 갱신한다. 이후 매 Action에서 새 raw 목표를 검사하고 기존 `SafetyGuard.check()`가 최종 출력, 관절 피드백, Tracking 및 시각을 검사한다. 전송 목표를 별도 큐에 보관해 뒤따라가지 않는다. 이 검사는 물리 이동의 안전성을 보장하지 않는다.

## 최초 시험 운영값과 승인 범위

| 항목 | 연구팀 검토안 | 근거·한계 |
| --- | --- | --- |
| 시작 자세 | 기존 Home, 허용 오차 축별 0.5° | Home 수집·재ARM 기록. 실제 fresh 관절은 당일 재확인 |
| 관절 운영 범위 | J1~J5 기존 승인 범위 유지; J6는 Capstone Home 90.507° 기준 88.507°~92.507° | 첫 모델 Mock 37개 출력의 최대 Home 이탈 1.542°(J2)를 포함하는 **시험 설계 후보**. Home ±1.5°에서는 2명령 후 범위 차단. ±2°의 충돌 안전은 실측되지 않음 |
| 속도 | 축별 45°/s = 0.7853981634 rad/s | 기존 QP 운용 상한. Guard가 명령·측정 속도 검사 |
| Step·raw 첫 목표 거리 | 축별 1.5° = 0.0261799388 rad | QP 45°/s÷30Hz, ACT 20Hz에도 완화하지 않음. 첫 raw 목표의 Step도 검사 |
| raw 목표의 현재 관절 거리 | 축별 1.5° = 0.0261799388 rad | 짧은 시험의 보수적 후보. 이후에도 `bounded_rate`는 `min(이 값, tracking_error)` 적용; 멀면 중단 |
| 명령·측정 가속도 | 축별 `[4,3,4,5,3,5] rad/s²` | QP 측정 qdot/위치 차분 최대의 올림 외피. 기계 한계나 ACT absolute mode 추종 인증은 아님. Guard가 명령·측정에 같은 배열 적용 |
| Tracking | 축별 `[0.05,0.05,0.05,0.08,0.05,0.08] rad`, 150ms | QP fresh-command p99 올림 후보. J1은 0.05rad 초과가 약 300ms 지속돼 정상 QP에서도 trip 가능. Guard는 직전 전송 목표와 현재 피드백을 비교하며 QP 토픽 수신시각 정렬과 다름. 임의 완화하지 않음 |
| 정지 | qdot≤0.01rad/s·fresh idle 연속250ms, 확인 timeout1000ms | 기존 Home 정지 판정·현행 Controller 격리 경로. 1000ms는 물리 정지 시간 보증이 아닌 소프트웨어 판정 기한 |
| 명령권·Watchdog | `supervised_direct_link`, `enp11s0`, 직접 링크 재검사100ms, 기존 Watchdog·2초 실행 | 로컬 직접 route와 현행 QP/ACT lock·TCP/ROS 충돌 검사 재사용. 다른 Writer 종료와 감독은 매 시험 당일 확인 |
| SDK/모델 | GA12 qualified E-stop, SDK 비율0.8/7, `bounded_rate`, 두 RGB·20Hz·7D | 기존 계약 유지 |

연구팀은 **Home ±2°의 감독형 시험 구간**, QP 가속도·Tracking 후보를 이 첫 시험의 Guard 중단값으로 사용할지, raw 목표 거리1.5°, 정지 판정 기한1000ms와 직접 링크 재검사100ms를 채택할지 한 번에 승인해야 한다. 축 범위는 제어기 J1~5 ±175°·J6 ±215°보다 좁지만 장애물 없는 영역의 증명은 아니다. 실제 시작 자세·주변 장애물·다른 Writer/Conty 종료·E-stop 접근은 **승인과 별개로 당일 확인**한다. `stop_validation_record`, `external_exclusion_record`, 양쪽 `review_record`에는 승인된 절차·근거와 당일 확인 책임을 실제대로 적어야 한다. 입력 중단·PC/망 장애 시 Python stop 도달은 보장되지 않으며 현장 감독과 물리 E-stop이 잔여 위험이다.

## 실제 Checkpoint의 오프라인 검증

선택 Checkpoint `act1_real10_executionfit_20261103_v1/.../008000/pretrained_model`을 CPU로 재로딩해 저장된 시작 관측 10개를 다시 추론했다. 위 **후보** 범위·가속도·목표 거리·Tracking 값을 넣은 격리 Mock에서 raw 목표 10개 모두 범위·Step·Tracking 거리 안에 있었고, 첫 출력 10개 모두 최종 Guard 통과 후 Mock 전송됐다. 최대 출력 가속도는 4.0rad/s²였다. raw 목표가 Tracking 거리 밖인 경우와 첫 Step 초과는 전송 전 차단하는 집중 테스트를 통과했다.

같은 Checkpoint의 2초 Mock 반복에서 Home ±1.5°는 2명령 후 범위 차단으로 fault/stop이 발생했다. **Home ±2°에서는** 모델 추론5회, Mock 명령37회, 관절 상태 변경 후 재추론, raw 대비 출력 조정7회, 최종 stop 확인·lease 반환이 있었다. 명령 Step 최대0.859°, 축별 명령 가속도 최대 `[0.447,3.000,3.134,0.642,1.678,0.777] rad/s²`였다. RGB는 저장 프레임으로 고정되고 Mock 관절만 변했다. 실물 영상 변화·추종·지연은 검증하지 않았다. 실제 예측은 당일 시작 자세에서 Guard가 재판정한다.

앞선 점검의 CPU 실행은 Codex 샌드박스에서 GPU가 노출되지 않았기 때문이다. 같은 `.venv-act`를 워크스테이션 환경에서 실행하면 RTX PRO 6000 Blackwell과 CUDA가 정상 인식된다. 2026-11-04 실제 두 D435·Indy7 `/joint_states`를 사용한 `--device cuda` 비구동 시험에서 유효 관측100개, Chunk11개, Action100개/5초, 추론 p50 7.812ms·최대9.094ms, 20Hz 일정 누락0을 확인했다. 로봇 명령0개다. 이 결과는 실제 구동 성능이나 Guard 승인이 아니다. 근거: `outputs/temporary/inference/real_observation_gpu_20261104/`.

근거: `tmp/act_first_bounded_check_20261104_run3/result.json`, `tmp/act_first_bounded_loop_2deg_20261104_result/{result.json,events.jsonl}`, `tmp/real_site/qp_measurement_02/analysis_report.md`. 집중 회귀 `test_bounded_targets.py` 12개 통과. ROS/SDK/실물 명령은 없었다.

## 승인 후 실행 순서

1. 연구팀이 위 후보와 잔여 위험을 승인해 두 JSON에 **승인값·근거를 반영**했다. `reviewed_for_real=true`다.
2. 기존 Checkpoint와 두 프로필로 `scripts/bin/act execute --mode real --preflight-only`를 실행해 exit0/`profile_valid:true`를 확인했다. 이는 파일 검사 통과이며 실물 실행 허가는 아니다.
3. 당일 작업자가 두 D435·Mark7·실제 Home 관절, 직접 케이블, 다른 Writer/Conty 종료, 빈 영역과 E-stop 접근을 확인하고 **별도로 실물 구동을 명시 승인**한다.
4. 기존 `act execute --mode real --duration 2` 명령을 새 출력 경로로 실행한다. 명령 전송, 실제 관절 변화, 새 카메라 프레임과 관절 상태를 이용한 재추론, fresh stop과 명령권 반환을 로그로 각각 확인한다. Guard 차단·예상 밖 동작이면 기존 stop/E-stop 절차로 중단하고 자동 재개하지 않는다.

정확한 현장 명령·중단·로그 경로는 `ai/execute/REAL_INFERENCE.md`와 최종 training run의 `FINAL_PIPELINE_REPORT.md`를 따른다. Git commit과 실물 시험은 아직 없다.
