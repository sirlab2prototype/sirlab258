"""Task 1 settings. Team Home values are read from the existing QP YAML only."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
TASK_NAME = "indy7_basket_to_table1"


@dataclass(frozen=True)
class TaskConfig:
    task_id: str
    home_source: str
    home_joint_deg: tuple
    home_vel_ratio: float
    home_acc_ratio: float
    home_tolerance_deg: float
    home_stable_ms: float
    home_timeout_seconds: float
    prepare_timeout_seconds: float
    max_act_seconds: float
    home_path_reviewed_for_real: bool = False

    def __post_init__(self):
        q = np.asarray(self.home_joint_deg, dtype=float)
        if self.task_id != TASK_NAME or q.shape != (6,) or not np.isfinite(q).all():
            raise ValueError("Invalid Task/Home target")
        for name in ("home_vel_ratio", "home_acc_ratio", "home_tolerance_deg", "home_stable_ms",
                     "home_timeout_seconds", "prepare_timeout_seconds", "max_act_seconds"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0: raise ValueError(name)
        if self.home_stable_ms / 1000 >= min(self.prepare_timeout_seconds, self.home_timeout_seconds):
            raise ValueError("Home stability window must fit timeouts")

    @property
    def home_rad(self): return np.deg2rad(self.home_joint_deg)

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, path):
        settings = json.loads(Path(path).read_text())
        source = (ROOT / settings["home_source"]).resolve()
        if not source.is_relative_to(ROOT): raise ValueError("Home source must be inside this project")
        values = yaml.safe_load(source.read_text())["qp_teleop"]["ros__parameters"]
        return cls(**settings, home_joint_deg=tuple(values["capstone_home_joint_deg"]),
                   home_vel_ratio=values["capstone_home_vel_ratio"], home_acc_ratio=values["capstone_home_acc_ratio"])
