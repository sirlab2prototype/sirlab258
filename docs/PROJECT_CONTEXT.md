# sirlab258 — 현재 상태와 다음 작업

2026-11-05 ACT SafetyGuard 캡스톤 후보: `ai/execute/config/indy7_real_reviewed.json`의
관절 범위를 J1~5 ±174°·J6 ±214°로, 20Hz 명령 Step을 1.5°에서 2.25°로 조정했다.
성공 시연10개/8997프레임의 Action·실측 관절 통계와 기존 45°/s 속도 상한에 근거한
오프라인 후보이며 `reviewed_for_real=false`다. Home/목표-실측 오차/속도/가속도/
tracking/Stop·Watchdog는 유지했다. 이전 Home ±2° 5초 실물 시험 설정은
`ai/execute/config/indy7_real_5sec_20261105_backup.json`에 그대로 보존했다.
새 전 범위의 작업 공간·충돌·실물 추종은 승인 또는 검증되지 않았다.

2026-11-05 최종 ACT 실물 Closed-loop 5초 시험:
`outputs/temporary/inference/act_real_test_5sec_20261105T102020`의 result/summary가
`complete`/`duration_complete`, 생성·수락 Chunk 각10개, SDK 시도·ACK 각96회,
추론 p50 7.071ms/p95 8.420ms, 측정 관절 변화0.0249755rad, 실제 Action19.9975FPS,
기한 누락0, safety fault 없음, Indy 정지 확인 및 lease 정상 반환을 기록했다.
두 D435·관절 입력과 관절 피드백의 실제 움직임, 새 관측을 이용한 재추론까지 확인한
5초 Closed-loop 세션이다. 다음 단계는 실제 작업 성능 개선이다. 파지 성공률·목표 추종
정확도·장시간 안정성은 아직 검증하지 않았다. 추가 실물 실행·학습은 하지 않았다.
현행 경로는 두 D435 RGB·Indy7 6축 관절·Mark7 명령 상태 → ROS 관측 → ZMQ ACT
추론(40×7 Chunk) → 20Hz Scheduler/Controller/SafetyGuard → IndyDCP3 절대 관절 명령 →
새 관측 재추론이다. 사용 모델은 성공 시연10개로 학습한 아래 보존 Checkpoint다:
`outputs/preserved/training_runs/act1_real10_executionfit_20261103_v1/training/checkpoints/008000/pretrained_model`.

2026-11-05 ACT 상태 신선도 후속: act_real_test_20261105T094912에서 SDK26 ACK·3 Chunk와
실제 움직임, 정지 확인/lease 정상 반환 기록을 읽었다. 새 양쪽 RGB sequence는
13/14→23/24→38/39, 관절2→9→19로3개 요청 모두 갱신·응답 수락을 확인했다.
Controller의 feedback 조회 전 now와 조회 중 교체된 캐시 timestamp를 비교하면 음수
age로 stale 판정하는 코드 경쟁을 확인·수정했다. 당시 실패 표본/검사 now는 미기록이라
그 실행의 음수 age 자체는 확정하지 않는다. 거부 시 signed age를 기록하고 Driver의
상태 로그도 실제 캐시 timestamp를 사용한다. 제한·Guard·정지는 유지, 집중17개 통과.
실물/ROS/신규 학습/커밋은 없으며 실제 정상 종료 재시험이 남았다.

소프트웨어 확인: 2026-11-02. 실물 정보는 날짜와 근거를 구분한다.
작업 규칙은 [AGENTS](../AGENTS.md), 운영 진입점은 [README](../README.md)를 따른다.

## 구현 기준과 Git

- **2026-11-02 사용자 확인: Indy7 J6 문제는 완전히 해결됐다.** 현재 대화에서 사용자가
  명시적으로 확인했으며 J6 미해결을 전제로 한 과거 연결/이동 차단은 현재 기준이 아니다.
  복구 원인·방법·측정값을 에이전트가 새로 확인했다는 뜻은 아니다. 과거 진단 기록은 보존한다.
- 사용자는 Home 포함 수집을 위한 필요한 단계를 진행하도록 요청했고 현장 안전도 확인했다.
  J6 복구 검증은 반복하지 않는다. qdes의 중간 목표 의미·단위·시간과 수집 통합 검증은 별개다.

- 검증 구현 보존 기준: `f7d03b93d3044d136fb14d960b38e3e2373398d4`.
- Recorder/Core-108/Correction 기준: `b3bbb3b01b015aa60f769604c80db17fd723fac5`.
- 제거한 Pool의 마지막 미커밋 원문은 `78794cfdebddf7d08d7f3c671027d6bc064509b6`에 먼저 보존했다.
- `main`을 사용한다. 문서·정리 커밋으로 HEAD는 달라질 수 있으므로 매번 status/diff/log를 확인한다.
- 2026-11-01 구형 코드·생성물 정리 이력에서는 제어·Topic/QoS·데이터 형식·기록 시간 계약을 변경하지 않았다.

## 목적·환경·구조

선반 칸1의 가변 위치/회전 바구니를 동일 Home에서 파지해 고정 table drop zone으로 옮기는
시연을 수집하고 ACT로 학습·평가하는 팀 캡스톤이다. feasible workspace는 실물 검증 없이 만들지 않는다.

- Ubuntu24.04 / Python3.12 / ROS2 Jazzy, Indy7·Mand.ro Mark7·Xbox·D435 두 대.
- ROS/제어 `.venv`와 ACT `.venv-act` 분리. 로봇 NumPy1.26.4와 ACT NumPy2.2.6을 합치지 않는다.
- vendored LeRobot0.5.1, 과거 환경 기록은 Torch2.10.0+cu130/torchvision0.25.0+cu130이다.
  CUDA 검증 범위와 현재 사용 가능 여부는 별개다. 현재 의존성 원본은 requirements와 vendored pyproject다.
- 카메라 serial: `317222074298`, `844212071939`; 설정은 RGB640×480·30FPS이다.
- Mark7 설정 기본값은 `/dev/ttyACM0`,115200이다. 실제 포트·펌웨어·손가락 위치 피드백 지원은 미확인이다.
- 2026-11-03 약지 복구 시험에서 위 포트의 `R`/6축 position·current·temperature CSV를
  실제 수신했고 로컬 제조사 통신 PDF를 발견했다. 운영 노드의 피드백 구현은 없다.
  사용자는 약지가 많이 접힌 상태와 Reverse 시험에서 더 접힘을 확인했다. 복구 미완료,
  후속 명시 승인으로 약지 임시 Reset+100ms Open을2회 시험했으나 이 시험으로 복구하지
  못했다. 최종 Idle 후 복구 프로세스를 종료했고 추가200ms 시험은 자동 승인 검토 거절로
  실행하지 않았다. **이후 사용자가 그리퍼 전원 재인가로 해결됐다고 확인했다.**
  읽기 전용 점검에서 일반 Mark7/QP가 이미 다시 실행 중이고 기존 Open[-10×5,0]·
  speed50/current100 설정을 확인했다. 에이전트는 중복 실행·추가 Reset/구동을 하지 않았다.
  전원 재인가의 내부 복구 원인·정확한 영점/센서 보정은 확인하지 않았다.
  과거 자료의 미확인 범위 갱신과 근거는 [Mark7 조사](MARK7_STATE_FEEDBACK_INVESTIGATION.md) 후속을 따른다.
- `192.168.1.114`는 launch의 접속 대상 설정이다. 2026-11-02 읽기 관측에서 위 Home 수집 장비 신원을 확인했다.
- OMY는 별도 ROBOTIS 경로이며 현재 Indy7 수집과 통합 검증된 장비로 취급하지 않는다.

| 코드 | 현재 역할 |
| --- | --- |
| `capstone_teleop`·`capstone_mark7`, `scripts/bin/*` | 기존 QP·장비 launch·명령 wrapper |
| `ai/record/indy7_act_record.py`, `collection.py` | 수집·조건/Attempt·정상/교정 분리·장부/receipt/reconcile |
| `ai/observation_timing.py`, `dataset_contract.py` | 인과적 관측 선택·20FPS 시간/7D/두 영상 계약 |
| `ai/train/{validate_dataset,run_train,checkpoint}.py` | 검증→Episode 분할→Train 전용 통계→공식 학습→재로딩 검증 |
| `ai/serve/{infer,dry_run,zmq_act_server}.py` | localhost v2 추론 서버·관측·RPC·스케줄러 |
| `ai/execute/{controller,real_session,real_driver,task_executor}.py` | Mock/감독형 Real 명령·정지·명령권, 별도 Task/Home 상태 관리 |
| `ai/evaluate/main.py` | 오차·실행 신뢰성·별도 작업자 평가 보고서 |

## outputs 분류 및 신규 수집 경로 (2026-11-01)

