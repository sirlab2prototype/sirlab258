# 프로젝트 정리 조사 기록 — 2026-10-31

대상은 `/home/sirlab2/sirlab258`, branch `main`이다. 기능 개발·하드웨어 실행 없이
파일·문서만 정리했다. 조사 중 로봇/ROS 노드/카메라/시리얼을 실행하거나 접속하지 않았다.

## 정리 전 보존과 범위

- 이전 HEAD: `3d10bbb` (`chore: snapshot capstone system before ACT recorder improvements`).
- 정리 전 구현 보존: `b3bbb3b01b015aa60f769604c80db17fd723fac5`
  (`feat: finalize ACT recorder and correction collection`). 파일 삭제 전에 완료했다.
- Recorder/Core-108/교정/UI/시간 계약/테스트/실행 wrapper/프로젝트 상태 15개 파일만
  명시적으로 추가했다. Dataset·영상·캐시·checkpoint는 추가하지 않았다.
- 학습·추론·실행·평가와 QP Gripper QoS의 기존 미커밋 작업은 별도 작업으로 보존했다.
  README/ACT_SETUP은 혼합 변경이 있어 구현 커밋에서 제외했다. 정리 커밋에는 두 파일의
  문서 정리 부분만 별도로 stage하고, 원래 미커밋 내용은 작업 트리에 남겼다.
- Git identity는 기존 설정을 사용했다. push/reset/전역 설정 변경은 하지 않았다.

## 분류와 참조 조사

자체 파일 151개와 삭제 캐시 52개의 경로·역할·직접 참조·결정은
[파일별 조사 목록](PROJECT_CLEANUP_INVENTORY.tsv)에 기록했다.
분류는 A 52개, B 5개, C 65개, D 81개다. 신규 조사 문서 두 파일은 이 기준 목록에
포함하지 않았다. 가상환경·외부 코드·실행 산출물은 아래 보호 영역 단위로 분류했다.

직접 import뿐 아니라 root/Bash wrapper, ROS `setup.py` console_scripts,
launch 파일, YAML/JSON 설정, `python -m`/subprocess 및 테스트의 참조를 대조했다.
직접 참조 미검출은 미사용의 증거로 취급하지 않았다. 함수/클래스는 외부 API·동적 사용을
배제할 근거가 부족하므로 삭제하지 않았다. TSV의 참조 열은 직접 텍스트 매칭의 일부 예시이며
실행 도달성을 보장하는 전체 호출 그래프가 아니다.

