"""Offline tests. All created data lives in pytest's project-local basetemp."""
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ai import dataset_contract as contract
from ai.train.validate_dataset import validate_dataset, main
# Reuse the existing ROS-stub fixture; never initialize a ROS node.
from ai.record.test.test_indy7_act_record import recorder, HistoryHarness, MONO


def edit_json(path, mutate):
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def edit_parquet(root, subtree, mutate):
    path = next((root / subtree).rglob("*.parquet"))
    table = pq.read_table(path)
    rows = table.to_pylist()
    mutate(rows)
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)


@pytest.fixture(scope="module")
def real_base(tmp_path_factory):
    """Use actual Recorder commit + LeRobot 0.5.1 video encoding, not a mock."""
    # Fixture setup is temporarily invoked with a MonkeyPatch for the ROS stubs.
    patch = pytest.MonkeyPatch()
    generator = recorder.__wrapped__(patch)
    module = generator
    module.IMAGE_SHAPE = contract.IMAGE_SHAPE
    root = tmp_path_factory.mktemp("actual-recorder")
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    dataset_root = root / "success"
    def factory(outcome):
        if dataset_root.exists():
            return LeRobotDataset("local/indy7_act", root=dataset_root, vcodec="h264")
        return LeRobotDataset.create(repo_id="local/indy7_act", root=dataset_root,
                                     robot_type="indy7", fps=20, features=module._features(),
                                     use_videos=True, image_writer_threads=0, vcodec="h264")
    session = module.EpisodeSession(root / "pending", factory, 20, "basket")
    for episode in range(2):
        started = (10 + episode) * 1_000_000_000
        session.start(np.zeros(7, dtype=np.float32), started / 1e9,
                      timing={"sample_monotonic_ns": started})
        for i in range(3):
            sample_ns = started + (i + 1) * 50_000_000
            frame = {**{key: np.full(contract.IMAGE_SHAPE, i * 30 + episode, dtype=np.uint8)
                        for key in contract.CAMERA_KEYS},
                     "observation.state": np.array([i * .01] * 6 + [i % 2], dtype=np.float32),
                     "action": np.array([i * .02] * 6 + [i % 2], dtype=np.float32), "task": "basket"}
            session.add_frame(frame, {"sample_monotonic_ns": sample_ns, "sources": {}},
                              session.deadline.consume(sample_ns))
        session.stop(sample_ns)
        save_success_and_verify(session, dataset_root, episode)
    patch.undo()
    yield dataset_root


@pytest.fixture
def dataset(real_base, tmp_path):
    root = tmp_path / "dataset"
    shutil.copytree(real_base, root)
    return root


def sidecar(root, episode=0):
    return root / contract.TIMING_DIRECTORY / f"episode-{episode:06d}"


def save_success_and_verify(session, root, episode_index):
    """Check the real commit and timing/video evidence, independent of UI language."""
    frames, pending = session.frames, session.path
    message = session.save("success", root)
    assert not session.save_failed, message
    assert session.state == "READY" and session.path is None, message
    assert session.last_saved == {
        "outcome": "success", "dataset_episode_index": episode_index, "condition_id": None,
    }, message
    assert not pending.exists(), message

    info = json.loads((root / "meta/info.json").read_text())
    assert info["total_episodes"] == episode_index + 1
    metadata = json.loads((sidecar(root, episode_index) / "episode.json").read_text())
    assert metadata["status"] == "saved" and metadata["outcome"] == "success"
    assert metadata["dataset_episode_index"] == episode_index and metadata["frames"] == frames
    assert metadata["integrity"] == {"valid": True, "violations": []}
    assert metadata["demonstration"]["type"] == "normal"
    timing = [json.loads(line) for line in
              (sidecar(root, episode_index) / "frames.jsonl").read_text().splitlines()]
    assert [row["frame_index"] for row in timing] == list(range(frames))

    # The read-only Validator checks Parquet, every referenced video frame and
    # timing sidecars, including the exact Episode index and timestamp grid.
    report = validate_dataset(root, repo_id=contract.SUCCESS_REPO_ID)
    assert report["trainable"], report
    assert report["total_episodes"] == report["success_episodes"] == episode_index + 1
    assert report["total_frames"] == info["total_frames"]
    assert report["decoded_video_frames"] == report["total_frames"] * len(contract.CAMERA_KEYS)
    assert report["episode_manifest"][-1]["episode_index"] == episode_index
    assert report["episode_manifest"][-1]["length"] == frames
    return report


