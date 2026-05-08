import { WsEnvelope, WsEventType, isRecord } from "./types";

export interface WsTransportEvent {
  kind: "error" | "abnormal_close";
  url: string;
  timestampMs: number;
  // Present on close events; absent for "error" since onerror gives us no code.
  code?: number;
  // Total consecutive transport failures observed since the last clean
  // onopen. Lets the App surface a derived alert after a configurable
  // threshold (S0.6 item B3 = 3).
  consecutiveErrors: number;
}

const WS_EVENTS: WsEventType[] = ["TEL_UPDATE", "VIS_UPDATE", "LINK_STATUS", "WARNING"];

function isWsEventType(value: unknown): value is WsEventType {
  return typeof value === "string" && WS_EVENTS.includes(value as WsEventType);
}

export function parseWsEnvelope(value: unknown): WsEnvelope | null {
  if (!isRecord(value)) {
    return null;
  }

  if (!isWsEventType(value.event)) {
    return null;
  }

  if (!isRecord(value.data)) {
    return null;
  }

  if (typeof value.timestamp_s !== "number" || !Number.isFinite(value.timestamp_s)) {
    return null;
  }

  // seq must be a finite, non-negative integer. Loose acceptance of
  // negative or fractional values would corrupt downstream order/dedup
  // logic that treats seq as a monotonic counter.
  if (
    typeof value.seq !== "number" ||
    !Number.isInteger(value.seq) ||
    value.seq < 0
  ) {
    return null;
  }

  const wsVersion =
    typeof value.ws_ver === "number" && Number.isFinite(value.ws_ver) ? value.ws_ver : undefined;

  return {
    ws_ver: wsVersion,
    event: value.event,
    data: value.data,
    timestamp_s: value.timestamp_s,
    seq: value.seq,
  };
}

interface ReconnectingWsClientOptions {
  url: string;
  onEnvelope: (envelope: WsEnvelope) => void;
  onConnectionChange: (connected: boolean) => void;
  minBackoffMs?: number;
  maxBackoffMs?: number;
  /**
   * Sec-WebSocket-Protocol subprotocols to offer on the upgrade. The backend
   * uses `aot.bearer.<token>` to carry the API token here instead of in the
   * URL — subprotocol headers stay out of reverse-proxy logs, browser
   * history, and referrer chains.
   */
  subprotocols?: string[];
  /**
   * Random source for the ±30% reconnect-jitter. Default `Math.random`.
   * Tests inject a deterministic generator.
   */
  rng?: () => number;
  /**
   * Force-close + reconnect when no message has arrived in this many ms.
   * Default 1000. The owning App should call `setHeartbeatTimeoutMs` once
   * `vis_fresh_s` is known so the watchdog tracks the actual stream cadence.
   */
  heartbeatTimeoutMs?: number;
  /**
   * Notified on every onerror or abnormal close (close.code !== 1000).
   * Receives a `consecutiveErrors` counter that resets on the next clean
   * onopen — so the App can decide to escalate to a derived warning after
   * N back-to-back failures.
   */
  onTransportEvent?: (event: WsTransportEvent) => void;
}

export class ReconnectingWsClient {
  private readonly url: string;
  private readonly onEnvelope: (envelope: WsEnvelope) => void;
  private readonly onConnectionChange: (connected: boolean) => void;
  private readonly minBackoffMs: number;
  private readonly maxBackoffMs: number;
  private readonly subprotocols: string[] | undefined;
  private readonly rng: () => number;

  private ws: WebSocket | null = null;
  private reconnectTimerId: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelayMs: number;
  private active = false;
  private connected = false;
  private heartbeatTimeoutMs: number;
  private heartbeatTimerId: ReturnType<typeof setTimeout> | null = null;
  private readonly onTransportEvent: ((event: WsTransportEvent) => void) | undefined;
  private consecutiveErrors = 0;
  // Set true by onerror; cleared on each new connect(). Prevents the
  // onclose handler from double-counting the same lifecycle's failure
  // (onerror -> ws.close() -> onclose with abnormal code, which would
  // otherwise bump the counter twice for one real failure).
  private errorAlreadyCounted = false;

  constructor(options: ReconnectingWsClientOptions) {
    this.url = options.url;
    this.onEnvelope = options.onEnvelope;
    this.onConnectionChange = options.onConnectionChange;
    this.minBackoffMs = options.minBackoffMs ?? 500;
    this.maxBackoffMs = options.maxBackoffMs ?? 15000;
    this.subprotocols = options.subprotocols && options.subprotocols.length > 0
      ? [...options.subprotocols]
      : undefined;
    this.rng = options.rng ?? Math.random;
    this.heartbeatTimeoutMs = options.heartbeatTimeoutMs ?? 1000;
    this.onTransportEvent = options.onTransportEvent;
    this.reconnectDelayMs = this.minBackoffMs;
  }

