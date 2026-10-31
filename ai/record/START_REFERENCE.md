# Recorder: s 처리 시각을 고정한 수신 대기

`./act-record`의 `s`는 메인 루프가 명령을 처리한 순간의 paired monotonic/Unix/ROS
clock을 `t0`로 저장한다. 키보드 읽기 thread는 문자열만 Queue에 넣는다. 물리 키 입력
순간이나 stdin thread가 읽은 시각이 아니라 **메인 루프의 처리 시각**이다.

## 이전 오류의 발생 경로

`HistoryAligner.prepare(options, history, timing)`는 timing의 sample 시각을 기준으로
Header가 그 시각 이하인 최신 수신 메시지를 검사한다. 이 메서드는 수신을 기다리지
않는다. `s` 직후 캐시를 검사하면 아직 callback에 도착하지 않은 최신 Header를
고를 수 없다.30FPS의 약33.3ms 주기와33~36ms 전달 지연이 합쳐져, 정상 스트림의
최신 **수신 캐시**조차 기준 시각에서50ms보다 오래될 수 있다.

반면 일반 기록은 `FixedDeadline(20, t0 + receive_wait)`를 사용하므로, 첫 release는
`t0 + receive_wait + 50ms`이고 관측 기준은 그보다receive_wait만큼 이전이다.
기존 즉시 prepare에는 이 수신 여유가 없었다.

중단된 작업 트리에는5초·연속3개 안정화 초안이 있었다. 이번 최종 요구와 다르므로
변경 전 파일/diff를 outputs/act_recorder_start_reference_20261031/에 보존하고,
해당 시작 부분만 고정 기준1회 검증으로 바꿨다. 기존 미커밋 Recorder 기능은 유지했다.

## 현재 동작

1. `s`를 처리하면 `PendingStart`가 t0와 그때 알려진 Gripper 이벤트를 저장한다.
   EpisodeSession은 READY이고 Dataset/Pending 디렉터리·학습 프레임을 만들지 않는다.
2. 기존 receive_wait_ms(기본60)가 지난 첫 메인 루프에서 수신 History를 검증한다.
   sleep60ms나 대기 while 루프는 없다. 기존 rclpy.spin_once가 매번 계속 실행된다.
3. `HistoryAligner.prepare()`에 **원래 t0 timing**과 수신 후의 History를 전달한다.
   영상·Joint는 Header<=t0만 선택한다. t0 후 생성된 미래 프레임으로 채우지 않는다.
   현재 clock과 t0의 연속성·History overflow도 검사한다.
4. Header 없는 Gripper는 수신 시각만 알 수 있으므로 t0에 이미 알고 있던 값을 사용한다.
   첫 메시지가 t0 뒤 도착했다고 t0의 상태로 역추정하지 않는다. 이후 이벤트는 기록
   프레임의 기존 인과적 선택에 따라 적용한다. 작업자 가정 입력은 Recorder에 없다.
5. 정상일 때 별도 s 없이 RECORDING으로 전환한다. Session의 시작 시각과 Deadline은
   **수신 완료 시각이 아닌 t0**를 사용한다. 기다리는 동안 받은 새 QP/Gripper 이벤트를
   뒤늦게 시작한 에피소드의 과거 이벤트로 잘못 제외하지 않는다.
6. 유효하지 않으면 오류와 details를 출력하고 READY로 돌아간다. 다른 기준을 찾는
   자동 재시도는 없으며 새 s는 새 t0를 만든다. 반복 s는 진행 중 t0를 바꾸지 않는다.

예시(기본20Hz/60ms):

| 시각 | 동작 |
|---|---|
| t0 | s 처리, 시작 기준 고정 |
| t0+60ms 부근 | t0의 관측 검증, 통과하면 RECORDING |
| t0+50ms | 첫 기록 프레임의 관측 기준 시각 |
| t0+110ms 부근 | 첫 기록 release: frame_index=0, schedule.slot=1 |
| 이후50ms마다 | 기존 연속 격자로 기록 |

