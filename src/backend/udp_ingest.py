import json
import socket
from collections.abc import Callable
from typing import Any


def start_udp_receiver(
    bind_ip: str, port: int, max_bytes: int, on_msg: Callable[[dict[str, Any]], None]
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_ip, port))

    while True:
        data, _addr = sock.recvfrom(max_bytes + 1)
        if len(data) > max_bytes:
            # Drop oversize datagrams (per spec)
            continue
        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            continue
        on_msg(msg)