- 사용자 확인 기준 정식 ACT 학습 Dataset은 아직 0개다.
- `outputs/act_gui_integration_test`만 장기 보존하며 기존 위치·이름을 유지한다.
  실제 Indy7 Recorder 통합 테스트 결과로 Success 1개/900 frames,
  Correction 1개/1696 frames와 조건 정의108개가 있다. 정상108개 수집 완료가 아니다.
  사용자 제공 실물 통합 결과이며 이번 정리에서 장비·영상을 재검증하지 않았다.
- 나머지 기존18개 폴더는 `outputs/temporary/{inference,verification}`으로 이동했다.
  실물 Pilot·합성 Checkpoint·검증 근거도 임시 검증 산출물이며 정식 Dataset과 구분한다.
- 새 정식 Collection은 최초부터 `outputs/preserved/datasets/<dataset_id>`에 생성한다.
  기존 기본값은 유지한다. 경로·명명·기존 Collection 재개 제한은 [outputs 관리](OUTPUTS.md)를 따른다.
- 이동 전후 기존19개 폴더412파일의 목록·SHA-256과 링크 대상을 대조했다.
  근거: `tmp/outputs_cleanup_vv70otzr/`. 기존 내부 메타데이터·장부·receipt는 수정하지 않았다.
- 정리 검증: 경로 보호·합성 저장/재개·메타데이터 평가 **66 passed**. 새 중첩 경로의
  격리 Collection 생성/재개도 확인했다. ROS·장비·학습·추론 실행은 없었다.

## Indy7 ACT 파일 기반 Dataset 선택 (2026-11-01)

- 경로 없는 `act record`와 `python -m ai.record.gui`는 데이터셋 선택 GUI를 연다.
  GUI는 목록 새로고침·선택한 데이터셋 열기만 제공한다. 행동 등록·계획 가져오기·ID 입력·조합/생성 폼은 제거했다.
- 정의 원본은 `ai/record/plans/<dataset_id>.json`이다. 파일 하나에 행동·계획·실제 조건 전체를 담으며
  실행/새로고침으로 자동 인식한다. 코드 수정·등록 명령·커밋·ROS 재빌드는 필요 없다.
- 최초 열기에서 `outputs/preserved/datasets/<dataset_id>`를 만들고 기존 스냅샷 형식으로 저장한다.
  이미 있으면 정의와 스냅샷 충돌·중복 ID·경로 충돌을 차단한다. 목록 조회는 쓰기나 ROS 초기화를 하지 않는다.
- 기존 GUI 등록부는 더 이상 사용하지 않지만 과거 Collection·등록 자료를 변환하거나 삭제하지 않는다.
  기존 Collection·통합 테스트 위치·명시 `--collection-dir` 실행·저장/복구 엔진을 유지한다.
- 원본 Core-108 그대로의 정의 JSON 하나만 제공한다. Core-55는 만들지 않는다.
  조건 ID·순서·설명·자유 필드로 임의 조건 수를 지원한다. 현재 정식 수집 데이터셋은0개다.
- 새 사용 기준은 최상위 [act-record.md](../act-record.md)다. 앞선 GUI 방식 검증236개 이력은
  `tmp/dataset_manager_3k51w707/`에 보존한다. 이번 파일 기반 검증은 아래 결과를 따른다.
- 최종 Recorder 전체 오프라인 회귀: **253 passed, 0 failed, 0 skipped** (113.50초).
  자동 검색/갱신·최초 열기/재개·임의 조건 수·오류/중복/경로/스냅샷 충돌 차단과 기존 GUI를 포함한다.
  실제 Tk는 Fake worker/합성 자료로 DPI1·2·3과720×420/1000×580/1600×1000에서 검증했다.
- 기존 outputs412파일과 링크2개의 경로·내용은 전후 동일하다. 원래 위치의 실수집 Collection
  읽기 전용 audit를 통과했다. 이전 미커밋 녹화/저장/복구 소스는 변경하지 않았고 정식 경로는 비어 있다.
  근거: `tmp/file_dataset_manager_jb_35avl/{verification.json,final.xml,final.log}`.
  실제 장비·ROS 노드·ACT 학습/추론 실행 및 Git 커밋은 하지 않았다.

## GUI 초기 크기·공통 배율 개선 (2026-11-01)

- 선택창과 녹화창의 초기 목표는 QHD 기준1300×820이다. 화면·WM 최대 크기에서
  테두리 여유를 빼고 작은 화면에 맞춰 조절한다. 시스템 DPI/환경은 변경하지 않는다.
- 선택창도 기존 `gui_scaling.UIScale`을 재사용한다. 제목·설명·표 글꼴/헤더/행 높이/열 너비·
  버튼·간격·안내를 창 비율에 맞게 조절하며 표는 가용 공간과 기존 스크롤을 사용한다.
- 배율1 기준960×660과 기존 범위의 녹화창 동작은 유지한다. 작은 화면용 하한은0.55로 확장했다.
  선택 화면에 등록·편집 폼을 추가하지 않았고 Dataset/녹화/저장/복구 엔진은 변경하지 않았다.
- 최종 GUI 관련 오프라인 회귀: **149 passed, 0 failed, 0 skipped** (15.31초).
  DPI1·2·3, 저해상도 초기 배치, QHD 목표, 확대/축소·최대화/복원·이벤트 병합,
  기존 녹화 GUI·데이터셋 자동 검색/열기/재개를 포함한다. Fake worker/합성 자료만 사용했다.
- 기존 outputs412파일과 링크2개의 경로·내용은 전후 동일하며 관련 없는 이전 소스도 보존했다.
  근거: `tmp/gui_resize_kbuhnqi4/{verification.json,final.xml,final.log}`.
  실제 Indy7·ROS 노드·카메라·ACT 학습/추론 및 Git 커밋은 실행하지 않았다.

## 정식 실행 CLI (2026-11-01)

- 기본 명령은 `indy`, `omy`, `act`이며 D435 전용 `camera`, `cam1`, `cam2`를 추가했다. 구현은 `scripts/bin`에 있고
  `scripts/install_commands.sh`가 `~/.local/bin`에 연결한다. 재연결은 `--replace`로 symlink만 교체한다.
- `indy [driver|teleop]`은 카메라 없이 기존 ROS 제어 launch·인자를 유지한다. `indy cam`은 제거했다. 실제 제어·자동 ARM 제한은 동일하다.
- `act record`는 기존 GUI/터미널 Recorder 분기를 유지한다. 나머지 train/validate/infer/execute/task/evaluate와
  학습 환경·실제 실행 잠금·Mock 계약은 유지한다. `act`만 입력하면 도움말이다.
- `omy`는 공용 `/opt/workspace/omy` 사전 점검/status/doctor/logs만 제공한다.
  공유 컨테이너·로봇 프로세스 시작/정지/재시작 및 Leader/Follower 자동 구동은 제공하지 않는다.
- 중복 루트 실행 래퍼4개는 제거했다. `act-record.md` 등 문서는 유지했다.
  `scripts/bin`의 기존 cap* 및 act-record는 내부 호환 경로로 남기되 사용자 명령으로 설치하지 않는다.
  과거 실험/장애 문서의 옛 명령은 이력으로 유지하며 현재 명령 안내는 README를 따른다.
- 계정 `.bash_aliases`는 백업 후 indy/omy 함수만 제거하고 재로딩 시 기존 함수 캐시를 해제한다.
  `.bashrc`·개인 alias·공용 OMY 파일·기존 환경과 장비 코드는 변경하지 않았다.
- CLI 가짜 분기·경로/링크/인자/설치 충돌 및 메타데이터 평가 **82 passed, 0 failed**.
  설치된 세 명령의 실제 --help와 함수 우선순위도 확인했다. 실제 장비·카메라·ROS·수집·학습·추론은 실행하지 않았다.
  근거: `tmp/cli_cleanup_6hxymi06/{verification.json,final.xml,final.log}`.
- 현재 bootstrap의 requirements 파일 누락 문제는 별개로 남아 있다. CLI 설치는 bootstrap·의존성 재설치 없이 수행했다.

## D435 독립 CLI (2026-11-01)

- `camera`는 D435317222074298+844212071939, `cam1`은844212071939, `cam2`는317222074298을 실행한다.
  CLI 번호만 교체했으며 namespace/토픽/ACT 설정과 serial별 잠금은 그대로 유지한다.
  번호 교체 후 CLI/잠금/가짜 launch 회귀32개를 통과했다. 근거: `tmp/camera_mapping_bk6tyhg1/verification.json`.
  `scripts/install_commands.sh`가 사용자 `~/.local/bin`에6개 명령을 연결한다. D405 명령은 추가하지 않았다.
