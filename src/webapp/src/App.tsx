import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { getBackendConfig, postIntent } from "./api";
import ControlPanel from "./components/ControlPanel";
import ErrorBoundary from "./components/ErrorBoundary";
import StatusBadge, { BadgeSeverity } from "./components/StatusBadge";
import TelemetryPanel from "./components/TelemetryPanel";
import TrackingSummary from "./components/TrackingSummary";
import VideoPanel from "./components/VideoPanel";
import Warnings from "./components/Warnings";
import {
  ControlMode,
  IntentRequest,
  LinkStatus,
  StatusAlert,
  TelUpdate,
  TrackingState,
  VisUpdate,
  WarningEntry,
  WsEnvelope,
  asLinkStatus,
  asWarningPayload,
  parseTelUpdate,
  parseVisUpdate,
  formatNumber,
  readNumber,
  toControlModeName,
  toTrackingStateName,
} from "./types";
import { formatSeconds, projectAge } from "./utils/format";
import { DEFAULT_HISTORY_LIMIT, pushHistorySample } from "./utils/historyBuffer";
import { runOverlayMathDevAssertions } from "./utils/overlayMathAssertions";
import { ReconnectingWsClient } from "./ws";

type IntentFeedbackKind = "idle" | "sending" | "success" | "error";

const MAX_WARNING_EVENTS = 120;
const DERIVED_WARNING_HOLD_MS = 1500;
const DEFAULT_VIS_FRESH_S = 0.25;
const DEFAULT_TEL_FRESH_S = 0.5;

interface AppState {
  wsConnected: boolean;
  lastWsMessageAtMs: number | null;
  linkStatus: LinkStatus | null;
  linkUpdatedAtMs: number | null;
  linkEnvelopeTimestampS: number | null;
  latestTel: TelUpdate | null;
  latestVis: VisUpdate | null;
  warnings: WarningEntry[];
  confidenceHistory: number[];
  ageHistory: number[];
  selectedMode: ControlMode;
  armed: boolean;
  intentPending: boolean;
  intentFeedbackKind: IntentFeedbackKind;
  intentFeedbackMessage: string;
  schemaMismatchDetail: string | null;
}

interface PendingStreamBatch {
  linkStatus?: LinkStatus;
  linkUpdatedAtMs?: number;
  linkEnvelopeTimestampS?: number;
  latestTel?: TelUpdate;
  latestVis?: VisUpdate;
  overlayConfidenceSample?: number;
  overlayAgeSample?: number;
  lastMessageAtMs: number | null;
}

interface SelectedTrackingData {
  trackingState: number | null;
  confidence: number | null;
  boundW: number | null;
  boundH: number | null;
  targetX: number | null;
  targetY: number | null;
  ageSFromMessage: number | null;
}

function areStatusAlertsEqual(a: readonly StatusAlert[], b: readonly StatusAlert[]): boolean {
  if (a === b) {
    return true;
  }
  if (a.length !== b.length) {
    return false;
  }
  for (let index = 0; index < a.length; index += 1) {
    const left = a[index];
    const right = b[index];
    if (left.id !== right.id || left.detail !== right.detail || left.severity !== right.severity) {
      return false;
    }
  }
  return true;
}

type AppAction =
  | { type: "WS_CONNECTION_CHANGED"; connected: boolean }
  | {
      type: "STREAM_BATCH";
      batch: PendingStreamBatch;
    }
  | {
      type: "WARNING_RECEIVED";
      warning: WarningEntry;
      receivedAtMs: number;
    }
  | { type: "CLEAR_WARNINGS" }
  | { type: "SCHEMA_MISMATCH_DETECTED"; detail: string }
  | { type: "SET_SELECTED_MODE"; mode: ControlMode }
  | { type: "SET_ARMED"; armed: boolean }
  | { type: "INTENT_PENDING"; message: string }
  | {
      type: "INTENT_FINISHED";
      kind: "success" | "error";
      message: string;
    };

