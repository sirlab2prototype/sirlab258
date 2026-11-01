# Indy7 ACT Recorder 데스크톱 GUI

Core-108 수집을 기존 Recorder 엔진의 버튼 입력으로 사용한다. 로봇 제어·장비 launch·
Home·Gripper 버튼은 없다. 기존 CLI와 실행 wrapper는 그대로다.

## 실행

현재 연구실 PC의 데스크톱 또는 X 디스플레이가 전달되는 터미널에서:

```bash
cd /home/sirlab2/sirlab258
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
source scripts/activate_act.sh
python -m ai.record.gui --collection-dir datasets/indy7_act_core108
```

이는 실행 방법이며 실물 연결 승인이 아니다. [현재 안전 제한](../../docs/PROJECT_CONTEXT.md)을
확인하고 승인된 범위에서 기존 장비 스택을 별도로 운용한다. GUI는 관측을 구독하지만
장비를 시작하거나 제어하지 않는다. 장비·카메라가 없으면 기존 시작 검사가 실패한다.

같은 Collection 경로로 재실행하면 기존 audit·중단 복구 정책을 적용한다. CLI와 GUI를
같은 Collection에 동시에 연결하지 않는다. 기존 Collection writer lock으로 두 번째 실행을 차단한다.
디스플레이가 없으면 ROS 초기화 전에 종료한다. `python -m ai.record.gui --help`는
Tk 창·ROS·Dataset을 시작하지 않는다. 프로젝트에서 이미 쓰는 Tkinter를 사용하며 추가 패키지를 설치하지 않는다.

## 화면과 조작

기본 화면은 네 영역만 표시한다.

1. 한 줄 진행 현황: 정상 n/108 · 교정 수 · 정상 기준 진행률.
2. 배치 조건: 위치·회전·깊이를 강조하고 순서·ID·Attempt는 보조 정보로 표시.
3. 상태 표시등/텍스트 · `00:00` 녹화 시간 · 일반/교정 모드 · 다섯 조작 버튼.
   REVIEW/SAVING에서는 저장 대상 Success/Correction을 명시한다.
4. 작은 조건 선택·수집 현황·이력 확인·상세 정보·배치/메모 버튼과 한 줄 상태 메시지.

카메라·FPS·프레임·교정 마커 시각·Pending·긴 로그는 기본 화면에서 제외하고
**상세 정보**의 수신/기록·진단/로그 탭에서만 표시한다. 녹화 시작 검증·오류 판단은 기존 엔진이다.
카메라 미수신 메타데이터만으로 경고를 만들지 않는다. 실제 시작 실패 응답이 있으면
Camera A/B 등을 구분한 한국어 요약과 원본 진단을 제공한다.
INVALID/SAVE FAILED/Collection Blocked와 이전 Pending 안내는 기본 화면에서도 별도 안내한다.
저장이 확인된 뒤의 staging 정리 경고는 저장 실패로 오인하지 않도록 구분한다.

기본 창은 **960×660**, 최소 **860×640**, 최대 콘텐츠 폭은 **1000px**다.
1024×768 수준에서도 기본 화면을 스크롤하지 않고 모든 조작 버튼에 접근할 수 있다.
큰 창에서는 여백만 늘린다. 긴 한글/Depth 안내는 줄바꿈하며 오류 영역도 확보한다.
Noto Sans CJK KR을 우선 선택하고 설치된 한글 폰트를 순서대로 사용한다.
폰트는 픽셀 단위(본문14, 보조12, 배치20, 상태22)로 지정해 데스크톱 Tk2배 배율이
모든 글씨를 다시 확대하지 않게 한다. 다른 앱/시스템 DPI·폰트 설정은 변경하지 않는다.
대기=회색, 녹화=초록, 검토/시작 검사=노랑, 저장=파랑, 오류=빨강이며 텍스트를 함께 표시한다.

- 진행률·조건 ID·수집 순서·Attempt·한글 위치/회전/깊이는 실제 Collection에서 읽는다.
- `녹화 시작(s)`은 기존 `prepare → 고정 시작 기준 → 60ms 관측 검사`를 수행한다.
  미측정 배치는 null이며 `ready`나 좌표 입력은 필요 없다.