- Indy setup의 카메라 포함을 제거했다. Driver/Mark7/Xbox/QP/GUI와 기존 인자는 유지한다.
  `show_camera`는 setup의 호환 인자일 뿐이다. 카메라 명령에서는 미리보기만 제어한다.
  내부 caps도 새 indy에 위임한다. 소스 launch/YAML을 직접 지정하므로 이번 변경에 ROS 재빌드는 필요 없다.
- serial별 flock을 `~/.local/bin/.sirlab258-camera-locks`에서 같은 계정·다른 clone 간 공유한다.
  묶음 실행은 하나라도 잠겨 있으면 전체 거절한다. 이미 실행 중인 프로세스를 종료/재시작하지 않는다.
  lock 파일은 지우지 않으며 종료 시 FD 잠금이 해제된다. 외부/다른 계정·직접 ros2/SDK 점유는 알 수 없다.
  강제 비정상 종료로 자식이 남는 경우까지 장치 소유권을 보증하지 않는다.
- 기존 RGB namespace/토픽·YAML·ACT 두 영상/640×480/20FPS 계약은 유지했다.
  OMY·D405·공용 환경·VLA/브리지 구현은 변경하지 않았다.
- 최종 가짜 launch/노드·잠금 경합·독립 종료/재시작·CLI 회귀·Recorder GUI 테스트:
  **62 passed, 0 failed, 0 skipped** (2.96초). 실제 카메라 명령은 --help만 실행했다.
  설치된 ROS CLI 소스로 절대 launch 파일 인자 형식을 확인했다. 실제 영상 발행·ROS 종료 시
  장치 반환·외부 점유 오류는 별도 실물 검증이 필요하다.
- 기존 미커밋 ACT/OMY/제어/수집 자료 해시는 유지됐다.
  근거: `tmp/camera_cli_66g5gpfc/{verification.json,release.xml,release.log}`.
  실제 장비·카메라 스트리밍·ROS·학습·추론 실행 및 Git 커밋은 하지 않았다.

## Home 포함 ACT 수집 (2026-11-02)

- 사용자 J6 해결·현장 안전 확인 후 운영자의 기존 Home에서 qdes 중간 목표를 실측했다.
  serial PD47I7E0D002 / controller3.3.2-GA12 / SDK3.4.2.2에 한정한다.
  2400표본/20.0006Hz, 이동69표본, state RPC 최대4.91ms/4 getter17.20ms,
  오류/75ms 초과 간격0. degree 목표·미분 대응을 장비별 근거로 기록했다.
- `ai/record/config/home_capture.json`을 활성화했다. `act record`가 읽기 전용 독립 source를
  시작하고 `/capstone/recording_context`의 measured q와 Home qdes를 기존20FPS/7D에 정렬한다.
  QP 명령/기존 movej/재ARM/Driver는 유지한다. 당시에는 Home 완료 후 r을 요구했으며,
  2026-11-06부터 종료 Home 요구는 명시 옵션으로만 적용한다. 실제 작업 성공 확인 후 o다.
  Home 없는 기존 터미널 수집은 `--no-home-capture`로 Home 전에 r를 사용한다.
- 프로필은 collection_only이며 원문 SHA/신원/버전 결합이다. 원문은
  `outputs/preserved/validation/home_qdes_20261102_0rx_dvsw/`에 보존했다.
  두 D435·state/context 수신 확인과 전체 실제 이동7D Episode 저장 검증은 구분한다.
- 관련 오프라인424개와 마지막 집중110개 통과. 근거 `tmp/home_live_0rx_dvsw/`.
  기존 Collection/Receipt 보호와 Core108 호환성을 유지한다. 빌드·커밋·학습/추론은 하지 않았다.
- 2026-11-03 사용자가 그리퍼 개폐 정상 동작을 확인했다. 실제 Recorder에서 latched
  성공 명령 상태0.0과 두 RGB/state/context를 수신하고 HistoryAligner 시작·무결성 검사를
  통과했다(fault0). 초기 gripper 차단은 해소됐다. 이동/그리퍼 명령은 보내지 않았다.
  근거: 위 보존 디렉터리의 `gripper_start_20261103.json`. 전체 실제 이동 Episode 저장은
  아직 확인하지 않았으며 첫 기술 Pilot에서 확인한다.
- 수집한 controller 목표를 Teleop으로 재생하는 별도 replay는 미검증/잠금 유지. 실행할 때의
  추종/보간/속도 의미는 별도 검증 대상이다. [Home 수집](ACT_HOME_RECORDING.md)을 따른다.

## ACT 학습 Snapshot (2026-11-03)

- `act snapshot --collection <경로> [반복] --dataset <경로> [반복] --name <이름>
  --expect-episodes <전체 성공 수>`를 제공한다. 10개 하드코딩/부분 선택/자동 중복 제거는 없다.
- 단일 입력은 success Dataset 독립 복사, 다중 입력은 LeRobot0.5.1 공식 aggregate_datasets다.
  `outputs/training_snapshots/<name>/` 자체가 표준 v3 Dataset 루트이며 기존 act train에 전달한다.
- 기존 Recorder/Home/OMY/공식 LeRobot/학습 코드는 바꾸지 않았다. Snapshot은 Collection
  공유 잠금을 읽기 전용으로 사용하며 해당 Recorder 창을 정상 종료한 뒤 생성한다.
- 원본 Episode/Attempt와 출력 Episode 매핑, 이전 Snapshot ID를 manifest로 추적한다.
  Timing frames 원문을 유지하고 병합 시 episode.json의 출력 identity/index를 연결한다.
- 성공4개 단일 복사와 원본4개+복사4개인 중복 포함8개 병합을 테스트했다. 공식 로더 인식과
  기존 Validator의 metadata/data·매핑 검사만 수행했다. 영상 전체 디코딩·Home/기록 시각
  정밀 재검증·학습/추론·ROS/장비 접근은 없었다. [Snapshot](../ai/train/SNAPSHOTS.md)을 따른다.

## Real Inference 소프트웨어 연결 (2026-11-03)

- 기존 Backend/역정규화·두 RGB 관측·20Hz Scheduler·SafetyGuard·ExecutionController·Authority·
  Watchdog와 guarded ROS Driver를 재사용한다. 명령은 절대 rad→한 번 deg→MoveTeleJ method10이다.
- SDK3.4.2.2 현재/원래 joint_pos_limit min/max와 SafetyLimits를 시작 시 읽어 검토된 운영 범위와
  대조한다. 실제 wire에는 속도/가속도 배열이 없다. 제조사 참고 한계와 운영 한계는 분리한다.
- SDK Servo/boot/violation/stop/safety/teleop/motion 상태와 CAT2 stop_motion+stop_teleop가 연결됐다.
  실제 정지는 fresh idle/qdot 안정 구간 확인 후에만 확정하며 미확인은 quarantine이다.
- 사용자는 제어함이 PC에 연결되고 Xbox/Conty 태블릿을 끊을 수 있다고 확인했다. **제어함은 켜 둔다.**
  감독형 직접 유선 시험은 `authority_mode: supervised_direct_link`, 최대5초로 준비했다.
  다른 Writer/Conty 종료와 직접 케이블·빈 영역·E-stop 접근을 현장에서 확인하는 조건이다.
- 이 모드는 별도 Supervisor/heartbeat 없이 kernel의 직접 유선 route/interface/carrier,
  기존 QP/ACT 잠금 소유자/파일 identity, 다른 로컬 controller TCP 연결과 ROS 충돌을 확인한다.
  이상이면 기존 정지 경로로 세션을 폐기한다. 임의 SDK client를 커널에서 강제로 차단하는 ACL은
  아니다. 탐지 전 단발 명령 위험까지 제거하지 않으므로 다른 client를 새로 실행할 수 있는
  공유/비통제 환경에서는 사용하지 않는다. 기존 external interlock 모드는 유지한다.
- E-stop은 정지 상태 3구간 capture + 해당 FW 필드 의미 검토 + 원문 해시/장비 신원 결합 후
  SDK에서 직접 사용할 수 있다. Ready를 E-stop 해제로 추정하지 않는다. 해제해도 세션 자동
  재개/복구/Home은 없다. 현재 실제 필드 의미·운영 제한·정지/통신 장애는 현장 확인 전이다.
- RPC UNIMPLEMENTED 진단: getter별 성공/실패/gRPC 코드를 보존하며 선택 진단 실패 후에도 관측한다.
  필수 E-stop 실패는 INVALID 유지하고 공식 SafetyFunction 조회는 미검증 별도 원문으로만 저장한다.
- `scripts/inspect_indy7_real_readonly.py`의 기본 실행은 비연결이다. 명시적 getter capture와
  오프라인 하드웨어 draft 생성을 지원한다. `act execute --mode real --preflight-only`는
  모델/ROS/SDK 없이 설정 오류를 출력한다. 기존 두 프로필의 미확정 null은 임의로 채우지 않았다.