const INITIAL_STATE: AppState = {
  wsConnected: false,
  lastWsMessageAtMs: null,
  linkStatus: null,
  linkUpdatedAtMs: null,
  linkEnvelopeTimestampS: null,
  latestTel: null,
  latestVis: null,
  warnings: [],
  confidenceHistory: [],
  ageHistory: [],
  selectedMode: ControlMode.Manual,
  armed: false,
  intentPending: false,
  intentFeedbackKind: "idle",
  intentFeedbackMessage: "Ready.",
  schemaMismatchDetail: null,
};

function normalizeConfidenceSample(value: number | null): number {
  if (value === null) {
    return 0;
  }
  if (value < 0) {
    return 0;
  }
  if (value > 1) {
    return 1;
  }
  return value;
}

function normalizeAgeSample(value: number | null): number {
  if (value === null || value < 0) {
    return 0;
  }
  return value;
}

function toTrackingBadgeSeverity(trackingState: number | null): BadgeSeverity {
  switch (trackingState) {
    case TrackingState.TargetDetected:
      return "info";
    case TrackingState.Tracking:
      return "good";
    case TrackingState.Searching:
      return "warn";
    case TrackingState.NoTarget:
      return "neutral";
    default:
      return "neutral";
  }
}

function mapBlockedReason(reason: string | null): { label: string; severity: BadgeSeverity } | null {
  if (!reason) {
    return null;
  }

  if (reason === "stale_vis") {
    return {
      label: "Vision stale",
      severity: "warn",
    };
  }

  if (reason === "no_vis") {
    return {
      label: "No vision",
      severity: "bad",
    };
  }

  return {
    label: `Blocked: ${reason}`,
    severity: "warn",
  };
}

function selectTrackingData(
  overlaySource: "VIS" | "TEL",
  latestVis: VisUpdate | null,
  latestTel: TelUpdate | null,
): SelectedTrackingData {
  if (overlaySource === "TEL") {
    return {
      trackingState: readNumber(latestTel?.tracking_state),
      confidence: readNumber(latestTel?.confidence),
      boundW: readNumber(latestTel?.bound_w),
      boundH: readNumber(latestTel?.bound_h),
      targetX: readNumber(latestTel?.target_x),
      targetY: readNumber(latestTel?.target_y),
      ageSFromMessage: readNumber(latestTel?.tel_age_s),
    };
  }

  return {
    trackingState: readNumber(latestVis?.tracking_state),
    confidence: readNumber(latestVis?.confidence),
    boundW: readNumber(latestVis?.bound_w),
    boundH: readNumber(latestVis?.bound_h),
    targetX: readNumber(latestVis?.loc_x),
    targetY: readNumber(latestVis?.loc_y),
    ageSFromMessage: readNumber(latestVis?.vis_age_s),
  };
}

function reducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "WS_CONNECTION_CHANGED":
      return {
        ...state,
        wsConnected: action.connected,
      };
    case "STREAM_BATCH": {
      const nextState: AppState = {
        ...state,
        lastWsMessageAtMs: action.batch.lastMessageAtMs,
      };

      if (action.batch.linkStatus) {
        nextState.linkStatus = action.batch.linkStatus;
        nextState.linkUpdatedAtMs = action.batch.linkUpdatedAtMs ?? state.linkUpdatedAtMs;
        nextState.linkEnvelopeTimestampS =
          action.batch.linkEnvelopeTimestampS ?? state.linkEnvelopeTimestampS;
      }

      if (action.batch.latestTel) {
        nextState.latestTel = action.batch.latestTel;
      }

      if (action.batch.latestVis) {
        nextState.latestVis = action.batch.latestVis;
      }

      if (action.batch.overlayConfidenceSample !== undefined) {
        nextState.confidenceHistory = pushHistorySample(
          state.confidenceHistory,
          action.batch.overlayConfidenceSample,
          DEFAULT_HISTORY_LIMIT,
        );
      }

      if (action.batch.overlayAgeSample !== undefined) {
        nextState.ageHistory = pushHistorySample(
          state.ageHistory,
          action.batch.overlayAgeSample,
          DEFAULT_HISTORY_LIMIT,
        );
      }

      return nextState;
    }
    case "WARNING_RECEIVED":
      return {
        ...state,
        lastWsMessageAtMs: action.receivedAtMs,
        warnings: [action.warning, ...state.warnings].slice(0, MAX_WARNING_EVENTS),
      };
    case "CLEAR_WARNINGS":
      return {
        ...state,
        warnings: [],
      };
    case "SCHEMA_MISMATCH_DETECTED":
      if (state.schemaMismatchDetail !== null) {
        return state;
      }
      return {
        ...state,
        schemaMismatchDetail: action.detail,
      };
    case "SET_SELECTED_MODE":
      return {
        ...state,
        selectedMode: action.mode,
      };
    case "SET_ARMED":
      return {
        ...state,
        armed: action.armed,
      };
    case "INTENT_PENDING":
      return {
        ...state,
        intentPending: true,
        intentFeedbackKind: "sending",
        intentFeedbackMessage: action.message,
      };
    case "INTENT_FINISHED":
      return {
        ...state,
        intentPending: false,
        intentFeedbackKind: action.kind,
        intentFeedbackMessage: action.message,
      };
    default:
      return state;
  }
}

