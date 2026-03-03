from __future__ import annotations

from src.vision.udp_vis_sender import VisUdpSender


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload: bytes, addr: tuple[str, int]) -> int:
        self.sent.append((payload, addr))
        return len(payload)

    def close(self) -> None:
        return None


def _valid_vis_payload() -> dict[str, float | int | str]:
    return {
        "type": "VIS",
        "seq": 1,
        "timestamp_s": 100.0,
        "tracking_state": 3,
        "loc_x": 0.25,
        "loc_y": -0.1,
        "bound_w": 0.2,
        "bound_h": 0.3,
        "confidence": 0.9,
    }


def test_udp_vis_sender_compact_payload_is_within_512_bytes() -> None:
    fake_socket = _FakeSocket()
    sender = VisUdpSender(host="127.0.0.1", port=9003, max_payload_bytes=512, sock=fake_socket)

    sender.send(_valid_vis_payload())

    assert sender.sent_ok == 1
    assert sender.sent_fail == 0
    assert sender.dropped_oversize == 0
    assert len(fake_socket.sent) == 1
    encoded, destination = fake_socket.sent[0]
    assert destination == ("127.0.0.1", 9003)
    assert len(encoded) <= 512
    assert b" " not in encoded


def test_udp_vis_sender_drops_oversize_payload() -> None:
    fake_socket = _FakeSocket()
    sender = VisUdpSender(host="127.0.0.1", port=9003, max_payload_bytes=512, sock=fake_socket)

    payload = _valid_vis_payload()
    payload["debug_blob"] = "x" * 1024

    sender.send(payload)

    assert sender.sent_ok == 0
    assert sender.sent_fail == 0
    assert sender.dropped_oversize == 1
    assert fake_socket.sent == []
