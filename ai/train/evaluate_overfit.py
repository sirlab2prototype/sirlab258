#!/usr/bin/env python3
"""Offline physical-unit ACT error; never a real-robot task success rate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import numpy as np
import torch
from torch.utils.data import DataLoader
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from ai import dataset_contract as contract
from ai.serve.lerobot_act_backend import LeRobotActBackend
from ai.train.checkpoint import assert_policy_contract
from ai.train.pipeline_utils import write_json

def _empty():
    return {"joint_abs": np.zeros(6), "hold_joint_abs": np.zeros(6), "joint_samples": 0,
            "gripper_abs": 0., "gripper_correct": 0, "hold_gripper_correct": 0, "gripper_samples": 0}


def _summary(item):
    n, g = item["joint_samples"], item["gripper_samples"]
    per_joint = item["joint_abs"] / n
    hold = item["hold_joint_abs"] / n
    return {"joint_mae_rad": float(per_joint.mean()), "joint_mae_per_dimension_rad": per_joint.tolist(),
            "hold_joint_mae_rad": float(hold.mean()), "hold_joint_mae_per_dimension_rad": hold.tolist(),
            "gripper_mae": item["gripper_abs"] / g, "gripper_accuracy_at_0_5": item["gripper_correct"] / g,
            "hold_gripper_accuracy": item["hold_gripper_correct"] / g, "valid_chunk_positions": n,
            "beats_joint_hold_baseline": bool(per_joint.mean() < hold.mean())}


def evaluate_checkpoint(checkpoint, dataset_root, repo_id, episodes, *, device="cpu", batch_size=8):
    if not episodes:
        return {"status": "not_available", "reason": "Pilot run has no held-out episodes",
                "generalization_validated": False, "robot_success_rate": None}
    backend = LeRobotActBackend(checkpoint, device=device)
    assert_policy_contract(backend.policy.config)
    offsets = [i / contract.FPS for i in range(backend.policy.config.chunk_size)]
    ds = LeRobotDataset(repo_id, root=Path(dataset_root), episodes=episodes,
                        delta_timestamps={"action": offsets}, video_backend="pyav")
    if {int(value) for value in ds.hf_dataset["episode_index"]} != set(episodes):
        raise ValueError("Validation episode filter mismatch")
    totals, per_episode = _empty(), {int(ep): _empty() for ep in episodes}
    try:
        with torch.inference_mode():
            for physical in DataLoader(ds, batch_size=batch_size, shuffle=False, num_workers=0):
                targets = physical["action"].detach().cpu().clone()
                states = physical["observation.state"].detach().cpu().clone()
                valid = ~physical["action_is_pad"].cpu()
                batch = backend.preprocessor(physical)
                prediction = backend.postprocessor(backend.policy.predict_action_chunk(batch)).cpu()
                if prediction.shape != targets.shape or not torch.isfinite(prediction).all():
                    raise ValueError("Invalid validation action output")
                for i, ep in enumerate(physical["episode_index"].tolist()):
                    predicted = prediction[i][valid[i]]
                    target = targets[i][valid[i]]
                    hold = states[i].expand_as(target)
                    for accum in (totals, per_episode[int(ep)]):
                        accum["joint_abs"] += (predicted[:, :6] - target[:, :6]).abs().sum(0).numpy()
                        accum["hold_joint_abs"] += (hold[:, :6] - target[:, :6]).abs().sum(0).numpy()
                        accum["joint_samples"] += len(target)
                        accum["gripper_abs"] += (predicted[:, 6] - target[:, 6]).abs().sum().item()
                        accum["gripper_correct"] += ((predicted[:, 6] >= .5) == (target[:, 6] >= .5)).sum().item()
                        accum["hold_gripper_correct"] += ((hold[:, 6] >= .5) == (target[:, 6] >= .5)).sum().item()
                        accum["gripper_samples"] += len(target)
    finally:
        ds.finalize()
    return {"status": "evaluated", "metric": "offline unnormalized action chunk error, padding masked",
            "episodes": episodes, "summary": _summary(totals),
            "per_episode": {str(ep): _summary(item) for ep, item in per_episode.items()},
            "generalization_validated": False, "robot_success_rate": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--dataset-repo-id", default=contract.SUCCESS_REPO_ID)
    parser.add_argument("--episodes", required=True, help="JSON list of held-out episode IDs")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    from ai.train.validate_dataset import validate_dataset
    report = validate_dataset(args.dataset_dir, repo_id=args.dataset_repo_id)
    if not report["trainable"]:
        raise SystemExit("Dataset validation blocked evaluation")
    if args.output.exists() or args.output.resolve().is_relative_to(Path(args.dataset_dir).resolve()):
        raise SystemExit("Evaluation output must be a new file outside Dataset")
    result = evaluate_checkpoint(args.checkpoint, args.dataset_dir, args.dataset_repo_id, json.loads(args.episodes),
                                 device=args.device, batch_size=args.batch_size)
    write_json(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