- 이번에는 실제 SDK/RPC·ROS·시리얼·로봇 구동을 하지 않았고 운영 프로세스도 건드리지 않았다.
  Recorder/Home 녹화/Snapshot/데이터/OMY·공식 소스는 변경·재검증하지 않았다.
  이번 변경 집중 Fake/Mock **48 passed** (`tmp/real_commission_20261103/final.xml`).
  [현장 명령과 통과 기준](../ai/execute/REAL_INFERENCE.md)을 따른다.

## 수집에서 평가까지

Home 후속 호환성 점검(2026-11-02): 실제 구독 콜백을 쓰는 Fake ROS에서 Home 없는
Core-108 Success/Correction 저장 Action이 기존 경로와 동일하고 Receipt 재개가 가능함을
확인했다. 당시 live reader/필수 Home 종료는 비활성이었다. 현재 활성 상태는 위 Home 수집 항목을 따른다.
설치 QP는 현재 소스와 정적으로 일치하지만 실행 중인 QP 버전·실제 ROS 수신은 미확인이다.
GUI 오류 설명과 비연결 설치 점검, qdes 진단의 초기화/응답/종료·분석을 보강했다.
관련 전체 회귀417개, Fake wrapper2개, 마지막 추가 호환성/진단33개(부분집합)가 통과했다.
근거 `tmp/recorder_compat_jsjoocks/`; QP 제어/telemetry와 기존 Collection/Receipt12개는
이 단계 시작 전후 해시가 동일하다.
장비 연결·SDK RPC·Home·ROS 제어 노드·학습/추론·빌드·커밋은 하지 않았다.
이전 조사 때의 J6 확인 전 읽기 연결 금지는 위 사용자 해결 확인으로 갱신됐다. 상세는
[호환성 및 관찰 절차](RECORDER_COMPATIBILITY_AND_QDES_OBSERVATION.md)를 따른다.

실물 운용 승인 및 장비 상태 확인 → 카메라/상태/QP 목표 → Recorder/Core-108 → Success Dataset →
Validator → ACT 학습/Checkpoint → Synthetic 또는 승인된 ROS 관측 Dry Run → Mock 실행/Task → 평가.
이는 실물 모터까지 ACT로 연결해 검증했다는 뜻이 아니다.

- 일반 `s → r → o`, 교정 `s → x → r → o`; x는 녹화를 계속하고 첫 마커를 유지한다.
- [Recorder GUI](../ai/record/GUI.md)는 Tk 버튼을 동일 엔진에 전달한다. 기존 CLI/wrapper·저장 계약을 유지하며 영상 미리보기·로봇 제어는 없다. GUI 실물/화면 통합 검증은 미완료다.
- 목표는 L/C/R × A1..A6 × 여섯 Depth의 정상 성공108개다. 원본 ID와 Shuffle은 유지한다.
- 조건표는 `docs/ACT_108_Episode_Execution_Log_Template.md`를 코드가 직접 읽는다. 이동·수정 금지.
- `ready`는 제거됐다. s에서 조건·Attempt·선택적 좌표를 확정하고 60ms 시작 검사 후 기록한다.
- 미측정 좌표는 null이다. 활성 Home 프로필에서도 작업 종료 자세에서 r로 끝낼 수 있다.
  `--require-home-complete`는 종료 Home 완료를 추가로 요구한다. 미검증 Home 녹화는 차단한다.
- `datasets/indy7_act_core108/{success,correction,failure,pending}`와 `collection.json`, `receipts/`를 함께 보존한다.
- 정상만 Core 완료에 포함한다. Correction은 미완료를 유지하고 동일 조건의 다음 Attempt를 안내한다.
- 신규 Failure 저장은 없으며 과거 Failure 이력은 읽는다. INVALID 저장 차단·Pending·reconcile을 유지한다.
- 학습 입력은 `datasets/indy7_act_core108/success`다. 독립 모드 기본 `datasets/indy7_act`와 다르다.
- Correction 전체 Episode·교정 시작 프레임/시각은 저장하지만 Correction ACT 학습·혼합·실패 구간 제외는 미구현이다.
- 관절 action은 절대 목표 rad이며 gripper0/1은 성공 응답을 받은 명령 상태다. 실제 파지 센서값이 아니다.

## 검증 범위

| 항목 | 확인 범위 / 근거 | 확대하면 안 되는 범위 |
| --- | --- | --- |
| 과거 수동 제어 | 2026-10-02 등의 실물 운용 이력. [Teleop](TELEOP.md), [J6 인계](INDY7_J6_FAULT_HANDOFF.md) | 최신 전체 stack의 실물 인증 |
| 기존 Recorder 실물 입력 Pilot | 사용자 실측과 `outputs/temporary/verification/act_recorder_pilot_20261031/success` 저장 기록: 5 Episode/3264 frames/20FPS. sidecar는 saved·success·integrity valid | 정식108개 완료·최신 Core/Correction·물리적 파지 성공. 이번에는 영상을 재검증하지 않음 |
| 최신 Core-108/Correction | 정리 후 CPU 회귀의120개, 원본108개 합성 저장·중단/부분 저장·reconcile·교정 마커 포함 | 최신 실물 통합 검증은 미완료 |
| ACT CPU 연결 | 2026-11-01 GUI 디자인 개편 후 `547 passed / 0 failed / CUDA2 deselected` (기존482+GUI65), 공식 소형 ACT3-step·Checkpoint·추론·Mock·평가 | 최종 모델 수렴·실물 ACT 성능 |
| Recorder GUI | 실험용 네 영역 UI·기술 정보 팝업, GUI65개 검증. 실제 Tk에서 Fake 상태로 렌더링·최소860×640/기본960×660·긴 Depth/오류 경계 확인. 엔진·bridge 유지 | ROS 실수신 부하·실물 운용 미검증 |
| 과거 CUDA 합성 연결 | 별도 `outputs/temporary/verification/act_final_offline_20261031T025706Z_09a8f399/verification.json`의 CPU/CUDA3-step 기록 | 최신482개에서 CUDA 재검증한 결과가 아님 |
| 실제 ROS 입력 ACT 추론 | 2026-11-05 두 D435·Indy7 관절·Mark7 명령 상태로 5초 Closed-loop 완료, 새 관측 재추론 확인. 최종 로그는 문서 첫 항목 | 영상 변화별 작업 성능·장시간 안정성 |
| ACT 실제 관절 구동·Real Home | 5초 세션에서 SDK ACK96회·측정 관절 변화0.0249755rad·정지 확인. 별도 Real Home 실행은 미검증/잠금 유지 | 물체 파지·작업 성공·목표 추종 정확도·Real Home 검증으로 확대하지 않음 |

직전482개 증거는 `tmp/act_compat_pe61fnjr/{verification.json,results.xml,suite.log}`에 보존했다.
구형 합성 Dataset·Checkpoint·캐시는 제거하고 실행/평가 메타데이터·로그는 `evidence/`에 남겼다.
이번 정리 후 결과는 `tmp/act_cleanup_1jj8_97d/{verification.json,results.xml,suite.log}`에 보존했다.
이후 GUI 추가 전체 회귀510개와 최종 GUI30개 기록은 `tmp/act_gui_djcd2anu/{verification.json,final_results.xml,final_suite.log,gui_final_release.xml}`에 보존한다. 고유 테스트는512개다.
이후 디자인 개편 최종547개 회귀·보호 대조·Fake Tk 화면은 `tmp/act_gui_design_eten3gqc/`에 보존한다. 상세 사용법은 GUI 문서가 기준이다.
CPU 전체 회귀 결과와 복구 커밋은 [정리 기록](PROJECT_CLEANUP_AUDIT.md)의 최신 항목을 따른다.
벤치마크는 일반 pytest와 별개다. 가짜 ROS QoS 설정과 임시 경로를 보완해 3초 합성 실행으로 58 frames·시간 무결성 valid를 확인했다.

## 기술적 제약·안전 제한

- 최신 장비 상태·접속 대상·위치 정확성·실제 QoS/clock·카메라 mount/USB 대역폭은 이번에 확인하지 않았다.
- 과거 J6 큰 음수/FAULT 반복 관측과 후속33.47° 관측은 [인계](INDY7_J6_FAULT_HANDOFF.md)의 날짜별 기록이다.
  현재 같은 값이라고 단정하지 않는다. **2026-11-02 사용자가 J6 문제의 완전 해결을 확인했다.**
- 과거 J6 미해결 때문에 두었던 연결/이동 제한은 현재 대화의 사용자 확인으로 해제됐다.
  실제 명령은 현재 승인된 Home 수집 준비 범위에서만 수행하며 calibration/encoder/Servo/SDO
  복구 write를 추가로 실행하지 않는다.
