# sirlab258 작업 규칙

Indy7·Mark7·Xbox QP·두 RGB 카메라로 시연을 수집하고 LeRobot ACT로 연결하는 팀 프로젝트다.
자체 제어는 `ros2_ws/src/capstone_*`, ACT는 `ai/{record,train,serve,execute,evaluate}`에 있다.

## 시작과 기준
- `docs/PROJECT_CONTEXT.md` 전체와 관련 기능 문서·필요한 handoff를 먼저 읽는다.
- Git status·관련 diff를 확인해 기존 작업과 요청 범위를 구분한다. README는 문서 진입점이다.
- CLI·동작은 소스로 확인한다. 날짜가 있는 과거 기록을 현재 장비 상태로 옮기지 않는다.
- 사실·추론·미확인을 구분하고, 판단 변경에는 근거·검증 범위를 남긴다.

## 구현·Git 보호
- 요청과 무관하게 제어·자동 ARM·키맵·Topic/QoS·20FPS·시간 검사·원본 Shuffle을 바꾸지 않는다.
- 기존 미커밋·untracked 작업을 덮어쓰거나 임의 삭제·reset/stash/clean하지 않는다.
- 커밋·push는 현재 요청의 승인 범위에서만 수행하며 대상 파일과 staged diff를 확인한다.
- 공식 Indy/ROBOTIS submodule·vendored LeRobot·다른 프로젝트·전역 환경은 명시적 요청 없이 변경하지 않는다.

## 실물·데이터
- 실제 장비 연결·ROS 관측·launch·SDK/시리얼 호출·기존 장비 프로세스 종료는 현재 사용자 승인 범위만 수행한다.
- `caps`는 실제 장비·QP 자동 ARM 경로이며 Driver 종료도 제어 명령을 보낼 수 있다.
- Context의 기존 실물 제한은 문서 변경·Pilot·Mock 성공으로 해제하지 않는다.
- Dataset·Pilot·Pending·receipt·학습에 필요한 Checkpoint·검증 근거를 보호한다. Git ignored는 삭제 허가가 아니다.
- 테스트는 장비 접근·GPU/시간·기존 데이터 변경 가능성을 먼저 확인하고 새 격리 경로를 쓴다.

## 문서와 검증
- 구현 존재·정적 확인·합성/Mock 검증·실물 검증을 구분한다.
- 서비스 성공·명령 상태·Mock 완료를 물리 위치·파지·작업 성공으로 표시하지 않는다.
- Correction 수집과 Correction 학습 지원을 구분한다.
- 동작·CLI·데이터 계약·검증 범위가 바뀌면 기능 문서와 Context의 관련 항목만 갱신한다.
- 읽기 전용 요청이면 문서를 포함해 파일을 갱신하지 않는다. 상세 이력은 기존 조사 문서에 둔다.
