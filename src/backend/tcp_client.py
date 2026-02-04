import json
import socket
import struct
from typing import Any


class TcpCommandClient:
    def __init__(self, host: str, port: int, max_frame_bytes: int = 4096) -> None:
        self.host = host
        self.port = port
        self.max_frame_bytes = max_frame_bytes
        self.sock: socket.socket | None = None

    def connect(self, timeout_s: float = 1.0) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout_s)
        s.connect((self.host, self.port))
        s.settimeout(None)
        self.sock = s

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send_cmd(self, payload: dict[str, Any]) -> None:
        if not self.sock:
            raise RuntimeError("TCP client not connected")
        raw = json.dumps(payload).encode("utf-8")
        if not (1 <= len(raw) <= self.max_frame_bytes):
            raise ValueError("payload too large")
        header = struct.pack(">I", len(raw))
        self.sock.sendall(header + raw)
