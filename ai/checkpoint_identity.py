"""Existing server bundle identity, shared without importing Torch or ZMQ."""
import hashlib
from pathlib import Path


def checkpoint_identifier(directory):
    digest = hashlib.sha256()
    for path in sorted(Path(directory).iterdir()):
        if path.is_file() and path.suffix in (".json", ".safetensors"):
            digest.update(path.name.encode() + b"\0")
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
    return digest.hexdigest()