| 분류 | 경로 | 역할·근거와 결정 |
| --- | --- | --- |
| A | `ai/**/__pycache__/*.pyc` (외부 LeRobot 제외), `scripts/__pycache__/*.pyc`, `ros2_ws/src/capstone_*/**/__pycache__/*.pyc` | 원본 `.py` 존재와 Git ignored를 확인한 bytecode 46개 삭제. 기능·테스트 원본 유지 |
| A | `.pytest_cache/` 파일 6개 | pytest가 재생성하는 캐시. ignored 확인 후 파일별 삭제, 빈 디렉터리만 제거 |
| B | `ACT_SETUP.md` | 환경 설치·카메라 연결 및 장비 실행 구분 유지. 중복 Recorder 조작표/저장 설명을 `ai/record/COLLECTION.md` 링크로 통합 |
| B | `README.md` | 상단에 수집 운영·ACT 환경·실험 계획 링크 추가. 다른 미커밋 파이프라인 설명은 그대로 보존 |
| B | `ai/record/START_REFERENCE.md` | 60ms 시작 기준·한계·sidecar 계약 유지. 이전 입력 thread/Failure/UI 설명을 현행 코드와 일치시킴 |
| B | `ai/train/configs/README.md` | 없는 matrix script/design 문서를 실행 기준으로 삼던 설명 제거. 현재 CLI 소스/환경 문서 연결. 과거 실험 설계 수치는 역사적 메모로 보존 |
| B | `docs/PROJECT_CONTEXT.md` | 현재 경로 예시에 correction 추가, 오래된 Git snapshot 갱신, 이번 조사 링크 추가. 과거 장애 근거는 유지 |
| C | `ai/record/`, `ai/dataset_contract.py`, `ai/observation_timing.py` | 현재 Recorder/장부/교정/시간 계약. 코드 변경은 두 파일의 EOF 빈 줄 3개 제거뿐이며 AST 동일 확인 |
| C | `ai/record/test/*` | 기존 테스트 전부 유지. `benchmark_bounded_history.py`도 시간·메모리 계약 재현 도구이므로 유지 |
| C | `docs/ACT_Data_Collection_Plan.md`, `docs/ACT_108_Episode_Execution_Log_Template.md` | 실험 기준·원본 Condition ID·고정 Shuffle·분포. 원문 그대로 유지 |
| C | `ros2_ws/src/capstone_*/` | ROS 패키지/launch/config/테스트. GUI는 setup.py와 xbox launch의 실제 진입점이므로 유지 |
| C | root wrapper, `scripts/bootstrap.sh`, `scripts/install_commands.sh`, `scripts/bin/*`, `scripts/activate_act.sh` | 새 PC 재구축·외부 CLI 호출에 필요. `omy`도 설치 명령 목록과 README에서 참조되므로 유지 |
| C | `scripts/diagnose_indy7_j6_readonly.py`, `docs/INDY7_J6_FAULT_HANDOFF.md` | 장애 진단 재현 및 제조사 인계 근거. 네트워크 진단 자체는 실행하지 않음 |
| D | `act`, `ai/checkpoint_identity.py`, `ai/train/`·`ai/serve/`의 기존 변경/추가 파일, `ai/execute/`, `ai/evaluate/`, `ai/test/`, 관련 추가 문서 | 별도 미커밋 작업. CLI/subprocess/테스트 참조가 존재하며 이번 정리에서 삭제·커밋하지 않음 |
| D | `ai/train/configs/act_{a,b,c,d}_s{0,1,2}.yaml` | 현행 Trainer가 자동 로딩하지 않지만 과거 실험 사용 여부 불명. 12개 모두 유지 |
| D | `scripts/rotate_ros_image.py` | 과거 wrist 토픽용 독립 도구. 인계 문서에는 기록되어 있으나 현재 camera launch 호출은 미확인. 외부 수동 호출 가능성 때문에 유지 |
| D | `docs/ACT_OFFLINE_READINESS.md`, `docs/ACT_ROS_STAGE1.md`, `docs/ACT_ROS_RECOVERY.md`, `docs/MARK7_STATE_FEEDBACK_INVESTIGATION.md` | 별도 미커밋 조사/검증 기록. 소유·완료 범위를 이번 정리로 판단하지 않고 보존 |

동일 내용 파일은 빈 Python `__init__.py`와 ROS `resource/*` 마커뿐이었다.
패키지 발견·설치에 필요한 별도 경로이므로 중복 제거하지 않았다.
`.gitignore`는 이미 환경·캐시·데이터·출력·복구 자료를 제외하므로 변경하지 않았다.
삭제 가능한 일회성 실행 스크립트/이전 Recorder 구현이라는 확실한 근거는 찾지 못했다.

## 보호 영역 — 수정·삭제하지 않음

아래 수치는 `.git` 디렉터리를 제외하고 symlink를 따라가지 않는 파일/symlink의
`lstat().st_size` 합계다. 디스크 할당량(`du`) 또는 외부 symlink 대상 크기와는 다르다.

| 영역 | 파일 수 | 바이트 | 보존 근거 |
| --- | ---: | ---: | --- |
| `.venv-act/` | 47,936 | 6,691,672,948 | 실행 환경·외부 라이브러리 |
| `.venv/` | 1,019 | 13,273,047 | 로봇용 환경·설치된 CLI 링크 |
| `ai/lerobot_source/` | 822 | 12,425,360 | vendored 공식 LeRobot |
| `external/` | 317 | 62,219,649 | 공식 ROBOTIS 저장소 |
| `ros2_ws/src/external/` | 552 | 212,957,249 | 공식 Indy ROS 소스 |
| `outputs/` | 388 | 330,046,558 | Pilot·synthetic 검증 결과·시작 기준 백업·진단·학습/실행 증거 |
| `ros2_ws/{build,install,log}/` | 1,978 | 222,997,643 | 현재 설치·실행 재현과 장애 로그; 정리로 재빌드 요구하지 않음 |
| root `build/`, `install/`, `log/` | 23 | 1,038,691 | 생성물이나 사용/진단 가치 미확정이라 보존 |
| `.hf_cache/` | 80 | 1,048,469 | 오프라인 실행·환경 재현에 쓰이는 cache |

`datasets/`, `checkpoints/`, `recordings/`, `tmp/`는 조사 시 루트에 존재하지 않았다.
Dataset/장부/receipt/pending/영상/checkpoint는 `outputs/` 아래에 있더라도 삭제 대상으로
삼지 않았다. `outputs/act_recorder_pilot_20261031`과 과거 Failure도 그대로 보존했다.
outputs 388개 파일은 정리 전후 SHA-256을 대조하고, 나머지 보호 영역은 파일 목록·크기·
mtime을 대조한다. 외부 저장소의 내부 Git metadata도 수정하지 않는다.

