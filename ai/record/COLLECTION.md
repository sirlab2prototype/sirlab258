# Indy7 ACT Recorder / Core-108

기존 Recorder의 구독·프레임 저장·20Hz 고정 그리드·60ms 시작 검증·시간 무결성 검사를 유지한다.
Recorder는 로봇·카메라·Xbox·그리퍼를 시작하거나 제어하지 않는다.

## 실행 및 조작

```bash
cd /home/sirlab2/sirlab258
./act-record --collection-dir datasets/indy7_act_core108
```

같은 명령으로 재개한다. 실제 장비 운용·workspace 검증은 별도 승인 후 진행한다.
SSH에서 한 줄 입력 후 Enter로 전달하며 화면 지우기나 주기적 화면 갱신은 없다.
환경 경로·전체 시간 설정·장문의 배치 설명은 일반 시작 화면에 출력하지 않는다.
Recorder 프로세스 안에서 공개 로깅 API로 Dataset 진행 막대와 정보성 인코더 로그를 제한한다.
stderr를 통째로 숨기지 않으며 ROS/코덱 경고·오류는 유지한다. 외부 라이브러리 소스는 수정하지 않는다.

| 명령 | 동작 |
| --- | --- |
| s | Condition/Attempt/배치 확정 → 관측 검증 → 녹화 시작 |
| x | RECORDING 중 일반→교정 전환. 녹화와 타임라인 계속 유지 |
| r | 시간 무결성 검사 후 REVIEW. 자동 저장 없음 |
| o | 일반은 Success, 교정은 Correction Dataset 저장 확정 |
| d | 미저장 시연 또는 준비된 Attempt 폐기 |
| q | 종료. 정상 미저장 REVIEW는 먼저 o/d로 해결 |
| progress | 정상/교정/남은 조건/폐기 집계와 현재 조건 |
| progress all | 원본 순서의 전체 108개 목록 |
| history ID | 시도별 상태·Dataset·실제 Episode Index 요약 |
| history ID detail | 원본 세부 장부 JSON |
| condition ID | 미완료 조건 선택 |
| placement key=value ... | 선택적 목표/실측 좌표와 배치 메모 |
| result key=value ... | REVIEW 결과·단계별 성공 여부·품질/메모 |
| help | 전체 명령 안내 |
| guide | 배치 기준 안내 |

사용자 ready 명령은 제거했다. x/failure로 새 Failure Episode를 저장할 수 없다.
별도 교정 전환 키는 없다. 중복 x는 첫 마커를 유지한다.

## 최초 화면

새 장부 예시다. 값은 실제 장부에서 계산하며 기존 진행이 있으면 복원된 값을 표시한다.

```text
========== INDY7 ACT RECORDER ==========

[수집 현황]
정상 시연 : 0 / 108
교정 시연 : 0
남은 조건 : 108

========== [현재 수집 조건] ==========

수집 순서 : 001 / 108
조건 ID   : 108
시도      : 1회차

바구니 위치 : 오른쪽 (R)
바구니 회전 : 반시계방향 많이 (A6)
바구니 깊이 : 자유 배치 (Random)

[현재 상태] 녹화 대기

바구니 배치 후 s
=====================================
```

방향의 긴 설명은 guide에만 표시한다. 정상 녹화 중 주기적 로그는 없다.
r 화면의 시간은 실제 stop-start monotonic 차이이며 frames/20으로 꾸미지 않는다.
시작 검사 통과는 기존 관측·시간 검사와 pending 초기화에 성공한 뒤에만 표시한다.
오류 코드·실제 지연/제한·진단 정보·실제 pending 경로는 오류 화면에 남긴다.

## 조건 및 배치

원본은 docs/ACT_108_Episode_Execution_Log_Template.md다. Episode 열은 Condition ID이며
LeRobot Episode Index와 다르다. ID/Order/Shuffle은 유지한다. 108개는 L/C/R × A1..A6 ×
각 여섯 Depth이며 각 조합에 Depth가 한 번씩 있다.

| 내부 값 | 화면 표시 |
| --- | --- |
| L / C / R | 왼쪽 / 가운데 / 오른쪽 |
| A1 / A2 / A3 | 시계방향 많이 / 보통 / 조금 |
| A4 / A5 / A6 | 반시계방향 조금 / 보통 / 많이 |
| Front / Front-Middle / Middle | 입구 쪽 / 입구 쪽과 중간 사이 / 중간 |
| Middle-Back / Back / Random | 중간과 벽 쪽 사이 / 벽 쪽 / 자유 배치 |

회전은 선반을 위에서 내려다보는 기준이고 0°는 선반 정면과 평행한 바구니다.
시계방향(-)은 왼쪽 끝이 벽 쪽, 오른쪽 끝이 입구 쪽으로 이동한다. 반시계방향(+)은 반대다.
기존 A1~A6 수치 범위는 변경하지 않았다. Depth는 고정 cell이 아닌 분포 기준이다.
좌표/각도를 자동 추천하거나 샘플링하지 않는다.