- teleop/move/Home/Zero/Recover/calibration, encoder·offset·multi-turn reset, Servo/brake/SDO/PDO write를 임의 실행하지 않는다.
  `reset_servo`, `reset_overflow_count`, `set_robot_zero_as_current`, `set_servo`/`set_servo_rx`,
  `set_brake`와 brake release/수동 강제 이동도 안전한 조회·복구로 취급하지 않는다.
- `indy`/`indy driver`/`indy teleop`와 Driver 시작/종료는 관측 전용이 아니다. 기존 장비 노드·시리얼을 임의 종료하지 않는다.
- 실제 getter·ROS 구독도 목적과 API를 설명하고 현재 사용자 승인 범위에서만 수행한다.
- Gripper 무명령 실제 초기 상태 조회는 명세·펌웨어 근거 미확인이다. 임의0/1이나 Open/Grasp로 우회하지 않는다.
  QP/Recorder는 RELIABLE/TRANSIENT_LOCAL/depth1, 추론 RosSource는 RELIABLE/VOLATILE/depth10이다.
- ROS Dry Run의 operator-initial-gripper는 명시적 가정이며 Recorder/실제 실행에서는 사용할 수 없다.
- Real ACT에는 SDK readiness/state·자격 확인된 E-stop·선택한 운용 방식의 명령권·실물 운영 제한/초기 자세 검증이 필요하다.
  감독형 직접 PC 시험 조건은 위 Real Inference 항목을 따른다. Mock 제한값을 실물 값으로 복사하지 않는다.
  QP는 외부 장애물 충돌 회피를 제공하지 않는다.
- dual DCP 구조는 기존 설계다. 장애 원인으로 단정하거나 single-owner 재설계를 적용한 것처럼 쓰지 않는다.

## 해결 이력·보류·다음 작업

- 해결된 소프트웨어 문제: 구형 SAVED 문자열 의존 테스트, Train-only 통계 로딩/역정규화,
  두 카메라·7D 학습/추론 연결. 구형 잘못된 정규화 Checkpoint는 자동 수정하지 않는다.
- 보류: `scripts/rotate_ros_image.py`는 과거 wrist 독립 도구이며 수동 외부 호출 여부가 미확인이라 유지했다.
- 구형 Pool·단일 카메라 YAML은 Git 원문과 설계 메모를 남기고 제거했다. 현행 v2 경로로 사용하지 않는다.
- 다음 단계: 실제 작업 성능·미관측 pose 평가와 실패 원인 분석. 추가 수집/학습/현장 실행은
  각각 현재 승인과 기존 안전 절차에 따른다. 과거 Core-108 목표와 별도 Real Home 검증은 유지한다.
- 문서·코드 정리는 이 실물 작업의 승인이 아니다. 제조사 문의는 초안·과거 조사와 실제 송신/답변을 구분한다.
- 상세 공식 복구 자료·미확인 원인·제조사 요청은 J6 인계, Mark7 자료는 [피드백 조사](MARK7_STATE_FEEDBACK_INVESTIGATION.md)를 따른다.

아래 날짜별 기록은 당시 상태와 검증 범위의 이력이다. 현재 상태는 문서 첫 항목과
위 검증 표를 기준으로 읽는다.

### Real 정지 감지 후속 (2026-11-03)
- 사용자가 물리적 E-stop 정지와 CONTY 수동 복구 전 명령 거부를 반복 확인했다. 반복 성능 시험은 요구하지 않는다.
- getter-only 실측: JointLimitConfig/current-original는 UNIMPLEMENTED, Boot20010 접속 거부.
  기존 control/Servo/motion/stop/violation/safety_control/teleop 및 SafetyLimits 등은 응답했다.
  원문 tmp/real_site/rpc_support_readonly_20261103.json. 이동/Servo/정지/복구/설정 write는 없었다.
- 매 monitor 갱신에서 제어 자격을 검사해 불가/미확인 시 기존 sticky trip을 호출하도록 보완했다.
  직접 영향 Mock24개 통과. 필수 limit/boot의 미지원 문제와 FW별 E-stop 의미는 정상으로 대체하지 않았다.

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

## 기존 QP/Home 운용 설정 재사용 후속
연구팀 내부 검토가 승인 주체다. 별도 제조사/지도교수 서명을 필수 조건으로 추가하지 않는다.
ACT 미승인 검토본에 기존 QP45°/s,1.5°/command ceiling, exact Home/initial0.5°,
정지 low-qdot0.01/stable250ms,Home25ms RPC fail-fast budget을 의미별로 재사용했다.
로컬 route/sysfs만으로 enp11s0 direct interface를 확인했다(로봇 접속 없음).
config/indy7_real_value_sources.json이 출처/실물 사용/ACT 적용 범위를 기록한다. 전체
reviewed_for_real=false. 운영영역/amax/동적오차/실제 stop time 등은 미확정이다.
Mock20개 통과(tmp/profile_reuse_20261103.xml). Home/Recorder/Snapshot/GA12/운영 프로세스와
기존 데이터는 변경하지 않았다. 제조사 최대범위나 Home 도착오차를 ACT 운영영역/Tracking
한계로 복사하지 않았다. 자세한 현재 비교표는 PROFILE_REVIEW.md 후속을 따른다.

## QP 계측 준비
ROS 구독 전용 scripts/log_qp_readonly.py와 QP_MEASUREMENT.md를 추가했다. 기존 제어/SDK
연결을 변경하지 않는다. q/qdot·송신 후 발행 Action·context/status/graph 원문과 수신시각을
기록하고 오프라인 속도/표본 가속도/수신기준 Tracking을 계산한다. 정확한 SDK 송신/Stop
요청·완료/전체 Servo·Fault는 기존 토픽에 없어 이 로거로 보장하지 않는다. 완전한 정지
성능 측정 준비는 미완료이며 탐색 계측 단계다. 합성 분석1개 통과, 실물/ROS 미실행.

## Home 전후 QP 전진 drift 조사
사용자가 전진 시 Z/Pitch 이상을 보고해 Home 전후를 우선 비교했다. 원인은 미확정이며
제어 코드·파라미터를 수정하지 않았다. Home 도입 전7977e75와 현재 Jacobian/qp_step/solver가
동일하고,3d10bbb 이후 Home/재ARM/제어8개 함수는 recording telemetry 제외 AST동일이다.
Home 도입 때 SDK 직접 ARM→ROS Driver ARM/fresh origin 전환은 있었지만 이후 정상 운용
기준에도 포함돼 인과를 단정하지 않는다. 순수 전진 입력 Z/rotation0 및 재ARM reset 등
Offline14개 통과. 자세 유지는 기존 weighted cost이며 hard constraint가 아니다.
근거 tmp/qp_home_drift_audit/report.md. 장비/모드/프로세스 변경·실물 테스트·커밋은 없었다.

## 실제 SUCCESS10 CUDA 학습/오프라인 추론 완료 (2026-11-03)
정식 act1_108_001 SUCCESS10/8997frames를 기존 Snapshot으로 독립 고정했다:
outputs/training_snapshots/act1_pipeline10_20261103_v1. Correction/Discard 제외. 전체 Validator
17994 RGB frames decode·20FPS·7D·timing 검증 통과. 기존 데이터/Recorder/Home/QP는 미변경.
기존 독립 act train으로 CUDA AMP ACT500step,batch8,chunk40,n_action_steps10,pilot-only
(10개 전체 Train,heldout없음),표준ResNet18/512모델을 학습했다. run은
outputs/preserved/training_runs/act1_real10_pipeline_20261103_v1이며 Checkpoint000500/pretrained_model,
저장 processor/정규화 복원/reset 검증 통과,optimizer500update. 실제 학습 모델이 이제 존재한다.
이전 '모델 없음/synthetic뿐'은 이번 생성 전 이력이다. 저장 실제관측160개로 output[160,40,7]
유한값 확인. 단,모든 첫 목표가 현재관절 대비1.5deg를 초과해 기존 Guard가 차단해야 한다.
형식 eligible_for_inference는 실물 실행/학습 성능 보장이 아니다. 초기 목표 최대오차0.556rad,
명목20Hz chunk 가속도최대17.969rad/s². 프로필을 완화해 통과시키지 않는다.
현재 단계는 데이터→학습→저장관측 오프라인 추론 완료이며 실제 Closed-loop/파지/Home학습
성공은 미검증. 상세는 run/PIPELINE_REPORT.md. 추가 장비 연결/제어·Gitcommit은 없었다.

