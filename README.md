# sirlab258 — Indy7 ACT 캡스톤

Indy7·Mark7·Xbox QP Teleop·두 RGB 카메라로 바구니 시연을 수집하고,
LeRobot ACT 학습·추론·평가로 연결하는 팀 프로젝트다.

## 먼저 읽기

- Codex 작업 규칙: [AGENTS.md](AGENTS.md)
- 현재 구현·검증·제약·다음 작업: [PROJECT_CONTEXT](docs/PROJECT_CONTEXT.md)
- 산출물 분류·새 정식 데이터 경로: [outputs 관리](docs/OUTPUTS.md)
- 데이터셋 JSON 작성·Recorder 사용법: [act-record.md](act-record.md)
- 작업자 수집 안내: [Recorder / Core-108](ai/record/COLLECTION.md)

과거 실물 Pilot과 최신 소프트웨어 검증은 다르다. 최신 Core-108/Correction과
실제 ACT 모터 실행은 실물 통합 검증 완료 상태가 아니다.
`indy`는 실제 장비와 QP 자동 ARM 경로를 시작한다. 실행 예시나 Mock 성공은
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
| `scripts/bin/{indy,omy,act}` | 환경 활성화·명령 설치·실행 진입점 |
| `docs` | 현황·실험 계획·장애/조사 근거 |
| `ros2_ws/src/external/indy-ros2`, `external/open_manipulator`, `ai/lerobot_source` | 보호할 공식 소스 |

## 실행 진입점과 운영 문서

아래는 지원 범위 안내다. 이 문서를 읽는 것만으로 실행하지 않는다.

| 진입점 | 역할·상세 문서 |
| --- | --- |
| `indy`, `indy driver`, `indy teleop` | 실제 장비 운영. [Teleop/장비 가이드](docs/TELEOP.md) |
| `act record` / 기존 `--collection-dir` | [수집·저장·재개·reconcile](ai/record/COLLECTION.md) |
| `act validate`, `act train` | [검증 기준](ai/train/VALIDATION.md), [학습·재개·Checkpoint](ai/train/TRAINING.md) |
| `act infer` | [Synthetic/ROS 관측 Dry Run](ai/serve/INFERENCE.md) |
| `act execute`, `act task` | [Mock 실행 계층](ai/execute/EXECUTION.md), [Task/Home](ai/execute/TASK_EXECUTOR.md) |
| `act evaluate` | [평가 기록·보고서](ai/evaluate/EVALUATION.md) |

`act setup`은 지원하지 않는다. `indy`는 설치 명령이 아니다.
`act`는 기능별 dispatcher다. 중복 루트 실행 래퍼는 제거했으며 `act-record.md`는 사용 문서로 유지한다.

## 사용자 단위 명령 설치

기존 환경을 그대로 사용하며 의존성을 재설치하지 않는다. 저장소에서 한 번 실행한다.

```bash
./scripts/install_commands.sh
export PATH="$HOME/.local/bin:$PATH"   # PATH에 없을 때만; 현재 계정은 이미 설정됨
unset -f indy omy                      # 이전 함수를 가진 기존 터미널에서 한 번
hash -r
indy --help
omy --help
act --help
```

설치기는 `~/.local/bin/{indy,omy,act,camera,cam1,cam2}`를 연결하고, 옛 설치기가 만든 것으로 확인된
`.venv/bin`의 capi/capg/capx/caps/omy/act-record 링크만 제거한다. 무관한 파일은 덮어쓰지 않는다.
다른 clone 위치로 연결을 바꾸려면 새 저장소에서 `./scripts/install_commands.sh --replace`를 쓴다.
이는 기존 심볼릭 링크만 교체하며 일반 파일은 거부한다. 실행기는 실제 링크 대상을 기준으로 ROOT를 계산한다.
가상환경 activation/설치 산출물 자체의 이동 가능성은 별개이며 필요하면 새 경로에서 재구성한다.
상대 데이터 경로는 기존처럼 호출한 디렉터리 기준이며, 어디서든 작업할 때는 데이터·출력 절대경로를 명시한다.
인자 없는 `act record` GUI는 프로젝트 루트로 이동한 뒤 시작한다.

