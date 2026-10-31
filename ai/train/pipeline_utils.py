"""Small reproducibility helpers for the local ACT training pipeline."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import random
import subprocess

import numpy as np
import pyarrow.parquet as pq

from ai import dataset_contract as contract


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def split_episodes(ids, seed=0, validation_fraction=.2, pilot_only=False):
    ids = sorted(ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Episode IDs must be nonempty and unique")
    if not 0 < validation_fraction < 1:
        raise ValueError("Validation fraction must be between 0 and 1")
    if len(ids) < 2 or pilot_only:
        return {"train": ids, "validation": [], "seed": seed, "pilot_only": True,
                "generalization_validated": False}
    shuffled = ids.copy()
    random.Random(seed).shuffle(shuffled)
    count = max(1, min(len(ids) - 1, round(len(ids) * validation_fraction)))
    return {"train": sorted(shuffled[count:]), "validation": sorted(shuffled[:count]),
            "seed": seed, "pilot_only": False, "generalization_validated": False}


def fingerprint(root):
    """Content hash, including metadata/sidecars, excluding runtime HF caches."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    files, byte_count = 0, 0
    for directory in ("meta", "data", "videos"):
        for path in sorted((root / directory).rglob("*")):
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"Dataset file escapes root: {path}")
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            size = path.stat().st_size
            digest.update(str(size).encode() + b"\0")
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            files += 1
            byte_count += size
    return {"algorithm": "sha256-path-size-content-v1", "sha256": digest.hexdigest(), "files": files, "bytes": byte_count}


def train_stats(root, episodes):
    """Reuse official per-episode statistics aggregation; exclude validation."""
    from lerobot.datasets.compute_stats import aggregate_stats
    from lerobot.datasets.utils import unflatten_dict
    selected = []
    for path in sorted((Path(root) / "meta/episodes").glob("*/*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
            for ep in batch.to_pylist():
                if ep["episode_index"] in episodes:
                    flat = {key: np.asarray(value) for key, value in ep.items() if key.startswith("stats/")}
                    selected.append(unflatten_dict(flat)["stats"])
    if len(selected) != len(episodes):
        raise ValueError("Missing per-episode statistics")
    return {key: {name: value.tolist() for name, value in stats.items()}
            for key, stats in aggregate_stats(selected).items()}


def cache_estimate(frames):
    size = frames * len(contract.CAMERA_KEYS) * int(np.prod(contract.IMAGE_SHAPE))
    return {"rgb_bytes": size, "rgb_gib": size / 1024**3,
            "note": "RGB only; tensor chunks, workers, batches, model and optimizer require additional memory"}


def environment():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=Path(__file__).resolve().parents[2], text=True).strip()
    versions = {}
    for package in ("lerobot", "torch", "torchvision", "numpy", "av", "pyarrow", "accelerate", "safetensors"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {"python": platform.python_version(), "platform": platform.platform(), "dependencies": versions,
            "git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain"))}