## ACT 첫 Action 오차 원인 분리
기존500step/160관측과 동일 Snapshot 분석 결과 exact index/target/state 일치. 관측160개에서
실제Action-state1.5deg 초과35%,예측-label 및 예측-state는100%. 전체8997프레임에서는
실제Action-state42.636% (QP49.812%,Homeqdes0%). 이는 Home 기록 결함을 재발견했다는 뜻이
아니며 QP requested target과 측정관절,Home 내부qdes의 생성 단계 차이를 구분해야 한다.
실제 Episode 시작10개 원래오차최대0.00791deg이나 모델10개모두실패(J2/J3 평균2.532/3.296deg).
기존Guard는 첫명령만 current feedback과 비교하고 이후Step은 이전목표와 비교한다. 따라서
160개 독립진입 시험과 연속실행은 다르다. 같은kind 연속목표의20Hz Step초과는 QP15.031%,
Home30.083%. 최대QP4.444deg는 기록50ms/원본명령100ms 및held→new 전환 사례다.
데이터는 원래 절대목표계약상 유효하나 unchanged1.5deg ACT guard와 샘플링 호환 문제가 있어
사용자 조건에 따라 무작정 추가학습하지 않았다. 기존500step체크포인트/원본/안전제한 미변경.
독립act train 재개 검사의ACT config 등록누락을 run_train.resume_plan의 ACTConfig import로
최소수정. fresh subprocess 회귀1개와 실제saved optimizer/scheduler 검사통과(학습미실행).
500step cosine horizon 보존 시 이후 LR1e-6임을 확인. 원인·관절별분포·그리퍼·재개검사
근거는 tmp/real_site/act_first_action_01/{report.json,report.md,analyze.py}.

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

## ACT 자율 진단·재학습 및 실제 모델 Mock 피드백 후속 (2026-11-03)
기존10개/8997frame Snapshot과500/5000 Checkpoint를 보존했다. 160x40 target/state/pad를
Parquet와 대조해 shift/cross-episode/mask 불일치0 확인. 저장 processor 검증·모듈 gradient와
실제 weight update 확인. fixed1 진단은 첫관절 MAE0.702deg/max1.466deg까지 fit; 전체10개
일반화 증거가 아니다. 기존500-horizon resume LR1e-6은 정체의 확인된 요인이다. Backbone
별도 LR1e-5 원인 가설은 실제 optimizer 단일group1e-4라 기각. latent posterior 진단도
주요 train/infer gap을 지지하지 않았다. 정규화/관절순서/target 이동으로 숫자를 맞추지 않았다.
독립 act train fresh12000step(1e-4→1e-6) 후 weights-only init+새 scheduler로8000step 수행,
전체 본학습20000step 한도 유지. 두번째만 기존10/40 prefix L1weight4, KL/구조/데이터/추론
불변. 추가 step과 weighting의 기여를 독립 분리한 실험은 아니다. 기본 weight1/resume
보존; 새 --init-checkpoint와 --execution-prefix-weight의 provenance/호환 검사 추가.
최종선택: outputs/preserved/training_runs/act1_real10_executionfit_20261103_v1/training/checkpoints/008000/pretrained_model.
같은160관측 pred-label평균4.497→2.422deg; 시작10개 pred-state평균1.565→0.540deg,
최대1.372deg, 1.5deg초과10→0. 실제backend batch1/reset반복과MockGuard도10개통과.
단,160개 각시점을 독립 시작으로 보면121개가1.5deg초과(정답 자체56개초과), 중간 자세
재현 오차는 남아 있다. in-sample이며 heldout/실물 성공률은 검증하지 않았다.
실제 최종model→기존Scheduler→Controller/Guard→MockDriver→새state→실제재추론을
가상시간2초/5추론/37명령으로 확인. 저장RGB고정·관절만Mock갱신·in-process RPC이며
실물영상/네트워크지연/물리추종시험이 아니다. step최대1.369deg,속도27.387deg/s;
가속도9.560rad/s²는Mock한계200하의결과로Real 승인값이 아니다. bounded_rate와첫strict
검사를유지했고이시나리오raw-output보정0. 정상합성/단절/NaN은별도구분,stop이후명령0,
Mock정지·lease반환확인. 집중72개통과(70+신규2). 전체기존테스트 반복은 하지 않았다.
Safety Profile/GA12/QP/Home/Recorder/Snapshot/OMY는이번작업에서변경하지 않았다.
Real preflight는qualified_sdk E-stop 유지, 미승인운영범위/가속도/오차/정지·배제기록 등으로
예상exit2. 두 reviewed_for_real=false유지. 연구팀내부승인 및현장제어/피드백시험이남았다.
로봇/ROS/SDK접속·명령·실물시험·Gitcommit없음. 상세수치/실험/현장명령은선택run의
FINAL_PIPELINE_REPORT.md, 원시분석은 tmp/act_final_pipeline_20261103/를 따른다.

## ACT 최초 명령 bounded_rate 후속 (2026-11-04)
앞선 `첫 Action strict`는 수정 전 이력이다. 현행 bounded_rate는 첫 raw 목표의
관절 범위/현재 거리/기존 1.5° Step을 먼저 검사하고, 통과한 목표의 **첫 출력만**
측정 속도에서 시작하는 기존 v/a/Step 교집합으로 제한한다. 모든 출력은 기존 Guard로
재검사하며 새 Chunk는 새 raw 목표를 사용한다. 제한값·strict mode·QP/Home/Recorder는
변경하지 않았다. 실제 Checkpoint의 저장 시작 관측10개가 후보 QP 가속도 외피
[4,3,4,5,3,5]rad/s²에서 Mock 첫 명령을 보냈고, Home±2° 후보의2초 Mock에서
5재추론/37명령/stop·lease 반환을 확인했다. Home±1.5°는2명령 후 범위 차단.
RGB는 저장 프레임 고정, 관절만 Mock 갱신이며 실물 검증은 아니다.
집중12테스트 통과. 두 Real 프로필 reviewed_for_real=false 유지, 실제 승인/구동 전이다.
승인안과 근거는 ai/execute/SAFETY_APPROVAL_PROPOSAL.md를 따른다.

## Writer 복구와 실관측 비구동 ACT (2026-11-04)
`indy`의 QP 진입 실패는 `tmp/runtime/indy7_writer.lock`에 남은 `held` 기록 때문이었다.
로컬 보유 프로세스/컨트롤러 TCP 연결은 없었고, 현장 작업자는 물리 정지와 다른 Writer
종료를 확인했다. 신규 getter capture `tmp/real_site/writer_stop_20261104_readonly.json`에서
PD47I7E0D002/GA12 장비의 idle·이동 아님·낮은 qdot 3표본을 확인한 후 같은 inode를
보존하며 수동 reconciliation했다. 당시 `released` 기록과 capture SHA256을 확인했고
`tmp/real_site/writer_reconciliation_20261104.json`에 복구 근거를 별도 보존했다.
에이전트는 자동 ARM 경로인 QP를 직접 실행하지 않았다. 복구 후 별도로 실행된
`qp_teleop` PID 583677과 `indy_driver` PID 583673을 관측했고 QP가 Writer를 다시
`held`로 획득했다. Driver의 실제 `/joint_states` 6축 표본도 수신했다. 종료 시 잠금
반환·Cartesian 동작은 아직 실물 검증하지 않았다. 정상 종료의 최종 정지 ACK 및
250ms idle/무이동/qdot 확인 실패 시 잠금을 quarantined로 남기도록 QP 종료만 수정했다.
GET 전용 관절 publisher로 `/joint_states` 20Hz 2400표본을 발행했다. 기존 D435 두
영상과 작업자 확인 Mark7 초기0(센서 피드백 아님)을 ACT CPU Dry Run에 입력해
Chunk 1개/로그 Action 10개를 얻었다. 다음 Chunk는 CPU 추론이 20Hz 일정에 못 미쳐
`inference_cannot_keep_up`으로 종료됐다. 로봇 명령0개; Safety Profile의 두
`reviewed_for_real=false` 유지. 실제 Guard 승인/물리 움직임은 확인하지 않았다.
운영·복구 절차는 ai/execute/EXECUTION.md를 따른다.

## CUDA 실관측 비구동 추론과 최초 시험 프로필 승인 (2026-11-04)
Codex 샌드박스에서는 NVML/CUDA 장치가 보이지 않았지만 같은 `.venv-act`를 워크스테이션
환경에서 실행하면 RTX PRO 6000 Blackwell/CUDA13.0/Torch2.10.0+cu130이 정상이다.
ACT 서버에 `--device cuda`를 명시한 실제 두 D435·Driver `/joint_states` 비구동 시험은
5초 동안 유효 관측100개, Chunk11개, Action100개, 20Hz 일정 누락0으로 완료됐다.
GPU 입력 Tensor는 cuda:0, 추론 p50 7.812ms/최대9.094ms, chunk당 GPU 메모리 peak
약0.278GiB였다. Mark7 초기0은 작업자 확인값이며 센서값이 아니다. 로봇 명령0개,
실물 Guard 승인/동작 검증은 포함되지 않는다. 근거는
`outputs/temporary/inference/real_observation_gpu_20261104/`이다.
연구팀은 `ai/execute/SAFETY_APPROVAL_PROPOSAL.md`의 제한된 최초 시험 운영값을
2026-11-04 12:30 UTC 승인했다. 두 실물 JSON에 승인값을 반영했고 기존 비구동 Preflight
`profile_valid:true`/오류0을 확인했다. 현장 실제 구동 승인은 별도이며 QP/기존 Driver
종료, Mark7 전용 서비스·확인된 초기 상태, 작업 공간과 E-stop·작업자 위치 확인이 남았다.