- `교정 시점 표시(x)`는 녹화를 계속한다. 첫 마커만 유지하고 이후 버튼을 비활성화한다.
- `녹화 중지(r)` 후 REVIEW에서 Success/Correction 저장 대상을 확인한다.
- `저장(o)`은 실제 작업 성공 확인 대화상자를 거친다. 실제 Pick→Place를 완료한 시연만 저장한다.
  저장·영상·sidecar·장부·receipt 검증을 통과하기 전에는 진행률을 증가시키지 않는다.
- `폐기(d)`은 확인 후 기존 discard를 호출한다. 녹화 중에는 먼저 r을 사용한다.
  시작 검사 중에는 r/d로 취소할 수 있다. 실패 저장 후에는 폐기하지 못한다.
- `s → r → o`: Success 저장 후 다음 조건. `s → x → r → o`: Correction 저장 후 같은 조건의 다음 Attempt.
  `s → r → d`: Dataset 없이 같은 조건의 다음 Attempt.

키보드 s/x/r/o/d도 사용할 수 있다. 좌표·ID·로그에 글을 입력/선택하는 동안에는 단축키를 처리하지 않는다.
실제 작업 결과는 GUI가 판단하지 않는다. 작업 실패 시 폐기하고, 교정 시연도 작업 완료 후 저장한다.
Correction은 Core-108 정상108개에 포함하지 않으며 Correction 학습은 미구현이다.

하단 **조건 선택** 또는 **이력 확인** 팝업의 조건 ID 칸에 `037`을 입력하고 실행한다.
수집 현황 팝업에서 전체108개 목록을, 이력 팝업에서 원본 세부 장부도 조회할 수 있다.
하단 **배치·메모** 팝업은 기존 key=value parser를 사용한다. 예:

```text
actual_x=43.2 actual_y=null actual_yaw=-30 notes="측정 메모"
```

**시작 전 배치 반영**은 선택적 좌표를, **검토 메모 반영**은 REVIEW/INVALID의 결과 메모를 기록한다.
좌표 단위는 cm/degree이며 목표 target_*와 실측 actual_*를 구분한다.
수집 현황·전체108개·이력·도움말·배치 기준은 기존 Collection/terminal_ui 출력이다.
상세 장부 조회는 기존 `python -m ai.record.collection history --help` 및
[CLI 운영 문서](COLLECTION.md)의 `history ID detail`을 사용한다.

## 표시 값과 영상

**실시간 영상 미리보기는 제공하지 않는다.** 새로운 영상 구독·복사·변환·저장 큐를 만들지 않는다.
Camera A=317222074298, B=844212071939는 mount 역할을 추정한 이름이 아니다.
기존 콜백 메타데이터만 최대5Hz로 전달하며 최근1초 내 수신이면 '수신 중'으로 표시한다.
이 표시는 ROS graph 연결·센서 정상·기록 시간 정합성 검증을 대신하지 않는다.
저장 중에는 콜백 갱신 대기를 표시하고, 캐시된 수신 시각의 경과 시간을 GUI에서도 계속 계산한다.
기존50/75ms 등의 기록 무결성 기준은 바꾸지 않는다.

FPS는 `EpisodeSession.timing_summary().actual_sample_fps`로, 기록 프레임의 기준시각 간 측정값이다.
센서 촬영률·GUI 갱신률·저장 처리율을 의미하지 않는다. 충분한 기록이 없으면 '미확정'이다.
경과 시간은 기존 monotonic 시작/정지 시각을 사용하며 REVIEW에서는 정지한다.

교정 입력 시각은 **Tk 버튼/단축키 콜백에서 명령을 생성한 시점**이다. 큐 처리 시각과 별도 저장한다.
기존 correction version1 필드·0-based 프레임·타임라인·검증 규칙을 유지하고
`boundary_source=gui_button_received`로 출처를 구분한다. 기존 `stdin_line_received`와
`command_processing_fallback`도 계속 읽는다. 기존 자료를 변환하지 않는다. GUI 마커는
이 출처 값을 지원하는 현재 Recorder/Validator에서 읽는다.
GUI 이벤트 처리 지연 이전의 물리적 실패 시점·마우스 하드웨어 시각을 측정한 값이 아니다.

## 종료·오류·복구

- 정상 창 닫기/종료는 기존 q다. 녹화 중이면 무결성 검사 후 REVIEW로 전환하고 미저장 정상
  시연이 있으면 종료를 보류한다. o/d를 결정한 뒤 다시 종료한다. 자동 저장·자동 폐기는 없다.
