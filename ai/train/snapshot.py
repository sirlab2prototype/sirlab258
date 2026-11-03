"""Create a standalone ACT Snapshot from success Collections/Datasets."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import subprocess
import sys
import uuid

from ai.record.collection import dataset_inventory

PROJECT = Path(__file__).resolve().parents[2]
SNAPSHOTS = PROJECT / "outputs/training_snapshots"


def inside_project(path):
    path = Path(path).expanduser().absolute()
    if path.resolve() != path or not path.is_relative_to(PROJECT):
        raise ValueError(f"Use a real path inside this project, without symlinks: {path}")
    return path


def signature(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError(f"Only regular files are supported: {path}")
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def inventory(root):
    """No content hashing: detect replaced, modified, added or removed files."""
    result = {}
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in folders:
            if (Path(directory) / name).is_symlink():
                raise ValueError(f"Symlinks are not supported: {Path(directory) / name}")
        for name in files:
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = signature(path)
    return result


def copy_file(source, target, expected):
    # O_NOFOLLOW also protects the open against a replaced final path component.
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as src:
        s = os.fstat(src.fileno())
        if (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) != expected:
            raise ValueError(f"Source changed before copying: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        s = os.fstat(src.fileno())
        if (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) != expected:
            raise ValueError(f"Source changed while copying: {source}")


def publish(staging, destination):
    """Linux atomic rename, refusing even an existing empty destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def describe_input(path, locks):
    path = inside_project(path)
    collection = path if (path / "collection.json").is_file() else None
    if collection is None and path.name == "success" and (path.parent / "collection.json").is_file():
        collection = path.parent
    source = inside_project(collection / "success") if collection else path
    ledger, ledger_signature = None, None
    if collection:
        lock = locks.enter_context((collection / ".collection.lock").open("rb"))
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"Close this Collection's Recorder after saving: {collection}") from None
        ledger_signature = signature(collection / "collection.json")
        ledger = json.loads((collection / "collection.json").read_text())
        if ledger.get("directory") != str(collection):
            raise ValueError("Collection identity/path mismatch")
    before = inventory(source)
    info = json.loads((source / "meta/info.json").read_text())
    if info.get("codebase_version") != "v3.0":
        raise ValueError("Snapshot requires a LeRobot v3 Dataset")
    entries = dataset_inventory(source)  # Metadata and referenced-file existence, no decoding.
    successes = {a["dataset_episode_index"]: a for a in ledger["attempts"] if a["status"] == "SUCCESS"} if ledger else None
    if successes is not None and set(successes) != set(range(len(entries))):
        raise ValueError("Collection SUCCESS entries do not match Dataset Episodes")
    prior_path = source / "snapshot_manifest.json"
    prior = json.loads(prior_path.read_text()) if prior_path.is_file() else None
    prior_episodes = {e["output_episode_index"]: e for e in prior["episodes"]} if prior else {}
    origins, identities = [], set()
    for entry in entries:
        ep = entry["metadata"]
        index = ep["episode_index"]
        folder = source / f"meta/recorder_timing/episode-{index:06d}"
        meta = json.loads((folder / "episode.json").read_text())
        if not (folder / "frames.jsonl").is_file() or meta.get("status") != "saved" or meta.get("outcome") != "success":
            raise ValueError(f"Episode {index} is not a saved success with Timing metadata")
        identity = meta.get("dataset_repo_id")
        if not isinstance(identity, str) or not identity:
            raise ValueError(f"Episode {index} lacks Dataset identity")
        identities.add(identity)
        attempt = successes[index] if successes is not None else None
        receipt = collection / "receipts" / f"{attempt['attempt_id']}.json" if attempt else None
        if receipt and not receipt.is_file():
            raise ValueError(f"Episode {index} lacks its Collection receipt")
        origins.append({"input_episode_index": index, "frames": ep["length"],
                        "origin": prior_episodes[index]["origin"] if prior else {
                            "collection": str(collection) if collection else None,
                            "dataset": str(source), "dataset_repo_id": identity,
                            "episode_index": index, "attempt_id": attempt["attempt_id"] if attempt else meta.get("attempt_id"),
                            "receipt": str(receipt) if receipt else None}})
    if len(identities) != 1:
        raise ValueError("Dataset contains inconsistent repo identities")
    for relative in ("meta/stats.json", "meta/tasks.parquet"):
        if not (source / relative).is_file():
            raise ValueError(f"Missing Dataset metadata: {relative}")
    return {"source": source, "collection": collection, "ledger_signature": ledger_signature,
            "before": before, "info": info, "episodes": origins, "repo_id": identities.pop(),
            "snapshot_id": prior.get("snapshot_id") if prior else None}


