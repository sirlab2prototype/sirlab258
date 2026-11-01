"""Real CPU ACT smoke tests, isolated synthetic data, no ROS or robot commands."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import av
import numpy as np
import pytest
import torch
from fractions import Fraction
from lerobot.datasets.lerobot_dataset import LeRobotDataset

from ai import dataset_contract as contract
from ai.train import run_train
from ai.train.checkpoint import verify_checkpoint
from ai.train.pipeline_utils import split_episodes, cache_estimate, fingerprint
from ai.train.validate_dataset import validate_dataset
from ai.train.test.test_validate_dataset import real_base, dataset, sidecar


def arguments(root, output, steps=2):
    return ["--dataset-dir", str(root), "--output-dir", str(output), "--steps", str(steps),
            "--batch-size", "1", "--num-workers", "0", "--chunk-size", "2", "--n-action-steps", "1",
            "--device", "cpu", "--no-use-amp", "--dev-small", "--save-freq", "1", "--log-freq", "1", "--lr", ".001"]


@pytest.fixture(scope="module")
def trained(real_base, tmp_path_factory):
    torch.set_num_threads(2)
    output = tmp_path_factory.mktemp("pipeline") / "run"
    manifest = run_train.main(arguments(real_base, output))
    assert manifest["status"] == "complete"
    return output, manifest, real_base


def test_real_cpu_training_reload_and_validation(trained):
    output, manifest, root = trained
    assert manifest["completed_step"] == 2
    assert manifest["checkpoint_validation"]["chunk_shape"] == [2, 7]
    assert manifest["eligible_for_inference"] and not manifest["robot_safety_verified"]
    assert set(manifest["split"]["train"]).isdisjoint(manifest["split"]["validation"])
    assert manifest["offline_validation"]["status"] == "evaluated"
    assert manifest["offline_validation"]["robot_success_rate"] is None
    config = json.loads((Path(manifest["checkpoint"]) / "train_config.json").read_text())
    assert config["policy"]["chunk_size"] == 2
    assert config["optimizer"]["lr"] == .001
    assert config["dataset"]["episodes"] == manifest["split"]["train"]
    assert not manifest["cache"]["enabled"]
    assert (output / "result.json").exists()
    train_stats = json.loads((output / "train_metadata/stats.json").read_text())
    assert train_stats["action"]["count"] == [3]
    assert fingerprint(root) == manifest["dataset_fingerprint"]


def test_portable_checkpoint_without_dataset(trained, tmp_path, monkeypatch):
    from ai.serve.lerobot_act_backend import LeRobotActBackend
    import lerobot.datasets.dataset_metadata as metadata
    output, manifest, root = trained
    relocated = tmp_path / "moved"
    shutil.copytree(manifest["checkpoint"], relocated)
    def forbidden(*args, **kwargs):
        raise AssertionError("Portable checkpoint must not initialize Dataset metadata")
    monkeypatch.setattr(metadata.LeRobotDatasetMetadata, "__init__", forbidden)
    report = verify_checkpoint(relocated)
    assert report["valid"] and not report["dataset_required"]
    backend = LeRobotActBackend(relocated, device="cpu")
    assert tuple(backend.policy.config.input_features["observation.state"].shape) == (7,)


def test_resume_real_optimizer_and_new_output(trained, tmp_path):
    output, original, root = trained
    checkpoint = Path(original["checkpoint"])
    def hashes():
        values = {}
        for path in output.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    values[str(path.relative_to(output))] = hashlib.file_digest(stream, "sha256").hexdigest()
        return values
    before = hashes()
    result = run_train.main(["--dataset-dir", str(root), "--resume", str(output), "--output-dir", str(tmp_path / "resumed"), "--steps", "3"])
    assert result["completed_step"] == 3 and result["resume_state"]["step"] == 2
    assert result["resume_state"]["optimizer_tensor_keys"] > 0
    assert result["split"] == original["split"]
    assert hashes() == before


@pytest.mark.parametrize("change", ["lr", "chunk", "seed", "dataset"])
def test_resume_mismatch(trained, tmp_path, change):
    output, manifest, root = trained
    args = ["--dataset-dir", str(root), "--resume", str(output), "--steps", "3", "--output-dir", str(tmp_path / "must-not-create")]
    if change == "lr": args += ["--lr", ".002"]
    elif change == "chunk": args += ["--chunk-size", "3"]
    elif change == "seed": args += ["--seed", "4"]
    else:
        changed = tmp_path / "changed"
        shutil.copytree(root, changed)
        (changed / "meta/provenance.json").write_text("{}")
        args[1] = str(changed)
    with pytest.raises(ValueError, match="mismatch"):
        run_train.main(args)
    assert not (tmp_path / "must-not-create").exists()


def test_existing_output_is_preserved(trained):
    output, manifest, root = trained
    with pytest.raises(ValueError, match="existing runs"):
        run_train.main(arguments(root, output))


def test_invalid_checkpoint_files(trained, tmp_path):
    _, manifest, _ = trained
    broken = tmp_path / "broken"
    shutil.copytree(manifest["checkpoint"], broken)
    (broken / "model.safetensors").unlink()
    with pytest.raises(ValueError, match="missing"):
        verify_checkpoint(broken)


def test_corrupted_processor_is_blocked(trained, tmp_path):
    _, manifest, _ = trained
    broken = tmp_path / "processor"
    shutil.copytree(manifest["checkpoint"], broken)
    (broken / "policy_postprocessor.json").write_text("{")
    with pytest.raises(Exception):
        verify_checkpoint(broken)


def test_split_reproducibility_and_pilot():
    first = split_episodes(list(range(10)), seed=8)
    assert first == split_episodes(list(reversed(range(10))), seed=8)
    assert first != split_episodes(list(range(10)), seed=9)
    assert set(first["train"]) | set(first["validation"]) == set(range(10))
    assert not set(first["train"]) & set(first["validation"])
    assert split_episodes([0])["pilot_only"]
    assert split_episodes([0])["validation"] == []


def test_cache_estimate_and_defaults():
    estimate = cache_estimate(20 * 60)
    assert estimate["rgb_bytes"] == 2 * 640 * 480 * 3 * 1200
    assert 2.05 < estimate["rgb_gib"] < 2.07
    assert run_train.resolve_settings(SimpleNamespace())["ram_cache"] is False


def test_hold_baseline_and_chunk_boundary(real_base):
    ds = LeRobotDataset("local/indy7_act", root=real_base, episodes=[0], delta_timestamps={"action": [0, .05, .1]}, video_backend="pyav")
    try:
        baseline = run_train.copy_state_baseline(ds)
        assert baseline["l1_per_dimension"][:6] == pytest.approx([.01] * 6)
        assert baseline["l1_per_dimension"][6] == 0
        # Last frame repeats only this episode's final action and masks padding.
        item = ds[len(ds) - 1]
        assert item["episode_index"].item() == 0
        assert item["action_is_pad"].tolist() == [False, True, True]
        assert torch.equal(item["action"][0], item["action"][2])
        indices, _ = ds._get_query_indices(2, 0)
        assert indices["action"] == [2, 2, 2]
    finally:
        ds.finalize()


@pytest.mark.parametrize("fault", ["late_damage", "pts_gap", "frame_count"])
def test_entire_video_is_checked(dataset, fault):
    path = next(dataset.rglob("*.mp4"))
    if fault == "late_damage":
        raw = path.read_bytes()
        # Damage a later media packet, preserving container metadata/first image.
        with av.open(str(path)) as stream:
            packets = [packet for packet in stream.demux(video=0) if packet.size]
            packet = packets[-1]
            start, size = packet.pos, packet.size
        path.write_bytes(raw[:start] + bytes(size) + raw[start + size:])
        with av.open(str(path)) as stream:
            # The first frame remains decodable: checking only it would pass.
            assert next(stream.decode(video=0)).to_ndarray(format="rgb24").shape == contract.IMAGE_SHAPE
    else:
        with av.open(str(path)) as source:
            images = [frame.to_ndarray(format="rgb24") for frame in source.decode(video=0)]
        if fault == "frame_count": images = images[:-1]
        with av.open(str(path), "w") as container:
            stream = container.add_stream("libx264", rate=20)
            stream.width, stream.height, stream.pix_fmt = 640, 480, "yuv420p"
            for index, image in enumerate(images):
                frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                frame.pts = index * 50 + (50 if fault == "pts_gap" and index >= 1 else 0)
                frame.time_base = Fraction(1, 1000)
                for packet in stream.encode(frame): container.mux(packet)
            for packet in stream.encode(): container.mux(packet)
    report = validate_dataset(dataset)
    assert not report["trainable"], report
    assert report["error_counts"].get("video_decode", 0) + report["error_counts"].get("video_timestamp", 0)
    assert report["video_validation_s"] >= 0


def test_cli_dispatch_and_dry_run(real_base, tmp_path):
    import subprocess
    help_result = subprocess.run(["./scripts/bin/act", "--help"], capture_output=True, text=True, check=True)
    assert "train|validate" in help_result.stdout
    result = subprocess.run(["./scripts/bin/act", "train", *arguments(real_base, tmp_path / "planned"), "--dry-run"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((tmp_path / "planned/run.json").read_text())
    assert manifest["status"] == "planned" and not (tmp_path / "planned/training").exists()
    unknown = subprocess.run(["./scripts/bin/act", "setup"], capture_output=True, text=True)
    assert unknown.returncode == 2


def test_pilot_without_validation_real_training(real_base, tmp_path):
    output = tmp_path / "pilot"
    result = run_train.main([*arguments(real_base, output, steps=1), "--pilot-only", "--ram-cache"])
    assert result["status"] == "complete" and result["split"]["validation"] == []
    assert result["offline_validation"]["status"] == "not_available"
    assert not result["split"]["generalization_validated"]
    assert result["cache"]["enabled"]


def test_trainer_zero_exit_without_checkpoint_is_not_success(real_base, tmp_path, monkeypatch):
    class NoCheckpoint:
        stdout = iter([])
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def wait(self): return 0
    monkeypatch.setattr(run_train, "environment", lambda: {})
    monkeypatch.setattr(run_train.subprocess, "Popen", lambda *args, **kwargs: NoCheckpoint())
    output = tmp_path / "false-success"
    with pytest.raises(FileNotFoundError):
        run_train.main(arguments(real_base, output))
    report = json.loads((output / "result.json").read_text())
    assert report["status"] == "failed" and not report["eligible_for_inference"]


def test_train_only_stats_do_not_use_validation(real_base, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from ai.train.pipeline_utils import train_stats
    changed = tmp_path / "stats-test"
    shutil.copytree(real_base, changed)
    expected = train_stats(changed, [1])
    for path in (changed / "meta/episodes").rglob("*.parquet"):
        table = pq.read_table(path)
        rows = table.to_pylist()
        for row in rows:
            if row["episode_index"] == 0:
                row["stats/action/mean"] = [999.] * 7
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    assert train_stats(changed, [1]) == expected


def test_resume_missing_state_and_corrupt_policy(trained, tmp_path):
    output, _, root = trained
    cloned = tmp_path / "source"
    shutil.copytree(output, cloned, symlinks=True)
    args = run_train.parse_args(["--dataset-dir", str(root), "--steps", "3"])
    state = cloned / "training/checkpoints/000002/training_state/rng_state.safetensors"
    data = state.read_bytes()
    state.unlink()
    with pytest.raises(ValueError, match="missing training_state"):
        run_train.resume_plan(cloned, fingerprint(root), args)
    state.write_bytes(data)
    config = cloned / "training/checkpoints/000002/pretrained_model/config.json"
    value = json.loads(config.read_text())
    value["n_encoder_layers"] = 7
    config.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="config mismatch"):
        run_train.resume_plan(cloned, fingerprint(root), args)