좌표는 선택 사항이다. placement 없이 s를 입력하면 모두 null로 보존한다.
입력 필드는 target_x/target_y/target_yaw와 actual_x/actual_y/actual_yaw로 구분한다.
x/y는 cm, yaw는 degree이며 미측정은 null이다. coordinate_frame, measurement_method,
notes도 입력할 수 있다. 카메라에서 좌표를 추정하지 않는다.
s에서 배치를 동결한다. 시작 검증 실패 시 PREPARED Attempt를 유지하고 s 재시도는 같은
번호를 사용한다. 배치를 바꾸려면 d로 준비 시도를 폐기한 뒤 새 Attempt를 시작한다.

기존 운영 규칙대로 **Home 복귀 전에 r**로 종료한다. Home을 기록 Action에 추가하지 않는다.
Mark7 초기 관측이 필요하다는 조건은 유지하며 임의 gripper 값으로 우회하지 않는다.

## 저장·폐기·진행률

- 일반: s → r → o → Success. 검증·장부 커밋 완료 후 Core 진행률 증가, 다음 조건 안내.
- 교정: s → x → r → o → Correction. 전체 Episode 보존, Core에는 미포함.
  같은 조건의 다음 Attempt를 안내하며 Shuffle/Collection Order는 유지한다.
- 폐기: d. Dataset에 추가하지 않는다. 실제 삭제 확인 후에만 폐기 완료를 표시한다.
  Attempt 이력은 남긴다. INVALID 폐기는 INVALID 상태와 별도 폐기 완료 필드를 유지한다.
- INVALID: 저장 불가. q로 pending을 보존해 종료하거나 d로 폐기한다.
- 저장 오류: 추가 저장·폐기를 차단하고 pending과 복구 경로를 보존한다.

o는 작업자의 저장 확정이며 물리적 작업 성공을 자동 판정하지 않는다. result로
reach_success, align_success, grasp_success, lift_success, shelf_retract_success,
transport_success, table_approach_success, place_success를 true/false/null로 입력할 수 있다.
failure_reason, demo_quality, notes는 선택적 서술값이다.

## 교정 마커의 시간 의미

**물리적 파지 실패 시점이 아니다.** stdin 스레드가 Enter로 끝난 x 한 줄을 읽은 직후의
monotonic/시스템 시각과 메인 루프의 명령 처리 시각을 따로 저장한다. 키를 누른 순간이나
네트워크 지연 이전의 원격 시각은 측정하지 않는다.

교정 시작은 기록 기준 시각(reference_monotonic_ns)이 입력 수신 시각 이상인 첫 프레임이다.
60ms 수신 대기 때문에 x 처리 당시의 기록 프레임 수를 그대로 사용하지 않는다.
첫 대응 프레임이 없으면 UI에는 '기록 프레임 대응 대기'를 표시하고 다음 기록에서 연결한다.
큐 처리가 늦어 이미 대응 프레임이 있다면 그 프레임을 연결한다. 기존 NPZ와 timing 행은
수정하지 않는다. 첫 마커는 이후 x로 덮어쓰지 않는다.

pending/저장 timing episode.json의 최상위 demonstration 필드에 저장하고,
같은 정보가 장부의 demonstration_type/correction 및 새 receipt에 연결된다.

```text
demonstration.type = normal | correction
demonstration.correction:
  version = 1
  frame_index_origin = 0
  input_received_monotonic_ns
  input_received_system_time_ns
  command_processed_monotonic_ns
  boundary_monotonic_ns
  boundary_source = stdin_line_received | command_processing_fallback
  start_frame_index
  start_dataset_timestamp_s
  start_reference_monotonic_ns
  start_reference_system_time_ns
```

start_frame_index는 **0부터 시작**하며 timestamp는 기존 LeRobot의 frame_index/20 초다.
참조 시각과 입력 수신 시각은 별도 필드다. timestamp를 물리적 실패 경과 시간으로 해석하지 않는다.
직접 API 호출처럼 stdin 시각이 없으면 처리 시각을 쓰고 fallback임을 명시한다.
실제 터미널 명령에는 수신 시각이 있다. 대응 프레임이 하나도 없으면 Correction 저장을 거부한다.
저장 전에 timing 행·첫 대응 프레임·timestamp를 대조해 손상이나 불일치를 차단한다.

전체 Episode를 보존하므로 교정 전 실패 유도 구간도 남는다. 이번에는 학습기·ACT 모델·혼합
학습·Action Chunk·fine-tuning을 변경하지 않았다. 기존 학습기가 마커를 자동 이해하거나
실패 구간을 자동 제외한다고 가정하면 안 된다.

## 무결성·이전 데이터 호환

```text
datasets/indy7_act_core108/
  collection.json
  receipts/
  success/
  correction/
  failure/       과거 데이터가 있으면 보존, 새 Episode 추가 없음
  pending/
```

