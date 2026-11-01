# Indy7 ACT Recorder 사용 설명서

Recorder는 **파일로 정의한 데이터셋을 선택해서 녹화**한다. GUI에서 행동·계획·ID를 입력하거나
편집하지 않는다. 로봇은 Indy7, 정책은 ACT, 카메라와 observation/action 형식은 기존 검증된 계약을 사용한다.

## 1. 새 데이터셋 정의 파일 만들기

프로젝트 루트의 `ai/record/plans/`에 UTF-8 JSON 파일을 저장한다.
**파일 하나가 데이터셋 하나**이며 파일명은 반드시 `<dataset_id>.json`이어야 한다.
다른 행동을 추가해도 Python 수정·등록 명령·Git 커밋·ROS 2 재빌드는 필요 없다.

예를 들어 새로운 행동의 조건이2개라면 `act2_002_001.json`을 아래 예제로 작성한 뒤
작업자가 실제 사용할 행동 설명과 조건으로 바꾼다. 예제는 형식 설명용이며 실물 검증된 작업 계획이 아니다.

```json
{
  "version": 1,
  "dataset_id": "act2_002_001",
  "robot": "indy7",
  "policy": "act",
  "action": {
    "id": "act2",
    "description": "작업자가 정의한 새 행동의 이름과 설명"
  },
  "plan": {
    "version": 1,
    "id": "my_action_v1",
    "description": "작업자가 확인한 두 조건 수집 계획",
    "kind": "custom",
    "conditions": [
      {
        "condition_id": 10,
        "collection_order": 1,
        "description": "첫 번째 조건의 배치와 수행 방법",
        "fields": {"object": "작업자 지정 물체", "variant": "A"}
      },
      {
        "condition_id": 20,
        "collection_order": 2,
        "description": "두 번째 조건의 배치와 수행 방법",
        "fields": {"object": "작업자 지정 물체", "variant": "B"}
      }
    ]
  }
}
```

## 2. JSON 필드와 검증 규칙

아래 필드는 모두 필수다. 스키마에 없는 필드는 자유 속성용 `fields` 안에 둔다.

| 필드 | 의미·허용 값 |
|---|---|
| `version` | 데이터셋 정의 형식 버전. 정수 `1` |
| `dataset_id` | 저장 디렉터리 이름. 예: `act2_002_001` |
| `robot` / `policy` | 각각 `indy7` / `act` 고정 |
| `action.id` | `act1`, `act2` 등의 행동 ID. `act` 뒤에1 이상의 정수 |
| `action.description` | 비어 있지 않은 행동 이름·설명. 선택 목록에 표시하고 녹화 `task`로 저장 |
| `plan.version` | 정수 `1` |
| `plan.id` | 계획 이름. 소문자 영문으로 시작, 영문·숫자·`_`·`-`, 최대64자 |
| `plan.description` | 비어 있지 않은 계획 설명 |
| `plan.kind` | 새 계획은 `custom`. 기존 Core-108은 `core108` |
| `plan.conditions` | 실제 조건 목록.1개 이상 |
| `condition_id` | 중복 없는 양의 정수.10·20처럼 연속하지 않아도 됨 |
| `collection_order` | 수집 순서. 중복·누락 없이1..N |
| `description` | 사용자 정의 조건별 안내. 비어 있지 않은 문자열 |
| `fields` | 사용자 정의 속성 JSON 객체. 속성이 없으면 `{}` |

Dataset ID는 `act<행동번호>_<조건수 최소3자리>_<수집본 일련번호 최소3자리>` 형식이다.
`act1_108_001`, `act1_055_001`, `act1_055_002`, `act2_108_001` 등의 이름을 쓸 수 있으나
행동 ID와 조건 수가 실제 JSON 내용과 일치해야 한다. 일련번호는1 이상이다.
`108`·`055`는 계획 조건 수이며 실제 저장 완료 Episode 수가 아니다.

조건 목록이3개인데 이름이 `act2_055_001`이면 오류다. 이름을 보고 조건을 자동 생성하지 않는다.
중복 JSON 키, NaN/Infinity, 중복 조건 ID, 잘못된 순서, 빈 설명, 지원하지 않는 로봇·정책도 거부한다.

## 3. 조건별 설명과 자유 속성

`description`에는 작업자가 보고 준비할 배치·변형·수행 방법을 적는다.
`fields`에는 행동에 필요한 속성을 자유롭게 작성한다. 문자열·유한한 수·boolean·null·배열·객체를 쓸 수 있다.
예를 들어 `{"object":"상자","orientation":"작업자 지정","notes":null}`처럼 기록할 수 있다.
이 값은 조건 안내와 sidecar 출처 기록이며 로봇 목표나 제어 명령을 자동 생성하지 않는다.

