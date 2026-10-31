# 이전 학습 조건표 (참고용)

이 디렉터리의 12개 YAML은 과거 단일 RGB 실험의 `condition={a,b,c,d}` ×
`seed={0,1,2}` 조건 메모다. 현재 Indy7 두 카메라 ACT 학습의 실행 설정이 아니며,
`run_train.py`는 이 YAML을 자동으로 읽지 않는다.

현재 학습 진입점은 루트 `act-train` → `ai.train.run_train`이며, 설정은 해당
[run_train.py](../run_train.py)의 CLI와 실행 결과에 저장된 설정을 기준으로
확인한다. 환경 재구축은 [ACT 환경](../../../ACT_SETUP.md)을 참고한다.

이전 문서가 가리키던 `scripts/train/run_main_matrix.sh`와
`docs/experiment_design.md`는 현재 작업 트리에 없다. 해당 문서의 실행 완료·
subset manifest 생성 주장은 현재 소스만으로 확인할 수 없어 운영 안내에서 제외했다.
YAML 자체는 과거 실험 재현에 쓰였는지 확인되지 않아 보존한다.

과거 문서에 기록된 설계 메모는 A=G5×60, B=(G1,G6,G8)×20,
C=G1..G9에 총 60개 분산, D=G1..G9×20(180개), 각 조건 seed 0/1/2였다.
또한 단일 RGB·100,000 steps·20,000 step 간격 저장·최종 checkpoint 평가·
중단 시 처음부터 재실행으로 서술되어 있었다. 이를 실행했다는 증거나 subset
manifest는 이번 조사에서 확인하지 않았으며, 현재 학습의 설정/재개 규칙으로 사용하지 않는다.