장부 version 1을 그대로 읽는 추가 필드 방식이다. 과거 SUCCESS/FAILURE/INVALID/Discard,
Attempt·Dataset Index·receipt를 변환하지 않는다. 과거 receipt에 새 필드를 강제로 붙이지 않는다.
새 CORRECTION 상태가 있는 장부를 이전 프로그램으로 열지 않는다. 과거 실패는 Core에 포함하지
않으며 progress/history에서 확인한다. 기존 Failure 미해결 저장도 증거가 완전하면 연결만 복구한다.

Success/Correction은 같은 저장 경로를 사용한다:

1. 예상 Episode Index와 대상 baseline을 SAVING 의도로 원자적 JSON+fsync.
2. 기존 LeRobot 공개 add_frame/save_episode/finalize와 timing sidecar 발행.
3. Dataset/디렉터리 fsync, FPS/robot/features·개수·인덱스·프레임 범위·누락/orphan 대조.
4. 정확한 Attempt/배치, Parquet frame/timestamp, timing 행, 두 영상 디코딩 프레임 수 대조.
5. Correction은 입력 마커·첫 대응 프레임·timestamp도 대조.
6. 파일 SHA-256 receipt fsync 후 장부 커밋. 이때만 저장 완료·진행률 표시.
7. pending 정리. 정리만 실패하면 저장 완료와 leftover 경고를 구분.

중간 저장 실패를 같은 Episode 재저장으로 해결하지 않는다. 종료 후:

```bash
.venv-act/bin/python -m ai.record.collection reconcile --collection-dir datasets/indy7_act_core108
```

- 대상이 baseline과 완전히 동일: 추가 Episode 없는 INTERRUPTED로 기록하고 pending 보존.
- 완전한 Dataset+동일 Attempt sidecar·마커 증거: receipt/장부 연결만 완성.
- 부분 저장, sidecar 누락, count/index/해시/마커/영상 불일치: 차단 유지. 자동 성공·rollback·삭제 없음.

reconcile은 Dataset에 Episode를 추가하거나 기존 파일/Index를 수정하지 않는다. 자동 대조로
해결되지 않으면 전체 디렉터리와 pending을 보존하고 별도 복사본에서 조사한다. 복구된 staging도
자동 재저장하거나 삭제하지 않는다. 중단된 PREPARED/RECORDING/REVIEW는 INTERRUPTED로
보존하고 다음 Attempt를 사용한다. 교정 입력·매핑 metadata와 raw도 보존하고 자동 이어 녹화하지 않는다.
정상 fsync를 지원하는 로컬 파일시스템을 전제로 하며 실제 PC 전원 차단 실험은 하지 않았다.

Pilot outputs/act_recorder_pilot_20261031은 편입·수정·삭제하지 않는다. 같은 장부의 동시 managed
writer는 flock으로 차단한다. 다른 프로그램으로 Dataset에 동시에 append하지 않는다.
자료가 있는데 장부가 없으면 자동 편입하지 않는다.

## 독립 Recorder

--collection-dir를 생략한 기존 모드를 유지한다. 정상은 기존 --root, 교정은 별도의
--correction-root (기본 datasets/indy7_act_corrections), staging은 기존 --pending-root다.
원래 repo-id와 --resume 정책을 유지한다. --failure-root/--failure-repo-id는 과거 경로 호환용이며
새 쓰기 경로가 아니다. 경로 중첩은 거부한다. 독립 모드도 x는 교정 전환이고 o로 유형별 저장한다.
Condition ID나 Core 진행률을 임의로 표시하지 않는다. Core receipt/장부 reconcile은 Collection
모드의 기능이다. 독립 모드의 부분 저장은 기존처럼 pending을 보존하고 수동 대조해야 한다.

## 오프라인 검증

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD" .venv-act/bin/python -m pytest -q -p no:cacheprovider \
  ai/record/test/test_recorder_ui_correction.py ai/record/test/test_collection.py \
  ai/record/test/test_indy7_act_record.py ai/record/test/test_pending_start.py
```

모든 데이터는 pytest 임시 경로의 합성 Dataset이다. ROS는 stub이며 실제 노드/장비를 실행하지 않는다.
실물 SSH 입력 지연·디스크 부하·관측 수신·운영 동작은 별도 승인 후 검증해야 한다.

최종 검증: **120 passed, 0 failed** (93.74초). 108개 전체 합성 정상 저장, Success/Correction 각각의
강제 프로세스 종료 후 중복 없는 대조 복구, 교정 마커/유효 구간, 과거 v1 Failure/receipt 호환,
정상 화면·경고 로그 유지·기존 시간 무결성 회귀를 포함한다. 실제 PC 전원 차단·장비 동작 검증은 아니다.
문법 검사, 실행 래퍼의 bash -n, git diff --check를 통과했다. 입력/영상/Dataset 계약 보호 함수의
AST 대조도 통과했다. 실제 수집 데이터·Pilot·외부 라이브러리·학습 코드는 수정하지 않았다.