표의 관측 기준 시각은 생성/저장 실행 순서가 아니다. 첫 기록은110ms에 실행되면서
50ms 시점의 관측을 선택한다. t0 검증 자체는 학습 프레임으로 저장하지 않는다.
Dataset 명목 timestamp는 기존대로 frame_index/20이다.

60ms는 예약된 검사 시각이지 ROS callback 지연의 보장이 아니다. 메인 루프의 짧은
polling 지연은 기존 기록 방식처럼 허용하되 t0는 옮기지 않는다. 검사를 실행하기도 전에
첫 기록 release(t0+110ms)에 도달하면 `start_receive_deadline_exceeded`로 READY에
복귀한다. 첫 slot을 건너뛰거나 새 시작 시각으로 지연을 숨기지 않는다.60ms 검사에서
데이터가 없으면 즉시 실패하며, 나중에 도착한 데이터로 자동 재검사하지 않는다.

## 유지한 조건

- Camera age50ms, Camera skew40ms, State/전체 observation75ms 그대로
- RECORDING의 IntegrityError → INVALID/REVIEW, 성공·실패 Dataset 양쪽 저장 차단
- 기존 성공/실패/Pending 디렉터리,7D/RGB2개/20FPS feature와 Timing Sidecar 그대로
- 프레임 보간·복제·건너뛰기·압축 없음. 최초 slot 지연도 기존 Fail-fast로 처리
- Driver/Xbox/Mark7 및 ACT ROS Dry Run Recovery 변경 없음
- r/d는 시작 대기를 취소하고 READY, q/EOF/Ctrl+C는 대기를 취소하고 기존 종료 처리
- 기존 Pending 시연의 저장/폐기/부분 저장 실패 보호 그대로

## 실환경 확인

이미 실행 중인 장치 스택을 별도로 재시작하지 않고 기존 실행 방식을 사용한다.
기존 Dataset에 추가하는 경우:

```bash
cd /home/sirlab2/sirlab258
./act-record --resume
```

Pilot에서 별도 경로를 사용했다면 기존 --root/--failure-root/--pending-root 등 인자는
그대로 유지한다. 기존 Dataset을 덮어쓰거나 다른 수집 데이터와 섞지 않는다.

이번 개발에서는 실행하지 않았다. `s`+Enter 후 고정 reference 출력과 약60ms 뒤의
검증 결과를 확인한다. 상태가 유효하면 자동 RECORDING으로 바뀌며 실패하면 오류를
보이고 READY로 돌아간다. 첫 확인은 필요하면 움직임 없이 짧게 기록 후 r/d로 폐기해
실제 성공 Dataset과 섞지 않는다. 장치 제어 명령/상태 조작을 자동으로 실행하지 않는다.

기록을 저장해 확인하는 경우 Timing Sidecar에서 다음을 검사한다.

- start_monotonic_ns가 처음 출력한 고정 reference와 같음
- 첫 alignment.reference_monotonic_ns = start+50,000,000ns
- 첫 schedule.deadline_monotonic_ns = start+110,000,000ns
- 원본 Header<=해당 frame reference, frame_index는0부터 연속, slot=index+1
- integrity.valid=true, missed_deadlines=0, stale_sample_skips=0

## 한계

이 변경은 시작 시 수신 여유가 빠져 있던 경로를 해결하지만 **모든 시작 실패를
없애지는 않는다**.예약된 검사 때 아직 처리되지 않은 메시지, 실제 프레임 누락/67ms 간격,
고정 t0에서 실제50ms age/40ms skew 초과, Joint/Gripper 미확인, clock 이상 및 메인
루프 지연은 계속 시작을 거부할 수 있다. 미래 프레임을 가져오거나 허용값을 늘려
성공시키지 않는다. 시작 실패 빈도 감소 여부는 실제 로그로 추가 확인해야 한다.
