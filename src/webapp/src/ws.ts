import { WsEnvelope, WsEventType, isRecord } from "./types";

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

  if (typeof value.seq !== "number" || !Number.isFinite(value.seq)) {
    return null;
  }

  const wsVersion =
    typeof value.ws_ver === "number" && Number.isFinite(value.ws_ver) ? value.ws_ver : undefined;

  return {
    ws_ver: wsVersion,
    event: value.event,
    data: value.data,
    timestamp_s: value.timestamp_s,
    seq: Math.trunc(value.seq),
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
  private reconnectTimerId: number | null = null;
  private reconnectDelayMs: number;
  private active = false;
  private connected = false;

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
    this.reconnectDelayMs = this.minBackoffMs;
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
    if (this.ws !== null) {
      this.ws.close();
      this.ws = null;
    }
    this.setConnected(false);
  }

  private connect(): void {
    if (!this.active) {
      return;
    }

    this.clearReconnectTimer();

    try {
      this.ws = this.subprotocols
        ? new WebSocket(this.url, this.subprotocols)
        : new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectDelayMs = this.minBackoffMs;
      this.setConnected(true);
    };

    this.ws.onmessage = (event: MessageEvent<unknown>) => {
      this.handleMessage(event.data);
    };

    this.ws.onerror = () => {
      if (this.ws !== null) {
        this.ws.close();
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.setConnected(false);
      if (this.active) {
        this.scheduleReconnect();
      }
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
    }, jitteredMs) as unknown as number;

    this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, this.maxBackoffMs);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimerId === null) {
      return;
    }

    clearTimeout(this.reconnectTimerId);
    this.reconnectTimerId = null;
  }

  private setConnected(nextConnected: boolean): void {
    if (this.connected === nextConnected) {
      return;
    }

    this.connected = nextConnected;
    this.onConnectionChange(nextConnected);
  }
}
