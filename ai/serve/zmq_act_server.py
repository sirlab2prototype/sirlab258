#!/usr/bin/env python3
"""Localhost two-camera ACT chunk server. No ROS or robot commands."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import signal
import time
import uuid

import numpy as np
import zmq
from ai.serve.protocol import PROTOCOL, decode_request, local_endpoint, validate_chunk
from ai.checkpoint_identity import checkpoint_identifier


class InferenceService:
    def __init__(self, backend, model_id, *, max_observation_age_ms=250):
        self.backend, self.model_id = backend, model_id
        self.chunk_size = backend.policy.config.chunk_size
        self.n_action_steps = backend.policy.config.n_action_steps
        self.active_session = None
        self.session_started = -1
        self.last_request_ids = set()
        self.generated_chunks = 0
        self.max_age_ns = round(max_observation_age_ms * 1e6)

    def handle(self, parts):
        received = time.monotonic_ns()
        header = {}
        try:
            header, observation = decode_request(parts)
            command, session = header["command"], header["session_id"]
            reply = {"protocol": PROTOCOL, "session_id": session, "request_id": header["request_id"],
                     "ok": True, "checkpoint_id": self.model_id, "server_received_monotonic_ns": received,
                     "chunk_size": self.chunk_size, "n_action_steps": self.n_action_steps, "fps": 20}
            reply["session_generated_chunks"] = self.generated_chunks
            if command == "reset":
                started = header.get("session_started_monotonic_ns")
                if type(started) is not int or started < self.session_started or not 0 <= started <= received:
                    raise ValueError("obsolete_or_invalid_session_reset")
                self.backend.reset()
                self.active_session, self.session_started = session, started
                self.last_request_ids.clear()
                self.generated_chunks = 0
                reply["session_generated_chunks"] = 0
                return [json.dumps({**reply, "reset": True}).encode()]
            if session != self.active_session:
                raise ValueError("inactive_session")
            if command == "end":
                self.backend.reset()
                self.active_session = None
                self.last_request_ids.clear()
                return [json.dumps({**reply, "ended": True}).encode()]
            if command != "predict":
                raise ValueError("unsupported_command")
            if header["request_id"] in self.last_request_ids:
                raise ValueError("duplicate_request")
            if len(self.last_request_ids) >= 4096:
                # IDs must be unique for the whole session without unbounded RAM.
                raise ValueError("session_request_limit_reset_required")
            self.last_request_ids.add(header["request_id"])
            age = received - header["observation_monotonic_ns"]
            if not 0 <= age <= self.max_age_ns:
                raise ValueError("stale_or_future_observation")
            started = time.monotonic_ns()
            chunk = validate_chunk(self.backend.predict_chunk(observation), self.chunk_size)
            completed = time.monotonic_ns()
            self.generated_chunks += 1
            reply.update(shape=list(chunk.shape), output_valid=True, inference_ns=completed - started,
                         server_completed_monotonic_ns=completed,
                         observation_monotonic_ns=header["observation_monotonic_ns"], observation_age_at_receive_ns=age)
            if getattr(self.backend, "last_timing", None):
                reply["inference_stages"] = self.backend.last_timing
            reply["session_generated_chunks"] = self.generated_chunks
            return [json.dumps(reply).encode(), np.ascontiguousarray(chunk, dtype="<f4").tobytes()]
        except Exception as exc:
            return [json.dumps({"protocol": PROTOCOL, "ok": False, "output_valid": False,
                    "session_id": header.get("session_id"), "request_id": header.get("request_id"),
                    "checkpoint_id": self.model_id, "server_received_monotonic_ns": received,
                    "server_completed_monotonic_ns": time.monotonic_ns(),
                    "error": {"code": type(exc).__name__, "message": str(exc)}}).encode()]


def serve(service, endpoint, *, stop=None, ready=None, event=None):
    context = zmq.Context()
    socket = context.socket(zmq.REP)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVHWM, 2)
    socket.setsockopt(zmq.SNDHWM, 2)
    socket.setsockopt(zmq.MAXMSGSIZE, 4_000_000)
    if "[::1]" in endpoint: socket.setsockopt(zmq.IPV6, 1)
    try:
        socket.bind(local_endpoint(endpoint))
        if ready: ready()
        while stop is None or not stop():
            if socket.poll(100):
                parts = socket.recv_multipart()
                result = service.handle(parts)
                if event: event(json.loads(result[0]))
                socket.send_multipart(result)
    finally:
        socket.close()
        context.term()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", "--model", required=True)
    parser.add_argument("--endpoint", default="tcp://127.0.0.1:5557")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--max-observation-age-ms", type=float, default=250)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    local_endpoint(args.endpoint)
    import torch
    from ai.train.checkpoint import verify_checkpoint, check_files
    from ai.serve.rgb_act_backend import RGBActBackend
    if args.torch_threads < 1 or not np.isfinite(args.max_observation_age_ms) or args.max_observation_age_ms <= 0:
        parser.error("Invalid threads/observation age")
    torch.set_num_threads(args.torch_threads)
    device = ("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else args.device
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable")
    for path in (args.ready_file, args.log):
        if path and path.exists(): raise ValueError(f"Refusing to overwrite: {path}")
    model = check_files(args.checkpoint)
    verification = verify_checkpoint(model, device=device)
    backend = RGBActBackend(model, device=device)
    if backend.policy.config.temporal_ensemble_coeff is not None:
        raise ValueError("This chunk client does not implement temporal ensembling")
    model_id = checkpoint_identifier(model)
    service = InferenceService(backend, model_id, max_observation_age_ms=args.max_observation_age_ms)
    metadata = {"run_id": args.run_id or uuid.uuid4().hex, "checkpoint": str(model), "checkpoint_id": model_id, "protocol": PROTOCOL,
                "chunk_size": service.chunk_size, "n_action_steps": service.n_action_steps, "fps": 20,
                "device": str(backend.device), "device_name": torch.cuda.get_device_name(backend.device) if backend.device.type == "cuda" else "CPU",
                "verification": verification}
    running = True
    def stop_handler(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    def ready():
        if args.ready_file:
            with args.ready_file.open("x") as stream: json.dump(metadata, stream, indent=2)
        print(json.dumps({"server_ready": True, **metadata}), flush=True)
    stream = args.log.open("x") if args.log else None
    try:
        def event(value):
            if stream:
                stream.write(json.dumps({"event": "server_response", "run_id": metadata["run_id"],
                                         "logged_monotonic_ns": time.monotonic_ns(), **value}) + "\n")
                stream.flush()
        serve(service, args.endpoint, stop=lambda: not running, ready=ready, event=event)
    finally:
        if stream: stream.close()


if __name__ == "__main__":
    main()
