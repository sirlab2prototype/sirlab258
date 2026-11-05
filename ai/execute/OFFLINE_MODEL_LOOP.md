# ACT 오프라인 모델 루프 진단

`offline_model_loop.py`는 저장된 첫 RGB 관측을 고정하고, 기존 ACT Scheduler와 Controller에
Mock 관절 상태를 되먹임한다. ROS·Indy7 SDK·Mark7 시리얼 명령은 사용하지 않는다.
출력은 **새 격리 디렉터리**에만 쓴다. 기존 Dataset과 Checkpoint는 읽기만 한다.

```bash
PYTHONPATH=. .venv-act/bin/python -m ai.execute.offline_model_loop \
  --checkpoint outputs/preserved/training_runs/act1_real10_executionfit_20261103_v1/training/checkpoints/008000/pretrained_model \
  --dataset outputs/training_snapshots/act1_pipeline10_20261103_v1 \
  --output outputs/temporary/inference/offline_model_loop_new --mode model
```

`--mode normal`은 모델 예측 대신 작은 합성 목표를 사용하며,
`disconnect`·`nan`은 Mock 장애 경로를 시험한다. `model`은 CUDA Checkpoint 추론을
수행하므로 이번 정리에서는 실행하지 않았다. 합성 Dataset/Backend로 실제 Mock 루프의
40×7 Action 경로와 출력 계약을 검증했다. 결과의 `mock_commands`와
`state_changed`는 물리 동작·파지·현장 성능의 근거가 아니다.
