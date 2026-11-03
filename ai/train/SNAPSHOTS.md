# ACT 학습 Snapshot

`act snapshot`은 하나 이상의 Collection/성공 Dataset/기존 Snapshot 전체를 새 독립
LeRobot v3 Dataset으로 고정한다. 10/30/50/108 등의 개수는 제한하지 않으며
`--expect-episodes`는 **입력 전체의 성공 Episode 합계**다. 부분 선택·자동 중복 제거는 제공하지 않는다.

## 단일 Collection

Recorder에서 저장을 마친 뒤 해당 Recorder 창을 정상 종료한다. 로봇·QP·카메라는 종료할
필요가 없다. Snapshot은 기존 `.collection.lock`을 읽기 전용 공유 잠금으로 열며,
Recorder가 사용 중이면 복사를 거부한다. Snapshot 완료 후 같은 Collection을 재개할 수 있다.

```bash
act snapshot \
  --collection outputs/preserved/datasets/act1_108_001 \
  --name act1_first10_v1 --expect-episodes 10
```

결과는 `outputs/training_snapshots/act1_first10_v1/` **자체가 Dataset 루트**다.
`success/` 전체의 Parquet·MP4·Metadata·Recorder Timing을 일반 파일로 복사한다.
하드링크/심볼릭 링크, Correction/Failure/Pending·Collection 장부 복사는 없다.
원본 Dataset identity와 내부 번호를 유지하며 별도의 `snapshot_id`를 manifest에 기록한다.

## 다중 입력

`--collection`과 `--dataset`을 반복할 수 있다. Collection 입력을 지정한 순서로 먼저 처리하고,
그 뒤 Dataset 입력을 지정한 순서로 처리한다. Dataset은 saved success Timing을 갖춰야 하며
Correction/Failure는 거부한다. 동일 실제 경로의 중복 입력은 거부한다.

```bash
act snapshot \
  --collection outputs/preserved/datasets/act1_108_001 \
  --collection outputs/preserved/datasets/act1_108_002 \
  --name act1_combined_v1 --expect-episodes 30

# 이미 만든 Snapshot도 입력 가능
act snapshot \
  --dataset outputs/training_snapshots/act1_first10_v1 \
  --dataset outputs/training_snapshots/act1_next20_v1 \
  --name act1_snapshots30_v1 --expect-episodes 30
```

다중 입력은 설치된 LeRobot 0.5.1의 공식 `aggregate_datasets`를 호출한다.
Episode/global Frame/Task 번호, Parquet·영상 경로·통계는 공식 API가 재작성한다.
영상은 공식 API의 packet remux로 연결하며 전체 프레임 디코딩/재인코딩을 하지 않는다.
입력 간 features 전체·FPS·robot_type이 같아야 한다. 출력 repo id는 `local/<name>`다.
공식 API의 캐시와 임시 파일은 이번 작업의 숨겨진 staging 안에 격리하고 원격 접근을 금지한다.

**겹치는 Snapshot을 병합하면 같은 원본 시연이 중복 포함된다.** 자동으로 제거하지 않는다.
입력 Snapshot이 이미 병합본이어도 각 Episode의 최초 원본 출처를 manifest에서 이어받는다.

## Timing과 출처

`meta/recorder_timing/episode-NNNNNN/frames.jsonl`은 원문 바이트를 유지한다.
병합 시 출력 폴더 번호와 `episode.json`의 dataset_episode_index/repo_id만 새 Dataset에 맞춘다.
추가 `snapshot_source`는 원본 연결이고, 원래 episode.json도
`snapshot_provenance/episode-NNNNNN.json`에 그대로 보존한다. 원본 Collection/Receipt는 쓰지 않는다.

루트 `snapshot_manifest.json`에는 다음을 기록한다.

- Snapshot ID/이름/생성 UTC 시각/경로, v3와 출력 repo id, 복사 또는 공식 병합 방식.
- 입력 Collection/Dataset 경로·repo id·기존 Snapshot ID·Episode/Frame 수.
- 각 입력 Episode → 출력 Episode 매핑, 최초 Collection/Dataset/Episode/Attempt/Receipt 출처.
- 출력 Episode/전체 Frame 수와 상대 파일명·크기 목록, 실행한 검증 범위.

Manifest와 provenance는 표준 Dataset 구조 바깥의 추가 파일이며 LeRobot loader/공식 병합을
방해하지 않는다. 내부 metadata를 임의로 변환하거나 Recorder/Home/학습기를 변경하지 않는다.

## 학습

Manifest의 `dataset_repo_id`를 명시한다. 단일 입력은 원본 id를 유지한다.
아래는 **사용자가 별도로 실행할 학습 명령**이며 Snapshot 생성 시 학습은 시작하지 않는다.

```bash
# 위 단일 입력의 원본 repo id가 local/indy7_act인 경우
act train --dataset-dir outputs/training_snapshots/act1_first10_v1 \
  --dataset-repo-id local/indy7_act \
  --output-dir outputs/temporary/training/act1_first10_v1_run1

# 병합 출력의 repo id는 local/<name>
act train --dataset-dir outputs/training_snapshots/act1_combined_v1 \
  --dataset-repo-id local/act1_combined_v1 \
  --output-dir outputs/temporary/training/act1_combined_v1_run1
```

Snapshot 생성은 복사 전후 파일 목록·크기·inode/mtime/ctime와 Collection 장부 변경 징후를
확인하고 기존 Validator의 metadata/data 검사로 schema·인덱스·통계·Sidecar 출력 연결을
확인한다. 원본 Receipt 전체 해시, 영상 전체 디코딩, Recorder 시각/Home 정밀 검사는
반복하지 않는다. 향후 실제 `act train`은 기존 학습 preflight를 그대로 수행한다.
Snapshot 단계의 제한된 검증을 영상 전체 재검증 완료로 표시하지 않는다.

같은 이름은 빈 디렉터리까지 덮어쓰지 않는다. Linux `renameat2(RENAME_NOREPLACE)`로 완성된
결과만 원자적으로 공개한다. 실패 시 이번 호출의 staging만 정리한다. 강제 종료 시 남을 수 있는
숨겨진 `.이름.copying-*`은 최종 Snapshot이 아니며 기존 자료를 자동 삭제하지 않는다.
Snapshot에는 자동 read-only chmod를 걸지 않으므로 직접 수정하지 말고 별도 이름을 생성한다.

## 이번 확인 (2026-11-03)

정식 act1_108_001은 성공0개, act_gui_integration_test는 성공4개였다.
`test_gui_success4_v1`은4개/3598프레임의 단일 복사,
`test_merge_gui4_plus_copy4_v1`은 원본4개+그 복사4개인8개/7196프레임의 **중복 포함 병합 테스트**다.
정식8개 시연이나 새로운10개 Snapshot으로 취급하지 않는다.
둘 다 공식 LeRobotDataset 로딩과 원문 Timing 보존을 확인했다. 학습·추론·ROS·장비 접근은 없었다.