def fingerprint(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob("*") if p.is_file()}


def test_real_dataset_passes_without_writes_or_download(dataset, monkeypatch):
    import lerobot.datasets.dataset_metadata as metadata
    import lerobot.datasets.lerobot_dataset as datasets
    def forbidden(*args, **kwargs):
        raise AssertionError("Dataset constructor / network must not be called")
    monkeypatch.setattr(metadata, "snapshot_download", forbidden)
    monkeypatch.setattr(metadata, "get_safe_version", forbidden)
    monkeypatch.setattr(datasets.LeRobotDataset, "__init__", forbidden)
    before = fingerprint(dataset)
    report = validate_dataset(dataset, repo_id="local/indy7_act")
    assert report["trainable"], report
    assert report["total_episodes"] == report["success_episodes"] == 2
    assert report["total_frames"] == 6 and report["decoded_video_frames"] == 12
    assert report["nominal_duration_s"] == .3
    assert report["statistics"]["action"]["gripper_distribution"] == {"0.0": 4, "1.0": 2}
    assert fingerprint(dataset) == before
    assert report["episode_manifest"][1]["dataset_from_index"] == 3


@pytest.mark.parametrize("mutation", ["one_camera", "wrong_name", "state_6d", "action_10d", "fps", "order"])
def test_schema_rejections(dataset, mutation):
    def mutate(info):
        features = info["features"]
        if mutation == "one_camera":
            features.pop(contract.CAMERA_KEYS[1])
        elif mutation == "wrong_name":
            features["observation.images.other"] = features.pop(contract.CAMERA_KEYS[0])
        elif mutation == "state_6d":
            features["observation.state"]["shape"] = [6]
        elif mutation == "action_10d":
            features["action"]["shape"] = [10]
        elif mutation == "order":
            features["action"]["names"][0:2] = ["joint1", "joint0"]
        else:
            info["fps"] = 30
    edit_json(dataset / "meta/info.json", mutate)
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["error_counts"]["schema"]


@pytest.mark.parametrize("mutation,code", [("failure", "outcome"), ("unknown", "outcome"),
    ("invalid", "integrity"), ("violations", "integrity"), ("missing_integrity", "integrity"),
    ("index", "sidecar"), ("missed", "timestamp"), ("skip", "timestamp")])
def test_sidecar_rejections(dataset, mutation, code):
    def mutate(meta):
        if mutation == "failure": meta["outcome"] = "failure"
        elif mutation == "unknown": meta.pop("outcome")
        elif mutation == "invalid": meta["integrity"]["valid"] = False
        elif mutation == "violations": meta["integrity"]["violations"] = [{"reason": "gap"}]
        elif mutation == "missing_integrity": meta.pop("integrity")
        elif mutation == "index": meta["dataset_episode_index"] = 99
        elif mutation == "missed": meta["timing_summary"]["missed_deadlines"] = 1
        else: meta["timing_summary"]["stale_sample_skips"] = 1
    edit_json(sidecar(dataset) / "episode.json", mutate)
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["error_counts"][code]


@pytest.mark.parametrize("target", ["meta/stats.json", "meta/tasks.parquet", "sidecar", "timing", "video", "parquet"])
def test_missing_files(dataset, target):
    path = {"sidecar": sidecar(dataset) / "episode.json", "timing": sidecar(dataset) / "frames.jsonl"}.get(target)
    if target == "video": path = next(dataset.rglob("*.mp4"))
    elif target == "parquet": path = next((dataset / "data").rglob("*.parquet"))
    elif path is None: path = dataset / target
    path.unlink()
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["missing_files"] >= 1


def test_video_decode_error(dataset):
    next(dataset.rglob("*.mp4")).write_bytes(b"broken video")
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["error_counts"]["video_decode"]


@pytest.mark.parametrize("key,value", [("observation.state", float("nan")), ("action", float("inf")),
                                     ("action", -.1), ("observation.state", 2), ("action", .5)])
def test_bad_values(dataset, key, value):
    edit_parquet(dataset, "data", lambda rows: rows[0][key].__setitem__(6, value))
    result = validate_dataset(dataset)
    assert not result["trainable"]
    assert result["error_counts"].get("value", 0) + result["error_counts"].get("gripper", 0) > 0


