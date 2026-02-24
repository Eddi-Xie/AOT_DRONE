from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

_VIS_DROP_REASON_ATTRS = {
    "oversize": "vis_drop_reason_oversize",
    "json": "vis_drop_reason_json",
    "schema": "vis_drop_reason_schema",
    "range": "vis_drop_reason_range",
    "semantics": "vis_drop_reason_semantics",
}


@dataclass
class SharedState:
    lock: Lock = field(default_factory=Lock)
    latest_tel: dict[str, Any] | None = None
    latest_vis: dict[str, Any] | None = None
    tel_seq: int = -1
    vis_seq: int = -1
    vis_rx_total: int = 0
    vis_rx_ok: int = 0
    vis_rx_bad: int = 0
    vis_drop_reason_oversize: int = 0
    vis_drop_reason_json: int = 0
    vis_drop_reason_schema: int = 0
    vis_drop_reason_range: int = 0
    vis_drop_reason_semantics: int = 0
    vis_last_seq: int = -1
    vis_last_timestamp_s: float | None = None
    vis_last_rx_monotonic_s: float | None = None

    def update_tel(self, msg: dict[str, Any]) -> None:
        with self.lock:
            self.latest_tel = msg
            self.tel_seq = int(msg.get("seq", self.tel_seq))

    def update_vis(self, msg: dict[str, Any]) -> None:
        self.record_vis_ok(msg=msg, rx_monotonic_s=time.monotonic())

    def record_vis_rx_total(self) -> None:
        with self.lock:
            self.vis_rx_total += 1

    def record_vis_ok(self, msg: dict[str, Any], rx_monotonic_s: float) -> None:
        with self.lock:
            self.latest_vis = msg
            self.vis_seq = int(msg.get("seq", self.vis_seq))
            self.vis_rx_ok += 1
            self.vis_last_seq = int(msg.get("seq", self.vis_last_seq))
            self.vis_last_timestamp_s = float(msg.get("timestamp_s", 0.0))
            self.vis_last_rx_monotonic_s = float(rx_monotonic_s)

    def record_vis_drop(self, reason: str) -> None:
        attr = _VIS_DROP_REASON_ATTRS.get(reason)
        if attr is None:
            raise ValueError(f"unsupported VIS drop reason: {reason}")
        with self.lock:
            self.vis_rx_bad += 1
            setattr(self, attr, getattr(self, attr) + 1)

    def get_latest_vis(self) -> dict[str, Any] | None:
        with self.lock:
            if self.latest_vis is None:
                return None
            return dict(self.latest_vis)

    def get_vis_age_s(self, now_monotonic_s: float | None = None) -> float | None:
        with self.lock:
            last_rx = self.vis_last_rx_monotonic_s
        if last_rx is None:
            return None
        now_s = now_monotonic_s if now_monotonic_s is not None else time.monotonic()
        return max(0.0, float(now_s) - last_rx)

    def get_vis_stats(self) -> dict[str, Any]:
        with self.lock:
            last_rx = self.vis_last_rx_monotonic_s
            vis_age_s = None
            if last_rx is not None:
                vis_age_s = max(0.0, time.monotonic() - last_rx)
            return {
                "vis_rx_total": self.vis_rx_total,
                "vis_rx_ok": self.vis_rx_ok,
                "vis_rx_bad": self.vis_rx_bad,
                "vis_drop_reason_oversize": self.vis_drop_reason_oversize,
                "vis_drop_reason_json": self.vis_drop_reason_json,
                "vis_drop_reason_schema": self.vis_drop_reason_schema,
                "vis_drop_reason_range": self.vis_drop_reason_range,
                "vis_drop_reason_semantics": self.vis_drop_reason_semantics,
                "vis_last_seq": self.vis_last_seq,
                "vis_last_timestamp_s": self.vis_last_timestamp_s,
                "vis_last_rx_monotonic_s": self.vis_last_rx_monotonic_s,
                "vis_age_s": vis_age_s,
            }

    def get_vis_status(
        self,
        connected_threshold_s: float = 1.0,
        now_monotonic_s: float | None = None,
    ) -> dict[str, Any]:
        if connected_threshold_s < 0:
            raise ValueError("connected_threshold_s must be >= 0")
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else time.monotonic()
        with self.lock:
            last_rx = self.vis_last_rx_monotonic_s
            vis_age_s = None
            vis_connected = False
            if last_rx is not None:
                vis_age_s = max(0.0, now_s - last_rx)
                vis_connected = vis_age_s <= connected_threshold_s
            return {
                "vis_connected": vis_connected,
                "vis_age_s": vis_age_s,
                "vis_rx_ok": self.vis_rx_ok,
                "vis_rx_bad": self.vis_rx_bad,
                "vis_last_seq": self.vis_last_seq,
                "vis_last_timestamp_s": self.vis_last_timestamp_s,
            }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            now_s = time.monotonic()
            last_rx = self.vis_last_rx_monotonic_s
            vis_age_s = None
            vis_connected = False
            if last_rx is not None:
                vis_age_s = max(0.0, now_s - last_rx)
                vis_connected = vis_age_s <= 1.0
            return {
                "tel": self.latest_tel,
                "vis": self.latest_vis,
                "tel_seq": self.tel_seq,
                "vis_seq": self.vis_seq,
                "vis_stats": {
                    "vis_rx_total": self.vis_rx_total,
                    "vis_rx_ok": self.vis_rx_ok,
                    "vis_rx_bad": self.vis_rx_bad,
                    "vis_drop_reason_oversize": self.vis_drop_reason_oversize,
                    "vis_drop_reason_json": self.vis_drop_reason_json,
                    "vis_drop_reason_schema": self.vis_drop_reason_schema,
                    "vis_drop_reason_range": self.vis_drop_reason_range,
                    "vis_drop_reason_semantics": self.vis_drop_reason_semantics,
                    "vis_last_seq": self.vis_last_seq,
                    "vis_last_timestamp_s": self.vis_last_timestamp_s,
                    "vis_last_rx_monotonic_s": self.vis_last_rx_monotonic_s,
                },
                "vis_status": {
                    "vis_connected": vis_connected,
                    "vis_age_s": vis_age_s,
                    "vis_rx_ok": self.vis_rx_ok,
                    "vis_rx_bad": self.vis_rx_bad,
                    "vis_last_seq": self.vis_last_seq,
                    "vis_last_timestamp_s": self.vis_last_timestamp_s,
                },
            }
