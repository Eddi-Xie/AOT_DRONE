from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from .state import SharedState
from .ws_manager import WsManager

_EVENT_TEL_UPDATE = "TEL_UPDATE"
_EVENT_VIS_UPDATE = "VIS_UPDATE"
_EVENT_LINK_STATUS = "LINK_STATUS"
_EVENT_WARNING = "WARNING"


class BackendBroadcaster:
    def __init__(
        self,
        state: SharedState,
        ws_manager: WsManager,
        tel_hz: float = 20.0,
        vis_hz: float = 20.0,
        link_hz: float = 2.0,
        vis_fresh_s: float = 0.25,
        tel_fresh_s: float = 0.5,
        cmd_timeout_s: float = 0.5,
        cmd_hz_nominal: float = 50.0,
        tel_hz_nominal: float = 50.0,
        warning_min_interval_s: float = 2.0,
        link_status_extra_provider: Callable[[float], dict[str, Any]] | None = None,
        clock: Callable[[], float] | None = None,
        wait_fn: Callable[[threading.Event, float], None] | None = None,
    ) -> None:
        if tel_hz <= 0:
            raise ValueError("tel_hz must be > 0")
        if vis_hz <= 0:
            raise ValueError("vis_hz must be > 0")
        if link_hz <= 0:
            raise ValueError("link_hz must be > 0")
        if vis_fresh_s < 0:
            raise ValueError("vis_fresh_s must be >= 0")
        if tel_fresh_s <= 0:
            raise ValueError("tel_fresh_s must be > 0")
        if cmd_timeout_s <= 0:
            raise ValueError("cmd_timeout_s must be > 0")
        if warning_min_interval_s < 0:
            raise ValueError("warning_min_interval_s must be >= 0")

        self._state = state
        self._ws_manager = ws_manager
        self.tel_hz = tel_hz
        self.vis_hz = vis_hz
        self.link_hz = link_hz
        self.vis_fresh_s = vis_fresh_s
        self.tel_fresh_s = tel_fresh_s
        self.cmd_timeout_s = cmd_timeout_s
        self.cmd_hz_nominal = cmd_hz_nominal
        self.tel_hz_nominal = tel_hz_nominal
        self.warning_min_interval_s = warning_min_interval_s
        self._link_status_extra_provider = link_status_extra_provider

        self._clock = clock or time.monotonic
        self._wait_fn = wait_fn or self._default_wait
        self._last_emit_s: dict[str, float | None] = {
            _EVENT_TEL_UPDATE: None,
            _EVENT_VIS_UPDATE: None,
            _EVENT_LINK_STATUS: None,
        }
        self._warning_last_emit_s: dict[str, float] = {}

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_heartbeat,
            name="ws-link-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self, join_timeout_s: float = 1.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_s)

    def build_link_status(self, now_monotonic_s: float | None = None) -> dict[str, Any]:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else self._clock()
        payload = self._state.get_link_status(
            vis_fresh_s=self.vis_fresh_s,
            tel_fresh_s=self.tel_fresh_s,
            cmd_timeout_s=self.cmd_timeout_s,
            cmd_hz=self.cmd_hz_nominal,
            tel_hz=self.tel_hz_nominal,
            now_monotonic_s=now_s,
        )
        if self._link_status_extra_provider is not None:
            payload.update(dict(self._link_status_extra_provider(now_s)))
        return payload

    def build_tel_update_data(self, now_monotonic_s: float | None = None) -> dict[str, Any] | None:
        tel_snapshot, tel_rx_s = self._state.get_latest_tel_with_meta()
        if tel_snapshot is None:
            return None
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else self._clock()
        payload = dict(tel_snapshot)
        payload["rx_monotonic_s"] = tel_rx_s
        payload["tel_age_s"] = self._state.get_tel_age_s(now_monotonic_s=now_s)
        return payload

    def build_vis_update_data(self, now_monotonic_s: float | None = None) -> dict[str, Any] | None:
        vis_snapshot, vis_rx_s = self._state.get_latest_vis_with_meta()
        if vis_snapshot is None:
            return None
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else self._clock()
        payload = dict(vis_snapshot)
        payload["rx_monotonic_s"] = vis_rx_s
        payload["vis_age_s"] = self._state.get_vis_age_s(now_monotonic_s=now_s)
        return payload

    def on_tel_update(self, _msg: dict[str, Any], rx_monotonic_s: float) -> None:
        self.broadcast_tel_update(now_monotonic_s=rx_monotonic_s)

    def on_vis_update(self, _msg: dict[str, Any], rx_monotonic_s: float) -> None:
        self.broadcast_vis_update(now_monotonic_s=rx_monotonic_s)

    def on_tel_drop(self, reason: str, detail: str) -> None:
        if reason == "oversize":
            tel_stats = self._state.get_tel_stats()
            drop_count = int(tel_stats["tel_drop_reason_oversize"])
            self.emit_warning(
                kind="tel_oversize_drop",
                detail=f"Oversize TEL datagram dropped: {detail} (count={drop_count})",
                severity="warn",
            )

    def on_vis_drop(self, reason: str, detail: str) -> None:
        if reason == "oversize":
            vis_stats = self._state.get_vis_stats()
            drop_count = int(vis_stats["vis_drop_reason_oversize"])
            self.emit_warning(
                kind="vis_oversize_drop",
                detail=f"Oversize VIS datagram dropped: {detail} (count={drop_count})",
                severity="warn",
            )

    def on_fc_connection_changed(self, connected: bool) -> None:
        if connected:
            self.emit_warning(
                kind="fc_reconnected",
                detail="Backend command bridge connected to FC TCP server",
                severity="info",
            )
        else:
            self.emit_warning(
                kind="fc_disconnected",
                detail="Backend command bridge disconnected from FC TCP server",
                severity="warn",
            )
        self.broadcast_link_status(force=True)

    def on_tracking_blocked(self, reason: str) -> None:
        self.emit_warning(
            kind=f"tracking_blocked_{reason}",
            detail=f"Tracking mode request blocked by backend safety gate ({reason})",
            severity="warn",
        )
        self.broadcast_link_status(force=True)

    def broadcast_tel_update(
        self,
        now_monotonic_s: float | None = None,
        force: bool = False,
    ) -> bool:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else self._clock()
        if not self._can_emit(_EVENT_TEL_UPDATE, now_s, force=force):
            return False
        payload = self.build_tel_update_data(now_monotonic_s=now_s)
        if payload is None:
            return False
        self._ws_manager.broadcast(_EVENT_TEL_UPDATE, payload, timestamp_s=now_s)
        self._last_emit_s[_EVENT_TEL_UPDATE] = now_s
        return True

    def broadcast_vis_update(
        self,
        now_monotonic_s: float | None = None,
        force: bool = False,
    ) -> bool:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else self._clock()
        if not self._can_emit(_EVENT_VIS_UPDATE, now_s, force=force):
            return False
        payload = self.build_vis_update_data(now_monotonic_s=now_s)
        if payload is None:
            return False
        self._ws_manager.broadcast(_EVENT_VIS_UPDATE, payload, timestamp_s=now_s)
        self._last_emit_s[_EVENT_VIS_UPDATE] = now_s
        return True

    def broadcast_link_status(
        self,
        now_monotonic_s: float | None = None,
        force: bool = False,
    ) -> bool:
        now_s = float(now_monotonic_s) if now_monotonic_s is not None else self._clock()
        if not self._can_emit(_EVENT_LINK_STATUS, now_s, force=force):
            return False
        payload = self.build_link_status(now_monotonic_s=now_s)
        self._ws_manager.broadcast(_EVENT_LINK_STATUS, payload, timestamp_s=now_s)
        self._last_emit_s[_EVENT_LINK_STATUS] = now_s
        return True

    def emit_warning(self, kind: str, detail: str, severity: str) -> bool:
        now_s = self._clock()
        last_emit_s = self._warning_last_emit_s.get(kind)
        if (
            last_emit_s is not None
            and self.warning_min_interval_s > 0.0
            and (now_s - last_emit_s) < self.warning_min_interval_s
        ):
            return False

        payload = {
            "kind": kind,
            "detail": detail,
            "severity": severity,
        }
        self._ws_manager.broadcast(_EVENT_WARNING, payload, timestamp_s=now_s)
        self._warning_last_emit_s[kind] = now_s
        return True

    def _run_heartbeat(self) -> None:
        period_s = 1.0 / self.link_hz
        while not self._stop_event.is_set():
            self.broadcast_link_status(force=True)
            self._wait_fn(self._stop_event, period_s)

    def _can_emit(self, event: str, now_s: float, force: bool) -> bool:
        if force:
            return True
        period_s = self._period_s(event)
        last_emit_s = self._last_emit_s.get(event)
        if last_emit_s is None:
            return True
        return (now_s - last_emit_s) >= period_s

    def _period_s(self, event: str) -> float:
        if event == _EVENT_TEL_UPDATE:
            return 1.0 / self.tel_hz
        if event == _EVENT_VIS_UPDATE:
            return 1.0 / self.vis_hz
        if event == _EVENT_LINK_STATUS:
            return 1.0 / self.link_hz
        return 0.0

    @staticmethod
    def _default_wait(stop_event: threading.Event, timeout_s: float) -> None:
        stop_event.wait(timeout=timeout_s)
