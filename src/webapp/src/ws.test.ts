import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReconnectingWsClient, parseWsEnvelope } from "./ws";

// Minimal hand-rolled WebSocket fake. We expose handles so tests can drive
// the lifecycle (open / message / error / close) deterministically.
type FakeWebSocketHandle = {
  url: string;
  protocols: string | string[] | undefined;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  close: ReturnType<typeof vi.fn>;
};

const fakeInstances: FakeWebSocketHandle[] = [];

function installFakeWebSocket(): void {
  fakeInstances.length = 0;
  class FakeWebSocket {
    onopen: FakeWebSocketHandle["onopen"] = null;
    onmessage: FakeWebSocketHandle["onmessage"] = null;
    onerror: FakeWebSocketHandle["onerror"] = null;
    onclose: FakeWebSocketHandle["onclose"] = null;
    close = vi.fn();
    url: string;
    protocols: string | string[] | undefined;

    constructor(url: string, protocols?: string | string[]) {
      this.url = url;
      this.protocols = protocols;
      fakeInstances.push(this as unknown as FakeWebSocketHandle);
    }
  }
  (globalThis as unknown as { WebSocket: unknown }).WebSocket =
    FakeWebSocket as unknown as typeof WebSocket;
}

function lastInstance(): FakeWebSocketHandle {
  const inst = fakeInstances[fakeInstances.length - 1];
  if (!inst) throw new Error("no fake WebSocket instance was constructed");
  return inst;
}

beforeEach(() => {
  installFakeWebSocket();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("parseWsEnvelope", () => {
  it("parses a well-formed envelope", () => {
    const out = parseWsEnvelope({
      event: "TEL_UPDATE",
      data: { foo: 1 },
      timestamp_s: 12.5,
      seq: 7,
    });
    expect(out).not.toBeNull();
    expect(out?.event).toBe("TEL_UPDATE");
    expect(out?.seq).toBe(7);
  });

  it("rejects unknown events", () => {
    expect(parseWsEnvelope({ event: "WAT", data: {}, timestamp_s: 0, seq: 0 })).toBeNull();
  });
});

describe("ReconnectingWsClient — backoff jitter + ceiling", () => {
  it("first reconnect delay falls within ±30% of minBackoffMs", () => {
    let lastDelay = 0;
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");

    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      minBackoffMs: 1000,
      maxBackoffMs: 15000,
      rng: () => 0.5, // jitter factor = 0.7 + 0.6*0.5 = 1.0 → exactly base
    });
    client.start();

    // Trigger the first reconnect by closing the open socket.
    const inst = lastInstance();
    inst.onclose?.(new Event("close") as CloseEvent);

    expect(setTimeoutSpy).toHaveBeenCalled();
    const calls = setTimeoutSpy.mock.calls;
    lastDelay = calls[calls.length - 1][1] as number;
    expect(lastDelay).toBe(1000);

    client.stop();
  });

  it("rng=0 hits the lower jitter bound (-30% of base)", () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      minBackoffMs: 1000,
      rng: () => 0,
    });
    client.start();
    lastInstance().onclose?.(new Event("close") as CloseEvent);

    const calls = setTimeoutSpy.mock.calls;
    const delay = calls[calls.length - 1][1] as number;
    expect(delay).toBe(700); // 1000 * (0.7 + 0.6*0) = 700

    client.stop();
  });

  it("rng=1 hits the upper jitter bound (+30% of base)", () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      minBackoffMs: 1000,
      rng: () => 1,
    });
    client.start();
    lastInstance().onclose?.(new Event("close") as CloseEvent);

    const calls = setTimeoutSpy.mock.calls;
    const delay = calls[calls.length - 1][1] as number;
    expect(delay).toBe(1300); // 1000 * (0.7 + 0.6*1) = 1300

    client.stop();
  });

  it("backoff doubles on repeated reconnects and caps at maxBackoffMs", () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      minBackoffMs: 1000,
      maxBackoffMs: 15000,
      rng: () => 0.5,
    });
    client.start();

    const baseDelays: number[] = [];
    for (let cycle = 0; cycle < 8; cycle += 1) {
      lastInstance().onclose?.(new Event("close") as CloseEvent);
      const calls = setTimeoutSpy.mock.calls;
      baseDelays.push(calls[calls.length - 1][1] as number);
      // Fast-forward to fire the reconnect timer; this constructs the next
      // FakeWebSocket so the next onclose has somewhere to land.
      vi.runOnlyPendingTimers();
    }

    // Should grow 1000 → 2000 → 4000 → 8000 → 15000 → 15000 → 15000 → 15000
    expect(baseDelays.slice(0, 4)).toEqual([1000, 2000, 4000, 8000]);
    expect(baseDelays[4]).toBe(15000);
    expect(baseDelays[7]).toBe(15000);

    client.stop();
  });

  it("default ceiling is 15s when maxBackoffMs is unspecified", () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      minBackoffMs: 1000,
      rng: () => 0.5,
    });
    client.start();
    for (let cycle = 0; cycle < 8; cycle += 1) {
      lastInstance().onclose?.(new Event("close") as CloseEvent);
      vi.runOnlyPendingTimers();
    }
    const calls = setTimeoutSpy.mock.calls;
    expect(calls[calls.length - 1][1]).toBe(15000);
    client.stop();
  });

  it("backoff resets to minBackoffMs after a successful onopen", () => {
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      minBackoffMs: 1000,
      rng: () => 0.5,
    });
    client.start();

    // Pump backoff up.
    lastInstance().onclose?.(new Event("close") as CloseEvent);
    vi.runOnlyPendingTimers();
    lastInstance().onclose?.(new Event("close") as CloseEvent);
    vi.runOnlyPendingTimers();
    // After 2 cycles base = 4000.

    // Successful open should reset.
    lastInstance().onopen?.(new Event("open"));
    lastInstance().onclose?.(new Event("close") as CloseEvent);

    const calls = setTimeoutSpy.mock.calls;
    expect(calls[calls.length - 1][1]).toBe(1000);

    client.stop();
  });
});
