"""Delegate to the vendored LeRobot trainer for the RGB-only dataset."""

from __future__ import annotations

from lerobot.scripts import lerobot_train
import os

from ai import dataset_contract as contract

def main():
    stats_root = os.environ.get("INDY7_ACT_TRAIN_STATS")
    if not stats_root:
        raise ValueError("Use act train: verified Train-only statistics are required")
    original = lerobot_train.make_dataset

    def make_training_dataset(cfg):
        from lerobot.datasets.io_utils import cast_stats_to_numpy, load_json
        from pathlib import Path
        dataset = original(cfg)
        expected = contract.dataset_features()
        if dataset.meta.fps != contract.FPS or any(
                any(dataset.meta.features.get(key, {}).get(field) != value[field] for field in ("shape", "names", "dtype"))
                for key, value in expected.items()):
            raise ValueError("Dataset features changed before training")
        if {int(value) for value in dataset.hf_dataset["episode_index"]} != set(cfg.dataset.episodes):
            raise ValueError("Training episode filter mismatch")
        # This is a standalone Train-only stats file, not a Dataset root.
        # load_stats(root) searches root/meta/stats.json and would return None.
        dataset.meta.stats = cast_stats_to_numpy(load_json(Path(stats_root) / "stats.json"))
        return dataset

    lerobot_train.make_dataset = make_training_dataset
    original_processors = lerobot_train.make_pre_post_processors
    def make_training_processors(*args, **kwargs):
        from ai.train.processor_contract import verify_processors
        processors = original_processors(*args, **kwargs)
        verify_processors(*processors)
        return processors
    lerobot_train.make_pre_post_processors = make_training_processors
    weight=float(os.environ.get('INDY7_ACT_EXECUTION_PREFIX_WEIGHT','1'))
    if weight!=1:
        from ai.train.execution_prefix_loss import install
        original_policy=lerobot_train.make_policy
        def make_weighted_policy(*args,**kwargs):return install(original_policy(*args,**kwargs),weight)
        lerobot_train.make_policy=make_weighted_policy
    lerobot_train.train()


if __name__ == "__main__":
    main()