def validate_output(root, repo_id):
    """Reuse training schema/index/value checks, without rechecking recorded timing/Home/videos."""
    from ai.train.validate_dataset import _Validator
    class SnapshotValidator(_Validator):
        def sidecar(self, ep):
            index = ep["episode_index"]
            folder = self.root / f"meta/recorder_timing/episode-{index:06d}"
            meta = self.read_json(str(folder.relative_to(self.root) / "episode.json"))
            self.path(str(folder.relative_to(self.root) / "frames.jsonl"))
            if meta is None:
                return
            valid = (meta.get("dataset_episode_index") == index and meta.get("frames") == ep["length"]
                     and meta.get("status") == "saved" and meta.get("outcome") == "success"
                     and meta.get("dataset_repo_id") == repo_id)
            self.check(valid, "sidecar", f"Episode {index}: Snapshot sidecar mapping mismatch")
            self.success += int(valid)
            self.sidecar_repo_ids.add(meta.get("dataset_repo_id"))
    validator = SnapshotValidator(root, repo_id)
    if validator.metadata():
        validator.data()
    if any(validator.errors.values()):
        raise ValueError(f"Snapshot training schema/index validation failed: {validator.examples[:5]}")
    return {"metadata_data_mapping": "passed", "video_decode": "not_run",
            "recorded_timestamp_home_revalidation": "not_run"}


def aggregate(inputs, output, repo_id, workspace):
    # Isolate official metadata/Arrow caches from all source Datasets and the system environment.
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", HF_HOME=str(workspace / "hf"),
               HF_DATASETS_CACHE=str(workspace / "hf/datasets"), TMPDIR=str(workspace))
    spec = {"repo_ids": [i["repo_id"] for i in inputs], "roots": [str(i["source"]) for i in inputs],
            "aggr_repo_id": repo_id, "aggr_root": str(output)}
    subprocess.run([sys.executable, "-B", "-c",
                    "import json,sys; from lerobot.datasets.aggregate import aggregate_datasets; aggregate_datasets(**json.loads(sys.argv[1]))",
                    json.dumps(spec)], env=env, check=True)


