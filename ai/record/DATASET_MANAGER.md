# Indy7 ACT 데이터셋 선택 화면

현재 사용법과 JSON 전체 스키마는 프로젝트 최상위 [act-record.md](../.act record.md)가 기준이다.

- `ai/record/plans/<dataset_id>.json` 한 파일로 행동·계획·조건을 정의한다.
- 실행 또는 목록 새로고침으로 자동 인식한다. 별도 등록·코드 수정·커밋·ROS 재빌드는 필요 없다.
- GUI는 목록 확인과 선택한 데이터셋 열기만 제공한다. 행동 등록·계획 가져오기·ID 입력·조합 선택 폼은 없다.
- 최초 열기 시 JSON 스냅샷으로 Collection을 생성한다. 재개 시 원본 JSON과 저장 스냅샷의 충돌을 차단한다.
- 기존 Collection의 원래 경로·장부·sidecar·receipt는 변환하지 않는다. 원본 Core-108과 기존 명시 경로 실행을 유지한다.
- 녹화·Success/Correction·Pending/Recovery는 기존 Recorder 엔진을 사용한다.
- 관리 화면에서는 ROS를 시작하지 않는다. 녹화 화면 진입 시 기존 관측 worker가 시작되므로 실물 승인은 별도다.

구 GUI의 `outputs/preserved/catalog/`는 더 이상 자동 등록부로 사용하지 않으며 기존 파일이 있어도 변경·삭제하지 않는다.
오프라인 테스트 범위와 최신 결과는 [현재 상태](../../docs/PROJECT_CONTEXT.md)를 따른다.

두 GUI는 화면 가용 영역에 맞춘1300×820 초기 크기와 공통 `gui_scaling.py` 배율을 사용한다.
창을 조절하면 글꼴·버튼·표·안내가 함께 확대/축소된다. 상세 기준은 [GUI 안내](GUI.md)를 따른다.
