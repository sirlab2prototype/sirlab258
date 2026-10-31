# Mark7 실제 상태 피드백 조사

조사일: 2026-10-31. 장비 연결·ROS 노드 실행 없이 현재 작업 트리와 공개 제조사 자료를 조사했다.

## 결론

**현재 확보한 자료로는 외부 실제 상태 조회를 구현할 근거가 부족하다.**
Hall 센서와 내부 위치 제어의 존재는 제조사 자료로 확인되지만, USB 시리얼을 통해
현재 위치를 반환하는 명령·응답 명세, 펌웨어 소스, 설치된 펌웨어 버전은 확보하지 못했다.
이는 제품이 조회를 지원하지 않는다는 확정 결론이 아니라, 지원 여부가 미확인이라는 뜻이다.
임의의 조회 패킷이나 초기 0/1 발행은 추가하지 않았다. 따라서 별도 조작 없는 Recorder 시작이라는
최종 목표는 아직 달성되지 않았다.

이번 변경은 이 문서 추가뿐이다. 기존 미커밋 파일, Mark7/QP/Recorder/시간 정합성 코드,
Xbox 자동 ARM과 제어 방식은 수정하지 않았다.

## 조사한 자료와 근거 범위

| 자료 | 확인한 사실 | 확인할 수 없는 사항 |
| --- | --- | --- |
| [제조사 홈페이지](https://www.mand.ro/) | 제품군의 위치·속도·힘 제어와 유무선 통신을 소개 | USB 읽기 명령, 응답 프레임, 지원 펌웨어 버전 |
| [제조사가 BuyKOREA에 게시한 Mark7 제품 설명](https://buykorea.org/ec/prd/selectGoodsDetail.do?goodsSn=3634609), Engineering Perspective 2 | 손가락 내부 BLDC 모터 옆에 복수 Hall 센서와 제어 보드가 있다고 설명 | 외부에서 Hall 카운터를 읽는 API/프로토콜 |
| [제조사 OTWorld 제품 페이지](https://www.ot-world.com/exhibitors-products/product/otworld/7005/2860) | Mark7 계열에 여러 DoF 구성이 존재 | 프로젝트 실물의 정확한 모델·보드·펌웨어 |
| [해당 페이지의 제조사 16쪽 PDF](https://www.ot-world.com/files/otworld/products/fc7e9de4-e29f-4118-82b6-ce9556f8bb3e.pdf), 인쇄/PDF 12쪽 | Hall 위치 센서와 내부 폐루프 위치·속도·힘 제어를 명시 | 외부 피드백 경로·카운터 유효성·전원 재인가 시 원점 |
| 같은 PDF 14쪽 | 모델별 자유도와 위치·속도·힘 제어 사양 | USB 조회 명령·응답·펌웨어 호환 표 |
| 프로젝트 Mark7 코드와 Git 이력 `368a3a6`, `a1b5e27` | 기존 송신 패킷과 Hall 카운터 드리프트 관련 주석 | 제조사 원본 통신 명세와 장치 펌웨어 구현 |

제조사 홈페이지 직접 열기는 시간 초과가 발생해 검색에 노출된 본문을 참고했다.
BuyKOREA와 OTWorld 본문은 열어 확인했다. PDF는 대부분 이미지여서 텍스트 추출만으로
판단하지 않고 12·14쪽을 렌더링하여 확인했다. 이 PDF는 제품 소개 자료이며 통신 매뉴얼이 아니다.

공개 웹에서 Mand.ro/Mandro/만드로, Mark7/Mark 7/마크7과 USB, serial, protocol,
firmware, Hall, 위치 피드백 조합 및 GitHub 관련 검색을 수행했다.
이번 검색에서는 적용 가능한 제조사 통신 매뉴얼·SDK·펌웨어 소스를 확보하지 못했다.
다른 제조사 그리퍼나 Mark-10 계측기, 동명의 제품 프로토콜은 사용하지 않았다.
프로젝트 내 파일 목록에서도 Mark7 펌웨어 소스나 원본 통신 PDF는 찾지 못했다.

## 현재 코드의 데이터 경로

### Mark7 노드

파일: `ros2_ws/src/capstone_mark7/capstone_mark7/mark7_node.py`

- 생성자에서 장치 파일을 열고 termios를 115200, 8-N-1로 설정하며 입력/출력 버퍼를 flush한다.
- 시작 시 preset을 전송하는 코드는 없다. 실제 장치에서 포트 열기나 제어선 변화가 어떤 영향을
  주는지는 이번 조사로 검증하지 않았고, 노드도 실행하지 않았다.
- `build_packet()`은 11바이트를 만든다:
  `FF 3F speed current p0 p1 p2 p3 p4 p5 01`.
- 위치 변환은 `max(1, min(255, (int(steps) + 21) // 2))`이다.
  이것은 현재 코드가 구현한 **목표 위치 인코딩**이며 수신 피드백 형식의 근거가 아니다.
- `write_packet()`은 명령을 두 번 전송하며 부분 쓰기를 처리한다.
- `open_callback()` / `grasp_callback()`은 `send_preset()`에 예외가 없으면 성공을 반환한다.
  메시지도 `command sent`이다. `os.read()`·응답 파서·장치 ACK 확인·동작 완료 확인은 없다.
- 실제 상태 Publisher, 상태 조회 서비스, 펌웨어 버전 조회 경로가 없다.
- Hall 카운터 드리프트와 음수 Open 목표 주석은 내부 위치 카운터에 관한 기존 설명이다.
  외부 조회 가능성, 측정 정밀도 또는 재부팅 후 절대 위치 유효성을 증명하지 않는다.

### QP Teleop

파일: `ros2_ws/src/capstone_teleop/capstone_teleop/qp_teleop.py`

- 내부 `gripper_state`는 0.0으로 초기화하지만 시작 시 토픽으로 발행하지 않는다.
- `gripper_command_done()`이 Trigger 성공을 받으면 Open=0.0 / Grasp=1.0을
  `/capstone/gripper_state` (`std_msgs/Float32`)에 발행한다.
- 의미는 마지막으로 전송 성공 응답을 받은 명령이다. 실제 손가락 위치·파지·동작 완료가 아니다.
- 현재 Publisher는 RELIABLE, TRANSIENT_LOCAL, depth=1이다.
- Mark7 서비스를 직접 호출한 명령이나 다른 제어 경로는 이 QP 콜백을 통과하지 않으므로
  QP의 마지막 명령 값과 실제 장치 상태는 달라질 수 있다.

### Recorder와 시간 정합성

파일: `ai/record/indy7_act_record.py`, `ai/observation_timing.py`

- Recorder도 RELIABLE, TRANSIENT_LOCAL, depth=1로 그리퍼를 구독한다.
  QP Publisher가 살아 있고 과거에 값을 발행했다면 새 Recorder가 보관된 값을 받을 수 있다.
  최초 발행 이전에는 받을 값이 없으며, Publisher 종료 후에도 영구 보관되는 저장소는 아니다.
- Float32에는 Header가 없으므로 Recorder는 콜백 수신 시각을 보관한다.
  재수신 시각으로 원래 센서 측정 시각이나 물리 동작 시각을 복원할 수 없다.
- `BoundedHistory`는 마지막 그리퍼 명령 이벤트를 시간 제한 없이 별도로 유지한다.
  현재 정책은 연속 측정 피드백의 freshness 검사가 아니다.
- `HistoryAligner`는 시작 기준 시각 이전에 수신한 0/1이 없으면
  `unknown_initial_gripper`로 거부한다. 잘못된 값은 `invalid_gripper_command`로 기록한다.
- `PendingStart`는 `s` 처리 시점의 그리퍼 이벤트를 고정한다. 대기 중 최초로 도착한
  Header 없는 값을 과거 시작 시점으로 소급하지 않는다.
- 따라서 QoS 변경, 내부 초기 0 발행, 마지막 명령 재발행으로 실제 초기 상태를 증명할 수 없다.

기존 `docs/ACT_ROS_STAGE1.md`의 VOLATILE 설명은 과거 조사 시점의 내용이다.
현재 작업 트리의 QP/Recorder는 모두 TRANSIENT_LOCAL이므로 이 보고서는 실제 코드를 기준으로 한다.

## 요구된 확인 항목의 판정

| 항목 | 판정 |
| --- | --- |
| 내부 손가락 위치 감지 | Hall 센서·내부 위치 제어 존재는 제조사 자료로 확인 |
| 외부에서 실제 손가락 위치 읽기 | 미확인. 읽기 API와 실제 장치 응답 근거 없음 |
| Hall 위치 카운터 외부 읽기 | 미확인. 센서 존재와 카운터 공개는 별개 |
| USB 시리얼 위치 피드백 | 미확인. 로컬 코드는 송신만 수행 |
| 별도 무동작 상태 조회 명령 | 미확인. 안전하다고 검증된 요청 바이트 없음 |
| 현재 펌웨어 지원 | 미확인. 버전·바이너리 출처·소스·호환 명세 없음 |
| 실제 위치로 Open/Grasp 판정 | 아직 불가. 측정값·유효성·원점·오차와 판정 기준이 필요 |

## 실물에 대해 추가로 확보해야 할 정보

제조사 또는 납품 자료에서 다음 사항을 확인해야 한다. 아래는 문의 항목이며 장치에 보낼 명령이 아니다.

1. 장비 모델/자유도, 손가락·손바닥 보드 리비전, USB 중계 보드 유무, 각 펌웨어 버전.
2. 설치 버전에 대응하는 통신 매뉴얼, SDK 수신 코드 또는 펌웨어 소스/배포 이력.
3. 목표 위치와 별개인 현재 측정 위치/Hall 카운터를 읽는 기능 및 USB까지의 반환 경로.
4. 읽기 전용 조회 요청·응답의 프레이밍, 길이, 축 순서, 단위, 부호, 무결성 검사,
   요청 대응 식별 방법, 오류/미지원 응답과 최대 응답 지연.
5. 조회·USB 연결·버전 확인이 구동, 원점 복귀, 카운터 초기화, 모드 변경을 유발하지 않는다는 근거.
6. 전원 재인가·통신 재접속 후 카운터 유효성, 드리프트/오버플로, 기준 위치 보존 여부,
   무동작 상태에서 초기 위치 유효성을 확인할 수 있는지.
7. 현재 값과 목표 값의 구별, 이동/정지/스톨/센서 오류 및 개별 축 통신 실패의 표현.

이 자료를 확보한 뒤에만, 별도 승인된 실물 시험으로 문서에 명시된 무동작 조회를 검증할 수 있다.
현재 실행 중인 시리얼 연결과 경쟁하는 두 번째 리더는 기존 수신 데이터를 소비할 수 있으므로
임의로 연결하지 않는다. 노드 종료·재시작·포트 열기·플래싱도 이번 작업에서 수행하지 않았다.

## 펌웨어 수정 필요 여부

현 단계에서는 단정할 수 없다.

- 기존 버전이 읽기 전용 피드백을 지원하면, 해당 명세를 이용한 호스트 수신 구현으로 가능할 수 있다.
- 손가락 내부 카운터만 있고 외부 반환이 없다면, 해당 제어기와 필요한 중계 펌웨어에
  현재 측정값·유효성·오류를 반환하는 읽기 전용 경로가 필요하다.
- 증분 카운터가 부팅 시 임의로 0이 되고 무동작 원점 확인 수단이 없다면,
  카운터 반환만 추가해도 실제 초기 위치를 알 수 없다. 신뢰 가능한 원점 보존 또는
  절대 위치 센싱 같은 추가 설계가 필요할 수 있다. 자동 Open으로 해결하지 않는다.

## 피드백 명세 확보 후 필요한 구현

아래는 향후 설계 조건이며 이번에 구현하거나 확정한 wire protocol이 아니다.

1. Mark7 노드가 단독으로 시리얼을 소유하고 읽기 전용 상태 수집을 수행한다.
   명령 쓰기와 조회/수신을 직렬화하며, 부분·병합 프레임, 잘못된 응답, 타임아웃,
   단선·재접속·펌웨어 미지원 상태를 처리한다. 시작 시 동작 preset은 전송하지 않는다.
2. 원시/변환 측정 위치, 단위, 축별 유효성, 취득 시각 또는 호스트 수신 시각과 그 의미,
   센서/통신 오류를 명시한 상태 인터페이스를 마련한다. 목표값과 측정값은 별도 필드/토픽이다.
3. Open/Grasp 분류는 유효한 실제 측정과 검증된 축별 기준·허용오차를 사용한다.
   중간 위치·이동 중·오차 범위 밖·원점 미확정·일부 축 실패·오래된 측정은 Unknown이다.
   물체를 잡으면 350 목표에 도달하지 않을 수 있다. 위치만으로 물체 접촉이나 파지 성공을
   증명할 수 없으므로 Grasp가 손 자세 분류인지 물체 파지인지를 먼저 정의해야 한다.
4. `/capstone/gripper_state`를 실제 분류에 재사용한다면 **QP의 기존 발행을 제거하거나
   명령 전용 토픽으로 이관하는 변경과 함께** 전환한다. QP의 Open/Grasp 서비스 호출과
   Xbox 자동 ARM/QP 제어는 유지한다. Mark7만 실제 상태를 발행하도록 구성한다.
   서로 다른 의미의 두 TRANSIENT_LOCAL Publisher는 각자 보관 샘플을 보내므로 허용하지 않는다.
5. Float32 0/1은 유지 가능하더라도 Unknown과 측정 유효성·freshness를 담을 보조 상태 또는
   새 메시지 계약이 필요하다. Unknown=-1/NaN을 현재 Recorder에 그냥 보내면 오류로 처리된다.
   미정의 값을 같은 인터페이스에 조용히 추가하지 않는다.
6. Recorder 재시작을 위해 최신 실제 상태를 RELIABLE/TRANSIENT_LOCAL로 제공하되,
   마지막 실제 값만 무기한 신뢰하지 않는다. 주기적 갱신과 만료/단선 시 Unknown을 처리하고
   Recorder도 실제 관측 freshness를 검증해야 한다. 캐시 수신을 새 측정으로 간주하지 않는다.
7. Recorder의 인과적 기준 시각 선택, receive margin, clock/header·gap·overflow 검사와
   실패 take 보존은 유지한다. 초기 Unknown은 시작 거부, 녹화 중 피드백 유효성 상실은
   명시적으로 무결성 실패로 처리하는 통합 검증이 필요하다.

## 비하드웨어 테스트

애플리케이션 소스와 기존 테스트를 수정하지 않고 다음 기존 테스트를 실행했다.
ROS 초기화, Node 생성/spin, 실제 시리얼 open/write, 로봇 서비스 호출은 수행하지 않았다.

- Mark7 패킷 생성/범위 검증: **8 passed**.
- Recorder와 PendingStart 오프라인 테스트: **76 passed**.
  초기 상태 누락 거부, 기준 시각 후 수신값 소급 금지, 그리퍼 이벤트 인과성,
  시간/헤더 이상, 누락/overflow, 데이터 저장 무결성 관련 기존 테스트를 포함한다.
- 이 결과는 기존 소프트웨어 동작에 대한 검증이며 실제 피드백 수신의 성공 증거가 아니다.

재현 명령 (프로젝트 루트, 기존 환경 사용):

```bash
source /opt/ros/jazzy/setup.bash
PYTHONPATH="$PWD/ros2_ws/src/capstone_mark7:$PWD/ros2_ws/src/capstone_teleop:$PWD${PYTHONPATH:+:$PYTHONPATH}" \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  ros2_ws/src/capstone_mark7/test/test_mark7_protocol.py

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" \
  .venv-act/bin/python -c 'import rclpy.qos; import pytest; raise SystemExit(pytest.main(["-q", "-p", "no:cacheprovider", "--tb=short", "ai/record/test/test_indy7_act_record.py", "ai/record/test/test_pending_start.py"]))'
```

Recorder 최초 실행은 자동 로드되는 ROS pytest 플러그인의 `lark` 의존성 누락으로 실패했다.
자동 플러그인을 끈 다음 실행은 기존 fixture가 `rclpy`를 stub으로 교체하면서
`rclpy.qos`를 준비하지 않아 76건 setup error가 발생했다.
최종 명령은 실제 `rclpy.qos`를 미리 import하여 QoS 타입만 제공한다.
이는 테스트 부트스트랩 보완이며 `rclpy.init()` 또는 장치 연결을 수행하지 않는다.
패키지 설치, 전역 환경 변경, 기존 테스트 수정은 하지 않았다.

실물 시험은 수행하지 않았다. 향후에도 내용 설명과 사용자 승인을 먼저 받아야 한다.