`indy`는 카메라를 제외한 Indy7·Mark7·Xbox/QP·GUI와 `driver`/`teleop`·기존 ROS launch 인자를 지원한다.
`indy cam`은 제거했다. 별도 터미널에서 `camera` 또는 `cam1`/`cam2`를 실행한다.
`act`의 인자 없는 실행은 도움말이며 `record/train/validate/infer/execute/task/evaluate`를 지원한다.
`act record` 인자 없음은 데이터셋 GUI, 인자 있음은 기존 터미널 Recorder다.
`omy`는 `/opt/workspace/omy`의 기존 컨테이너·Zenoh 사전 점검만 수행한다.
`status/doctor/logs`를 지원하며 시작·정지·재시작·Leader/Follower 자동 구동은 제공하지 않는다.

카메라317222074298(D435 A)은 Indy/OMY 공유,844212071939(D435 B)는 Indy 전용,
335122271033(D405)은 OMY 전용이다. 공유 D435를 중복 실행하지 않는다.
명령 번호는 `cam1` → `844212071939`, `cam2` → `317222074298`이며 `camera`는 두 대 모두 실행한다.
번호만 교체했으며 serial 기반 namespace·영상 토픽·ACT 카메라 역할은 유지한다.
새 카메라 명령은 같은 사용자 계정에서 serial별 advisory lock으로 중복 실행을 막는다.
다른 사용자·외부 ROS/SDK 점유는 확인하지 못한다. 장치 점유 오류가 나면 기존 프로세스를 종료하지 말고 확인한다.
`camera`는 두 잠금을 모두 확보해야 시작하며 하나라도 사용 중이면 전체 요청을 거절한다.
`cam1`과 `cam2`는 별도 터미널에서 동시에 실행할 수 있다. Ctrl-C는 해당 launch만 종료한다.
잠금 파일은 `~/.local/bin/.sirlab258-camera-locks/`에 남으며 지우지 않는다. 프로세스 종료 시 커널이 잠금을 해제한다.
현재 명령은 소스 launch/YAML의 절대경로를 계산해서 사용하므로 이 변경을 적용하는 데 ROS 재빌드는 필요 없다.
기존 workspace의 노드·패키지 설치는 필요하다. 직접 ros2 launch/SDK 실행은 잠금 보호를 우회한다.
실제 영상 발행·점유 실패·종료 후 장치 재사용은 별도 장비 검증이 필요하다.

## 현재 연구실 환경

Ubuntu 24.04 / Python 3.12 / ROS 2 Jazzy, vendored LeRobot 0.5.1을 사용한다.
ROS·제어는 `.venv`, ACT는 `.venv-act`로 분리한다. NumPy 버전이 다르므로 합치지 않는다.
ACT 환경 활성화·캐시 정책은 [TRAINING](ai/train/TRAINING.md#연구실-act-환경)을 따른다.

`requirements.txt`는 로봇 SDK·NumPy·empy, `requirements-dev.txt`는 ROS 개발 테스트용이다.
두 파일은 `scripts/bootstrap.sh`가 실제 읽는다. bootstrap은 submodule 초기화,
`.venv` 구성·ROS workspace 빌드·프로젝트 명령 설치를 하며 `.venv-act` 생성은 하지 않는다.
완전한 새 PC 배포 절차는 이 문서의 범위가 아니다.
현재 checkout에는 `requirements.txt`/`requirements-dev.txt`가 없으므로 bootstrap 전체는 그 사전 검사에서 중단된다.
이번 CLI 설치는 위 `install_commands.sh`만 사용하며 의존성 복구·환경 재설치는 수행하지 않았다.

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