## 오프라인 검증

실행 명령과 최종 결과는 아래에 기록한다. 실제 장비 검증 결과를 의미하지 않는다.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  .venv-act/bin/python -m pytest -q -p no:cacheprovider --tb=short \
  --basetemp=/tmp/sirlab-cleanup-recorder-tests \
  ai/record/test/test_recorder_ui_correction.py \
  ai/record/test/test_collection.py \
  ai/record/test/test_indy7_act_record.py ai/record/test/test_pending_start.py

source /opt/ros/jazzy/setup.bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$PWD/ros2_ws/src/capstone_mark7:$PWD/ros2_ws/src/capstone_teleop:$PWD${PYTHONPATH:+:$PYTHONPATH}" \
  .venv/bin/python -m pytest -q -p no:cacheprovider --tb=short \
  --basetemp=/tmp/sirlab-cleanup-teleop-tests \
  ros2_ws/src/capstone_mark7/test ros2_ws/src/capstone_teleop/test
```

- Recorder/Collection/교정/시간 계약: **120 passed in 94.59s**.
  UI, s 시작/60ms 실패, 일반/교정 저장, 첫 교정 마커·반복 x, 108개 전체 합성 저장,
  Shuffle/분포, Attempt/Discard/INVALID, 재개, 과거 Failure/receipt 호환,
  부분 저장·장부 쓰기 실패·강제 프로세스 종료·reconcile을 기존 테스트로 확인했다.
- QP/Xbox 입력/Mark7 패킷: **20 passed in 0.12s**.
  ROS 환경 스크립트만 source했고 노드 생성·시리얼 연결은 하지 않았다.
- 실패 0개. 기존 테스트 삭제/비활성화 없음. 합성 데이터는 `/tmp`에만 생성했다.
- 실기·카메라·QoS 통신·실제 Xbox 조작·실제 시리얼 응답 검증은 **미실행**.
  이번 문서/캐시 정리에서 학습 최적화나 전체 추론/평가 파이프라인도 재실행하지 않았다.
- 코드 2개 파일은 Python AST가 정리 전 커밋과 같고, 나머지 runtime/테스트 소스는
  정리 전 내용 그대로다. 기존 별도 미커밋 파일 68개도 SHA-256 일치를 확인했다.
- 보호 영역 53,115개 파일은 목록·크기·mtime이 같으며 `outputs/` 388개는
  내용 SHA-256까지 일치했다. 이 확인은 하드웨어 상태의 검증이 아니다.

## 정리 전후 규모

집계는 첫 구현 커밋 후·삭제 전에 확보한 목록을 기준으로 하며 `.git` 디렉터리를
제외하고 symlink 대상은 따라가지 않는다. Git pack 용량과 디렉터리 자체 용량은 제외한다.

| 항목 | 정리 전 | 정리 후 |
| --- | ---: | ---: |
| 파일 및 파일 symlink 수 | 53,318 | 53,268 |
| 논리 크기 (bytes) | 7,550,153,542 | 7,549,067,270 |

캐시 **52개, 1,145,026 bytes**를 삭제하고 조사 문서 두 개를 추가했다.
실제 저장 공간의 순감소는 1,086,272 bytes다. 대용량 환경·데이터를 남겼으므로
크기 감소는 작다. 추후 소유자 확인 없이 이를 추가 삭제하지 않는다.

## 후속 정리와 제한

- D 81개는 삭제 승인이 부족해서가 아니라, 별도 미커밋 작업/과거 실험/외부 호출에
  해당해 불필요하다는 근거가 부족하여 보존했다. 특히 단일 카메라 YAML과 회전 도구는
  실험 담당자의 사용 확인 후에만 제거 여부를 결정한다.
- 학습·추론·실행·평가의 변경은 이번 커밋과 별개로 검토/커밋해야 한다. 따라서 최종
  Git status가 clean이 아닌 것은 의도된 결과다. 상세 범위는 기존 파일별 목록 D를 참조한다.
- 날짜가 붙은 J6/ROS 조사 기록은 현재 코드 설명과 구분해 읽어야 한다. 장애 증거·
  재구축·오류 복구 정보는 남겼으며 과거 기록 전체를 최신 기능 설명으로 다시 쓰지 않았다.
- 정리 커밋은 `chore: remove obsolete files and consolidate project docs`로 별도 생성한다.
  정확한 최종 hash는 Git log와 최종 작업 보고를 따른다.
