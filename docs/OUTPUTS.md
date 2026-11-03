# outputs 관리

2026-11-01 사용자 확정 기준이다. 현재 정식 ACT 학습 데이터셋은 0개다.

```text
outputs/
├── training_snapshots/      # act snapshot: 독립 학습 Dataset, 원본과 별도
├── act_gui_integration_test/   # 기존 위치 유지: 실제 Recorder 통합 테스트
├── preserved/
│   ├── integration/          # 향후 보존할 통합 결과; 현재 비어 있음
│   └── datasets/             # 향후 정식 Collection
└── temporary/
    ├── training/
    ├── inference/            # 기존 act_infer_* 5개
    └── verification/         # 나머지 기존 검증 폴더13개
```

`act_gui_integration_test`만 기존 자료 중 장기 보존 대상이다. Success 1개와
Correction 1개이며 조건 정의108개와 실제 수집 완료 개수는 다르다.
기존 Pilot·합성 Dataset·Checkpoint·실행/평가 근거는 모두 임시 검증 산출물이다.
`temporary` 분류는 삭제 승인이 아니다. 사용 중인 파일은 이동·변경하지 않는다.
`outputs/`는 Git 제외 대상이므로 디렉터리 생성과 자료 보존은 별도로 관리한다.

## 새 정식 Collection 생성과 재개

새 데이터셋은 `ai/record/plans/<dataset_id>.json`으로 정의한다. [사용 설명서](.act record.md)를 따른다.
`act record`는 선택 화면을 열고, 최초 열기에서 Collection을 생성한다. 아래는 생성한 Collection의 CLI 재개 예시다.
장비 운영은 별도 승인 후 수행한다.

```bash
act record --collection-dir outputs/preserved/datasets/act1_108_001
```

최초에는 선택 화면에서 정의 JSON을 기준으로 새 장부를 만든다. 나중에는 같은 경로로
기존 장부·Dataset을 검증해 재개한다. 위 CLI 명령은 이미 생성된 Collection의 재개 예시다.
빈 경로를 CLI로 직접 지정하면 종전 Core-108 모드이므로 사용자 정의 계획 생성에는 쓰지 않는다.
GUI에서도 `--collection-dir outputs/preserved/datasets/act1_108_001`을 사용한다.
CLI와 GUI를 동일 Collection에 동시에 연결하지 않는다.

Collection은 `collection.json`, `receipts/`, `success/`, `correction/`, 과거 `failure/`,
`pending/`을 한 단위로 관리한다. 최초 생성 이후 이동하거나 이름을 변경하지 않는다.
명시 CLI의 기존 저장 기본값은 유지하며, 인자 없는 wrapper/GUI는 관리 화면을 연다. 이전 문서의
`datasets/indy7_act_core108`은 기존 경로 예시이며 새 정식 수집에는 위 경로를 명시한다.

이름은 `act<행동번호>_<계획조건수 3자리>_<수집본 일련번호 3자리>`다.
예: `act1_108_001`, `act1_055_001`, `act1_055_002`, `act2_108_001`.
조건 수는 계획값이며 실제 Success/Correction 수는 장부와 Dataset 메타데이터로 확인한다.
기본 Core-108의 원본 조건표는 유지하며, 사용자 정의 계획은 실제 조건 목록을 JSON으로 제공한다.
`055` 이름만으로55개 조건을 만들지 않으며 Core-55 조건표는 기본 제공하지 않는다.

향후 학습 입력은 해당 Collection의 `success/`이며 Collection 루트가 아니다.
Correction 학습·다중 Dataset 학습은 현재 지원하지 않는다.

## 임시 결과와 기존 기록

새 학습·추론 산출물은 기존 `--output-dir`로 각각
`outputs/temporary/training/<run_id>`, `outputs/temporary/inference/<run_id>`를 명시한다.
기본 출력 경로는 변경하지 않았다. 추론은 원본 Dataset 대신 `--checkpoint`로 모델을 선택한다.
이 안내는 학습·추론 실행 승인이 아니다.

기존18개 폴더는 이름과 내부 구조를 유지해 이동했다. 합성 Dataset·Checkpoint가 들어 있는
`act_final_offline_*`도 검증 묶음 전체를 `temporary/verification/`에 둔다.
과거 JSON·장부·sidecar·receipt·로그의 절대경로는 당시 출처 기록으로 그대로 유지한다.
이동된 옛 Collection은 보관용이며 기존 경로 검증 때문에 Recorder 재개/reconcile 대상이 아니다.
마이그레이션·증거 재작성·symlink 우회는 구현하지 않는다.

이동 대응표와 전후 파일 목록·SHA-256은 `tmp/outputs_cleanup_vv70otzr/`에 보존한다.
상세 이동 목록은 `moves.json`, 기존 자료 무변경 대조는 `verification.json`을 확인한다.

## 학습 Snapshot

`act snapshot`은 `outputs/training_snapshots/<name>/`에 성공 Dataset을 고정한다.
단일 입력은 독립 복사, 다중 입력은 공식 LeRobot 병합이다. Snapshot은 원본 Collection을
대체하지 않으며 자동 삭제 대상이 아니다. [Snapshot 사용법](../ai/train/SNAPSHOTS.md)을 따른다.
