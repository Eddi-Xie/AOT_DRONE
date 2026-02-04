from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class SharedState:
    lock: Lock = field(default_factory=Lock)
    latest_tel: dict[str, Any] | None = None
    latest_vis: dict[str, Any] | None = None
    tel_seq: int = -1
    vis_seq: int = -1

    def update_tel(self, msg: dict[str, Any]) -> None:
        with self.lock:
            self.latest_tel = msg
            self.tel_seq = int(msg.get("seq", self.tel_seq))

    def update_vis(self, msg: dict[str, Any]) -> None:
        with self.lock:
            self.latest_vis = msg
            self.vis_seq = int(msg.get("seq", self.vis_seq))

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "tel": self.latest_tel,
                "vis": self.latest_vis,
                "tel_seq": self.tel_seq,
                "vis_seq": self.vis_seq,
            }
