# Indy7 ACT 파이프라인 인수인계 — 2026-11-04

> **과거 시점 기록:** 아래 내용은 2026-11-04 작성 당시의 준비 상태를 보존한다.
> 현재 상태를 대표하지 않는다. 2026-11-05 최종 실물 Closed-loop 5초 시험은
> [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) 첫 항목과
> [REAL_INFERENCE.md](../ai/execute/REAL_INFERENCE.md)를 따른다. 해당 시험은
> `complete`/`duration_complete`, Chunk10개, SDK ACK96회, 실제 관절 변화와
> 정지 확인으로 끝났으며 파지 성공률·장시간 안정성 검증은 포함하지 않는다.

## 현재 목표와 상태

목표는 실제 Indy7 시연으로 학습한 ACT가 관측을 받아 추론하고, 안전 검사 후 명령을 내린 뒤 새 관측으로 다시 추론하는 흐름을 현장에서 확인하는 것이다. 바구니 파지 성공은 현재 시험의 완료 조건이 아니다.

2026-11-03 기준, 정상 SUCCESS 10개 수집 및 Snapshot 검증, 학습·Checkpoint 생성, 오프라인 추론과 Mock Closed-loop까지 완료했다. 실제 Indy7에 명령을 보내거나 연결하지 않았다. 실제 장비 피드백을 이용한 추론은 아직 검증되지 않았다.

## 녹화와 데이터

- 현재 녹화 실행 명령: 프로젝트 루트에서 `./act-record`. GUI에서 Collection을 선택해 시연을 기록한다.
- SUCCESS 데이터는 총 10 Episode, 8,997 Frames, 약 449.85초다. 카메라 2대, state/action 각 7D, 20Hz.
- 학습 Snapshot: `outputs/training_snapshots/act1_pipeline10_20261103_v1`
- Snapshot과 원본 Collection/Receipt는 변경하지 않았다. Home Action 기록은 앞서 실물 검증됐으며 이번 작업에서 다시 검사하지 않았다.

## 학습 결과

최종 Checkpoint:

```text
outputs/preserved/training_runs/act1_real10_executionfit_20261103_v1/training/checkpoints/008000/pretrained_model
```

기존 500·5,000-step Checkpoint를 보존했다. 별도 학습에서 12,000 step 후 해당 weights에서 새 optimizer/scheduler로 8,000 step을 학습했다. 최종 run은 Snapshot의 10 Episode 전부를 사용했으며 held-out 검증은 없다.

기존 resume은 원래 500-step cosine scheduler를 복원해 Learning Rate가 약 `1e-6`으로 이어졌다. 새 학습은 초기 Learning Rate `1e-4`를 사용했다. 마지막 실험은 ACT의 첫 `n_action_steps=10` Action target에 L1 loss 가중치 4를 적용했다. 이는 학습 설정이며 추론 출력이나 SafetyGuard를 우회하지 않는다.

동일 160개 저장 관측에서 첫 관절 목표의 평균 예측-정답 오차는 기존 500-step 4.497°에서 최종 2.422°로 감소했다. Episode 시작점 10개는 첫 목표와 현재 관절 간 최대 오차 1.372°로, 기존 1.5° 검사를 모두 통과했다. 중간 관측 160개를 각각 독립 시작점으로 검사하면 121개가 1.5°를 초과한다. 학습 데이터 재현 결과이며 일반화·작업 성공률을 입증하지 않는다.

상세 학습 설정·수치·평가 파일은 [최종 파이프라인 보고서](../outputs/preserved/training_runs/act1_real10_executionfit_20261103_v1/FINAL_PIPELINE_REPORT.md)를 따른다. 원시 offline 산출물은 `tmp/act_final_pipeline_20261103/`에 있다.

## Mock 통합 결과

최종 실제 Checkpoint를 기존 ACT backend, scheduler, controller, SafetyGuard와 Mock Driver에 연결했다. 2초 가상 실행에서 모델 추론 5회, Mock 명령 37회, 관절 상태 갱신 및 다음 추론을 확인했다. 종료 시 Mock 정지 확인과 명령권 반환이 기록됐다. 단절과 NaN fixture에서도 정지 이후 추가 명령이 없었다. 집중 테스트 72개가 통과했다.

이 시험은 실제 저장 RGB를 고정했고 Mock 관절 상태만 바꿨다. 실제 카메라 변화, 네트워크/RPC 지연, 로봇 추종과 정지 성능은 검증하지 않았다. Mock 제한값은 실물 승인값이 아니다.

## Real 실행 전 남은 일

`ai/execute/config/indy7_real_reviewed.json`과 `real_hardware_reviewed.json`은 여전히 `reviewed_for_real: false`다. Preflight는 미승인 운영 제한과 검토 기록이 남아 있어 실패하는 것이 현재 정상이다. 안전값을 모델 예측이 통과하도록 임의로 완화하지 않는다.

현장에서는 연구팀이 기존 근거를 바탕으로 운영 관절 범위, 가속도, 목표/추종 오차, 정지 검증과 다른 Writer 배제 기록을 검토해야 한다. 승인 뒤 실제 초기 관절 자세, 카메라·그리퍼 관측, Indy7 제어기 상태, 직접 유선 연결, Xbox/태블릿/Conty 및 다른 Writer 종료, 통제된 작업 공간과 물리 E-stop 접근을 확인한다. 제어기는 켜 둔다. E-stop 후 자동 재개·Home·Recover는 없다.

프로필 검토 뒤 실행할 비구동 Preflight:

```bash
cd /home/sirlab2/sirlab258
CHECKPOINT="$PWD/outputs/preserved/training_runs/act1_real10_executionfit_20261103_v1/training/checkpoints/008000/pretrained_model"
scripts/bin/act execute --mode real --preflight-only --checkpoint "$CHECKPOINT" \
  --config ai/execute/config/indy7_real_reviewed.json \
  --hardware-profile ai/execute/config/real_hardware_reviewed.json
```

통과 후에만 현장 감독자가 기존 Real 명령으로 짧은 시험을 시작한다. 실제 실행 예시, 정지와 로그 확인 명령은 [Real Inference 절차](../ai/execute/REAL_INFERENCE.md)를 따른다. 실행 중 관절 피드백 변화와 후속 추론을 확인해야 한다. ACK만으로 물리 이동을 판정하지 않는다.

## 보존 주의

- Git commit은 하지 않았다. 작업 트리에는 OMY/Mark7/QP 및 Real Inference 관련 기존 미커밋 변경이 있으므로 `reset`, `clean`, 덮어쓰기를 하지 않는다.
- 데이터, Receipt, 기존 Checkpoint와 Snapshot은 보존한다.
- 현재 작업은 실물 구동 준비에서 멈춘다. 실물 확인 전 Safety Profile 승인과 실제 Closed-loop 완료를 주장하지 않는다.

기본 프로젝트 배경과 운영 규칙은 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md), 현재 실물 실행 절차와 제한은 [REAL_INFERENCE.md](../ai/execute/REAL_INFERENCE.md)를 먼저 확인한다.
