"""One bounded RPC worker; sockets are worker-owned and replaced on every call."""
from concurrent.futures import ThreadPoolExecutor
import time
import zmq
from ai.serve.protocol import encode_request, decode_response, local_endpoint


class RPCClient:
    def __init__(self, endpoint, timeout_ms=250):
        self.endpoint = local_endpoint(endpoint)
        if timeout_ms <= 0: raise ValueError("Timeout must be positive")
        self.timeout_ms = timeout_ms
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="act-rpc")
        self.future = None
        self.closed = False

    def _call(self, header, observation, chunk_size):
        context = zmq.Context()
        socket = context.socket(zmq.REQ)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.SNDHWM, 1)
        socket.setsockopt(zmq.RCVHWM, 1)
        socket.setsockopt(zmq.IMMEDIATE, 1)
        if "[::1]" in self.endpoint: socket.setsockopt(zmq.IPV6, 1)
        try:
            socket.connect(self.endpoint)
            deadline = time.monotonic_ns() + self.timeout_ms * 1_000_000
            if not socket.poll(self.timeout_ms, zmq.POLLOUT): raise TimeoutError("server_connection_timeout")
            sent = time.monotonic_ns()
            socket.send_multipart(encode_request(header, observation), flags=zmq.NOBLOCK)
            remaining = max(0, (deadline - time.monotonic_ns()) // 1_000_000)
            if not socket.poll(remaining, zmq.POLLIN): raise TimeoutError("inference_response_timeout")
            response, chunk = decode_response(socket.recv_multipart(), header["session_id"], header["request_id"], chunk_size)
            return response, chunk, {"client_sent_monotonic_ns": sent, "client_received_monotonic_ns": time.monotonic_ns()}
        finally:
            socket.close()
            context.term()

    def submit(self, header, observation=None, chunk_size=None):
        if self.closed or self.future is not None:
            raise RuntimeError("RPC stopped or already pending")
        self.future = self.worker.submit(self._call, header, observation, chunk_size)

    def poll(self):
        if self.future is None or not self.future.done(): return None
        future, self.future = self.future, None
        return future.result()

    def control(self, header):
        self.submit(header)
        try:
            return self.future.result(timeout=self.timeout_ms / 1000 + 1)
        finally:
            self.future = None

    def close(self):
        self.closed = True
        self.worker.shutdown(wait=True, cancel_futures=True)
