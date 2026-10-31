"""Local-host ACT wire contract v2. No torch, ROS, or robot dependencies."""
import json
import time
from urllib.parse import urlparse

import numpy as np
from ai import dataset_contract as contract

PROTOCOL = "indy7_act_rgb2_v2"


def local_endpoint(endpoint):
    parsed = urlparse(endpoint)
    if parsed.scheme != "tcp" or parsed.hostname not in ("127.0.0.1", "::1") or not parsed.port:
        raise ValueError("Only literal localhost tcp endpoints are supported")
    return endpoint


def validate_observation(observation):
    if set(observation) != {*contract.CAMERA_KEYS, "observation.state"}:
        raise ValueError("Expected exactly two named RGB cameras and observation.state")
    for key in contract.CAMERA_KEYS:
        image = observation[key]
        if not isinstance(image, np.ndarray) or image.shape != contract.IMAGE_SHAPE or image.dtype != np.uint8:
            raise ValueError(f"Invalid RGB shape/dtype: {key}")
    state = observation["observation.state"]
    if not isinstance(state, np.ndarray) or state.shape != (7,) or state.dtype != np.float32 or not np.isfinite(state).all():
        raise ValueError("Expected finite float32 state[7]")
    if state[6] not in (0, 1):
        raise ValueError("Unknown/nonbinary gripper command state")


def validate_chunk(chunk, chunk_size):
    if not isinstance(chunk, np.ndarray) or chunk.shape != (chunk_size, 7) or chunk.dtype != np.float32 or not np.isfinite(chunk).all():
        raise ValueError("Invalid action chunk shape/dtype/finite values")
    return chunk


def envelope(command, session_id, request_id):
    return {"protocol": PROTOCOL, "command": command, "session_id": session_id,
            "request_id": request_id, "request_monotonic_ns": time.monotonic_ns()}


def encode_request(header, observation=None):
    parts = [json.dumps(header, allow_nan=False).encode()]
    if observation is not None:
        validate_observation(observation)
        parts += [observation["observation.state"].astype("<f4", copy=False).tobytes()]
        parts += [np.ascontiguousarray(observation[key]).tobytes() for key in contract.CAMERA_KEYS]
    return parts


def decode_request(parts):
    if not parts or len(parts[0]) > 65536:
        raise ValueError("Missing/oversized header")
    header = json.loads(parts[0])
    if header.get("protocol") != PROTOCOL:
        raise ValueError("protocol_version_mismatch")
    for key in ("session_id", "request_id"):
        if not isinstance(header.get(key), str) or not 0 < len(header[key]) <= 128:
            raise ValueError(f"Invalid {key}")
    if header.get("command") != "predict":
        if len(parts) != 1:
            raise ValueError("Control commands must not include arrays")
        return header, None
    if len(parts) != 4 or len(parts[1]) != 7 * 4 or any(len(part) != int(np.prod(contract.IMAGE_SHAPE)) for part in parts[2:]):
        raise ValueError("Expected fixed state[7] and two VGA RGB buffers")
    observation = {"observation.state": np.frombuffer(parts[1], dtype="<f4").copy()}
    observation.update({key: np.frombuffer(part, dtype=np.uint8).reshape(contract.IMAGE_SHAPE)
                        for key, part in zip(contract.CAMERA_KEYS, parts[2:])})
    validate_observation(observation)
    if header.get("clock_domain") != "same_host_monotonic":
        raise ValueError("Unsupported observation clock domain")
    if type(header.get("observation_monotonic_ns")) is not int or type(header.get("observation_system_time_ns")) is not int:
        raise ValueError("Missing observation reference clocks")
    return header, observation


def decode_response(parts, expected_session, expected_request, chunk_size=None):
    if not parts:
        raise ValueError("Empty inference response")
    header = json.loads(parts[0])
    if header.get("protocol") != PROTOCOL or header.get("session_id") != expected_session or header.get("request_id") != expected_request:
        raise ValueError("Response protocol/session/request mismatch")
    if not header.get("ok"):
        raise ValueError(f"Server rejected request: {header.get('error')}")
    if chunk_size is None:
        if len(parts) != 1:
            raise ValueError("Unexpected control response arrays")
        return header, None
    if len(parts) != 2 or header.get("shape") != [chunk_size, 7] or not header.get("output_valid"):
        raise ValueError("Malformed action response")
    chunk = np.frombuffer(parts[1], dtype="<f4").reshape(chunk_size, 7).copy()
    return header, validate_chunk(chunk, chunk_size)
