# ACT 데이터 수집 계획

## 1. Task 범위

1차 ACT 학습의 목표는 다음과 같다.

> **동일한 Home pose에서 시작한 Indy7이 선반 칸1의 feasible workspace 내 임의의 위치와 yaw로 놓인 바구니의 손잡이 중앙을 grasp하고, 선반에서 꺼낸 뒤 Table의 고정된 drop zone에 내려놓는다.**

초기 학습에서 변화시키는 요소:

- 바구니 가로 위치 `x`
- 바구니 깊이 위치 `y`
- 바구니 yaw `θ`

고정 요소:

- Indy7 Home pose
- Table drop zone
- 기본 grasp/transport strategy
- Camera 1 / Camera 2 위치

우선 가장 넓은 **칸1**에서 ACT를 학습하고, 해당 task가 안정적으로 수행된 이후 다른 칸으로 확장한다.

---

## 2. 실험 환경

### 선반 칸1

- Width: **117 cm**
- Height: **45 cm**
- Depth: **26.5 cm**
- 바닥면 크기: **117 × 26.5 cm**

### 바구니

- Width: **30.5 cm**
- Depth: **9 cm**
- Height: **7 cm**
- 바닥에서 손잡이 중앙까지 높이: **7 cm**
- 손잡이 길이: **30.5 cm**

### Indy7 Gripper

- 대략적인 폭: **9 cm**
- 대략적인 높이: **16 cm**
- Grasp 후 약 **3 cm 수직 Lift** 후 선반 밖으로 retract

---

## 3. Position Sampling — 3 × 1 영역

선반 바닥을 3×3으로 나누지 않고, 가로 방향만 세 영역으로 구분한다.

| 영역 | 선반 기준 범위 | 물리적 폭 |
|---|---:|---:|
| Left (L) | 0 ~ 39 cm | 39 cm |
| Center (C) | 39 ~ 78 cm | 39 cm |
| Right (R) | 78 ~ 117 cm | 39 cm |

이 영역은 바구니 전체가 해당 영역 안에 있어야 한다는 의미가 아니라, **바구니 중심 위치의 데이터 coverage를 관리하기 위한 sampling region**이다.

ACT 모델에는 `L/C/R` label을 입력하지 않는다.

### 깊이 방향

- 선반 깊이: **26.5 cm**
- 바구니 깊이: **9 cm**

깊이 방향을 Front/Middle/Back으로 고정 cell 분할하지 않는다.

각 demonstration마다 feasible range 안에서 바구니의 깊이 위치 `y`를 연속적으로 변화시킨다.

Front / Middle / Back은 별도 sampling cell이 아니라 **전체 dataset의 depth coverage를 확인하기 위한 기준**으로만 사용한다.

---

## 4. Feasible Workspace

바구니 크기가 **30.5 × 9 cm**이므로 선반 전체 `117 × 26.5 cm`를 바구니 중심이 자유롭게 움직일 수 있는 영역으로 간주하지 않는다.

바구니가 선반 밖으로 벗어나지 않고 선반 구조물 및 로봇과 충돌하지 않는 범위에서 `(x, y)`를 sampling한다.

특히 yaw가 커질수록 바구니가 깊이 방향으로 차지하는 공간이 증가하므로:

> **Position과 yaw를 독립적으로 무작위 선택하지 않는다.**

권장 순서:

1. yaw `θ` 선택
2. 해당 yaw에서 가능한 feasible `(x, y)` 범위 확인
3. 그 범위 안에서 위치 sampling
4. 선반 좌우/앞뒤 끝에서는 추가 safety margin 적용

실제 feasible workspace는 Pilot Test에서 확정한다.

---

## 5. Yaw Sampling

1차 데이터 수집 후보 yaw 범위:

- **-40° ~ +40°**

6개 yaw bin:

| Bin | Yaw 범위 |
|---|---:|
| A1 | -40° ~ -27° |
| A2 | -27° ~ -13° |
| A3 | -13° ~ 0° |
| A4 | 0° ~ +13° |
| A5 | +13° ~ +27° |
| A6 | +27° ~ +40° |

각 bin 내부에서도 동일 각도를 반복하지 않고 yaw를 변화시킨다.

데이터 수집 전에 실제 바구니를 놓고 ±40°의 간섭 여부를 확인한다.

실제 feasible yaw가 더 작다면:

1. 실제 최대 feasible yaw 확정
2. 전체 feasible yaw 범위를 6개 bin으로 다시 균등 분할

---

## 6. Core Dataset — 108 Episodes

Core dataset:

**3 position regions × 6 yaw bins × 6 demonstrations = 108 successful demonstrations**

