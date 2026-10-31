"""Historical single-camera pool wire format; not the Indy7 v2 inference path."""
import json
import numpy as np

PROTOCOL = "grip_act_rgb_v1"


def decode(parts):
    header = json.loads(parts[0])
    if header.get("protocol") != PROTOCOL:
        raise ValueError("protocol mismatch")
    if header.get("command") != "predict":
        return header, None
    if len(parts) != 3:
        raise ValueError("Legacy RGB-only predict requires three parts")
    return header, (np.frombuffer(parts[1], dtype="<f4").reshape(header["state_shape"]).copy(),
                    np.frombuffer(parts[2], dtype=np.uint8).reshape(header["rgb_shape"]).copy())