function formatError(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Request failed.";
}

export default function App(): JSX.Element {
  const backendConfig = useMemo(() => getBackendConfig(), []);
  const overlaySource = backendConfig.overlaySource;
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const pendingBatchRef = useRef<PendingStreamBatch>({
    lastMessageAtMs: null,
  });
  const frameIdRef = useRef<number | null>(null);
  const intentInFlightRef = useRef(false);
  const lastDesiredModeRef = useRef<ControlMode>(ControlMode.Manual);
  const derivedWarningHoldRef = useRef<Record<string, number>>({});
  const derivedAlertsStableRef = useRef<StatusAlert[]>([]);

  useEffect(() => {
    if (import.meta.env.DEV && import.meta.env.VITE_DEBUG_OVERLAY === "1") {
      runOverlayMathDevAssertions();
    }
  }, []);

  const flushPendingBatch = useCallback(() => {
    frameIdRef.current = null;

    const batch = pendingBatchRef.current;
    if (batch.lastMessageAtMs === null) {
      return;
    }

    dispatch({
      type: "STREAM_BATCH",
      batch,
    });

    pendingBatchRef.current = {
      lastMessageAtMs: null,
    };
  }, []);

  const scheduleFlush = useCallback(() => {
    if (frameIdRef.current !== null) {
      return;
    }

    frameIdRef.current = window.requestAnimationFrame(() => {
      flushPendingBatch();
    });
  }, [flushPendingBatch]);

  const handleParsedEnvelope = useCallback(
    (envelope: WsEnvelope) => {
      const receivedAtMs = Date.now();

      if (envelope.event === "WARNING") {
        const warningPayload = asWarningPayload(envelope.data);
        if (!warningPayload) {
          return;
        }

        dispatch({
          type: "WARNING_RECEIVED",
          warning: {
            ...warningPayload,
            receivedAtMs,
            envelopeSeq: envelope.seq,
          },
          receivedAtMs,
        });
        return;
      }

      const pending = pendingBatchRef.current;
      pending.lastMessageAtMs = receivedAtMs;

      if (envelope.event === "LINK_STATUS") {
        const linkStatus = asLinkStatus(envelope.data);
        if (linkStatus) {
          pending.linkStatus = linkStatus;
          pending.linkUpdatedAtMs = receivedAtMs;
          pending.linkEnvelopeTimestampS = envelope.timestamp_s;
        }
      } else if (envelope.event === "TEL_UPDATE") {
        const parsed = parseTelUpdate(envelope.data);
        if (parsed.mismatch !== null) {
          dispatch({ type: "SCHEMA_MISMATCH_DETECTED", detail: parsed.mismatch });
        }
        const latestTel = parsed.value;
        if (latestTel) {
          pending.latestTel = latestTel;
          if (overlaySource === "TEL") {
            pending.overlayConfidenceSample = normalizeConfidenceSample(readNumber(latestTel.confidence));
            pending.overlayAgeSample = normalizeAgeSample(readNumber(latestTel.tel_age_s));
          }
        }
      } else if (envelope.event === "VIS_UPDATE") {
        const parsed = parseVisUpdate(envelope.data);
        if (parsed.mismatch !== null) {
          dispatch({ type: "SCHEMA_MISMATCH_DETECTED", detail: parsed.mismatch });
        }
        const latestVis = parsed.value;
        if (latestVis) {
          pending.latestVis = latestVis;
          if (overlaySource === "VIS") {
            pending.overlayConfidenceSample = normalizeConfidenceSample(readNumber(latestVis.confidence));
            pending.overlayAgeSample = normalizeAgeSample(readNumber(latestVis.vis_age_s));
          }
        }
      }

      scheduleFlush();
    },
    [overlaySource, scheduleFlush],
  );

  useEffect(() => {
    const wsClient = new ReconnectingWsClient({
      url: backendConfig.wsUrl,
      subprotocols: backendConfig.apiToken
        ? [`aot.bearer.${backendConfig.apiToken}`]
        : undefined,
      onConnectionChange: (connected) => {
        dispatch({ type: "WS_CONNECTION_CHANGED", connected });
      },
      onEnvelope: (envelope) => {
        handleParsedEnvelope(envelope);
      },
      minBackoffMs: 500,
      maxBackoffMs: 5000,
    });

    wsClient.start();

    return () => {
      wsClient.stop();
      if (frameIdRef.current !== null) {
        window.cancelAnimationFrame(frameIdRef.current);
      }
    };
  }, [backendConfig.wsUrl, backendConfig.apiToken, handleParsedEnvelope]);

  useEffect(() => {
    const timerId = window.setInterval(() => {
      setNowMs(Date.now());
    }, 500);

    return () => {
      window.clearInterval(timerId);
    };
  }, []);

  const sendIntent = useCallback(
    async (
      payload: IntentRequest,
      successMessage: string,
      onSuccess?: () => void,
    ): Promise<void> => {
      if (intentInFlightRef.current) {
        return;
      }

      intentInFlightRef.current = true;
      dispatch({ type: "INTENT_PENDING", message: "Sending intent..." });

      try {
        await postIntent(backendConfig.httpUrl, payload, backendConfig.apiToken);
        dispatch({
          type: "INTENT_FINISHED",
          kind: "success",
          message: successMessage,
        });
        onSuccess?.();
      } catch (error: unknown) {
        dispatch({
          type: "INTENT_FINISHED",
          kind: "error",
          message: formatError(error),
        });
      } finally {
        intentInFlightRef.current = false;
      }
    },
    [backendConfig.httpUrl, backendConfig.apiToken],
  );

  const trackingBlockedReason = state.linkStatus?.tracking_blocked_reason ?? null;

  const onSelectMode = useCallback(
    (mode: ControlMode) => {
      if (mode === ControlMode.Tracking && trackingBlockedReason) {
        dispatch({
          type: "INTENT_FINISHED",
          kind: "error",
          message: `Tracking blocked: ${trackingBlockedReason}`,
        });
        return;
      }

      dispatch({ type: "SET_SELECTED_MODE", mode });
      lastDesiredModeRef.current = mode;

      void sendIntent({ desired_mode: mode }, `Mode intent sent: ${toControlModeName(mode)}.`);
    },
    [sendIntent, trackingBlockedReason],
  );

  const onToggleArm = useCallback(() => {
    const nextArm = !state.armed;
    const desiredMode = lastDesiredModeRef.current;

    void sendIntent(
      {
        desired_mode: desiredMode,
        arm: nextArm,
      },
      nextArm ? "Arm intent sent." : "Disarm intent sent.",
      () => {
        dispatch({ type: "SET_ARMED", armed: nextArm });
      },
    );
  }, [sendIntent, state.armed]);

  const onClearWarnings = useCallback(() => {
    dispatch({ type: "CLEAR_WARNINGS" });
  }, []);

  const selectedTrackingPayload = overlaySource === "VIS" ? state.latestVis : state.latestTel;
  const selectedTracking = useMemo(() => {
    if (overlaySource === "VIS") {
      return selectTrackingData("VIS", state.latestVis, null);
    }
    return selectTrackingData("TEL", null, state.latestTel);
  }, [overlaySource, selectedTrackingPayload]);

  const wsAgeS =
    state.lastWsMessageAtMs === null ? null : Math.max(0, (nowMs - state.lastWsMessageAtMs) / 1000);

  const projectedVisAgeS = projectAge(readNumber(state.linkStatus?.vis_age_s), state.linkUpdatedAtMs, nowMs);
  const projectedTelAgeS = projectAge(readNumber(state.linkStatus?.tel_age_s), state.linkUpdatedAtMs, nowMs);

  const visFreshThresholdRaw = readNumber(state.linkStatus?.vis_fresh_s);
  const visFreshThresholdS =
    visFreshThresholdRaw !== null && visFreshThresholdRaw > 0 ? visFreshThresholdRaw : DEFAULT_VIS_FRESH_S;
  const telFreshThresholdRaw = readNumber(state.linkStatus?.tel_fresh_s);
  const telFreshThresholdS =
    telFreshThresholdRaw !== null && telFreshThresholdRaw > 0 ? telFreshThresholdRaw : DEFAULT_TEL_FRESH_S;

  const trackingAgeS =
    selectedTracking.ageSFromMessage ?? (overlaySource === "VIS" ? projectedVisAgeS : projectedTelAgeS);
  const trackingFreshThresholdS = overlaySource === "VIS" ? visFreshThresholdS : telFreshThresholdS;

  const blockedBadge = mapBlockedReason(trackingBlockedReason);

  const derivedAlerts = useMemo<StatusAlert[]>(() => {
    const alerts: StatusAlert[] = [];
    const holdUntil = derivedWarningHoldRef.current;

    const held = (id: string, condition: boolean): boolean => {
      if (condition) {
        holdUntil[id] = nowMs + DERIVED_WARNING_HOLD_MS;
        return true;
      }
      return (holdUntil[id] ?? 0) > nowMs;
    };

    if (held("ws-disconnected", !state.wsConnected)) {
      alerts.push({
        id: "ws-disconnected",
        detail: "WebSocket disconnected.",
        severity: "error",
      });
    }

    if (held("fc-disconnected", state.linkStatus?.fc_connected === false)) {
      alerts.push({
        id: "fc-disconnected",
        detail: "FC command link is disconnected.",
        severity: "warn",
      });
    }

    if (held("vis-stale", projectedVisAgeS !== null && projectedVisAgeS >= visFreshThresholdS)) {
      alerts.push({
        id: "vis-stale",
        detail: `Vision stale (>= ${formatSeconds(visFreshThresholdS, 2)}).`,
        severity: "warn",
      });
    }

    if (held("tel-stale", projectedTelAgeS !== null && projectedTelAgeS >= telFreshThresholdS)) {
      alerts.push({
        id: "tel-stale",
        detail: `Telemetry stale (>= ${formatSeconds(telFreshThresholdS, 2)}).`,
        severity: "warn",
      });
    }

    if (trackingBlockedReason) {
      alerts.push({
        id: `tracking-blocked-${trackingBlockedReason}`,
        detail:
          trackingBlockedReason === "stale_vis"
            ? "Tracking blocked: Vision stale."
            : trackingBlockedReason === "no_vis"
              ? "Tracking blocked: No vision."
              : `Tracking blocked: ${trackingBlockedReason}`,
        severity: trackingBlockedReason === "no_vis" ? "error" : "warn",
      });
    }

    // Sticky for the rest of the session — drift between backend and webapp
    // schemas is a deploy-config bug, not a transient runtime state.
    if (state.schemaMismatchDetail !== null) {
      alerts.push({
        id: "schema-mismatch",
        detail: `Schema mismatch — ${state.schemaMismatchDetail}. Backend update needed.`,
        severity: "warn",
      });
    }

    return alerts.slice(0, 5);
  }, [
    nowMs,
    projectedTelAgeS,
    projectedVisAgeS,
    state.linkStatus?.fc_connected,
    state.schemaMismatchDetail,
    state.wsConnected,
    trackingBlockedReason,
    telFreshThresholdS,
    visFreshThresholdS,
  ]);
  const stableDerivedAlerts = useMemo(() => {
    const previous = derivedAlertsStableRef.current;
    if (areStatusAlertsEqual(previous, derivedAlerts)) {
      return previous;
    }
    derivedAlertsStableRef.current = derivedAlerts;
    return derivedAlerts;
  }, [derivedAlerts]);

  const fcConnected = state.linkStatus?.fc_connected ?? false;
  const actualMode =
    state.latestTel?.control_mode !== undefined && state.latestTel?.control_mode !== null
      ? state.latestTel.control_mode
      : null;

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div>
          <h1>AOT Drone Control Panel</h1>
          <p className="top-bar__subtitle">Backend HTTP: {backendConfig.httpUrl}</p>
          <p className="top-bar__subtitle">Backend WS: {backendConfig.wsUrl}</p>
        </div>

        <div className="top-bar__badges">
          <StatusBadge
            label={`WS ${state.wsConnected ? "Connected" : "Disconnected"}`}
            severity={state.wsConnected ? "good" : "bad"}
            subtext={wsAgeS === null ? "No messages yet" : `last ${formatNumber(wsAgeS, 1)}s ago`}
          />
          <StatusBadge
            label={`FC ${fcConnected ? "Connected" : "Disconnected"}`}
            severity={fcConnected ? "good" : "warn"}
          />
          <StatusBadge
            label={`State ${toTrackingStateName(selectedTracking.trackingState)}`}
            severity={toTrackingBadgeSeverity(selectedTracking.trackingState)}
            subtext={selectedTracking.trackingState === null ? "No source data" : undefined}
            title="Tracking state from selected overlay source"
          />
          {blockedBadge ? (
            <StatusBadge label={blockedBadge.label} severity={blockedBadge.severity} />
          ) : null}
        </div>
      </header>

      <ErrorBoundary sectionLabel="Warnings">
        <Warnings
          derivedAlerts={stableDerivedAlerts}
          warnings={state.warnings}
          onClear={onClearWarnings}
        />
      </ErrorBoundary>

      <main className="main-grid">
        <ControlPanel
          selectedMode={state.selectedMode}
          actualMode={actualMode}
          armed={state.armed}
          pending={state.intentPending}
          trackingBlockedReason={trackingBlockedReason}
          feedbackKind={state.intentFeedbackKind}
          feedbackMessage={state.intentFeedbackMessage}
          onSelectMode={onSelectMode}
          onToggleArm={onToggleArm}
        />

        <div className="right-column">
          <ErrorBoundary sectionLabel="Video">
            <VideoPanel
              latestTel={state.latestTel}
              latestVis={state.latestVis}
              overlaySource={overlaySource}
              videoUrl={backendConfig.videoUrl}
            />
          </ErrorBoundary>

          <ErrorBoundary sectionLabel="Tracking Summary">
            <TrackingSummary
              overlaySource={overlaySource}
              trackingState={selectedTracking.trackingState}
              trackingBlockedReason={trackingBlockedReason}
              confidence={selectedTracking.confidence}
              boundW={selectedTracking.boundW}
              boundH={selectedTracking.boundH}
              targetX={selectedTracking.targetX}
              targetY={selectedTracking.targetY}
              ageS={trackingAgeS}
              freshThresholdS={trackingFreshThresholdS}
              confidenceHistory={state.confidenceHistory}
              ageHistory={state.ageHistory}
            />
          </ErrorBoundary>

          <ErrorBoundary sectionLabel="Telemetry">
            <TelemetryPanel
              linkStatus={state.linkStatus}
              latestTel={state.latestTel}
              latestVis={state.latestVis}
              nowMs={nowMs}
              linkUpdatedAtMs={state.linkUpdatedAtMs}
              linkEnvelopeTimestampS={state.linkEnvelopeTimestampS}
            />
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