- 저장 중 닫으면 이벤트 루프를 유지하며 저장 종료를 기다린다. Dataset commit 중 스레드를 강제 종료하지 않는다.
- INVALID는 저장을 비활성화하고 실제 원인·Pending을 로그에 표시한다. 기존 d/q·보존 정책을 적용한다.
- SAVE FAILED/Collection Blocked는 저장·재녹화·폐기를 차단하고 오류·경로·기존 reconcile 명령을 표시한다.
  자동 재저장·자동 reconcile 버튼은 없다. GUI를 종료한 뒤 기존 복구 절차로 대조한다.
- SIGINT/SIGTERM 또는 GUI 이벤트 루프 비정상 종료는 기존 EOF 보존 경로로 전달한다.
  저장 중이면 현재 저장 호출이 반환한 뒤 종료하며, 강제 kill/전원 차단은 기존 저장 의도·receipt·reconcile로 복구한다.
- 진단 팝업은 최근200메시지 중 최대100,000자를 표시한다. 엔진 메시지는 터미널에도 남긴다.
  ROS/외부 라이브러리 경고·오류는 기존 터미널에서 확인한다. 로그를 Dataset 무결성 증거 대신 사용하지 않는다.

## 연결 구조와 검증

Tk 메인 스레드 → 크기1 명령 큐 → 기존 `indy7_act_record.main(argv, frontend=...)` worker.
기존 관측 노드 하나·Session 하나·Collection 하나를 사용하며 별도 ROS 제어 노드/저장 엔진은 없다.
GUI는 세션 객체를 직접 읽거나 수정하지 않고 메타데이터 사본만 받는다. 명령 수신 시 상태/Attempt
token을 확인해 중복 클릭·오래된 상태의 명령을 거부한다. 저장/ROS/프레임 처리는 Tk 콜백에서 실행하지 않는다.

테스트는 `ai/record/test/test_gui.py`의 Fake ROS·가상 시각·합성 Dataset으로 수행한다.
2026-11-01 전체 CPU 회귀는 **510 passed / 0 failed / CUDA2 deselected** (216.39초)다.
기존482개와 GUI28개를 포함하며 108개 합성 저장·CLI 회귀·공식 소형 ACT CPU3-step·
추론/Mock/평가를 확인했다. 이후 SSH stdout 단절·저장 중 카메라 수신 시각 표시를 보완한 뒤 GUI30개를
재검증해 **30 passed** (1.98초)했다. 고유 테스트는 합계512개이며 단일 실행의512개 결과는 아니다.
결과는 `tmp/act_gui_djcd2anu/{final_results.xml,final_suite.log,gui_final_release.xml,verification.json}`에 보존한다.
위 결과는 최초 GUI 구현 때의 이력이다. 이후 디자인 개편 검증은 아래를 따른다.

### 디자인 개편 검증 (2026-11-01)

엔진·worker/bridge·wrapper는 수정하지 않았다. 기존 GUI30개에 표시/팝업/실제 Tk 레이아웃 검사를 추가했다.
실제 디스플레이(Tk 배율 약2.0, Noto Sans CJK KR)에서 **Fake 데이터만** 사용해
대기·교정 녹화·REVIEW·INVALID·상세 팝업을 렌더링했다. 960×660, 1024×700,
최소860×640와 큰 창에서 버튼·긴 Depth·오류 안내·footer 경계를 확인했다.
검증 화면은 실제 장비의 관측/작업 결과가 아니다. ROS 실수신·실물 부하는 별도 확인 대상이다.
최종 CPU 회귀 결과와 Fake 화면은 `tmp/act_gui_design_eten3gqc/`에 보존한다.


최종 코드의 전체 CPU 회귀: **547 passed / 0 failed / CUDA2 deselected**, 223.50초.
기존 ACT/Recorder482개와 GUI65개(기존30+신규35)를 포함한다. Tk 표시 검사 skip은 없었다.
8건의 torchvision 영상 API deprecation 경고는 기존 경고다. 테스트를 삭제하거나 무결성 검사를 완화하지 않았다.
최소 창의 긴 Depth+INVALID 조합에서 발견한 높이 초과는 여백을 조정해 해결하고 같은 검사를 다시 통과했다.
검증 로그/XML·보호 해시 대조·화면은 `tmp/act_gui_design_eten3gqc/{final_results.xml,final_suite.log,verification.json,ready.png,review_correction.png,minimum_long_depth_error.png}`에 남겼다.
