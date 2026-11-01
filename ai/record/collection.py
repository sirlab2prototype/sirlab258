"""Core-108 condition ledger. No ROS, robot control, or coordinate sampling.

One atomic, fsynced JSON snapshot and immutable commit receipts; flock excludes
concurrent managed writers. LeRobot remains the only Dataset writer.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import tempfile
import uuid
from ai.record import terminal_ui as ui
from ai.record.correction_marker import validate as validate_correction

PROJECT = Path(__file__).resolve().parents[2]
PLAN = PROJECT / "docs/ACT_108_Episode_Execution_Log_Template.md"
DEFAULT_DIRECTORY = PROJECT / "datasets/indy7_act_core108"
DEPTHS = ("Front", "Front-Middle", "Middle", "Middle-Back", "Back", "Random")
REGIONS = {"L": "Left", "C": "Center", "R": "Right"}
# Operator labels only; plan IDs and serialized metadata keep their original values.
REGION_LABELS = {"L": "왼쪽", "C": "가운데", "R": "오른쪽"}
YAW_LABELS = {"A1": "시계방향 많이", "A2": "시계방향 보통", "A3": "시계방향 조금",
              "A4": "반시계방향 조금", "A5": "반시계방향 보통", "A6": "반시계방향 많이"}
DEPTH_LABELS = dict(zip(DEPTHS, ("입구 쪽", "입구 쪽과 중간 사이", "중간", "중간과 벽 쪽 사이", "벽 쪽", "자유 배치")))
STAGES = ("reach_success", "align_success", "grasp_success", "lift_success",
          "shelf_retract_success", "transport_success", "table_approach_success", "place_success")
POSE_FIELDS = tuple(f"{kind}_{axis}" for kind in ("target", "actual") for axis in ("x", "y", "yaw"))
TEXT_FIELDS = ("failure_reason", "demo_quality", "notes", "coordinate_frame", "measurement_method")
TERMINAL = {"SUCCESS", "CORRECTION", "FAILURE", "DISCARD", "INVALID", "INTERRUPTED"}


class CollectionError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def load_plan(path=PLAN):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) >= 5 and parts[0].isdigit() and parts[1].isdigit():
            rows.append(dict(condition_id=int(parts[0]), collection_order=int(parts[1]),
                             position_region=parts[2], yaw_bin=parts[3], depth_coverage=parts[4]))
    expected = set(range(1, 109))
    if len(rows) != 108 or {r["condition_id"] for r in rows} != expected or {
            r["collection_order"] for r in rows} != expected:
        raise CollectionError("Plan must contain exactly IDs/orders 1..108, without duplicates")
    distribution = Counter((r["position_region"], r["yaw_bin"], r["depth_coverage"]) for r in rows)
    if distribution != Counter({(region, f"A{yaw}", depth): 1
                                for region in REGIONS for yaw in range(1, 7) for depth in DEPTHS}):
        raise CollectionError("Plan Region × Yaw × Depth coverage is not one of each")
    return sorted(rows, key=lambda r: r["collection_order"])


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_tree(root):
    # Save happens in REVIEW. Flush data/video/footer/sidecar files BEFORE the
    # durable collection commit. No synchronous flush is added to frame writes.
    for directory, _, files in os.walk(root, topdown=False):
        for name in files:
            with (Path(directory) / name).open("rb") as stream:
                os.fsync(stream.fileno())
        sync_directory(directory)
    sync_directory(Path(root).parent)


def tree_fingerprint(root):
    root = Path(root)
    if not root.exists():
        return None
    return {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


def local_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise CollectionError("Dataset artifact escapes its root")
    return path


def dataset_inventory(root):
    """Read only local v3 Parquet metadata, including orphan-file detection.

    Never construct a Dataset here: incomplete metadata must not trigger a Hub
    download or create files during recovery.
    """
    root = Path(root)
    if not root.exists():
        return []
    import pyarrow.parquet as pq
    info = json.loads((root / "meta/info.json").read_text())
    if info.get("fps") != 20 or info.get("robot_type") != "indy7":
        raise CollectionError(f"Dataset FPS/robot contract mismatch: {root}")
    rows = []
    for path in sorted((root / "meta/episodes").rglob("*.parquet")):
        rows.extend(pq.read_table(path).to_pylist())
    rows.sort(key=lambda r: r["episode_index"])
    count = info["total_episodes"]
    if [r["episode_index"] for r in rows] != list(range(count)):
        raise CollectionError(f"Dataset episode metadata/count mismatch: {root}")
    if not count:
        raise CollectionError(f"Empty/partial Dataset requires inspection: {root}")
    references = set()
    result = []
    offset = 0
    for row in rows:
        data = info["data_path"].format(chunk_index=row["data/chunk_index"], file_index=row["data/file_index"])
        videos = [info["video_path"].format(video_key=key, chunk_index=row[f"videos/{key}/chunk_index"],
                                          file_index=row[f"videos/{key}/file_index"])
                  for key, feature in info["features"].items() if feature["dtype"] == "video"]
        if row["dataset_from_index"] != offset or row["dataset_to_index"] != offset + row["length"]:
            raise CollectionError(f"Dataset frame ranges mismatch: {root}")
        offset += row["length"]
        artifacts = [data, *videos]
        for name in artifacts:
            if not local_path(root, name).is_file():
                raise CollectionError(f"Missing Dataset artifact: {name}")
        references.update(artifacts)
        result.append({"metadata": {k: v for k, v in row.items() if not k.startswith("stats/")},
                       "artifacts": artifacts, "data": data, "videos": videos})
    present = {str(p.relative_to(root)) for folder, pattern in (("data", "*.parquet"), ("videos", "*.mp4"))
               for p in (root / folder).rglob(pattern)}
    if references != present or offset != info["total_frames"]:
        raise CollectionError(f"Orphan/missing files or total_frames mismatch: {root}")
    return result


class Collection:
    def __init__(self, directory, plan=PLAN, *, recovery=False):
        self.directory = Path(directory).expanduser().resolve()
        for relative in ("outputs/act_recorder_pilot_20261031",
                         "outputs/temporary/verification/act_recorder_pilot_20261031"):
            pilot = (PROJECT / relative).resolve()
            if self.directory.is_relative_to(pilot) or pilot.is_relative_to(self.directory):
                raise CollectionError("Formal collection and Pilot paths must be separate and non-nested")
        self.roots = {kind: self.directory / kind for kind in ("success", "correction", "failure")}
        self.pending_root = self.directory / "pending"
        for path in [*self.roots.values(), self.pending_root]:
            if path.resolve() != path:
                raise CollectionError("Collection data directories must not be symlinks")
        self.path = self.directory / "collection.json"
        if (self.directory / "meta/info.json").exists():
            raise CollectionError("A Dataset root cannot be used as a collection directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        sync_directory(self.directory.parent)
        self.lock = (self.directory / ".collection.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise CollectionError("Collection already open by another writer") from None
        self.blocked = False
        try:
            conditions = load_plan(plan)
            if self.path.exists():
                self.state = json.loads(self.path.read_text())
                if self.state["conditions"] != conditions or self.state["directory"] != str(self.directory):
                    raise CollectionError("Plan/order or collection location changed; inspect before continuing")
            else:
                if any(path.exists() for path in [*self.roots.values(), self.pending_root]):
                    raise CollectionError("Existing data without collection ledger cannot be adopted automatically")
                self.state = {"version": 1, "directory": str(self.directory), "created_at": now(),
                              "conditions": conditions, "attempts": []}
                self._persist()
            self.current = None
            self.selected = self.state.get("selected_condition_id")
            self.placement = self.empty_placement()
            if not recovery:
                self.audit()
                changed = False
                for attempt in self.state["attempts"]:
                    if attempt["status"] in ("PREPARED", "RECORDING", "REVIEW"):
                        manifest = Path(attempt["pending_path"]) / "episode.json" if attempt.get("pending_path") else None
                        pending = json.loads(manifest.read_text()) if manifest and manifest.exists() else {}
                        if pending and pending.get("collection", {}).get("attempt_id") != attempt["attempt_id"]:
                            raise CollectionError("Pending take identity differs from interrupted ledger attempt")
                        if pending.get("demonstration") is not None:
                            attempt.update(demonstration_type=pending["demonstration"]["type"],
                                           correction=pending["demonstration"].get("correction"))
                        if pending.get("integrity", {}).get("valid") is False:
                            attempt.update(status="INVALID", integrity=pending["integrity"], ended_at=now())
                        else:
                            attempt.update(status="INTERRUPTED", ended_at=now())
                        changed = True
                if changed:
                    self._persist()
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.lock.closed:
            self.lock.close()

    @staticmethod
    def empty_placement():
        return {**dict.fromkeys(POSE_FIELDS), **dict.fromkeys(TEXT_FIELDS),
                **dict.fromkeys(STAGES), "units": {"x": "cm", "y": "cm", "yaw": "degree"},
                "placement_confirmed": False}

    def _persist(self):
        if self.blocked:
            raise CollectionError("Ledger write previously failed; exit and reconcile before continuing")
        try:
            durable_json(self.path, self.state)
        except BaseException:
            self.blocked = True
            raise

    def completed(self):
        return {a["condition_id"] for a in self.state["attempts"] if a["status"] == "SUCCESS"}

    def condition(self):
        done = self.completed()
        if self.selected is not None:
            return next(c for c in self.state["conditions"] if c["condition_id"] == self.selected)
        return next((c for c in self.state["conditions"] if c["condition_id"] not in done), None)

    def select(self, condition_id):
        if self.current:
            raise CollectionError("Resolve current attempt before selecting another condition")
        if condition_id not in range(1, 109) or condition_id in self.completed():
            raise CollectionError("Unknown or already completed Condition ID")
        self.selected = condition_id
        self.state["selected_condition_id"] = condition_id
        self._persist()
        self.placement = self.empty_placement()

    def prepare(self):
        if self.blocked:
            raise CollectionError("수집 차단: 종료 후 reconcile로 대조하세요")
        if self.current:
            return self.current
        condition = self.condition()
        if condition is None:
            raise CollectionError("All 108 conditions are complete")
        # Called by s BEFORE capturing its fixed timing reference. No user ready command.
        self.placement["placement_confirmed"] = True
        identity = {**condition, "attempt_number": 1 + sum(a["condition_id"] == condition["condition_id"]
                                                         for a in self.state["attempts"]),
                    "attempt_id": str(uuid.uuid4()), "created_at": now()}
        self.current = {**identity, "placement": deepcopy(self.placement), "status": "PREPARED",
                        "dataset_kind": None, "dataset_episode_index": None, "pending_path": None,
                        "demonstration_type": "normal", "correction": None}
        self.state["attempts"].append(self.current)
        self.state["selected_condition_id"] = condition["condition_id"]
        self._persist()
        return self.current

    def context(self):
        if not self.current:
            raise CollectionError("Condition/attempt must be fixed before starting")
        return {k: deepcopy(self.current[k]) for k in (
            "condition_id", "collection_order", "position_region", "yaw_bin", "depth_coverage",
            "attempt_number", "attempt_id", "created_at", "placement")}

    def recording(self, pending_path):
        self.current.update(status="RECORDING", pending_path=str(pending_path), started_at=now())
        self._persist()

    def review(self, integrity):
        if not self.current:
            return
        self.current.update(status="REVIEW" if integrity["valid"] else "INVALID",
                            integrity=deepcopy(integrity), stopped_at=now())
        self._persist()

    def finish(self, status):
        previous_condition = self.current["condition_id"] if self.current else None
        if self.current:
            before = deepcopy(self.current)
            selected_before = self.state.get("selected_condition_id")
            # Preserve INVALID as its outcome, even if the operator removes raw files.
            if self.current["status"] == "INVALID":
                self.current["discard_requested"] = status == "DISCARD"
            else:
                self.current["status"] = status
            if status in ("SUCCESS", "CORRECTION", "FAILURE"):
                self.current["final_success"] = None if status == "CORRECTION" else status == "SUCCESS"
                self.current.pop("baseline", None)  # Only unresolved save intents need the full baseline.
            self.current["ended_at"] = now()
            self.state["selected_condition_id"] = None if status == "SUCCESS" else previous_condition
            try:
                self._persist()
            except BaseException:
                # A failed journal commit must not make progress appear complete in memory.
                self.current.clear()
                self.current.update(before)
                self.state["selected_condition_id"] = selected_before
                raise
        self.current = None
        self.selected = None if status == "SUCCESS" else previous_condition
        self.placement = self.empty_placement()

    def begin_save(self, outcome, frames):
        if outcome not in ("success", "correction"):
            raise CollectionError("새 Failure Episode 저장은 지원하지 않습니다")
        self.audit()
        attempt = self.current
        if not attempt or attempt["status"] != "REVIEW":
            raise CollectionError("Only a valid reviewed attempt may be saved")
        if attempt["condition_id"] in self.completed():
            raise CollectionError("Condition already completed")
        if outcome == "correction":
            if attempt.get("demonstration_type") != "correction":
                raise CollectionError("교정 시연 유형이 아닙니다")
            validate_correction(attempt.get("correction"), frames, 20)
        elif attempt.get("demonstration_type", "normal") != "normal":
            raise CollectionError("교정 시연을 Success Dataset에 저장할 수 없습니다")
        index = sum(a["status"] == outcome.upper() for a in self.state["attempts"])
        attempt.update(status="SAVING", dataset_kind=outcome, dataset_episode_index=index,
                       frames=frames, save_started_at=now(), baseline=tree_fingerprint(self.roots[outcome]))
        self._persist()  # Durable intent BEFORE opening/writing the Dataset.
        return index

    def _evidence(self, attempt, inventory, *, decode=False):
        kind, index = attempt["dataset_kind"], attempt["dataset_episode_index"]
        root = self.roots[kind]
        sidecar = root / "meta/recorder_timing" / f"episode-{index:06d}"
        meta = json.loads((sidecar / "episode.json").read_text())
        info = json.loads((root / "meta/info.json").read_text())
        for key, feature in meta["features"].items():
            if any(info["features"].get(key, {}).get(field) != feature[field] for field in ("dtype", "shape", "names")):
                raise CollectionError("Dataset/sidecar feature contract mismatch")
        context = {key: attempt[key] for key in self.context_keys()}
        if (meta.get("collection") != context or meta.get("outcome") != kind or
                meta.get("dataset_episode_index") != index or meta.get("dataset_root") != str(root) or
                meta.get("status") != "saved" or not meta.get("integrity", {}).get("valid") or
                meta.get("frames") != attempt["frames"] or meta.get("fps") != 20):
            raise CollectionError("Saved sidecar does not prove this exact condition/attempt")
        item = inventory[index]
        if item["metadata"]["length"] != attempt["frames"]:
            raise CollectionError("Saved frame count mismatch")
        names = [*item["artifacts"], str((sidecar / "episode.json").relative_to(root)),
                 str((sidecar / "frames.jsonl").relative_to(root))]
        evidence = {"attempt_id": attempt["attempt_id"], "dataset_kind": kind,
                    "dataset_episode_index": index, "metadata": item["metadata"],
                    "dataset_contract": {"fps": info["fps"], "robot_type": info["robot_type"], "features": info["features"]},
                    "sha256": {name: digest(local_path(root, name)) for name in names}}
        # Additive v1 extension: do not alter evidence for historical receipts.
        demonstration = meta.get("demonstration")
        if kind == "correction" or demonstration is not None:
            expected = {"type": attempt.get("demonstration_type", "normal"), "correction": attempt.get("correction")}
            if demonstration != expected or (kind == "correction") != (expected["type"] == "correction"):
                raise CollectionError("교정 메타데이터와 장부 연결이 일치하지 않습니다")
            evidence["demonstration"] = demonstration
            if kind == "correction":
                timings = [json.loads(line) for line in (sidecar / "frames.jsonl").read_text().splitlines()]
                validate_correction(expected["correction"], attempt["frames"], 20, timings)
        if decode:
            import pyarrow.parquet as pq
            import av
            rows = [r for r in pq.read_table(local_path(root, item["data"]),
                    columns=["episode_index", "frame_index", "timestamp"]).to_pylist()
                    if r["episode_index"] == index]
            if ([r["frame_index"] for r in rows] != list(range(attempt["frames"])) or
                    any(not math.isclose(r["timestamp"], i / 20, rel_tol=1e-7, abs_tol=1e-6) for i, r in enumerate(rows))):
                raise CollectionError("Dataset frame indices/timestamps mismatch")
            timings = [json.loads(line) for line in (sidecar / "frames.jsonl").read_text().splitlines()]
            if [r["frame_index"] for r in timings] != list(range(attempt["frames"])):
                raise CollectionError("Timing sidecar frame indices mismatch")
            for name in item["videos"]:
                with av.open(str(local_path(root, name))) as stream:
                    count = sum(1 for _ in stream.decode(video=0))
                # Recorder reopens after each episode: each video must hold this take only.
                if count != attempt["frames"]:
                    raise CollectionError("Video frame count mismatch; do not append")
        return evidence

    @staticmethod
    def context_keys():
        return ("condition_id", "collection_order", "position_region", "yaw_bin", "depth_coverage",
                "attempt_number", "attempt_id", "created_at", "placement")

    def receipt_path(self, attempt):
        return self.directory / "receipts" / f"{attempt['attempt_id']}.json"

    def audit(self, *, allow_saving=False):
        if self.state.get("version") != 1:
            raise CollectionError("Unsupported collection ledger version")
        attempts = self.state["attempts"]
        counts = Counter()
        conditions = {c["condition_id"]: c for c in self.state["conditions"]}
        for attempt in attempts:
            cid = attempt["condition_id"]
            counts[cid] += 1
            if (cid not in conditions or any(attempt[k] != v for k, v in conditions[cid].items()) or
                    attempt["attempt_number"] != counts[cid] or
                    attempt["status"] not in TERMINAL | {"PREPARED", "RECORDING", "REVIEW", "SAVING"}):
                raise CollectionError("Invalid condition identity, attempt sequence or status in ledger")
        ids = [a["attempt_id"] for a in attempts]
        if len(set(ids)) != len(ids):
            raise CollectionError("Duplicate attempt identity")
        success = [a["condition_id"] for a in attempts if a["status"] == "SUCCESS"]
        if len(set(success)) != len(success):
            raise CollectionError("Duplicate completed condition")
        selected = self.state.get("selected_condition_id")
        if selected is not None and (selected not in conditions or selected in success):
            raise CollectionError("Selected condition is unknown or already complete")
        if not allow_saving and any(a["status"] == "SAVING" for a in attempts):
            raise CollectionError("Unresolved save: run collection reconcile; never retry Dataset save")
        for kind, root in self.roots.items():
            if root.resolve() != root or any(p.is_symlink() for p in root.rglob("*")):
                raise CollectionError("Dataset symlinks are not supported for formal collection")
            inventory = dataset_inventory(root)
            linked = [a for a in attempts if a["status"] == kind.upper() or
                      (allow_saving and a["status"] == "SAVING" and a["dataset_kind"] == kind)]
            if sorted(a["dataset_episode_index"] for a in linked) != list(range(len(inventory))):
                raise CollectionError(f"Dataset/ledger indices mismatch ({kind}); inspect/reconcile, no writes allowed")
            for attempt in linked:
                if attempt["status"] == "SAVING":
                    continue
                receipt = json.loads(self.receipt_path(attempt).read_text())
                if receipt != self._evidence(attempt, inventory):
                    raise CollectionError("Dataset/sidecar changed since commit; collection blocked")

    def commit(self):
        attempt = self.current
        kind = attempt["dataset_kind"]
        sync_tree(self.roots[kind])
        self.audit(allow_saving=True)
        inventory = dataset_inventory(self.roots[kind])
        evidence = self._evidence(attempt, inventory, decode=True)
        receipt = self.receipt_path(attempt)
        if receipt.exists() and json.loads(receipt.read_text()) != evidence:
            raise CollectionError("Unresolved commit receipt differs from Dataset evidence")
        durable_json(receipt, evidence)
        sync_directory(self.directory)
        self.finish(kind.upper())

    def reconcile(self):
        """Complete ONLY proven commits. Never replay frames, edit or delete Dataset files."""
        # Only byte-for-byte unchanged destinations can be released as unsaved.
        # Anything partly written remains blocked, without editing Dataset files.
        for attempt in self.state["attempts"]:
            if attempt["status"] == "SAVING" and tree_fingerprint(self.roots[attempt["dataset_kind"]]) == attempt["baseline"]:
                attempt.update(status="INTERRUPTED", ended_at=now(), recovery="destination_unchanged_no_episode_added",
                               abandoned_save_intent={"dataset_kind": attempt["dataset_kind"],
                                                      "expected_episode_index": attempt["dataset_episode_index"]},
                               dataset_kind=None, dataset_episode_index=None)
                attempt.pop("baseline", None)
                self._persist()
        self.audit(allow_saving=True)
        for attempt in self.state["attempts"]:
            if attempt["status"] == "SAVING":
                self.current = attempt
                self.commit()
        self.audit()

    def stats(self):
        attempts = self.state["attempts"]
        return {"success": len(self.completed()),
                "correction": sum(a["status"] == "CORRECTION" for a in attempts),
                "discard": sum(bool(a.get("discard_completed", a["status"] == "DISCARD" or a.get("pending_discarded", False))) for a in attempts),
                "failure": sum(a["status"] == "FAILURE" for a in attempts)}

    def condition_view(self):
        row = self.condition()
        if row is None:
            return None
        number = self.current["attempt_number"] if self.current else 1 + sum(
            a["condition_id"] == row["condition_id"] for a in self.state["attempts"])
        return {**row, "attempt_number": number, "status": self.current["status"] if self.current else "READY",
                "position": f"{REGION_LABELS[row['position_region']]} ({row['position_region']})",
                "yaw": f"{YAW_LABELS[row['yaw_bin']]} ({row['yaw_bin']})",
                "depth": f"{DEPTH_LABELS[row['depth_coverage']]} ({row['depth_coverage']})"}

    def display(self, *, all_conditions=False, title="현재 수집 조건", with_status=True):
        if all_conditions:
            rows = [f"{r['collection_order']:03d} | {r['condition_id']:03d} | "
                    f"{'완료' if r['condition_id'] in self.completed() else '미완료'} | "
                    f"{REGION_LABELS[r['position_region']]}({r['position_region']}) | "
                    f"{YAW_LABELS[r['yaw_bin']]}({r['yaw_bin']}) | {DEPTH_LABELS[r['depth_coverage']]}"
                    for r in self.state["conditions"]]
            return ui.panel("전체 108개 조건", ["순서 | 조건 ID | 상태 | 위치 | 회전 | 깊이", *rows])
        return ui.condition(self, title, with_status)

    def progress(self):
        stats = self.stats()
        lines = [f"정상 시연 : {stats['success']} / 108 ({stats['success']/108*100:.1f}%)",
                 f"교정 시연 : {stats['correction']}", f"남은 조건 : {108-stats['success']}",
                 f"폐기 시도 : {stats['discard']}"]
        if stats["failure"]:
            lines.append(f"과거 실패 : {stats['failure']}")
        view = self.condition_view()
        if view:
            lines.extend(["", "[현재 조건]", f"수집 순서 : {view['collection_order']:03d} / 108",
                          f"조건 ID   : {view['condition_id']:03d}", f"시도      : {view['attempt_number']}회차",
                          f"상태      : {ui.STATUS.get(view['status'], view['status'])}", "",
                          "progress all : 전체 조건", f"history {view['condition_id']:03d}  : 조건별 이력"])
        return ui.panel("수집 현황", lines)

    def history(self, cid, detail=False):
        attempts = [a for a in self.state["attempts"] if a["condition_id"] == cid]
        if detail:
            return json.dumps(attempts, indent=2, ensure_ascii=False)
        lines = [f"조건 ID : {cid:03d}"]
        if not attempts:
            lines.append("시도 이력이 없습니다.")
        for a in attempts:
            dataset = a.get("dataset_kind")
            link = f"{dataset} / Episode {a['dataset_episode_index']}" if dataset and a['status'] in ("SUCCESS", "CORRECTION", "FAILURE") else "저장 없음"
            status = ui.STATUS.get(a['status'], a['status'])
            if a.get("discard_completed") is False:
                status += " (폐기 미확인)"
            elif a.get("pending_discarded"):
                status += " (폐기)"
            lines.append(f"{a['attempt_number']}회차 | {status} | {link} | {a['created_at']}")
            if a.get("correction"):
                index = a['correction'].get('start_frame_index')
                lines.append(f"  교정 시작: {index}프레임" if index is not None else "  교정 시작: 대응 프레임 미확정")
            if a.get("placement", {}).get("failure_reason"):
                lines.append(f"  사유: {a['placement']['failure_reason']}")
        lines.extend(["", f"history {cid:03d} detail : 세부 장부 JSON"])
        return ui.panel("조건별 이력", lines)

    def command(self, raw):
        tokens = shlex.split(raw)
        if not tokens:
            return None
        verb = tokens[0].lower()
        if verb == "progress":
            return self.display(all_conditions=True) if "all" in tokens[1:] else self.progress()
        if verb == "history":
            if len(tokens) not in (2, 3) or (len(tokens) == 3 and tokens[2] != "detail"):
                raise CollectionError("사용법: history ID [detail]")
            cid = int(tokens[1])
            if cid not in range(1, 109):
                raise CollectionError("조건 ID는 1..108입니다")
            return self.history(cid, detail=len(tokens) == 3)
        if verb == "condition" and len(tokens) == 2:
            self.select(int(tokens[1]))
            return self.display()
        if verb in ("placement", "result"):
            if verb == "placement" and self.current:
                raise CollectionError("Placement is frozen for this attempt")
            if verb == "result" and (not self.current or self.current["status"] not in ("REVIEW", "INVALID")):
                raise CollectionError("Result annotations require REVIEW")
            target = deepcopy(self.placement if verb == "placement" else self.current["placement"])
            allowed = (*POSE_FIELDS, *TEXT_FIELDS) if verb == "placement" else (*TEXT_FIELDS, *STAGES)
            for token in tokens[1:]:
                key, value = token.split("=", 1)
                if key not in allowed:
                    raise CollectionError(f"Unknown metadata field: {key}")
                if key in POSE_FIELDS:
                    parsed = None if value.lower() == "null" else float(value)
                    if parsed is not None and not math.isfinite(parsed):
                        raise CollectionError("Coordinates must be finite or null")
                    target[key] = parsed
                elif key in STAGES:
                    if value.lower() not in ("true", "false", "null"):
                        raise CollectionError("Stage values: true/false/null")
                    target[key] = {"true": True, "false": False, "null": None}[value.lower()]
                else:
                    target[key] = value
            if verb == "placement":
                target["placement_confirmed"] = False
                self.placement = target
            else:
                self.current["placement"] = target
                self._persist()
            return "[좌표 기록] 입력값을 반영했습니다. 미입력=null." if verb == "placement" else "[결과 기록] 메모를 반영했습니다."
        if verb == "help":
            return ui.help_text()
        if verb == "guide":
            return ui.guide()
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate-plan", "status", "history", "reconcile"))
    parser.add_argument("condition_id", type=int, nargs="?")
    parser.add_argument("--collection-dir", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--detail", action="store_true", help="history의 세부 JSON")
    args = parser.parse_args()
    if args.command == "history" and args.condition_id not in range(1, 109):
        parser.error("history requires a Condition ID from 1 to 108")
    try:
        rows = load_plan(args.plan)
        if args.command == "validate-plan":
            print("108 unique IDs/orders; 18 Region × Yaw groups; each contains all 6 depths once. Fixed order:")
            print(" ".join(f"{r['condition_id']:03d}" for r in rows))
            return 0
        if args.command in ("status", "history"):
            # Atomic snapshot reads work while a recorder holds the writer lock.
            path = args.collection_dir.expanduser().resolve() / "collection.json"
            if not path.exists():
                view = Collection.__new__(Collection)
                view.state = {"conditions": rows, "attempts": []}
                view.current, view.selected, view.placement = None, None, view.empty_placement()
                print(view.display(all_conditions=True) if args.command == "status" else "시도 이력이 없습니다.")
                return 0
            state = json.loads(path.read_text())
            if args.command == "history":
                view = Collection.__new__(Collection)
                view.state = state
                print(view.history(args.condition_id, args.detail))
            else:
                view = Collection.__new__(Collection)
                view.state, view.current, view.selected = state, None, state.get("selected_condition_id")
                view.placement = view.empty_placement()
                active = [a for a in state["attempts"] if a["status"] not in TERMINAL]
                if active:
                    view.current = active[-1]
                    view.selected = active[-1]["condition_id"]
                print(view.progress())
                pending = [a for a in state["attempts"] if a["status"] not in TERMINAL]
                if pending:
                    print("[미해결 시도]", json.dumps(pending, ensure_ascii=False))
            return 0
        collection = Collection(args.collection_dir, args.plan, recovery=True)
        try:
            collection.reconcile()
            print("[대조 완료] Dataset 파일을 수정하지 않고 저장 증거를 확인했습니다.\n" + collection.progress())
        finally:
            collection.close()
        return 0
    except (ValueError, OSError, KeyError, IndexError) as exc:
        print(ui.panel("수집 차단 - Collection Blocked", [f"오류 : {exc}", f"Pending : {args.collection_dir / 'pending'}", "복구 : " + ui.recovery_command(args.collection_dir, args.collection_dir / "pending")]))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