## QP 정상 종료 후 Writer 점검 (2026-11-04)
사용자가 기존 `indy` 세션을 종료했다. QP/Driver·제어기 TCP 연결이 없고 ROS에는
두 카메라만 남았으나 Writer 기록은 `quarantined`다. 종료 로그
`~/.ros/log/python3_583677_1793794810791.log`에 `stop_teleop was not acknowledged`가
있다. 설치 SDK/프로토콜의 StopTeleOp 응답은 최상위 `{code,msg}`인데 QP가
`response.code`를 요구한 코드 오류가 확인됐다. QP와 ACT 공통 응답 검사를
최소 수정했고 관련 오프라인 회귀5개가 통과했다. 현재 잠금은 변경하지 않았다.
새로운 현장 물리 정지·다른 Writer 종료 확인과 fresh getter capture를 근거로
기존 수동 reconciliation을 해야 ACT Real Driver가 Writer를 획득할 수 있다.

### Writer 수동 복구와 비구동 준비 후속 (2026-11-04)
사용자가 현장 정지·다른 QP/ACT/SDK Writer·Conty/다른 PC 제어 없음·제어권 충돌 없음을
확인했다. 확인자 실명과 실제 물리 확인 시각은 제공되지 않아 기록에서 null이다.
새 GET-only `tmp/real_site/writer_stop_20261104_2142.json`은 동일 serial/FW/SDK에서
3회 idle5, motion=false, qdot 최대0.002848rad/s, violation0을 보였다. 로컬 QP/Driver
프로세스와 제어기 TCP 연결 부재 확인 후 기존 수동 절차로 inode를 유지하며
`quarantined`→`released` 복구했고 flock 미점유를 확인했다. 근거는
`tmp/real_site/writer_reconciliation_20261104_2142.json`이다. 로봇 명령0개.
Mark7는 전용 `/capstone/real_gripper/{open,grasp}` 서비스와 transient-local
`/capstone/gripper_state` Publisher로 비구동 시작했다. Open/Close 명령은 보내지
않았으므로 초기 그리퍼 명령 상태는 아직 없다. 두 D435는 ROS 그래프에 남고 기존
QP/Driver는 없다. Real Guarded Driver는 승인 전 시작하지 않았다.

### 실제 Home 중심 J6 범위 정정 (2026-11-04)
연구팀은 승인한 Home ±2°가 실제 Capstone Home 중심임을 명확히 하며 J6만
88.507°~92.507°로 정정 승인했다. 다른 축은 설정 중심과 실제 Home 차이가 최대
0.005221°라 기존 범위를 유지했다. 두 실물 Profile의 검토 기록과 기존 승인안을
맞췄고 비구동 Preflight는 `profile_valid:true`/오류0이다. 속도·가속도·Tracking은
변경하지 않았다. Mark7 운영 노드는 시리얼 `R` 조회 결과를 실제 Open/Grasp로
분류·발행하지 않으며 Real Session은 성공한 명령 상태 이벤트를 요구한다.
현장 물리 Open 확인만으로 해당 이벤트를 생성하지 않았다. 실물 Open/관절 구동은
별도 승인 전 실행하지 않는다.

### Mark7 초기 명령 상태 확보 (2026-11-04)
사용자가 Mark7 Open 1회만 명시 승인했다. 전용 Mark7 노드의 단독 시리얼 점유와
`/capstone/real_gripper/open` 서비스를 확인한 뒤 Trigger를 정확히 한 번 호출해
`success=True`, `Mark7 open command sent` 응답과 노드의 Open preset 송신 로그를
확인했다. 기존 transient-local `/capstone/gripper_state`를 읽기 전용 ROS 구독으로
조회해 `0.0` 수신을 확인했다. 이는 성공 응답을 받은 **명령 상태**이며 손가락의
물리 위치 측정은 아니다. Indy7 Writer는 `released`; ACT/Servo/Home/관절 구동은
실행하지 않았다. 최대2초 실물 시험은 별도 명시 승인과 기존 runtime gate가 필요하다.

### 최초 ACT 실물 시험 시도 (2026-11-04)
사용자의 현장 구동 승인 후, 기존 프로필·CUDA·Checkpoint로 최대2초 Real Session을
한 번 시작했다. 첫 관절 명령 전 두 카메라 시간차가 42.42ms로 승인된 40ms를 넘어
`camera_skew`로 중단됐다. ACT Chunk 0개, 관절 명령 0개, Mark7 요청 0개다.
별도로 관측 노드와 Driver가 기본 ROS Executor를 동시에 spin해 Driver가 trip됐고,
SDK StopMotion/StopTeleOp RPC는 성공했으나 세션의 정지 확정은 실패해 Writer가
`quarantined`로 남았다. 사후 읽기 전용 getter 3회는 idle5, motion=false, queue0,
관절 변화 최대0.00033°를 보였다. 이 getter만으로 Writer를 자동 복구하지 않는다.
Driver에는 별도 SingleThreadedExecutor를 적용했고 장비 없는 동시 spin 검사를 통과했다.
실물 재시도는 하지 않았다. 근거: `outputs/temporary/inference/act_real_closed_loop_20261104_01/`,
`tmp/real_site/act_posttrial_20261104_01.json`.

### 첫 실물 시도 후 비구동 복구 (2026-11-04)
두 D435의 `rgb_camera.global_time_enabled=true`/`use_sim_time=false`와 실행 로그를
대조했다. 42.42ms 중단은 최신 프레임을 카메라별로 따로 고른 결과이며 한 카메라의
66.7ms Header 간격이 기록됐다. ACT ROS 관측에서만 기존 버퍼의 가장 가까운 인과적
프레임 쌍을 선택하도록 수정했다. 40ms 시간차·50ms 프레임 나이·기존 시계 검사는
유지하고 유효 쌍이 없으면 차단한다. Recorder는 기존 최신 프레임 선택을 유지한다.
실제 D435 두 대를 ACT의20Hz/60ms 수신 대기 기준으로 비구동 관측한 결과100회 중
99회가 유효했고 최대 시간차16.65ms, 최대 프레임 나이45.89ms였다. 1회는 유효
프레임 나이 조건을 만족하지 않아 차단됐다. 관측 ROS 스레드와 분리 Driver Executor의
실제 ROS 동시 spin 검사에서는 오류가 없었다. 실제 Guarded Driver는 실행하지 않았다.
첫 시도의 정지 확정 실패는 Driver Executor가 먼저 종료돼 ROS stop service future가
완료되지 못한 상태에서1초 timeout이 난 것이다. 별도 SDK StopMotion/StopTeleOp ACK는
정지 확정으로 취급하지 않았다. 사용자는 시도 후 현장 정지 및 다른 제어 종료를 다시
확인했다(확인자 이름 미제공). 다른 제어기 TCP/Writer 부재와 새 getter3회 idle5,
motion=false, queue0, qdot≤0.01rad/s를 확인한 후 공식 수동 절차로 Writer를
`released`로 복구했다. 프로필 Preflight는 valid/오류0이다. 실제 재시험은 별도
현장 구동 승인 전 수행하지 않는다. 근거: `tmp/real_site/{camera_pair_exact_dry_20261104_01.json,
act_writer_stop_20261104_2217.json}`.

### 두 번째 ACT 실물 시험 시도와 이중 잠금 복구 (2026-11-04)
사용자가 새 현장 안전 확인과 최대2초 구동을 승인한 후 한 번 실행했다. CUDA 모델과
실제 Driver `/joint_states` 수신을 확인했지만, 첫 시도에서 남은 별도
`tmp/runtime/act_real_session_lease.json`의 `quarantined`를 기존 수동 복구가
처리하지 않아 명령 Lease 획득 단계에서 중단됐다. 관절 명령/Mark7 요청/Chunk 모두0,
`real_gateway_trip`도0이다. Lease를 얻기 전이라 SDK 정지 요청은 발생하지 않았고
세션 정지 확정도 이 시험에서 검증되지 않았다. Guarded Driver는 미확정 종료로
Writer를 다시 격리했다. 자동 재시도는 하지 않았다.
공식 수동 복구가 동일한 최신 정지 getter 증거·현장 확인·TCP/Writer 부재를
검사한 뒤 ACT Lease와 Indy7 Writer를 모두 inode 유지로 복구하도록 보완했다.
집중 오프라인 테스트2개를 통과했다. 사용자의 두 번째 시도 후 현장 정지/제어권
확인(이름 미제공)과 새 getter3회 idle5, motion=false, queue0,
qdot 최대0.0038rad/s를 근거로 두 상태를 `released`로 복구했다. 다음 실물
Closed-loop는 새 현장 구동 승인 전 실행하지 않는다. 근거:
`outputs/temporary/inference/act_real_closed_loop_20261104_02/`,
`tmp/real_site/act_retrial_stop_20261104_02.json`.