기존 Core-108의 위치·회전·Depth 필드를 새로운 행동에 요구하지 않는다.
긴 설명·자유 속성은 녹화 화면에서 요약될 수 있으며 **수집 현황 → 전체 계획 조건**에서 원문을 확인한다.

## 4. Core-108과 다른 조건 수

`ai/record/plans/act1_108_001.json`은 원본
`docs/ACT_108_Episode_Execution_Log_Template.md`의108개 조건을 그대로 담는다.
기존 조건 ID·수집 순서·위치·회전·Depth의 의미와 분포 검증을 유지한다.
`core108`은 기존 `position_region`, `yaw_bin`, `depth_coverage` 필드를 사용하며 한국어 설명을 기존 표시에 따라 제공한다.
원본 조건표를 임의로 변경하거나 Core-108의 순서를 섞지 않는다.

다른 수집본을 만들 때는 별도 파일과 새 Dataset ID를 사용한다.
Core-55 조건표는 제공하지 않는다. 실제55개 조건을 정의한 경우에만 `kind: custom`의
55개 조건 목록으로 파일을 작성한다. 55개 합성 조건은 테스트용일 뿐 실제 수집 계획으로 제공하지 않는다.

## 5. 자동 인식과 선택 화면

프로젝트 루트에서 실행한다.

```bash
./act-record
```

또는 ACT 환경에서 `python -m ai.record.gui`를 사용한다.
기본 검색 위치는 `ai/record/plans/` 바로 아래의 `*.json`이며 하위 폴더를 재귀 검색하지 않는다.
다른 프로젝트 내부 폴더는 사용하지 않는다. 프로젝트 내부의 별도 폴더를 쓰려면:

```bash
python -m ai.record.gui --plans-dir ai/record/plans
```

파일을 저장한 뒤 다음 실행 또는 **목록 새로고침**으로 인식한다. 목록에는 데이터셋 ID,
행동 이름, 계획 조건 수, Success 수, Correction 수, 상태(수집 전/수집 중/완료)를 표시한다.
잘못된 정의는 `오류` 행으로 표시하고 열기를 막는다. 행을 선택하면 파일명과 오류 원문을 볼 수 있다.
긴 이름은 가로 스크롤, 상세 설명과 오류는 줄바꿈·세로 스크롤로 확인한다.

기존 `outputs/preserved/datasets/`의 Collection도 목록에 표시한다.
`outputs/act_gui_integration_test`와 `datasets/indy7_act_core108`은 존재하면 원래 위치로 표시한다.
목록 읽기만으로 Collection 생성·복구·장부 수정은 하지 않으며 ROS를 시작하지 않는다.
GUI의 주요 버튼은 **목록 새로고침**, **선택한 데이터셋 열기** 두 가지다.

선택창과 녹화창의 초기 크기는 QHD 기준 약1300×820이다. 화면·창 관리자 영역을 확인해
작은 모니터에서는 화면 안에 들어오도록 줄인다. 수동 확대·축소 시 기존 공통 UIScale로
글씨·버튼·간격과 표의 헤더·행 높이·열 너비도 함께 조절한다. 표는 가용 공간을 채우고
긴 값과 안내는 기존 스크롤로 보존한다. 시스템 DPI는 변경하지 않는다.

## 6. 최초 생성과 이어서 수집

1. 정의 JSON을 저장하고 목록을 갱신한다.
2. 데이터셋을 선택해 행동·조건 수·오류 여부를 확인한다.
3. **선택한 데이터셋 열기**를 누른다.
4. 최초에는 `outputs/preserved/datasets/<dataset_id>`를 배타적으로 생성하고 JSON 스냅샷과 해시를 장부에 저장한다.
5. 이미 있으면 저장된 정의와 현재 JSON을 대조한다. 일치할 때 기존 Recorder로 들어간다.
6. 기존 녹화 GUI에서 `s → r → o`로 Success를, `s → x → r → o`로 Correction을 저장한다.

정상 저장만 조건 완료에 포함한다. Correction은 별도 보존하며 같은 조건의 다음 Attempt로 이어진다.
실물 작업 완료 여부는 작업자가 판단한다. 기존 무결성 검사를 통과하지 못한 자료는 저장할 수 없다.
Core-108의 Home 복귀 전 녹화 종료 등 기존 운영 규칙도 유지한다.

녹화 화면 진입 시 기존 worker가 ROS 관측 구독을 시작한다. 관리 화면만 여는 것과 구분한다.
기존 Collection writer lock, Pending, receipt 검증과 reconcile 절차를 그대로 사용한다.
실수집·영상·Parquet·장부·receipt는 Collection 전체 단위로 보존한다.

