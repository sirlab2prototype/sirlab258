# sirlab258 — 현재 상태와 다음 작업

2026-11-05 최종 ACT 실물 Closed-loop 5초 시험:
`outputs/temporary/inference/act_real_test_5sec_20261105T102020`의 result/summary가
`complete`/`duration_complete`, Chunk10개, SDK ACK96회, 측정 관절 변화
0.0249755rad, 실제 Action19.9975FPS, 기한 누락0, safety fault 없음,
stop_confirmed=true, lease_quarantined=false를 기록했다. 해당 세션의 명령·관측·정지
성공이며 물리 파지·작업 성공은 미검증이다. 추가 실물 실행·학습은 하지 않았다.

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
- 이번 정리는 구형 코드·생성물·문서 정리다. 제어·Topic/QoS·데이터 형식·기록 시간 계약은 변경하지 않는다.

## 목적·환경·구조

선반 칸1의 가변 위치/회전 바구니를 동일 Home에서 파지해 고정 table drop zone으로 옮기는
시연을 수집하고 ACT로 학습·평가하는 팀 캡스톤이다. feasible workspace는 실물 검증 없이 만들지 않는다.

- Ubuntu24.04 / Python3.12 / ROS2 Jazzy, Indy7·Mand.ro Mark7·Xbox·D435 두 대.
- ROS/제어 `.venv`와 ACT `.venv-act` 분리. 로봇 NumPy1.26.4와 ACT NumPy2.2.6을 합치지 않는다.
- vendored LeRobot0.5.1, 과거 환경 기록은 Torch2.10.0+cu130/torchvision0.25.0+cu130이다.
  CUDA 검증 범위와 현재 사용 가능 여부는 별개다. 현재 의존성 원본은 requirements와 vendored pyproject다.
- 카메라 serial: `317222074298`, `844212071939`; 설정은 RGB640×480·30FPS이다.
- Mark7 설정 기본값은 `/dev/ttyACM0`,115200이다. 실제 포트·펌웨어·손가락 위치 피드백 지원은 미확인이다.
- `192.168.1.114`는 launch의 접속 대상 설정이다. 2026-11-02 읽기 관측에서 위 Home 수집 장비 신원을 확인했다.
- OMY는 별도 ROBOTIS 경로이며 현재 Indy7 수집과 통합 검증된 장비로 취급하지 않는다.

| 코드 | 현재 역할 |
| --- | --- |
| `capstone_teleop`·`capstone_mark7`, `scripts/bin/*` | 기존 QP·장비 launch·명령 wrapper |
| `ai/record/indy7_act_record.py`, `collection.py` | 수집·조건/Attempt·정상/교정 분리·장부/receipt/reconcile |
| `ai/observation_timing.py`, `dataset_contract.py` | 인과적 관측 선택·20FPS 시간/7D/두 영상 계약 |
| `ai/train/{validate_dataset,run_train,checkpoint}.py` | 검증→Episode 분할→Train 전용 통계→공식 학습→재로딩 검증 |
| `ai/serve/{infer,dry_run,zmq_act_server}.py` | localhost v2 추론 서버·관측·RPC·스케줄러 |
| `ai/execute/{controller,task_executor}.py` | Mock 명령/정지·명령권·Task/Home 상태 관리 |
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
  QP 명령/기존 movej/재ARM/Driver는 유지한다. Home 완료 후 r, 실제 성공 확인 후 o다.
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
- ACT 실물 replay는 미검증/잠금 유지. 수집한 controller 목표를 Teleop으로 실행할 때의
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
- 미측정 좌표는 null이다. 활성 Home 프로필 사용 시 Home 완료 후 r로 끝낸다.
  `--no-home-capture`는 Home 전에 r이며 미검증 Home 녹화는 차단한다.
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
| 실제 ROS 입력 ACT 추론 | CLI 구현·fake ROS 검증 존재; 별도 실물 관측 추론 실행 완료 근거는 미확인 | Recorder Pilot을 추론 검증으로 대체하지 않음 |
| ACT 실제 모터 실행·Real Home | 미검증·CLI/API 잠금 유지 | Mock Task/Home 성공으로 해제하지 않음 |

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
- Real ACT에는 독립 readiness/state provider·외부 writer 배제·실물 제한값·프로젝트 Home 검증이 추가로 필요하다.
  Mock 제한값을 실물 값으로 복사하지 않는다. QP는 외부 장애물 충돌 회피를 제공하지 않는다.
- dual DCP 구조는 기존 설계다. 장애 원인으로 단정하거나 single-owner 재설계를 적용한 것처럼 쓰지 않는다.

## 해결 이력·보류·다음 작업

- 해결된 소프트웨어 문제: 구형 SAVED 문자열 의존 테스트, Train-only 통계 로딩/역정규화,
  두 카메라·7D 학습/추론 연결. 구형 잘못된 정규화 Checkpoint는 자동 수정하지 않는다.
- 보류: `scripts/rotate_ros_image.py`는 과거 wrist 독립 도구이며 수동 외부 호출 여부가 미확인이라 유지했다.
- 구형 Pool·단일 카메라 YAML은 Git 원문과 설계 메모를 남기고 제거했다. 현행 v2 경로로 사용하지 않는다.
- 다음 순서: 사용자 확인으로 J6 해결 기록 유지 → 승인된 Home qdes/최신 Recorder 실물 통합 확인 →
  실제 feasible workspace·카메라 역할 검증 → Pilot/정상108개 → 실제 성공 데이터 학습 →
  승인된 ROS 관측 추론·최종 모델 부하 검증 → 별도 실물 실행 안전 검증·미관측 pose 평가.
- 문서·코드 정리는 이 실물 작업의 승인이 아니다. 제조사 문의는 초안·과거 조사와 실제 송신/답변을 구분한다.
- 상세 공식 복구 자료·미확인 원인·제조사 요청은 J6 인계, Mark7 자료는 [피드백 조사](MARK7_STATE_FEEDBACK_INVESTIGATION.md)를 따른다.