| Position | Yaw bins | Bin당 Demo | 총 Demo |
|---|---:|---:|---:|
| Left | 6 | 6 | 36 |
| Center | 6 | 6 | 36 |
| Right | 6 | 6 | 36 |
| **Total** |  |  | **108** |

같은 `Position × Yaw bin`의 6개 demonstration도 동일하게 반복하지 않는다.

각 demonstration에서 변화:

- `x`
- `y`
- `yaw`

### Depth Coverage

각 Position × Yaw 조합의 6개 demonstration은 가능한 범위에서 아래와 같이 depth를 다양화한다.

1. Front
2. Front-Middle
3. Middle
4. Middle-Back
5. Back
6. Random

단, 큰 yaw에서는 extreme depth가 물리적으로 불가능할 수 있으므로 **feasible range 내에서만 sampling**한다.

목표는 특정 좌표 암기가 아니라 `(x, y, yaw)` 공간에 대한 coverage 확보이다.

---

## 7. 데이터 수집 순서

`Left 36 → Center 36 → Right 36` 순으로 수집하지 않는다.

전체 108개의 조건을 사전에 작성한 뒤 **shuffle하여 수집**한다.

목적:

- Operator fatigue와 특정 위치 조건의 결합 방지
- 시간에 따른 조명 변화 bias 감소
- 로봇 온도/상태 변화 bias 감소
- 특정 yaw bin이 특정 시간대에 몰리는 문제 감소

모든 episode에 실제 `x`, `y`, `yaw`를 기록한다.

---

## 8. Teleoperation / Demonstration 동작 원칙

### 기본 원칙

> **Continuous and smooth trajectory를 기본으로 한다.**

액션 전환마다 인위적으로 1초씩 멈추지 않는다.

다만 다음과 같은 물리적으로 필요한 짧은 안정화는 허용한다.

- Grasp 직전 정렬 안정화
- Gripper close 완료 대기
- Place 직전 자세 안정화
- Gripper open 완료 대기

모든 demonstration의 움직임 스타일과 속도를 가능한 한 일관되게 유지한다.

### 기본 trajectory

`Home → Pre-grasp → Align → Descend → Close → Lift 3 cm → Retract → Safe Waypoint → Table Approach → Place → Open → Retract`

세부 원칙:

1. 동일한 Home pose에서 시작
2. 손잡이 중앙 위쪽으로 접근
3. Gripper yaw를 손잡이에 정렬
4. 손잡이 중앙 부근 grasp
5. Gripper close
6. 약 3 cm 수직 Lift
7. 선반 밖으로 retract
8. 동일한 safe waypoint를 통해 Table 방향 이동
9. 고정 drop zone에 place
10. Gripper open
11. 위쪽으로 retract
12. Episode 종료

### Home Position 처리

Home 복귀를 ACT의 action으로 학습하려면 모든 demonstration에서 일관되게 포함한다.

권장 구조:

`Home → Pick → Transport → Place → Retract → Home`

단, Home 복귀가 단순한 고정 joint trajectory이고 시각적 reasoning이 필요 없다면 ACT가 아니라 deterministic control로 분리하는 것도 고려한다.

작업 진행 중 이유 없이 임의로 Home으로 복귀하는 demonstration은 수집하지 않는다.

---

## 9. Camera 구성

### Camera 1 — Shelf View

Indy7과 선반 전체가 보이도록 고정 배치한다.

주요 관측 대상:

- 바구니 `(x, y)`
- yaw
- Indy7 접근 및 정렬
- Grasp
- Lift
- Shelf Retract

사전 확인:

- Left / Center / Right 모두 관측 가능
- Front / Back에서도 손잡이 확인 가능
- 큰 yaw에서도 handle direction 판별 가능
- Basket + Handle + Gripper가 충분히 보임

### Camera 2 — Table Top View

Table을 위에서 내려다보도록 고정 배치한다.

주요 관측 대상:

- Table 접근
- Drop zone 상대 위치
- Place
- Release

Table + Drop Zone + Basket + Gripper가 충분히 보여야 한다.

두 RGB 카메라는 전체 데이터 수집 동안 위치를 변경하지 않는다.

---

## 10. Pilot Test

Core dataset 전에 **5~10개의 pilot episode**를 수행한다.

확인 항목:

