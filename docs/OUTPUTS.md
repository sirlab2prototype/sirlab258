# outputs 관리

2026-11-01 사용자 확정 기준이다. 현재 정식 ACT 학습 데이터셋은 0개다.

```text
outputs/
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

저장소 루트에서 사용할 명령 예시다. 장비 운영은 별도 승인 후 수행한다.

```bash
./act-record --collection-dir outputs/preserved/datasets/act1_108_001
```

처음 사용할 때 지정한 경로에 새 장부를 만든다. 나중에도 같은 경로와 명령을 사용하면
기존 장부·Dataset을 검증해 재개한다. Collection 모드는 기존 코드가 resume을 설정한다.
GUI에서도 `--collection-dir outputs/preserved/datasets/act1_108_001`을 사용한다.
CLI와 GUI를 동일 Collection에 동시에 연결하지 않는다.

Collection은 `collection.json`, `receipts/`, `success/`, `correction/`, 과거 `failure/`,
`pending/`을 한 단위로 관리한다. 최초 생성 이후 이동하거나 이름을 변경하지 않는다.
경로를 생략했을 때의 기존 기본값은 변경하지 않았다. 이전 문서의
`datasets/indy7_act_core108`은 기존 경로 예시이며 새 정식 수집에는 위 경로를 명시한다.

이름은 `act<행동번호>_<계획조건수 3자리>_<수집본 일련번호 3자리>`다.
예: `act1_108_001`, `act1_055_001`, `act1_055_002`, `act2_108_001`.
조건 수는 계획값이며 실제 Success/Correction 수는 장부와 Dataset 메타데이터로 확인한다.
현재 Recorder 조건표는108개로 고정돼 있다. `055` 이름만으로55조건 계획을 지원하지 않는다.
55조건·다른 행동 계획 지원은 별도 검토하며 원본108개 조건표는 변경하지 않는다.

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