## 7. 계획 스냅샷과 변경 제한

Collection 생성 이후에는 Dataset ID·행동·계획·조건을 바꾸지 않는다.
JSON이 저장 스냅샷과 달라지면 GUI는 오류를 표시하며 기존 자료를 수정하거나 해시를 다시 만들지 않는다.
목록을 선택한 뒤 JSON을 수정한 경우도 열기 전에 재검사한다.

- 실수로 변경했다면 원래 JSON을 복원한다.
- 새 조건·새 행동·설명을 사용하려면 **새 Dataset ID와 새 파일**을 만든다.
- JSON이 없어도 기존 Collection의 저장 스냅샷은 남아 목록에서 재개할 수 있다.
  같은 이름의 잘못된 JSON이 있으면 우회하지 않고 오류로 막는다.
- Collection 디렉터리 이동·이름 변경·메타데이터 수동 치환은 지원하지 않는다.
- 기존 스냅샷 없는 Core-108 Collection은 종전 검증 방식으로 연다. 변환하지 않는다.

기존 직접 실행도 유지한다. 이 경로는 저장된 Collection을 사용하며 JSON 편집을 적용하지 않는다.

```bash
./act-record --collection-dir outputs/act_gui_integration_test
python -m ai.record.gui --collection-dir outputs/act_gui_integration_test
```

사용자 정의 계획은 선택 화면의 최초 열기로 만든 뒤 직접 경로로 재개한다.
빈 경로에 기존 CLI를 직접 지정하면 종전 Core-108 호환 모드로 생성되므로 새 사용자 정의 계획 생성에 쓰지 않는다.

## 8. 오류 해결

| 오류 | 조치 |
|---|---|
| JSON 구문·중복 키 오류 | 선택 행의 파일명·파서 오류를 확인하고 JSON을 수정한 뒤 새로고침 |
| 파일명과 ID 불일치 | 파일 이름을 `<dataset_id>.json`과 일치시킴 |
| Dataset ID/조건 수 불일치 | 목록의 실제 조건 개수와 이름의 수를 확인. 없는 조건은 자동 보충하지 않음 |
| 중복 Dataset ID | 같은 ID를 선언한 모든 중복 정의를 정리. 기존 Collection 파일은 건드리지 않음 |
| 스냅샷 불일치 | 원래 정의 JSON을 복원하거나 새 ID의 별도 수집본 사용 |
| 경로에 기존 자료/빈 디렉터리 존재 | 자동 편입·덮어쓰기하지 않음. 경로와 장부를 조사하거나 새 ID 사용. 임의 삭제 금지 |
| symlink 경로 | 실제 프로젝트 내부 경로 사용. 링크로 이동/보호 검사를 우회하지 않음 |
| 다른 writer가 사용 중 | 기존 GUI/CLI를 정상 종료한 뒤 재개. 프로세스 강제 종료 금지 |
| SAVE FAILED·Pending·receipt 오류 | 추가 저장·임의 수정 금지. 전체 자료 보존 후 기존 [복구 절차](ai/record/COLLECTION.md) 확인 |

과거 GUI 등록부 `outputs/preserved/catalog/`가 있더라도 자동 등록·변환·삭제하지 않는다.
이전 방식으로 만든 Collection은 저장된 스냅샷으로 목록에서 계속 사용할 수 있다.

## 9. 실제 수집 전 확인

- [현재 프로젝트 Context](docs/PROJECT_CONTEXT.md)의 실물 제한과 별도 승인 범위를 먼저 확인한다.
  문서 변경·오프라인 테스트 성공은 장비 복구나 ACT 실물 실행 승인이 아니다.
- 필요한 수동 장비 stack은 승인된 절차로 별도 운용한다. Recorder는 장비를 자동 시작하거나 제어하지 않는다.
- 기존 두 카메라의 실제 mount·영상·관절/Action·Gripper 명령 상태와 시작 관측을 확인한다.
  READY 표시만으로 센서·시간 정합성이 검증된 것은 아니며 기존 시작 검사와20FPS 시간 계약을 유지한다.
- 올바른 데이터셋·행동·조건·Attempt인지 확인하고 동시에 다른 Recorder를 연결하지 않는다.
- 영상 저장 공간·쓰기 권한과 Pending/장부/receipt 보존 상태를 확인한다.
- 임의 좌표·자동 배치·목표를 생성하지 않는다. 검증된 범위에서 작업하고 실제 작업 완료 후 저장한다.

로봇 제어, 다른 로봇·정책, 다중 데이터셋 학습, Correction 학습, Collection 마이그레이션은 이 기능에 포함하지 않는다.
