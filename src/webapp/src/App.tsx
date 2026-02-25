import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { getBackendConfig, postIntent } from "./api";
import ControlPanel from "./components/ControlPanel";
import TelemetryPanel from "./components/TelemetryPanel";
import VideoPanel from "./components/VideoPanel";
import Warnings from "./components/Warnings";
import {
  ControlMode,
  IntentRequest,
  LinkStatus,
  StatusAlert,
  TelUpdate,
  VisUpdate,
  WarningEntry,
  WsEnvelope,
  asLinkStatus,
  asTelUpdate,
  asVisUpdate,
  asWarningPayload,
  formatNumber,
  toControlModeName,
} from "./types";
import { runOverlayMathDevAssertions } from "./utils/overlayMathAssertions";
import { ReconnectingWsClient } from "./ws";

type IntentFeedbackKind = "idle" | "sending" | "success" | "error";

interface AppState {
  wsConnected: boolean;
  lastWsMessageAtMs: number | null;
  linkStatus: LinkStatus | null;
  linkUpdatedAtMs: number | null;
  linkEnvelopeTimestampS: number | null;
  latestTel: TelUpdate | null;
  latestVis: VisUpdate | null;
  warnings: WarningEntry[];
  selectedMode: ControlMode;
  armed: boolean;
  intentPending: boolean;
  intentFeedbackKind: IntentFeedbackKind;
  intentFeedbackMessage: string;
}

interface PendingStreamBatch {
  linkStatus?: LinkStatus;
  linkUpdatedAtMs?: number;
  linkEnvelopeTimestampS?: number;
  latestTel?: TelUpdate;
  latestVis?: VisUpdate;
  lastMessageAtMs: number | null;
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
  selectedMode: ControlMode.Manual,
  armed: false,
  intentPending: false,
  intentFeedbackKind: "idle",
  intentFeedbackMessage: "Ready.",
};

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

      return nextState;
    }
    case "WARNING_RECEIVED":
      return {
        ...state,
        lastWsMessageAtMs: action.receivedAtMs,
        warnings: [action.warning, ...state.warnings].slice(0, 3),
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
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const pendingBatchRef = useRef<PendingStreamBatch>({
    lastMessageAtMs: null,
  });
  const frameIdRef = useRef<number | null>(null);
  const intentInFlightRef = useRef(false);
  const lastDesiredModeRef = useRef<ControlMode>(ControlMode.Manual);

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
        const latestTel = asTelUpdate(envelope.data);
        if (latestTel) {
          pending.latestTel = latestTel;
        }
      } else if (envelope.event === "VIS_UPDATE") {
        const latestVis = asVisUpdate(envelope.data);
        if (latestVis) {
          pending.latestVis = latestVis;
        }
      }

      scheduleFlush();
    },
    [scheduleFlush],
  );

  useEffect(() => {
    const wsClient = new ReconnectingWsClient({
      url: backendConfig.wsUrl,
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
  }, [backendConfig.wsUrl, handleParsedEnvelope]);

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
        await postIntent(backendConfig.httpUrl, payload);
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
    [backendConfig.httpUrl],
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

  const statusAlerts = useMemo<StatusAlert[]>(() => {
    const alerts: StatusAlert[] = [];
    const linkStatus = state.linkStatus;

    if (linkStatus === null) {
      return alerts;
    }

    if (!linkStatus.fc_connected) {
      alerts.push({
        id: "fc-disconnected",
        detail: "FC command link is disconnected.",
        severity: "warn",
      });
    }

    if (linkStatus.tracking_blocked_reason) {
      alerts.push({
        id: `tracking-blocked-${linkStatus.tracking_blocked_reason}`,
        detail: `Tracking blocked: ${linkStatus.tracking_blocked_reason}`,
        severity: "warn",
      });
    }

    if (
      linkStatus.vis_age_s !== null &&
      linkStatus.vis_fresh_s > 0 &&
      linkStatus.vis_age_s > linkStatus.vis_fresh_s
    ) {
      alerts.push({
        id: "vis-stale",
        detail: `Vision link stale (${formatNumber(linkStatus.vis_age_s, 2, " s")}).`,
        severity: "warn",
      });
    }

    const telStaleThresholdS = linkStatus.tel_hz > 0 ? Math.max(1.0, 3 / linkStatus.tel_hz) : 1.0;
    if (linkStatus.tel_age_s !== null && linkStatus.tel_age_s > telStaleThresholdS) {
      alerts.push({
        id: "tel-stale",
        detail: `Telemetry link stale (${formatNumber(linkStatus.tel_age_s, 2, " s")}).`,
        severity: "warn",
      });
    }

    return alerts;
  }, [state.linkStatus]);

  const wsAgeS =
    state.lastWsMessageAtMs === null ? null : Math.max(0, (nowMs - state.lastWsMessageAtMs) / 1000);

  const fcConnected = state.linkStatus?.fc_connected ?? false;
  const actualMode =
    state.latestTel?.control_mode !== undefined && state.latestTel?.control_mode !== null
      ? state.latestTel.control_mode
      : null;
  const overlaySource = backendConfig.overlaySource;

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div>
          <h1>AOT Drone Control Panel</h1>
          <p className="top-bar__subtitle">Backend HTTP: {backendConfig.httpUrl}</p>
          <p className="top-bar__subtitle">Backend WS: {backendConfig.wsUrl}</p>
        </div>

        <div className="top-bar__badges">
          <div className={`status-pill ${state.wsConnected ? "status-pill--ok" : "status-pill--warn"}`}>
            WS {state.wsConnected ? "Connected" : "Disconnected"}
          </div>
          <div className={`status-pill ${fcConnected ? "status-pill--ok" : "status-pill--warn"}`}>
            FC {fcConnected ? "Connected" : "Disconnected"}
          </div>
          <div className="top-bar__age">
            Last WS msg: {wsAgeS === null ? "n/a" : `${formatNumber(wsAgeS, 1, " s")} ago`}
          </div>
        </div>
      </header>

      <Warnings statusAlerts={statusAlerts} warnings={state.warnings} />

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
          <VideoPanel
            latestTel={state.latestTel}
            latestVis={state.latestVis}
            overlaySource={overlaySource}
            videoUrl={backendConfig.videoUrl}
          />

          <TelemetryPanel
            linkStatus={state.linkStatus}
            latestTel={state.latestTel}
            latestVis={state.latestVis}
            nowMs={nowMs}
            linkUpdatedAtMs={state.linkUpdatedAtMs}
            linkEnvelopeTimestampS={state.linkEnvelopeTimestampS}
          />
        </div>
      </main>
    </div>
  );
}