def create_snapshot(paths, name, expect_episodes):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", name):
        raise ValueError("Name must be 1..128 letters/digits/underscores/hyphens, starting with a letter/digit")
    if type(expect_episodes) is not int or expect_episodes < 1 or not paths:
        raise ValueError("Provide inputs and a positive --expect-episodes (combined total)")
    parent = inside_project(SNAPSHOTS)
    destination = parent / name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(errno.EEXIST, "Snapshot already exists", str(destination))
    with ExitStack() as locks:
        inputs = [describe_input(p, locks) for p in paths]
        if len({i["source"] for i in inputs}) != len(inputs):
            raise ValueError("Duplicate input Dataset paths")
        if any(parent.is_relative_to(i["source"]) for i in inputs):
            raise ValueError("Snapshot destination must not be inside an input Dataset")
        if sum(len(i["episodes"]) for i in inputs) != expect_episodes:
            raise ValueError(f"Expected {expect_episodes} success Episodes, found {sum(len(i['episodes']) for i in inputs)}")
        first = inputs[0]["info"]
        if any(any(i["info"].get(k) != first.get(k) for k in ("features", "fps", "robot_type")) for i in inputs[1:]):
            raise ValueError("Input feature/FPS/robot_type mismatch")
        parent.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix=f".{name}.copying-", dir=parent))
        output = workspace / "dataset"
        multi = len(inputs) > 1
        repo_id = f"local/{name}" if multi else inputs[0]["repo_id"]
        episodes = []
        try:
            if multi:
                aggregate(inputs, output, repo_id, workspace)
            else:
                source = inputs[0]
                output.mkdir()
                for relative, expected in sorted(source["before"].items()):
                    copy_file(source["source"] / relative, output / relative, expected)
                copied = inventory(output)
                if {f: s[2] for f, s in copied.items()} != {f: s[2] for f, s in source["before"].items()}:
                    raise ValueError("Copied file list/sizes differ from source")
                if any(copied[f][:2] == source["before"][f][:2] for f in copied):
                    raise ValueError("Snapshot must not share source inodes")
            offset = 0
            for input_index, source in enumerate(inputs):
                for origin in source["episodes"]:
                    src_index = origin["input_episode_index"]
                    dst_index = src_index + offset
                    folder = Path(f"meta/recorder_timing/episode-{src_index:06d}")
                    target = output / f"meta/recorder_timing/episode-{dst_index:06d}"
                    if multi:
                        shutil.copytree(source["source"] / folder, target, copy_function=shutil.copyfile)
                        meta_path = target / "episode.json"
                        meta = json.loads(meta_path.read_text())
                        meta["dataset_episode_index"], meta["dataset_repo_id"] = dst_index, repo_id
                        meta["snapshot_source"] = {"input_index": input_index, **origin}
                        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
                        # Exact original metadata remains available for provenance.
                        original = output / f"snapshot_provenance/episode-{dst_index:06d}.json"
                        original.parent.mkdir(exist_ok=True)
                        shutil.copyfile(source["source"] / folder / "episode.json", original)
                    episodes.append({"input_index": input_index, "output_episode_index": dst_index, **origin})
                offset += len(source["episodes"])
            # Original frame timing lines remain byte-identical; only output identity/index binding changes.
            validation = validate_output(output, repo_id)
            dataset_inventory(output)
            for source in inputs:
                if inventory(source["source"]) != source["before"] or (source["collection"] and
                        signature(source["collection"] / "collection.json") != source["ledger_signature"]):
                    raise ValueError("Source changed during Snapshot; no Snapshot was published")
            files = inventory(output)
            input_inodes = {sig[:2] for item in inputs for sig in item["before"].values()}
            if any(sig[:2] in input_inodes for sig in files.values()):
                raise ValueError("Output files must be independent copies")
            manifest = {"version": 1, "snapshot_id": uuid.uuid4().hex, "name": name,
                        "created_at": datetime.now(timezone.utc).isoformat(), "snapshot_path": str(destination),
                        "dataset_repo_id": repo_id, "codebase_version": "v3.0",
                        "operation": "lerobot.aggregate_datasets" if multi else "copy",
                        "episode_count": len(episodes), "total_frames": sum(i["info"]["total_frames"] for i in inputs),
                        "inputs": [{"input_index": n, "collection": str(i["collection"]) if i["collection"] else None,
                                    "dataset": str(i["source"]), "dataset_repo_id": i["repo_id"],
                                    "snapshot_id": i["snapshot_id"], "episode_count": len(i["episodes"]),
                                    "total_frames": i["info"]["total_frames"]} for n, i in enumerate(inputs)],
                        "episodes": episodes, "validation": validation,
                        "files": [{"path": f, "size": files[f][2]} for f in sorted(files) if f != "snapshot_manifest.json"]}
            with (output / "snapshot_manifest.json").open("w") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False, allow_nan=False)
                f.write("\n"); f.flush(); os.fsync(f.fileno())
            # Flush the complete new tree before atomic publication.
            for directory, _, names in os.walk(output, topdown=False):
                for filename in names:
                    with (Path(directory) / filename).open("rb") as stream:
                        os.fsync(stream.fileno())
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            publish(output, destination)
            return manifest
        finally:
            shutil.rmtree(workspace)  # Only this invocation's unpublished copy and caches.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, action="append", default=[], help="repeat for multiple Collections")
    parser.add_argument("--dataset", type=Path, action="append", default=[], help="repeat for success Datasets or existing Snapshots")
    parser.add_argument("--name", required=True)
    parser.add_argument("--expect-episodes", type=int, required=True, help="combined success Episode count")
    args = parser.parse_args(argv)
    try:
        result = create_snapshot([*args.collection, *args.dataset], args.name, args.expect_episodes)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Snapshot failed: {exc}\n")
    print(f"Snapshot: {result['snapshot_path']}\nEpisodes: {result['episode_count']} / frames: {result['total_frames']}")
    print(f"Dataset repo id: {result['dataset_repo_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