### 세 번째 ACT 실물 시험 시도 (2026-11-04)
새 현장 확인/최대2초 구동 승인 후 두 잠금 released, idle/Home 오차0.012° 이내,
두 D435/Mark7 상태0.0, Preflight valid/오류0을 확인하고 한 번 실행했다.
RTX PRO6000 CUDA에서 실제 관측으로 ACT Chunk1개를 만들었고 Guarded Driver
`/joint_states`도 수신했다. 첫 Action의20ms 송신 기한이 Adapter의 최종 송신 검사 전에
만료돼 `adapter_action_deadline_expired`로 중단했다. 관절 명령0, Mark7 요청0,
새 관측 재추론0이다. 카메라 무결성/Tracking 차단이나 Driver Executor trip은 없었다.
StopMotion/StopTeleOp SDK 응답 OK 후 fresh idle/qdot 안정 확인으로 세션 정지가
확정됐고 Writer/ACT Lease가 모두 정상 released로 반환됐다. 사후 getter3회도
idle5, motion=false, queue0, qdot 최대0.00238rad/s였다. 관절 전후 차이 최대
0.02588°는 ACT 명령에 따른 이동으로 해석하지 않는다. 제한값·Checkpoint·코드
변경 및 자동 재시도는 없었다. 실물 Closed-loop 검증은 미완료다. 근거:
`outputs/temporary/inference/act_real_closed_loop_20261104_03/`,
`tmp/real_site/act_trial3_post_20261104.json`.

### ACT 송신 중복 조회·정지 안정 구간 수정 (2026-11-05)
세 번째 실물 시도의20ms 송신 기한 초과를 분석했다. 당시 함수별 timing이 없어 정확한
병목별 기여는 미확인이다. poll 직후 feedback 재조회, 호출마다 TCP/TCP6 전체 조회와
feedback당4회 서비스 목록 조회의 중복을 확인했다. Controller는 방금 poll한 표본을
Guard에서 재검사하고, Adapter/Driver의 송신 직전 자격 검사를 유지한다. 서비스 검사는
한 목록을 공유한다. 기존20Hz Driver monitor가 직접 링크/외부 TCP 증거를 갱신하고
송신/Watchdog는 매번 Writer inode/owner·증거 유효기간100ms를 검사한다. scan 시작부터
나이를 세며 실패/만료는 sticky revoke, 늦은 갱신으로 기존 세션을 살리지 않는다.
TCP 탐지는 monitor 주기이며 다른 client 실행을 막는 현장 감독 조건은 유지한다.
20ms/FPS/관절 제한·SDK absolute 계약·학습/Recorder/Snapshot·GA12는 변경하지 않았다.
Controller/Adapter/provider/Driver/명령권 갱신 timing과 SDK 시도/ACK 수를 구분해 기록한다.
Real robot_commands_sent는 SDK ACK 수, 수집 불가는null이며 Controller 제출 수와 다르다.
정지 idle/fresh 검사 실패와 요청 전 표본에서 안정 타이머를 초기화한다. 복귀 후 연속
250ms를 다시 요구하며 Fault/정지/quarantine/자동 재개 금지는 유지한다. 수정 전 원문으로
회귀5개 실패를 재현했다. 로컬 IO30표본 중앙값2.2988→0.00665ms는 TEST-NET/Fake route의
파일 조회 비교이며 실물 송신 성능이 아니다. 근거 tmp/act_dispatch_fix_5sg5slgr/.
현장 다음 단계·정확한 명령·계측 해석은 ai/execute/REAL_INFERENCE.md 첫 항목을 따른다.
최종 CPU/Fake 회귀169 passed/학습·모델 추론 fixture2 deselected(2.19초), 신규 집중21개
포함. final_v2.xml과 baseline.xml에 보존했다. 비구동 Preflight valid/오류0이다.
기존 승인 기록은 최초2초이며 실제 구동/새 관측 재추론/정지는 여전히 재시험 대상이다.
로봇/ROS/SDK접속·명령·모델 추론·새 학습·Gitcommit은 하지 않았다.

### ACT 입력 준비 명령 (2026-11-05)
현행 `act prepare`는 별도 옵션 없이 전용 Mark7 Open 서비스가 준비된 뒤
실제 Open을 정확히 한 번 요청하고 성공 응답과 transient-local 명령 상태 `0.0`을
확인한다. 응답 실패·상태 미수신은 초기화 실패이며 소유 자식을 정리한다.
실행 시 물리 Open 동작을 예고한다. 성공 후 자식을 유지하며 지속 감시는 없다.
이전 `--init-gripper open` 선택 동작은 현재 적용되지 않는다. 이번 변경은 Fake ROS
단위 검증만 수행했고 실물 장비·ROS·Git 커밋은 실행하지 않았다.

이전 버전의 prepare는 RGB Header·ROS 그래프·초기 명령 상태를 감시하고
INPUTS_READY/NOT_READY를 표시했다. 실물에서 일시적인 `fresh_advancing_rgb` 거부가
있었으나 상세 원인은 미확정이었다. 재시도 감시를 거쳐 사용자 요청으로 지속 감시를
제거하고 카메라 wrapper·Mark7 source의 실행/소유 자식 정리만 남겼다.
현행은 위의 명시적 Open 1회 확인을 추가한 버전이다. 기존 카메라 serial 잠금과
Mark7 포트 점유 확인은 유지하며 외부 프로세스를 종료하지 않는다. 당시 Fake 검증
근거는 `tmp/act_prepare_tfre4kj5/tests.xml`과 관련 이력에 보존돼 있다.
현행 사용법은 [Real Inference 절차](../ai/execute/REAL_INFERENCE.md)를 따른다.

## 전진 구간 전용 ACT C 실험 (2026-11-06)

기존 성공10개 Snapshot에서 최초 지속 이동 0.25°보다 6프레임 앞을 시작으로,
첫 Grasp 명령 상태 전환 프레임을 배타적 끝으로 하는 별도 파생 Dataset을 만들었다.
10 Episode/2,267프레임이며 두 RGB와 관절·Action은 원본에서 같은 프레임을 선택했다.
Action 값은 비트 단위로 같고 영상은 재인코딩된다. 파생 Episode outcome은
`derived_forward_approach`로 표시하며 전체 작업 성공으로 세지 않는다. 학습·평가
결과와 상세 절단 근거는 `outputs/temporary/inference/act_forward_c_20261106_v1/`
및 별도 학습 run을 따른다. C 신규 8,000-step 학습은 A/B와 같은 seed·초기 가중치·
batch8·분할·optimizer/LR·실행 prefix 가중치4에서 완료됐다. 동일 원본 관측의 첫 실제
소비 index2 방향은 C 3/10(홀드아웃 0/2), 다음 index6은9/10이나 거리1.5° 제한은
각각1/10·0/10이다. 홀드아웃 이동 구간 MAE도 A/B보다 크다. 전진 전용 학습만으로
안정적인 초기 출발을 확인하지 못했다. 실물·Scheduler·SafetyGuard·원본은 변경하지 않았다.

## 전진 전용 실수집 Collection 준비 (2026-11-06)

새 `act2_010_001` 정의와 10조건 Collection 장부를 별도 생성했다. 현재 SUCCESS 0/10,
다음 조건1·다음 Episode0이다. 기존 파일 스키마는 `act<행동>_<조건수>_<일련번호>`라
제안된 자유형 ID는 사용하지 않았다. Home 프로필/qdes 검증은 유지하고 종료 시
`HOME_COMPLETE` 요구만 기본값에서 제거했다. 녹화 중 시작한 Home은 완료 전 종료를
계속 차단하며 `--require-home-complete` 명시 시 이전 종료 요구를 적용한다.
전진 수집 후 읽기 전용 `ai.record.validate_forward_collection`과 기존 Dataset
Validator로 20FPS/두 RGB·관절·Action·시각·Open·실측 전진·녹화 내 Home 없음 여부를
대조한다. Fake Recorder/Collection 집중97개 통과; 실제 장비 연결·녹화·학습은 없었다.