@pytest.mark.parametrize("mutation,code", [("frame_missing", "frame_count"), ("timestamp", "timestamp"),
    ("frame_index", "frame_index"), ("episode_index", "frame_index"), ("global_index", "frame_index")])
def test_data_axis_rejections(dataset, mutation, code):
    def mutate(rows):
        if mutation == "frame_missing": rows.pop(1)
        elif mutation == "timestamp": rows[1]["timestamp"] = .15
        elif mutation == "frame_index": rows[1]["frame_index"] = 4
        elif mutation == "episode_index": rows[1]["episode_index"] = 5
        else: rows[1]["index"] = 9
    edit_parquet(dataset, "data", mutate)
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["error_counts"][code]


def test_episode_metadata_index(dataset):
    edit_parquet(dataset, "meta/episodes", lambda rows: rows[0].__setitem__("episode_index", 42))
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["error_counts"]["episode_index"]


def test_sidecar_time_gap(dataset):
    path = sidecar(dataset) / "frames.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1]["dataset_nominal_timestamp_s"] = .15
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["timestamp_errors"]


def test_absent_dataset_does_not_create_directory(tmp_path):
    path = tmp_path / "not-created"
    result = validate_dataset(path)
    assert not result["trainable"] and not path.exists()


def test_empty_dataset(tmp_path):
    result = validate_dataset(tmp_path)
    assert not result["trainable"] and result["total_frames"] == 0


def test_zero_episode_metadata(dataset):
    edit_json(dataset / "meta/info.json", lambda info: info.update(total_episodes=0, total_frames=0))
    for path in (dataset / "meta/episodes").rglob("*.parquet"):
        table = pq.read_table(path)
        pq.write_table(table.slice(0, 0), path)
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["error_counts"]["empty_dataset"]


def test_cli_report_and_original_protection(dataset, tmp_path, capsys):
    output = tmp_path / "report.json"
    assert main(["--dataset-dir", str(dataset), "--report", str(output)]) == 0
    assert json.loads(output.read_text())["trainable"]
    assert "Training: ALLOWED" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--dataset-dir", str(dataset), "--report", str(dataset / "report.json")])
    assert not (dataset / "report.json").exists()


def test_contract_matches_recorder_and_returns_fresh_dictionaries(recorder):
    assert recorder._features() == contract.dataset_features((32, 32, 3))
    expected = contract.dataset_features()
    expected["action"]["names"][0] = "wrong"
    assert contract.dataset_features()["action"]["names"][0] == "joint0"


@pytest.fixture
def bounded_dataset(recorder, tmp_path):
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    recorder.IMAGE_SHAPE = contract.IMAGE_SHAPE
    harness = HistoryHarness(recorder, tmp_path)
    root = tmp_path / "bounded"
    harness.session.dataset_factory = lambda outcome: LeRobotDataset.create(
        repo_id="local/indy7_act", root=root, robot_type="indy7", fps=20,
        features=recorder._features(), use_videos=True, image_writer_threads=0, vcodec="h264")
    harness.begin()
    harness.generate(500)
    harness.tick(50)
    harness.tick(100)
    # Exactly one pending release may be cancelled by r in the real Recorder.
    harness.session.finish_aligned(harness.history.snapshot(MONO + 210_000_000), harness.timing(210))
    assert harness.session.integrity["valid"]
    save_success_and_verify(harness.session, root, 0)
    return root


def test_real_bounded_sidecar_and_cancelled_final_release(bounded_dataset):
    result = validate_dataset(bounded_dataset)
    assert result["trainable"], result
    assert result["episode_actual_fps_range"] == [20, 20]
    assert json.loads((sidecar(bounded_dataset) / "episode.json").read_text())["timing_summary"]["unprocessed_deadlines_at_stop"] == 1


@pytest.mark.parametrize("mutation", ["grid", "future_camera", "stale_state", "selection", "future_gripper", "future_action", "missing_source"])
def test_bounded_alignment_corruption(bounded_dataset, mutation):
    path = sidecar(bounded_dataset) / "frames.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    row = rows[1]
    if mutation == "grid": row["alignment"]["reference_monotonic_ns"] += 50_000_000
    elif mutation == "future_camera": row["sources"][contract.CAMERA_NAMES[0]]["header_stamp"]["sec"] += 1
    elif mutation == "stale_state": row["sources"]["state"]["reference_age_ns"] += 1
    elif mutation == "selection": row["alignment"]["selection_valid"] = False
    elif mutation == "future_gripper": row["sources"]["gripper"]["callback_monotonic_ns"] += 1_000_000_000
    elif mutation == "missing_source": row["sources"].pop("state")
    else:
        row["recorded_action"] = {"kind": "qp_target", "source": {"header_stamp": {"sec": 1_800_000_000, "nanosec": 0}}}
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    result = validate_dataset(bounded_dataset)
    assert not result["trainable"] and result["timestamp_errors"] + result["error_counts"].get("sidecar", 0) + result["error_counts"].get("integrity", 0)


