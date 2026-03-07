export enum ControlMode {
  Manual = 0,
  Tracking = 1,
  LandSafely = 2,
  Takeoff = 3,
}

export enum TrackingState {
  NoTarget = 1,
  TargetDetected = 2,
  Tracking = 3,
  Searching = 4,
}

export type WarningSeverity = "info" | "warn" | "error";
export type OverlaySource = "VIS" | "TEL";

export type WsEventType = "TEL_UPDATE" | "VIS_UPDATE" | "LINK_STATUS" | "WARNING";

export interface WsEnvelope<TData = Record<string, unknown>> {
  ws_ver?: number;
  event: WsEventType;
  data: TData;
  timestamp_s: number;
  seq: number;
}

export interface LinkStatus {
  fc_connected: boolean;
  fc_last_connect_attempt_s: number | null;
  cmd_tx_total: number;
  cmd_tx_ok: number;
  cmd_tx_fail: number;
  cmd_last_sent_monotonic_s: number | null;
  cmd_hz_est: number;
  tracking_blocked_reason: string | null;
  vis_age_s: number | null;
  vis_rx_ok: number;
  vis_rx_bad: number;
  vis_drop_reason_oversize: number;
  vis_drop_reason_json: number;
  vis_drop_reason_schema: number;
  vis_drop_reason_range: number;
  vis_drop_reason_semantics: number;
  tel_age_s: number | null;
  tel_rx_ok: number;
  tel_rx_bad: number;
  vis_fresh_s: number;
  tel_fresh_s: number;
  cmd_timeout_s: number;
  cmd_hz: number;
  tel_hz: number;
  [key: string]: unknown;
}

export interface TelUpdate {
  type?: string;
  seq?: number;
  timestamp_s?: number;
  control_mode?: number;
  tracking_state?: number;
  distFront_m?: number;
  distBack_m?: number;
  distBottom_m?: number;
  target_x?: number;
  target_y?: number;
  bound_w?: number;
  bound_h?: number;
  confidence?: number;
  cmd_age_s?: number;
  rx_monotonic_s?: number | null;
  tel_age_s?: number | null;
  [key: string]: unknown;
}

export interface VisUpdate {
  type?: string;
  seq?: number;
  timestamp_s?: number;
  tracking_state?: number;
  loc_x?: number;
  loc_y?: number;
  bound_w?: number;
  bound_h?: number;
  confidence?: number;
  rx_monotonic_s?: number | null;
  vis_age_s?: number | null;
  [key: string]: unknown;
}

export interface WarningPayload {
  kind: string;
  detail: string;
  severity: WarningSeverity;
}

export interface WarningEntry extends WarningPayload {
  receivedAtMs: number;
  envelopeSeq: number;
}

export interface StatusAlert {
  id: string;
  detail: string;
  severity: WarningSeverity;
}

export interface IntentRequest {
  desired_mode: ControlMode;
  arm?: boolean;
  setpoints?: Record<string, unknown>;
}

export interface BackendConfig {
  httpUrl: string;
  wsUrl: string;
  videoUrl: string;
  overlaySource: OverlaySource;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function readNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

export function readBoolean(value: unknown): boolean | null {
  if (typeof value !== "boolean") {
    return null;
  }
  return value;
}

export function readString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  return value;
}

export function readNullableNumber(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  return readNumber(value);
}

export function toControlModeName(value: number | null | undefined): string {
  switch (value) {
    case ControlMode.Manual:
      return "Manual";
    case ControlMode.Tracking:
      return "Tracking";
    case ControlMode.LandSafely:
      return "LandSafely";
    case ControlMode.Takeoff:
      return "Takeoff";
    default:
      return "Unknown";
  }
}

export function toTrackingStateName(value: number | null | undefined): string {
  switch (value) {
    case TrackingState.NoTarget:
      return "NoTarget";
    case TrackingState.TargetDetected:
      return "TargetDetected";
    case TrackingState.Tracking:
      return "Tracking";
    case TrackingState.Searching:
      return "Searching";
    default:
      return "Unknown";
  }
}

export function formatNumber(
  value: number | null | undefined,
  digits = 2,
  suffix = "",
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return `${value.toFixed(digits)}${suffix}`;
}

export function asLinkStatus(value: unknown): LinkStatus | null {
  if (!isRecord(value)) {
    return null;
  }

  const fcConnected = readBoolean(value.fc_connected);
  if (fcConnected === null) {
    return null;
  }

  return {
    ...value,
    fc_connected: fcConnected,
    fc_last_connect_attempt_s: readNullableNumber(value.fc_last_connect_attempt_s),
    cmd_tx_total: readNumber(value.cmd_tx_total) ?? 0,
    cmd_tx_ok: readNumber(value.cmd_tx_ok) ?? 0,
    cmd_tx_fail: readNumber(value.cmd_tx_fail) ?? 0,
    cmd_last_sent_monotonic_s: readNullableNumber(value.cmd_last_sent_monotonic_s),
    cmd_hz_est: readNumber(value.cmd_hz_est) ?? 0,
    tracking_blocked_reason: readString(value.tracking_blocked_reason),
    vis_age_s: readNullableNumber(value.vis_age_s),
    vis_rx_ok: readNumber(value.vis_rx_ok) ?? 0,
    vis_rx_bad: readNumber(value.vis_rx_bad) ?? 0,
    vis_drop_reason_oversize: readNumber(value.vis_drop_reason_oversize) ?? 0,
    vis_drop_reason_json: readNumber(value.vis_drop_reason_json) ?? 0,
    vis_drop_reason_schema: readNumber(value.vis_drop_reason_schema) ?? 0,
    vis_drop_reason_range: readNumber(value.vis_drop_reason_range) ?? 0,
    vis_drop_reason_semantics: readNumber(value.vis_drop_reason_semantics) ?? 0,
    tel_age_s: readNullableNumber(value.tel_age_s),
    tel_rx_ok: readNumber(value.tel_rx_ok) ?? 0,
    tel_rx_bad: readNumber(value.tel_rx_bad) ?? 0,
    vis_fresh_s: readNumber(value.vis_fresh_s) ?? 0,
    tel_fresh_s: readNumber(value.tel_fresh_s) ?? 0,
    cmd_timeout_s: readNumber(value.cmd_timeout_s) ?? 0,
    cmd_hz: readNumber(value.cmd_hz) ?? 0,
    tel_hz: readNumber(value.tel_hz) ?? 0,
  };
}

export function asTelUpdate(value: unknown): TelUpdate | null {
  if (!isRecord(value)) {
    return null;
  }
  return value as TelUpdate;
}

export function asVisUpdate(value: unknown): VisUpdate | null {
  if (!isRecord(value)) {
    return null;
  }
  return value as VisUpdate;
}

export function asWarningPayload(value: unknown): WarningPayload | null {
  if (!isRecord(value)) {
    return null;
  }

  const kind = readString(value.kind);
  const detail = readString(value.detail);
  const severity = readString(value.severity);

  if (
    kind === null ||
    detail === null ||
    (severity !== "info" && severity !== "warn" && severity !== "error")
  ) {
    return null;
  }

  return {
    kind,
    detail,
    severity,
  };
}
