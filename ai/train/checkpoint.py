"""Portable ACT bundle verification; never sends commands to a robot."""
from pathlib import Path
import json

import numpy as np

from ai import dataset_contract as contract
from ai.serve.lerobot_act_backend import LeRobotActBackend, resolve_pretrained_model_dir
from ai.checkpoint_identity import checkpoint_identifier


def portable_contract():
    return {"fps": contract.FPS, "features": contract.dataset_features(),
            "joint_state": contract.JOINT_STATE_SEMANTICS, "joint_action": contract.JOINT_ACTION_SEMANTICS,
            "gripper": contract.GRIPPER_SEMANTICS}


def assert_policy_contract(config):
    expected = {**{key: ("VISUAL", (3, 480, 640)) for key in contract.CAMERA_KEYS},
                "observation.state": ("STATE", (7,))}
    actual = {key: (value.type.value, tuple(value.shape)) for key, value in config.input_features.items()}
    outputs = {key: (value.type.value, tuple(value.shape)) for key, value in config.output_features.items()}
    if config.type != "act" or actual != expected or outputs != {"action": ("ACTION", (7,))}:
        raise ValueError(f"Checkpoint policy contract mismatch: {actual}, {outputs}")
    if config.n_action_steps > config.chunk_size or config.chunk_size < 1:
        raise ValueError("Invalid action chunk configuration")


def check_files(path):
    directory = resolve_pretrained_model_dir(str(path))
    required = ("config.json", "model.safetensors", "train_config.json",
                "policy_preprocessor.json", "policy_postprocessor.json")
    for name in required:
        if not (directory / name).is_file():
            raise ValueError(f"Incomplete checkpoint: missing {name}")
    from ai.train.processor_contract import check_processor_files
    check_processor_files(directory)
    # State files referenced by processor JSON are loaded/checked by the official API.
    return directory


def verify_checkpoint(path, device="cpu"):
    directory = check_files(path)
    manifest = json.loads((directory / "indy7_contract.json").read_text())
    expected = json.loads(json.dumps(portable_contract()))
    if manifest != expected:
        raise ValueError("Portable data contract mismatch")
    backend = LeRobotActBackend(directory, device=device)
    assert_policy_contract(backend.policy.config)
    observation = {**{key: np.zeros(contract.IMAGE_SHAPE, dtype=np.uint8) for key in contract.CAMERA_KEYS},
                   "observation.state": np.zeros(7, dtype=np.float32)}
    chunk = backend.predict_chunk(observation)
    if chunk.shape != (backend.policy.config.chunk_size, 7) or not np.isfinite(chunk).all():
        raise ValueError("Invalid unnormalized action chunk")
    first = backend.predict(observation)
    backend.reset()
    second = backend.predict(observation)
    if first.shape != (7,) or second.shape != (7,) or not np.isfinite([first, second]).all():
        raise ValueError("Invalid action after reset")
    return {"valid": True, "checkpoint": str(directory), "checkpoint_id": checkpoint_identifier(directory), "chunk_shape": list(chunk.shape),
            **backend.processor_verification,
            "action_shape": list(first.shape), "reset_inference": True, "dataset_required": False,
            "robot_safety_verified": False, "action_semantics": contract.JOINT_ACTION_SEMANTICS}
