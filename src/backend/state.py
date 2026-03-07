from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

CMD_SEQ_MAX = 2_147_483_647
_CMD_SEQ_MOD = CMD_SEQ_MAX + 1


def _initial_cmd_seq() -> int:
    return int(time.monotonic() * 1000.0) % _CMD_SEQ_MOD


_TEL_DROP_REASON_ATTRS = {
    "oversize": "tel_drop_reason_oversize",
    "json": "tel_drop_reason_json",
    "schema": "tel_drop_reason_schema",
    "range": "tel_drop_reason_range",
}

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
    latest_intent: dict[str, Any] | None = None
    tel_seq: int = -1
    vis_seq: int = -1
    intent_last_update_monotonic_s: float | None = None

    tel_rx_total: int = 0
    tel_rx_ok: int = 0
    tel_rx_bad: int = 0
    tel_drop_reason_oversize: int = 0
    tel_drop_reason_json: int = 0
    tel_drop_reason_schema: int = 0
    tel_drop_reason_range: int = 0
    tel_last_seq: int = -1
    tel_last_timestamp_s: float | None = None
    tel_last_rx_monotonic_s: float | None = None

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

    fc_connected: bool = False
    fc_last_connect_attempt_s: float | None = None
    cmd_tx_total: int = 0
    cmd_tx_ok: int = 0
    cmd_tx_fail: int = 0
    cmd_last_sent_monotonic_s: float | None = None
    cmd_hz_est: float = 0.0
    tracking_blocked_reason: str | None = None

    cmd_next_seq: int = field(default_factory=_initial_cmd_seq)
    last_cmd_seq: int | None = None
    last_cmd_desired_mode: int | None = None
    last_cmd_had_tracking: bool = False
    last_cmd_bytes: int | None = None

    def reset(self) -> None:
        with self.lock:
            self.latest_tel = None
            self.latest_vis = None
            self.latest_intent = None
            self.tel_seq = -1
            self.vis_seq = -1
            self.intent_last_update_monotonic_s = None

            self.tel_rx_total = 0
            self.tel_rx_ok = 0
            self.tel_rx_bad = 0
            self.tel_drop_reason_oversize = 0
            self.tel_drop_reason_json = 0
            self.tel_drop_reason_schema = 0
            self.tel_drop_reason_range = 0
            self.tel_last_seq = -1
            self.tel_last_timestamp_s = None
            self.tel_last_rx_monotonic_s = None

            self.vis_rx_total = 0
            self.vis_rx_ok = 0
            self.vis_rx_bad = 0
            self.vis_drop_reason_oversize = 0
            self.vis_drop_reason_json = 0
            self.vis_drop_reason_schema = 0
            self.vis_drop_reason_range = 0
            self.vis_drop_reason_semantics = 0
            self.vis_last_seq = -1
            self.vis_last_timestamp_s = None
            self.vis_last_rx_monotonic_s = None

            self.fc_connected = False
            self.fc_last_connect_attempt_s = None
            self.cmd_tx_total = 0
            self.cmd_tx_ok = 0
            self.cmd_tx_fail = 0
            self.cmd_last_sent_monotonic_s = None
            self.cmd_hz_est = 0.0
            self.tracking_blocked_reason = None
            self.cmd_next_seq = _initial_cmd_seq()
            self.last_cmd_seq = None
            self.last_cmd_desired_mode = None
            self.last_cmd_had_tracking = False
            self.last_cmd_bytes = None

    def update_tel(self, msg: dict[str, Any]) -> None:
        self.record_tel_ok(msg=msg, rx_monotonic_s=time.monotonic())

    def update_vis(self, msg: dict[str, Any]) -> None:
        self.record_vis_ok(msg=msg, rx_monotonic_s=time.monotonic())

    def update_intent(
        self,
        intent: dict[str, Any],
        update_monotonic_s: float | None = None,
    ) -> None:
        now_s = update_monotonic_s if update_monotonic_s is not None else time.monotonic()
        with self.lock:
            self.latest_intent = dict(intent)
            self.intent_last_update_monotonic_s = float(now_s)

    def get_latest_intent(self) -> dict[str, Any] | None:
        with self.lock:
            if self.latest_intent is None:
                return None
            return dict(self.latest_intent)

    def record_tel_rx_total(self) -> None:
        with self.lock:
            self.tel_rx_total += 1

    def record_tel_ok(self, msg: dict[str, Any], rx_monotonic_s: float) -> None:
        with self.lock:
            self.latest_tel = dict(msg)
            self.tel_seq = int(msg.get("seq", self.tel_seq))
            self.tel_rx_ok += 1
            self.tel_last_seq = int(msg.get("seq", self.tel_last_seq))
            self.tel_last_timestamp_s = float(msg.get("timestamp_s", 0.0))
            self.tel_last_rx_monotonic_s = float(rx_monotonic_s)

    def record_tel_drop(self, reason: str) -> None:
        attr = _TEL_DROP_REASON_ATTRS.get(reason)
        if attr is None:
            raise ValueError(f"unsupported TEL drop reason: {reason}")
        with self.lock:
            self.tel_rx_bad += 1
            setattr(self, attr, getattr(self, attr) + 1)

    def record_vis_rx_total(self) -> None:
        with self.lock:
            self.vis_rx_total += 1

    def record_vis_ok(self, msg: dict[str, Any], rx_monotonic_s: float) -> None:
        with self.lock:
            self.latest_vis = dict(msg)
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

    def record_fc_connect_attempt(self, attempt_monotonic_s: float | None = None) -> None:
        now_s = attempt_monotonic_s if attempt_monotonic_s is not None else time.monotonic()
        with self.lock:
            self.fc_last_connect_attempt_s = float(now_s)

    def set_fc_connected(self, connected: bool) -> None:
        with self.lock:
            self.fc_connected = bool(connected)

    def set_tracking_blocked_reason(self, reason: str | None) -> None:
        with self.lock:
            self.tracking_blocked_reason = reason

    def record_cmd_tx_attempt(self) -> None:
        with self.lock:
            self.cmd_tx_total += 1

    def record_cmd_tx_ok(self, sent_monotonic_s: float | None = None) -> None:
        now_s = sent_monotonic_s if sent_monotonic_s is not None else time.monotonic()
        with self.lock:
            self.cmd_tx_ok += 1
            prev_sent_s = self.cmd_last_sent_monotonic_s
            self.cmd_last_sent_monotonic_s = float(now_s)
            if prev_sent_s is not None:
                delta_s = now_s - prev_sent_s
                if delta_s > 0:
                    inst_hz = 1.0 / delta_s
                    if self.cmd_hz_est <= 0.0:
                        self.cmd_hz_est = inst_hz
                    else:
                        self.cmd_hz_est = (0.8 * self.cmd_hz_est) + (0.2 * inst_hz)

    def record_cmd_tx_fail(self) -> None:
        with self.lock:
            self.cmd_tx_fail += 1

    def reserve_cmd_seq(self, minimum: int | None = None) -> int:
        with self.lock:
            if minimum is not None:
                clamped_minimum = max(0, min(int(minimum), CMD_SEQ_MAX))
                if self.cmd_next_seq < clamped_minimum:
                    self.cmd_next_seq = clamped_minimum
            seq = self.cmd_next_seq
            self.cmd_next_seq = 0 if seq >= CMD_SEQ_MAX else seq + 1
            return seq

    def ensure_cmd_seq_minimum(self, minimum: int) -> None:
        with self.lock:
            clamped_minimum = max(0, min(int(minimum), CMD_SEQ_MAX))
            if self.cmd_next_seq < clamped_minimum:
                self.cmd_next_seq = clamped_minimum

    def record_last_cmd_payload(self, payload: dict[str, Any], payload_bytes: int) -> None:
        with self.lock:
            seq = payload.get("seq")
            desired_mode = payload.get("desired_mode")
            self.last_cmd_seq = int(seq) if isinstance(seq, int) else None
            self.last_cmd_desired_mode = (
                int(desired_mode) if isinstance(desired_mode, int) else None
            )
            self.last_cmd_had_tracking = bool("tracking" in payload)
            self.last_cmd_bytes = int(payload_bytes)

    def get_cmd_bridge_status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "fc_connected": self.fc_connected,
                "fc_last_connect_attempt_s": self.fc_last_connect_attempt_s,
                "cmd_tx_total": self.cmd_tx_total,
                "cmd_tx_ok": self.cmd_tx_ok,
                "cmd_tx_fail": self.cmd_tx_fail,
                "cmd_last_sent_monotonic_s": self.cmd_last_sent_monotonic_s,
                "cmd_hz_est": self.cmd_hz_est,
                "tracking_blocked_reason": self.tracking_blocked_reason,
                "last_cmd_seq": self.last_cmd_seq,
                "last_cmd_desired_mode": self.last_cmd_desired_mode,
                "last_cmd_had_tracking": self.last_cmd_had_tracking,
                "last_cmd_bytes": self.last_cmd_bytes,
            }

    def get_latest_tel(self) -> dict[str, Any] | None:
        with self.lock:
            if self.latest_tel is None:
                return None
            return dict(self.latest_tel)

    def get_latest_tel_with_meta(self) -> tuple[dict[str, Any] | None, float | None]:
        with self.lock:
            if self.latest_tel is None:
                return None, None
            return dict(self.latest_tel), self.tel_last_rx_monotonic_s

    def get_tel_age_s(self, now_monotonic_s: float | None = None) -> float | None:
        with self.lock:
            last_rx = self.tel_last_rx_monotonic_s
        if last_rx is None:
            return None
        now_s = now_monotonic_s if now_monotonic_s is not None else time.monotonic()
        return max(0.0, float(now_s) - last_rx)

    def get_tel_stats(self) -> dict[str, Any]:
        with self.lock:
            last_rx = self.tel_last_rx_monotonic_s
            tel_age_s = None
            if last_rx is not None:
                tel_age_s = max(0.0, time.monotonic() - last_rx)
            return {
                "tel_rx_total": self.tel_rx_total,
                "tel_rx_ok": self.tel_rx_ok,
                "tel_rx_bad": self.tel_rx_bad,
                "tel_drop_reason_oversize": self.tel_drop_reason_oversize,
                "tel_drop_reason_json": self.tel_drop_reason_json,
                "tel_drop_reason_schema": self.tel_drop_reason_schema,
                "tel_drop_reason_range": self.tel_drop_reason_range,
                "tel_last_seq": self.tel_last_seq,
                "tel_last_timestamp_s": self.tel_last_timestamp_s,
                "tel_last_rx_monotonic_s": self.tel_last_rx_monotonic_s,
                "tel_age_s": tel_age_s,
            }

    def get_latest_vis(self) -> dict[str, Any] | None:
        with self.lock:
            if self.latest_vis is None:
                return None
            return dict(self.latest_vis)

    def get_latest_vis_with_meta(self) -> tuple[dict[str, Any] | None, float | None]:
        with self.lock:
            if self.latest_vis is None:
                return None, None
            return dict(self.latest_vis), self.vis_last_rx_monotonic_s

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

    def get_link_status(
        self,
        vis_fresh_s: float,
        tel_fresh_s: float,
        cmd_timeout_s: float,
        cmd_hz: float,
        tel_hz: float,
        now_monotonic_s: float | None = None,
    ) -> dict[str, Any]:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else time.monotonic()
        with self.lock:
            vis_age_s = None
            if self.vis_last_rx_monotonic_s is not None:
                vis_age_s = max(0.0, now_s - self.vis_last_rx_monotonic_s)

            tel_age_s = None
            if self.tel_last_rx_monotonic_s is not None:
                tel_age_s = max(0.0, now_s - self.tel_last_rx_monotonic_s)

            return {
                "fc_connected": self.fc_connected,
                "fc_last_connect_attempt_s": self.fc_last_connect_attempt_s,
                "cmd_tx_total": self.cmd_tx_total,
                "cmd_tx_ok": self.cmd_tx_ok,
                "cmd_tx_fail": self.cmd_tx_fail,
                "cmd_last_sent_monotonic_s": self.cmd_last_sent_monotonic_s,
                "cmd_hz_est": self.cmd_hz_est,
                "tracking_blocked_reason": self.tracking_blocked_reason,
                "vis_age_s": vis_age_s,
                "vis_rx_ok": self.vis_rx_ok,
                "vis_rx_bad": self.vis_rx_bad,
                "vis_drop_reason_oversize": self.vis_drop_reason_oversize,
                "vis_drop_reason_json": self.vis_drop_reason_json,
                "vis_drop_reason_schema": self.vis_drop_reason_schema,
                "vis_drop_reason_range": self.vis_drop_reason_range,
                "vis_drop_reason_semantics": self.vis_drop_reason_semantics,
                "tel_age_s": tel_age_s,
                "tel_rx_ok": self.tel_rx_ok,
                "tel_rx_bad": self.tel_rx_bad,
                "vis_fresh_s": float(vis_fresh_s),
                "tel_fresh_s": float(tel_fresh_s),
                "cmd_timeout_s": float(cmd_timeout_s),
                "cmd_hz": float(cmd_hz),
                "tel_hz": float(tel_hz),
            }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "tel": self.latest_tel,
                "vis": self.latest_vis,
                "intent": self.latest_intent,
                "tel_seq": self.tel_seq,
                "vis_seq": self.vis_seq,
                "tel_stats": {
                    "tel_rx_total": self.tel_rx_total,
                    "tel_rx_ok": self.tel_rx_ok,
                    "tel_rx_bad": self.tel_rx_bad,
                    "tel_drop_reason_oversize": self.tel_drop_reason_oversize,
                    "tel_drop_reason_json": self.tel_drop_reason_json,
                    "tel_drop_reason_schema": self.tel_drop_reason_schema,
                    "tel_drop_reason_range": self.tel_drop_reason_range,
                    "tel_last_seq": self.tel_last_seq,
                    "tel_last_timestamp_s": self.tel_last_timestamp_s,
                    "tel_last_rx_monotonic_s": self.tel_last_rx_monotonic_s,
                },
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
                "cmd_status": {
                    "fc_connected": self.fc_connected,
                    "fc_last_connect_attempt_s": self.fc_last_connect_attempt_s,
                    "cmd_tx_total": self.cmd_tx_total,
                    "cmd_tx_ok": self.cmd_tx_ok,
                    "cmd_tx_fail": self.cmd_tx_fail,
                    "cmd_last_sent_monotonic_s": self.cmd_last_sent_monotonic_s,
                    "cmd_hz_est": self.cmd_hz_est,
                    "tracking_blocked_reason": self.tracking_blocked_reason,
                    "last_cmd_seq": self.last_cmd_seq,
                    "last_cmd_desired_mode": self.last_cmd_desired_mode,
                    "last_cmd_had_tracking": self.last_cmd_had_tracking,
                    "last_cmd_bytes": self.last_cmd_bytes,
                },
            }