  setHeartbeatTimeoutMs(timeoutMs: number): void {
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      return;
    }
    this.heartbeatTimeoutMs = timeoutMs;
    if (this.heartbeatTimerId !== null) {
      this.armHeartbeat();
    }
  }

  start(): void {
    if (this.active) {
      return;
    }
    this.active = true;
    this.connect();
  }

  stop(): void {
    this.active = false;
    this.clearReconnectTimer();
    this.clearHeartbeatTimer();
    if (this.ws !== null) {
      // Detach listeners before close() so any late-fired event from the
      // already-detached socket can't run our handlers (they are gated
      // on `this.active`, but belt-and-braces: the gate prevents the
      // counter bump; nulling the handlers stops the late callback
      // entirely).
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      // Pass an explicit clean-close code so any attached observers see a
      // 1000 rather than a synthetic abnormal-close.
      this.ws.close(1000, "client stop");
      this.ws = null;
    }
    this.setConnected(false);
  }

  private connect(): void {
    if (!this.active) {
      return;
    }

    this.clearReconnectTimer();
    this.errorAlreadyCounted = false;

    try {
      this.ws = this.subprotocols
        ? new WebSocket(this.url, this.subprotocols)
        : new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      if (!this.active) {
        return;
      }
      this.reconnectDelayMs = this.minBackoffMs;
      this.consecutiveErrors = 0;
      this.errorAlreadyCounted = false;
      this.setConnected(true);
      this.armHeartbeat();
    };

    this.ws.onmessage = (event: MessageEvent<unknown>) => {
      // Late frames after stop() must not re-arm the heartbeat or hand
      // payloads to a torn-down App.
      if (!this.active) {
        return;
      }
      this.armHeartbeat();
      this.handleMessage(event.data);
    };

    this.ws.onerror = () => {
      // Active gate: stop() may have triggered the underlying error.
      // Don't escalate operator-visible state on a deliberate teardown.
      if (!this.active) {
        return;
      }
      this.consecutiveErrors += 1;
      this.errorAlreadyCounted = true;
      const payload: WsTransportEvent = {
        kind: "error",
        url: this.url,
        timestampMs: Date.now(),
        consecutiveErrors: this.consecutiveErrors,
      };
      console.error("[ReconnectingWsClient] transport error", payload);
      this.onTransportEvent?.(payload);
      // Prevent the heartbeat from re-firing close() on an already-failed
      // socket while we wait for the browser to deliver the matching
      // onclose event.
      this.clearHeartbeatTimer();
      if (this.ws !== null) {
        this.ws.close();
      }
    };

    this.ws.onclose = (event: CloseEvent) => {
      this.clearHeartbeatTimer();
      this.ws = null;
      this.setConnected(false);

      // If the App tore us down via stop(), don't surface the close as a
      // transport failure — the browser delivers code 1005/1006 here even
      // when WE asked for the close.
      if (!this.active) {
        return;
      }

      const code = typeof event?.code === "number" ? event.code : undefined;
      // Don't double-count the same lifecycle's failure: onerror already
      // bumped the counter and dispatched the event. Code 1000 is a clean
      // close from anywhere; treat it as not-a-failure.
      const shouldReportFailure = !this.errorAlreadyCounted && code !== 1000;
      if (shouldReportFailure) {
        this.consecutiveErrors += 1;
        const payload: WsTransportEvent = {
          kind: "abnormal_close",
          url: this.url,
          timestampMs: Date.now(),
          code,
          consecutiveErrors: this.consecutiveErrors,
        };
        console.error("[ReconnectingWsClient] abnormal close", payload);
        this.onTransportEvent?.(payload);
      }

      this.scheduleReconnect();
    };
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw === "string") {
      this.handleMessageText(raw);
      return;
    }

    if (raw instanceof Blob) {
      void raw
        .text()
        .then((text) => {
          this.handleMessageText(text);
        })
        .catch(() => {
          // Ignore malformed frames.
        });
    }
  }

  private handleMessageText(rawText: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(rawText);
    } catch {
      return;
    }

    const envelope = parseWsEnvelope(parsed);
    if (envelope === null) {
      return;
    }

    this.onEnvelope(envelope);
  }

  private scheduleReconnect(): void {
    if (!this.active || this.reconnectTimerId !== null) {
      return;
    }

    // ±30% jitter spreads reconnects across a fleet so a flapping backend
    // doesn't see a thundering-herd retry on every restart.
    const jitterFactor = 0.7 + 0.6 * this.rng();
    const jitteredMs = Math.max(0, Math.round(this.reconnectDelayMs * jitterFactor));

    // setTimeout is available in browsers and the test runtime alike;
    // bypassing `window.` keeps this testable without jsdom.
    this.reconnectTimerId = setTimeout(() => {
      this.reconnectTimerId = null;
      this.connect();
    }, jitteredMs);

    this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, this.maxBackoffMs);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimerId === null) {
      return;
    }

    clearTimeout(this.reconnectTimerId);
    this.reconnectTimerId = null;
  }

  private armHeartbeat(): void {
    this.clearHeartbeatTimer();
    if (!this.active) {
      return;
    }
    this.heartbeatTimerId = setTimeout(() => {
      this.heartbeatTimerId = null;
      // Force-close the silent socket; onclose will schedule reconnect.
      if (this.ws !== null) {
        this.ws.close();
      }
    }, this.heartbeatTimeoutMs);
  }

  private clearHeartbeatTimer(): void {
    if (this.heartbeatTimerId === null) {
      return;
    }
    clearTimeout(this.heartbeatTimerId);
    this.heartbeatTimerId = null;
  }

  private setConnected(nextConnected: boolean): void {
    if (this.connected === nextConnected) {
      return;
    }

    this.connected = nextConnected;
    this.onConnectionChange(nextConnected);
  }
}
