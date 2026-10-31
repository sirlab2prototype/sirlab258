# sirlab258 — Indy7 ACT 캡스톤

Indy7·Mark7·Xbox QP Teleop·두 RGB 카메라로 바구니 시연을 수집하고,
LeRobot ACT 학습·추론·평가로 연결하는 팀 프로젝트다.

## 먼저 읽기

- Codex 작업 규칙: [AGENTS.md](AGENTS.md)
- 현재 구현·검증·제약·다음 작업: [PROJECT_CONTEXT](docs/PROJECT_CONTEXT.md)
- 작업자 수집 안내: [Recorder / Core-108](ai/record/COLLECTION.md)

과거 실물 Pilot과 최신 소프트웨어 검증은 다르다. 최신 Core-108/Correction과
실제 ACT 모터 실행은 실물 통합 검증 완료 상태가 아니다.
`caps`는 실제 장비와 QP 자동 ARM 경로를 시작한다. 실행 예시나 Mock 성공은
실물 실행 승인이 아니며 현재 안전 제한을 먼저 확인한다.

## 현재 기능과 데이터 흐름

```text
장비 영상·상태 + QP 목표 → Recorder/Core-108 → success/ → Validator → ACT 학습/Checkpoint
                                      ↘ correction/ (별도 보존; 학습 미구현)
Checkpoint → Synthetic/ROS 관측 Dry Run → Mock Execution/Task → 별도 평가
```

- 일반 수집: `s → r → o`. 교정 수집: `s → x → r → o` (x 이후 녹화 지속).
- 기본 수집 목표는 정상 성공 108개다. Correction·폐기는 완료 조건에 포함하지 않는다.
- Core-108의 학습 입력은 `datasets/indy7_act_core108/success`다.
  독립 Recorder 기본값 `datasets/indy7_act`와 자동으로 합치지 않는다.
- 두 RGB 영상과 state/action 7D, 20FPS 계약을 사용한다. 관절 action은 절대 목표 rad다.
- Real ACT/Task/Home은 잠금 상태다. Mock 완료·서비스 ACK는 물리 성공 증거가 아니다.

## 코드 구조

| 경로 | 역할 |
| --- | --- |
| `ros2_ws/src/capstone_teleop`, `capstone_mark7` | 기존 수동 제어·설정·launch |
| `ai/record`, `ai/observation_timing.py` | Recorder·Collection·교정·관측 시간 계약 |
| `ai/train`, `ai/dataset_contract.py` | 데이터 검증·학습·Checkpoint·공유 계약 |
| `ai/serve`, `ai/execute`, `ai/evaluate` | 추론·Mock 실행/Task·평가 |
| `scripts`, root wrappers | 환경 활성화·명령 설치·실행 진입점 |
| `docs` | 현황·실험 계획·장애/조사 근거 |
| `ros2_ws/src/external/indy-ros2`, `external/open_manipulator`, `ai/lerobot_source` | 보호할 공식 소스 |

## 실행 진입점과 운영 문서

아래는 지원 범위 안내다. 이 문서를 읽는 것만으로 실행하지 않는다.

| 진입점 | 역할·상세 문서 |
| --- | --- |
| `./caps`, `capi`, `capg`, `capx` | 실제 장비 운영. [Teleop/장비 가이드](docs/TELEOP.md) |
| `./act-record --collection-dir datasets/indy7_act_core108` | [수집·저장·재개·reconcile](ai/record/COLLECTION.md) |
| `./act validate`, `./act train` (기존 `./act-train` 호환) | [검증 기준](ai/train/VALIDATION.md), [학습·재개·Checkpoint](ai/train/TRAINING.md) |
| `./act infer` | [Synthetic/ROS 관측 Dry Run](ai/serve/INFERENCE.md) |
| `./act execute`, `./act task` | [Mock 실행 계층](ai/execute/EXECUTION.md), [Task/Home](ai/execute/TASK_EXECUTOR.md) |
| `./act evaluate` | [평가 기록·보고서](ai/evaluate/EVALUATION.md) |

`./act setup`은 지원하지 않는다. `./caps`는 설치 명령이 아니다.
`act`는 기능별 dispatcher이며 `act-train`, `act-record`, `caps` wrapper는 유지한다.

## 현재 연구실 환경

Ubuntu 24.04 / Python 3.12 / ROS 2 Jazzy, vendored LeRobot 0.5.1을 사용한다.
ROS·제어는 `.venv`, ACT는 `.venv-act`로 분리한다. NumPy 버전이 다르므로 합치지 않는다.
ACT 환경 활성화·캐시 정책은 [TRAINING](ai/train/TRAINING.md#연구실-act-환경)을 따른다.

`requirements.txt`는 로봇 SDK·NumPy·empy, `requirements-dev.txt`는 ROS 개발 테스트용이다.
두 파일은 `scripts/bootstrap.sh`가 실제 읽는다. bootstrap은 submodule 초기화,
`.venv` 구성·ROS workspace 빌드·프로젝트 명령 설치를 하며 `.venv-act` 생성은 하지 않는다.
완전한 새 PC 배포 절차는 이 문서의 범위가 아니다.

설치·이동 후 재빌드는 `ros2_ws`에서 일반 `colcon build`를 사용하는 기존 원칙을 유지한다.
현재 고정 공식 Indy 소스에서는 `--symlink-install`을 권장하지 않는다.
기존 `.venv*`, `ros2_ws/build/install/log`는 사용 중일 수 있으므로 임의 삭제하지 않는다.

## 검증·연구 자료

- 최신 결과와 한계: [현재 상태](docs/PROJECT_CONTEXT.md#검증-범위)
- 실물 전 조건: [준비도](docs/ACT_OFFLINE_READINESS.md), [ROS 관측 단계](docs/ACT_ROS_STAGE1.md)
- 실험 설계·원본 Shuffle: [수집 계획](docs/ACT_Data_Collection_Plan.md), [108개 조건표](docs/ACT_108_Episode_Execution_Log_Template.md)
- 기술 근거: [시작 시간 계약](ai/record/START_REFERENCE.md), [관측 복구](docs/ACT_ROS_RECOVERY.md)
- 과거 장애·미확인 피드백: [J6 인계](docs/INDY7_J6_FAULT_HANDOFF.md), [Mark7 조사](docs/MARK7_STATE_FEEDBACK_INVESTIGATION.md)
- 정리 이력: [PROJECT_CLEANUP_AUDIT](docs/PROJECT_CLEANUP_AUDIT.md)

기본 `pytest.ini`는 ROS 두 패키지만 수집한다. ACT 전체 회귀는 현재 상태 문서의
명시적 경로와 CPU 전용 범위를 사용하며, 일반 `pytest`만으로 전체 검증을 주장하지 않는다.
Pilot·Dataset·Pending·receipt·Checkpoint·outputs는 보존한다. Git 제외 파일도 삭제 허가가 아니다.
프로젝트 라이선스는 아직 선정되지 않았으며 외부 소스의 라이선스는 그대로 유지한다.
