"""Discover dataset JSON files and open immutable Collections. No ROS or Tk."""
from collections import Counter
from pathlib import Path
from ai.record.collection import Collection, PROJECT, sync_directory, saved_conditions
from ai.record.plans import read_json, validate_definition, definition_hash, builtin_plan

DATASETS = PROJECT / "outputs/preserved/datasets"
PLANS = PROJECT / "ai/record/plans"
LEGACY = (PROJECT / "outputs/act_gui_integration_test", PROJECT / "datasets/indy7_act_core108")
ERRORS = (ValueError, OSError, KeyError, TypeError)


def read_state(directory):
    directory = Path(directory).absolute()
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("Collection symlink 경로는 지원하지 않습니다")
    state = read_json(directory / "collection.json")
    if not isinstance(state, dict):
        raise ValueError("Collection 장부는 JSON 객체여야 합니다")
    if state.get("version") != 1 or state.get("directory") != str(directory):
        raise ValueError("장부 버전/위치 불일치")
    saved_conditions(state)
    if state.get("definition", {}).get("dataset_id", directory.name) != directory.name:
        raise ValueError("Dataset ID/location mismatch")
    return state


def progress(row, state):
    attempts = state["attempts"]
    done = {a["condition_id"] for a in attempts if a["status"] == "SUCCESS"}
    row.update(success=len(done), correction=sum(a["status"] == "CORRECTION" for a in attempts),
               total=len(state["conditions"]))
    definition = state.get("definition")
    row["action"] = definition["action"]["description"] if definition else "기존 바구니 → 테이블"
    row["status"] = "완료" if len(done) == row["total"] else "수집 중" if attempts else "수집 전"
    if any(a["status"] in {"PREPARED", "RECORDING", "REVIEW", "SAVING"} or
           (a["status"] == "INVALID" and not a.get("discard_completed", a.get("pending_discarded", False)))
           for a in attempts):
        row["detail"] = "미해결 이력이 있습니다. 녹화 진입 시 기존 Pending/Recovery 검사를 수행합니다."


def check_snapshot(state, definition):
    if state.get("definition") is None or definition_hash(state["definition"]) != definition_hash(definition):
        raise ValueError("정의 JSON과 저장된 스냅샷이 다릅니다. 원본 JSON을 복원하거나 새 Dataset ID를 사용하세요")


class DatasetManager:
    def __init__(self, datasets=DATASETS, plans=PLANS, legacy=LEGACY):
        self.datasets, self.plans = Path(datasets).absolute(), Path(plans).absolute()
        self.legacy = tuple(Path(p).absolute() for p in legacy)

    def check_roots(self):
        for path in (self.datasets, self.plans):
            if path.exists() and not path.is_dir():
                raise ValueError(f"폴더가 아닙니다: {path}")
            if path.resolve() != path:
                raise ValueError(f"symlink 경로는 지원하지 않습니다: {path}")

    @staticmethod
    def row(path=None, source=None):
        return {"key": str(source or path), "path": path, "source": source,
                "id": source.stem if source else path.name, "action": "—", "definition": None,
                "success": 0, "correction": 0, "total": 0, "status": "수집 전", "error": None, "detail": ""}

    def list_datasets(self):
        """Fresh, read-only scan. Broken files are visible and cannot be opened."""
        self.check_roots()
        result = []
        for source in sorted(self.plans.glob("*.json")):
            row = self.row(source=source)
            try:
                raw = read_json(source)
                if isinstance(raw, dict) and isinstance(raw.get("dataset_id"), str):
                    row["id"] = raw["dataset_id"]
                definition = validate_definition(raw)
                row.update(id=definition["dataset_id"], action=definition["action"]["description"],
                           total=len(definition["plan"]["conditions"]), definition=definition,
                           path=self.datasets / definition["dataset_id"])
                if source.stem != row["id"]:
                    raise ValueError("파일명과 dataset_id가 일치해야 합니다")
                if (definition["plan"]["kind"] == "core108" and
                        definition["plan"]["conditions"] != builtin_plan()["conditions"]):
                    raise ValueError("Core-108 원본 조건 ID·순서·의미가 다릅니다")
                if row["path"].exists() or row["path"].is_symlink():
                    state = read_state(row["path"])
                    progress(row, state)
                    check_snapshot(state, definition)
            except ERRORS as exc:
                row.update(error=f"{source.name}: {exc}", status="오류")
            result.append(row)
        counts = Counter(row["id"] for row in result)
        for row in result:
            if counts[row["id"]] > 1:
                row.update(error=f"중복 Dataset ID: {row['id']}. 모든 중복 정의를 정리하세요", status="오류")
        # Existing snapshots remain selectable without their original source.
        # A broken/conflicting source with the same filename/ID cannot be bypassed.
        covered = {row["id"] for row in result} | {row["source"].stem for row in result}
        existing = sorted(self.datasets.iterdir()) if self.datasets.exists() else []
        paths = [p for p in existing if p.name not in covered]
        paths += [p for p in self.legacy if p.exists() and p not in paths and p not in existing]
        for path in paths:
            row = self.row(path=path)
            try:
                state = read_state(path)
                progress(row, state)
                row["detail"] = row["detail"] or "저장된 Collection 계획을 사용합니다."
            except ERRORS as exc:
                row.update(error=f"{path.name}: {exc}", status="오류")
            result.append(row)
        return result

    def open_dataset(self, selected):
        """Re-scan on click: never trust a stale UI row or silently adopt a path."""
        matches = [row for row in self.list_datasets() if row["key"] == selected["key"]]
        if len(matches) != 1:
            raise ValueError("선택한 정의가 사라졌거나 변경됐습니다. 목록을 새로고침하세요")
        row = matches[0]
        if row["error"]:
            raise ValueError(row["error"])
        if (selected.get("definition") is not None and
                row["definition"] != selected["definition"]):
            raise ValueError("선택 후 정의 JSON이 변경됐습니다. 목록을 새로고침하세요")
        directory = row["path"]
        if not directory.exists() and not directory.is_symlink():
            if row["definition"] is None:
                raise ValueError("기존 Collection이 없습니다")
            self.datasets.mkdir(parents=True, exist_ok=True)
            try:
                directory.mkdir()  # Exclusive reservation; existing paths are never overwritten.
            except FileExistsError:
                pass  # A concurrent creator must have published exactly the same snapshot.
            else:
                sync_directory(self.datasets)
                collection = Collection(directory, definition=row["definition"])
                collection.close()
        state = read_state(directory)
        if row["definition"] is not None:
            check_snapshot(state, row["definition"])
        return directory
