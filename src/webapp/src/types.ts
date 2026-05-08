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
  apiToken?: string;
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

export interface ParsedUpdate<T> {
  // The validated payload, or null if the input wasn't even a record (silently skipped).
  value: T | null;
  // Set when the input was a record but failed schema/range validation. Receivers
  // should surface a one-shot warning so a backend-side schema drift doesn't fail
  // silently.
  mismatch: string | null;
}

const TRACKING_STATE_VALUES = new Set<number>([
  TrackingState.NoTarget,
  TrackingState.TargetDetected,
  TrackingState.Tracking,
  TrackingState.Searching,
]);

const CONTROL_MODE_VALUES = new Set<number>([
  ControlMode.Manual,
  ControlMode.Tracking,
  ControlMode.LandSafely,
  ControlMode.Takeoff,
]);

function readBoundedNumber(
  value: unknown,
  min: number,
  max: number,
): { value: number | null; outOfRange: boolean } {
  if (value === undefined) {
    return { value: null, outOfRange: false };
  }
  const parsed = readNumber(value);
  if (parsed === null) {
    return { value: null, outOfRange: true };
  }
  if (parsed < min || parsed > max) {
    return { value: null, outOfRange: true };
  }
  return { value: parsed, outOfRange: false };
}

function readEnumNumber(
  value: unknown,
  allowed: Set<number>,
): { value: number | null; outOfRange: boolean } {
  if (value === undefined) {
    return { value: null, outOfRange: false };
  }
  const parsed = readNumber(value);
  if (parsed === null || !allowed.has(parsed)) {
    return { value: null, outOfRange: true };
  }
  return { value: parsed, outOfRange: false };
}

export function parseTelUpdate(value: unknown): ParsedUpdate<TelUpdate> {
  if (!isRecord(value)) {
    return { value: null, mismatch: null };
  }

  const targetX = readBoundedNumber(value.target_x, -1, 1);
  const targetY = readBoundedNumber(value.target_y, -1, 1);
  const boundW = readBoundedNumber(value.bound_w, 0, 1);
  const boundH = readBoundedNumber(value.bound_h, 0, 1);
  const confidence = readBoundedNumber(value.confidence, 0, 1);
  const trackingState = readEnumNumber(value.tracking_state, TRACKING_STATE_VALUES);
  const controlMode = readEnumNumber(value.control_mode, CONTROL_MODE_VALUES);

  if (
    targetX.outOfRange ||
    targetY.outOfRange ||
    boundW.outOfRange ||
    boundH.outOfRange ||
    confidence.outOfRange ||
    trackingState.outOfRange ||
    controlMode.outOfRange
  ) {
    return { value: null, mismatch: "TEL_UPDATE field out of range or wrong type" };
  }

  const tel: TelUpdate = {
    ...value,
    type: readString(value.type) ?? undefined,
    seq: readNumber(value.seq) ?? undefined,
    timestamp_s: readNumber(value.timestamp_s) ?? undefined,
    control_mode: controlMode.value ?? undefined,
    tracking_state: trackingState.value ?? undefined,
    distFront_m: readNumber(value.distFront_m) ?? undefined,
    distBack_m: readNumber(value.distBack_m) ?? undefined,
    distBottom_m: readNumber(value.distBottom_m) ?? undefined,
    target_x: targetX.value ?? undefined,
    target_y: targetY.value ?? undefined,
    bound_w: boundW.value ?? undefined,
    bound_h: boundH.value ?? undefined,
    confidence: confidence.value ?? undefined,
    cmd_age_s: readNumber(value.cmd_age_s) ?? undefined,
    rx_monotonic_s: readNullableNumber(value.rx_monotonic_s),
    tel_age_s: readNullableNumber(value.tel_age_s),
  };

  return { value: tel, mismatch: null };
}

export function parseVisUpdate(value: unknown): ParsedUpdate<VisUpdate> {
  if (!isRecord(value)) {
    return { value: null, mismatch: null };
  }

  const locX = readBoundedNumber(value.loc_x, -1, 1);
  const locY = readBoundedNumber(value.loc_y, -1, 1);
  const boundW = readBoundedNumber(value.bound_w, 0, 1);
  const boundH = readBoundedNumber(value.bound_h, 0, 1);
  const confidence = readBoundedNumber(value.confidence, 0, 1);
  const trackingState = readEnumNumber(value.tracking_state, TRACKING_STATE_VALUES);

  if (
    locX.outOfRange ||
    locY.outOfRange ||
    boundW.outOfRange ||
    boundH.outOfRange ||
    confidence.outOfRange ||
    trackingState.outOfRange
  ) {
    return { value: null, mismatch: "VIS_UPDATE field out of range or wrong type" };
  }

  const vis: VisUpdate = {
    ...value,
    type: readString(value.type) ?? undefined,
    seq: readNumber(value.seq) ?? undefined,
    timestamp_s: readNumber(value.timestamp_s) ?? undefined,
    tracking_state: trackingState.value ?? undefined,
    loc_x: locX.value ?? undefined,
    loc_y: locY.value ?? undefined,
    bound_w: boundW.value ?? undefined,
    bound_h: boundH.value ?? undefined,
    confidence: confidence.value ?? undefined,
    rx_monotonic_s: readNullableNumber(value.rx_monotonic_s),
    vis_age_s: readNullableNumber(value.vis_age_s),
  };

  return { value: vis, mismatch: null };
}

export function asTelUpdate(value: unknown): TelUpdate | null {
  return parseTelUpdate(value).value;
}

export function asVisUpdate(value: unknown): VisUpdate | null {
  return parseVisUpdate(value).value;
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