def test_real_shared_video_and_parquet_files(real_base, tmp_path):
    """v3 can pack multiple episodes in one file; do not assume one file/episode."""
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    root = tmp_path / "packed"
    ds = LeRobotDataset.create(repo_id="local/indy7_act", root=root, robot_type="indy7", fps=20,
                              features=contract.dataset_features(), use_videos=True, image_writer_threads=0, vcodec="h264")
    for episode in range(2):
        for i in range(3):
            ds.add_frame({**{key: np.full(contract.IMAGE_SHAPE, i * 30 + episode, dtype=np.uint8) for key in contract.CAMERA_KEYS},
                          "observation.state": np.array([i * .01] * 6 + [i % 2], dtype=np.float32),
                          "action": np.array([i * .02] * 6 + [i % 2], dtype=np.float32), "task": "basket"})
        ds.save_episode(parallel_encoding=False)
    ds.finalize()
    shutil.copytree(real_base / contract.TIMING_DIRECTORY, root / contract.TIMING_DIRECTORY)
    result = validate_dataset(root)
    assert result["trainable"], result
    assert len(list((root / "data").rglob("*.parquet"))) == 1
    assert len(list(root.rglob("*.mp4"))) == 2
    # Also exercise the real Dataset read API in an isolated test-only root.
    loaded = LeRobotDataset("local/indy7_act", root=root, video_backend="pyav")
    assert tuple(loaded[0][contract.CAMERA_KEYS[1]].shape) == (3, 480, 640)
    assert loaded.meta.total_episodes == 2
    loaded.finalize()


def test_bad_physical_vector_type(dataset):
    path = next((dataset / "data").rglob("*.parquet"))
    table = pq.read_table(path)
    position = table.schema.get_field_index("action")
    table = table.set_column(position, "action", table.column("action").cast(pa.list_(pa.float64(), 7)))
    pq.write_table(table, path)
    report = validate_dataset(dataset)
    assert not report["trainable"] and report["error_counts"]["schema"]


def test_stale_normalization_stats(dataset):
    edit_json(dataset / "meta/stats.json", lambda stats: stats["action"].__setitem__("mean", [100.] * 7))
    result = validate_dataset(dataset)
    assert not result["trainable"] and result["error_counts"]["statistics"]


def test_train_wrapper_blocks_failure_before_dataset_constructor(dataset, monkeypatch):
    import types
    from ai.train import run_train
    edit_json(sidecar(dataset) / "episode.json", lambda data: data.update(outcome="failure"))
    monkeypatch.setattr(run_train, "parse_args", lambda: types.SimpleNamespace(dataset_dir=dataset, dataset_repo_id="local/indy7_act"))
    def forbidden(*args, **kwargs):
        raise AssertionError("Must not load Dataset or start training after failed validation")
    monkeypatch.setattr(run_train, "LeRobotDataset", forbidden)
    monkeypatch.setattr(run_train.subprocess, "run", forbidden)
    with pytest.raises(SystemExit, match="blocked training"):
        run_train.main()


def test_bad_input_paths_and_malformed_json(dataset, tmp_path):
    file = tmp_path / "plain-file"
    file.write_text("file is not a Dataset")
    assert not validate_dataset(file)["trainable"]
    (dataset / "meta/info.json").write_text("{")
    assert not validate_dataset(dataset)["trainable"]


def test_report_never_overwrites_existing_file(dataset, tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("keep")
    with pytest.raises(FileExistsError):
        main(["--dataset-dir", str(dataset), "--report", str(output)])
    assert output.read_text() == "keep"


def test_repo_identity_and_video_path_escape(dataset):
    assert not validate_dataset(dataset, repo_id="local/wrong")["trainable"]
    edit_json(dataset / "meta/info.json", lambda data: data.update(video_path="../../not-allowed.mp4"))
    report = validate_dataset(dataset)
    assert not report["trainable"] and report["error_counts"]["validation_read"]
