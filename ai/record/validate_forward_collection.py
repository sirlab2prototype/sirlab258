"""Read-only post-save check for a forward-only Indy7 ACT Collection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from ai.record.collection import dataset_inventory
from ai.record.dataset_manager import read_state
from ai.train.validate_dataset import validate_dataset

DEG = 180 / np.pi


def check(directory: Path) -> bool:
    directory = directory.expanduser().absolute()
    state = read_state(directory)
    definition = state.get("definition", {})
    conditions = state["conditions"]
    if definition.get("plan", {}).get("kind") != "custom" or not all(
        c.get("fields", {}).get("recording_scope") == "forward_approach_only" for c in conditions
    ):
        raise ValueError("Forward-only custom Collection이 아닙니다")
    success = sorted((a for a in state["attempts"] if a["status"] == "SUCCESS"),
                     key=lambda a: a["dataset_episode_index"])
    if [a["dataset_episode_index"] for a in success] != list(range(len(success))):
        raise ValueError("SUCCESS Episode 번호가 연속하지 않습니다")
    completed = {a["condition_id"] for a in success}
    next_condition = next((c for c in conditions if c["condition_id"] not in completed), None)
    print(f"SUCCESS {len(success)}/{len(conditions)} | 마지막 Episode {len(success)-1 if success else '없음'}"
          f" | 다음 조건 {next_condition['collection_order'] if next_condition else '완료'}"
          f" | 다음 SUCCESS Episode {len(success)}")
    root = directory / "success"
    print(f"저장 경로: {root}")
    if not success:
        print("저장된 Episode 없음; 실제 시연 검증은 수집 후 실행")
        return True

    report = validate_dataset(root, repo_id="local/indy7_act")
    print(f"기존 Dataset Validator: {'통과' if report['trainable'] else '실패'}"
          f" | 프레임 {report['total_frames']} | RGB 디코드 {report['decoded_video_frames']}"
          f" | 오류 {dict(report['error_counts'])}")
    inventory = dataset_inventory(root)
    if len(inventory) != len(success):
        raise ValueError("Collection 장부와 Dataset Episode 수가 다릅니다")
    good = bool(report["trainable"])
    endpoints = []
    for attempt in success:
        e = attempt["dataset_episode_index"]
        sidecar = root / f"meta/recorder_timing/episode-{e:06d}"
        meta = json.loads((sidecar / "episode.json").read_text())
        timing = [json.loads(line) for line in (sidecar / "frames.jsonl").open()]
        rows = [row for row in pq.read_table(root / inventory[e]["data"],
                columns=["episode_index", "observation.state", "action"]).to_pylist()
                if row["episode_index"] == e]
        action = np.asarray([row["action"] for row in rows], dtype=np.float64)
        measured = np.asarray([row["observation.state"] for row in rows], dtype=np.float64)
        endpoints.append(measured[-1, :6])
        n = len(rows)
        displacement = np.max(np.abs(action[:, :6] - action[0, :6]), axis=1) * DEG
        onset = next((i for i in range(max(0, n - 5)) if np.all(displacement[i:i+6] >= .25)), None)
        moved = (n >= 2 and measured[-1, 1] < measured[0, 1] - np.deg2rad(.25)
                 and measured[-1, 2] > measured[0, 2] + np.deg2rad(.25))
        open_only = bool(np.all(action[:, 6] == 0) and np.all(measured[:, 6] == 0))
        # A newly completed Home may still be the initial latched action. That
        # is allowed; a NEW Home generation or return during this take is not.
        start_context = timing[0]["recorded_action"].get("action_context") or {}
        initial_generation = start_context.get("home_generation")
        start_system_ns = meta["start_clock_observation"]["sample_system_time_ns"]
        forward_only = all(
            (context := row["recorded_action"].get("action_context") or {}).get("home_generation") == initial_generation
            and (context.get("phase") == "QP_TELEOP" or
                 (context.get("phase") == "HOME_COMPLETE" and context.get("transition_ns", start_system_ns+1) <= start_system_ns))
            for row in timing)
        home_source = meta.get("home_action_acceptance") == "live_qdes_profile_v1" and bool(meta.get("home_capture_profile"))
        episode_ok = (n == attempt["frames"] == meta["frames"] == len(timing) and
                      meta.get("integrity", {}).get("valid") is True and
                      meta.get("home_return_required") is False and home_source and
                      onset is not None and moved and open_only and forward_only and
                      (directory / "receipts" / f"{attempt['attempt_id']}.json").is_file())
        good &= episode_ok
        print(f"Episode {e:02d} | 조건 {attempt['condition_id']:02d} | {n}프레임"
              f" | 첫 지속 Action 이동 {onset if onset is not None else '없음'}프레임"
              f" | 실측 J2/J3 종점 변화 {((measured[-1,1:3]-measured[0,1:3])*DEG).round(2).tolist()}°"
              f" | Open만 {'예' if open_only else '아니오'}"
              f" | 녹화 내 Home 없음 {'예' if forward_only else '아니오'}"
              f" | {'통과' if episode_ok else '검토 필요'}")
    if len(endpoints) > 1:
        spread = np.ptp(np.asarray(endpoints), axis=0) * DEG
        print(f"종료 실측 자세 범위(6축 최대−최소): {spread.round(2).tolist()}°")
    print("바구니 위치·접근 경로·실제 작업 성공은 작업자가 별도로 확인해야 합니다.")
    return good


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        return 0 if check(args.collection_dir) else 1
    except (ValueError, OSError, KeyError, IndexError) as exc:
        print(f"수집 검증 실패: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