- [ ] Camera 1에서 선반 전체 관측
- [ ] Left / Center / Right 모두 바구니 명확히 관측
- [ ] Front / Back 위치에서도 바구니 및 손잡이 관측
- [ ] 다양한 yaw에서 handle direction 식별
- [ ] Indy7이 전체 feasible workspace에 접근 가능
- [ ] 실제 최대 feasible yaw 확인
- [ ] 3 cm Lift 안전성 확인
- [ ] Shelf Retract 안정성 확인
- [ ] Shelf → Table transport 안정성 확인
- [ ] Camera 2에서 Basket + Gripper 관측
- [ ] Place 과정 명확히 관측
- [ ] RGB 영상 정상 저장
- [ ] Robot state/action 정상 기록
- [ ] Timestamp synchronization 정상
- [ ] Frame drop 없음

Pilot 완료 후 feasible workspace와 최종 yaw 범위를 확정한다.

---

## 11. ACT v0 평가

Core 108개로 ACT v0 학습 후 약 **60회의 unseen pose evaluation**을 수행한다.

평가 pose는 학습에 사용한 정확한 `(x, y, yaw)`가 아닌 새로운 위치를 사용한다.

평가에 포함할 조건:

- Left / Center / Right
- Front / Middle / Back
- Small / Medium / Large yaw
- 좌우 extreme
- Front / Back extreme
- Position extreme + yaw extreme

단계별 평가:

`Reach → Align → Grasp → Lift → Shelf Retract → Transport → Table Approach → Place`

각 단계 성공 여부를 별도로 기록한다.

---

## 12. Boundary / Failure-based Dataset

ACT v0 평가 결과로 failure map을 작성한다.

모든 corner에 동일한 수를 추가하지 않고 실제 실패 영역을 보강한다.

예:

- Left extreme 실패 → Left boundary 추가
- Back + Large yaw 실패 → 해당 조합 추가
- ±max yaw 부근 실패 → Large yaw 추가
- Table placement 실패 → Placement demo 추가

초기 기준:

- **약 30~40개**

실제 평가 결과에 따라 조정한다.

---

## 13. Corrective Demonstration

특정 상태에서 반복적인 실패가 발생하면:

- **약 20~40개의 corrective demonstration** 추가

Autonomous failure trajectory 자체는 학습 데이터에 넣지 않는다.

대신 해당 실패 상태를 재현하고 사람이 올바른 recovery 행동을 demonstration한다.

예:

`Gripper가 손잡이에서 약간 벗어남 → 재정렬 → Grasp → Lift → Retract → Task 완료`

Corrective demonstration은 일반 Core demo와 구분하여 metadata에 표시한다.

---

## 14. 예상 최종 Dataset

| 단계 | 목적 | Demo |
|---|---|---:|
| Core | `(x,y,yaw)` coverage | 108 |
| Boundary / Failure-based | 취약 영역 보강 | 약 30~40 |
| Corrective | 반복 실패 상태 보완 | 약 20~40 |
| **예상 최종** |  | **약 158~188** |

---

## 15. 제외할 Demonstration

다음 episode는 제외하고 다시 수집한다.

- Operator 조작 실수
- 선반 또는 바구니와 충돌
- Grasp 실패
- 실패 후 억지로 다시 grasp
- 불필요한 반복 수정
- 지나치게 긴 정지
- 운반 중 basket slip
- Camera frame drop
- Timestamp synchronization 오류
- 사람이 카메라를 심하게 가림
- 평소와 크게 다른 trajectory

의도적 corrective demonstration은 별도 표시한다.

---

## 16. Metadata

각 episode마다 기록:

- `episode_id`
- `target_x`
- `target_y`
- `target_yaw`
- `actual_x`
- `actual_y`
- `actual_yaw`
- `x_error`
- `y_error`
- `yaw_error`
- `position_region`
- `yaw_bin`
- `depth_coverage`
- `grasp_success`
- `lift_success`
- `shelf_retract_success`
- `transport_success`
- `place_success`
- `final_success`
- `failure_reason`
- `demo_quality`
- `notes`

---

# 최종 진행 순서

**환경 치수 측정**

↓

**실제 feasible workspace 확인**

↓

**최대 feasible yaw 확인**

↓

**3 cm Lift 및 Retract 테스트**

↓

**L / C / R sampling region 설정**

↓

**Home / Pre-grasp / Safe Waypoint / Drop Zone 확정**

↓

**Camera 1 / Camera 2 최종 고정**

↓

**5~10 Pilot Episodes**

↓

**108개 `(x,y,yaw)` 수집 조건표 작성 및 Shuffle**

↓

**Core 108 Episodes 수집**

↓

**ACT v0 학습**

↓

**약 60회 Unseen Pose Evaluation**

↓

**Failure Map 작성**

↓

**취약 영역 30~40개 추가**

↓

**필요 시 Corrective Demonstration 20~40개 추가**

↓

**최종 ACT 평가**
