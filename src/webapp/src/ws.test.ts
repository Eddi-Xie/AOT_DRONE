import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReconnectingWsClient, parseWsEnvelope, WsTransportEvent } from "./ws";

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
  // Silence the production console.error logs during tests (they are exercised
  // explicitly in the transport-error block and would otherwise spam output).
  vi.spyOn(console, "error").mockImplementation(() => undefined);
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

  it("rejects non-record input (null, string, array)", () => {
    expect(parseWsEnvelope(null)).toBeNull();
    expect(parseWsEnvelope("not-an-object")).toBeNull();
    expect(parseWsEnvelope([])).toBeNull();
  });

  it("rejects when data is not a record", () => {
    expect(
      parseWsEnvelope({ event: "TEL_UPDATE", data: "x", timestamp_s: 0, seq: 0 }),
    ).toBeNull();
    expect(
      parseWsEnvelope({ event: "TEL_UPDATE", data: null, timestamp_s: 0, seq: 0 }),
    ).toBeNull();
  });

  it("rejects non-finite timestamp_s", () => {
    expect(
      parseWsEnvelope({ event: "TEL_UPDATE", data: {}, timestamp_s: Number.NaN, seq: 0 }),
    ).toBeNull();
    expect(
      parseWsEnvelope({
        event: "TEL_UPDATE",
        data: {},
        timestamp_s: Number.POSITIVE_INFINITY,
        seq: 0,
      }),
    ).toBeNull();
  });

  it("rejects non-integer / negative / non-number seq", () => {
    expect(
      parseWsEnvelope({ event: "TEL_UPDATE", data: {}, timestamp_s: 0, seq: 7.5 }),
    ).toBeNull();
    expect(
      parseWsEnvelope({ event: "TEL_UPDATE", data: {}, timestamp_s: 0, seq: -1 }),
    ).toBeNull();
    expect(
      parseWsEnvelope({ event: "TEL_UPDATE", data: {}, timestamp_s: 0, seq: "7" }),
    ).toBeNull();
    expect(
      parseWsEnvelope({ event: "TEL_UPDATE", data: {}, timestamp_s: 0, seq: Number.NaN }),
    ).toBeNull();
  });

  it("accepts each documented WsEventType", () => {
    for (const event of ["TEL_UPDATE", "VIS_UPDATE", "LINK_STATUS", "WARNING"]) {
      const out = parseWsEnvelope({ event, data: {}, timestamp_s: 0, seq: 0 });
      expect(out?.event).toBe(event);
    }
  });

  it("ws_ver of wrong type is silently dropped (forward-compat)", () => {
    const numeric = parseWsEnvelope({
      event: "WARNING",
      data: {},
      timestamp_s: 0,
      seq: 0,
      ws_ver: 2,
    });
    expect(numeric?.ws_ver).toBe(2);

    const stringy = parseWsEnvelope({
      event: "WARNING",
      data: {},
      timestamp_s: 0,
      seq: 0,
      ws_ver: "1",
    });
    expect(stringy).not.toBeNull();
    expect(stringy?.ws_ver).toBeUndefined();
  });
});

describe("ReconnectingWsClient — transport-error reporting", () => {
  it("emits onTransportEvent on onerror with kind=error and increments counter", () => {
    const events: WsTransportEvent[] = [];

    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      onTransportEvent: (e) => events.push(e),
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();

    inst.onerror?.(new Event("error"));
    inst.onerror?.(new Event("error"));
    inst.onerror?.(new Event("error"));

    expect(events.map((e) => e.kind)).toEqual(["error", "error", "error"]);
    expect(events.map((e) => e.consecutiveErrors)).toEqual([1, 2, 3]);
    expect(events[0].url).toBe("ws://test");
    expect(typeof events[0].timestampMs).toBe("number");
    expect(console.error).toHaveBeenCalled();

    client.stop();
  });

  it("emits onTransportEvent on abnormal close (code != 1000)", () => {
    const events: WsTransportEvent[] = [];

    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      onTransportEvent: (e) => events.push(e),
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();

    inst.onclose?.({ code: 1006 } as CloseEvent);

    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe("abnormal_close");
    expect(events[0].code).toBe(1006);
    expect(events[0].consecutiveErrors).toBe(1);

    client.stop();
  });

  it("onerror followed by abnormal onclose increments counter only ONCE (no double-count)", () => {
    // Regression test: previously onerror bumped the counter AND triggered
    // close(), then onclose with code 1006 bumped again — so a single real
    // failure produced two transport events and tripped the 3x alert at
    // ~2 actual failures. Fix: errorAlreadyCounted flag.
    const events: WsTransportEvent[] = [];
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      onTransportEvent: (e) => events.push(e),
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();

    inst.onerror?.(new Event("error"));
    inst.onclose?.({ code: 1006 } as CloseEvent);

    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe("error");
    expect(events[0].consecutiveErrors).toBe(1);

    client.stop();
  });

  it("does NOT emit on a clean close (code 1000)", () => {
    const events: WsTransportEvent[] = [];
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      onTransportEvent: (e) => events.push(e),
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onclose?.({ code: 1000 } as CloseEvent);
    expect(events).toEqual([]);
    client.stop();
  });

  it("intentional stop() does NOT emit a phantom abnormal-close event", () => {
    // Regression test: previously stop() called ws.close() with no args,
    // and the resulting onclose with code 1005 fired an abnormal-close
    // transport event + console.error on every webapp unmount.
    const events: WsTransportEvent[] = [];
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      onTransportEvent: (e) => events.push(e),
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    client.stop();
    // Even if a delayed onclose fires after stop(), it must not emit.
    inst.onclose?.({ code: 1005 } as CloseEvent);

    expect(events).toEqual([]);
  });

  it("resets the consecutive-error counter on the next onopen", () => {
    const events: WsTransportEvent[] = [];

    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      onTransportEvent: (e) => events.push(e),
      rng: () => 0.5,
    });
    client.start();

    // Two errors then a successful open.
    lastInstance().onerror?.(new Event("error"));
    lastInstance().onerror?.(new Event("error"));
    expect(events[1].consecutiveErrors).toBe(2);

    // Reconnect cycle: onclose pumps reconnect; new socket opens.
    lastInstance().onclose?.(new Event("close") as CloseEvent);
    vi.runOnlyPendingTimers();
    lastInstance().onopen?.(new Event("open"));

    // A subsequent error should restart the counter at 1.
    lastInstance().onerror?.(new Event("error"));
    const last = events[events.length - 1];
    expect(last.consecutiveErrors).toBe(1);

    client.stop();
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

  it("heartbeat fires close() when no message arrives within the timeout", () => {
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 500,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    expect(inst.close).not.toHaveBeenCalled();
    vi.advanceTimersByTime(499);
    expect(inst.close).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(inst.close).toHaveBeenCalledTimes(1);

    client.stop();
  });

  it("heartbeat is reset by every onmessage so a steady stream stays connected", () => {
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 500,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    for (let frame = 0; frame < 10; frame += 1) {
      vi.advanceTimersByTime(400); // less than timeout
      inst.onmessage?.({ data: '{"event":"WARNING","data":{},"timestamp_s":0,"seq":0}' } as MessageEvent);
    }

    expect(inst.close).not.toHaveBeenCalled();
    client.stop();
  });

  it("setHeartbeatTimeoutMs reconfigures an active watchdog", () => {
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 5000,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    // Tighten the watchdog mid-stream.
    client.setHeartbeatTimeoutMs(200);
    vi.advanceTimersByTime(199);
    expect(inst.close).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(inst.close).toHaveBeenCalledTimes(1);

    client.stop();
  });

  it("setHeartbeatTimeoutMs ignores non-positive / non-finite values", () => {
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 1000,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    client.setHeartbeatTimeoutMs(0);
    client.setHeartbeatTimeoutMs(-50);
    client.setHeartbeatTimeoutMs(Number.NaN);
    client.setHeartbeatTimeoutMs(Number.POSITIVE_INFINITY);

    // Original 1000 ms remains.
    vi.advanceTimersByTime(999);
    expect(inst.close).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(inst.close).toHaveBeenCalledTimes(1);

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

describe("ReconnectingWsClient — lifecycle interactions", () => {
  it("stop() during a pending reconnect cancels the reconnect timer", () => {
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      rng: () => 0.5,
    });
    client.start();

    // Trigger a reconnect cycle and verify a timer is pending.
    lastInstance().onclose?.(new Event("close") as CloseEvent);
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    const initialInstanceCount = fakeInstances.length;
    client.stop();
    expect(vi.getTimerCount()).toBe(0);

    // Advancing past the original reconnect delay must NOT construct a
    // new socket — the dead client should stay dead.
    vi.advanceTimersByTime(60_000);
    expect(fakeInstances.length).toBe(initialInstanceCount);
  });

  it("late onmessage after stop() does not re-arm a heartbeat timer", () => {
    // Pre-fix: a frame buffered between stop() and the matching onclose
    // ran the original onmessage closure, called armHeartbeat, and left
    // a stray setTimeout that fired long after teardown.
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 500,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    client.stop();
    expect(vi.getTimerCount()).toBe(0);

    // Even though stop() nulls listeners, simulate a stale callback by
    // invoking the original handler directly (defense-in-depth gate
    // exists on the active flag too).
    inst.onmessage?.({ data: '{"event":"WARNING","data":{},"timestamp_s":0,"seq":0}' } as MessageEvent);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("heartbeat fires close() and onclose schedules exactly one reconnect", () => {
    // Heartbeat-then-reconnect transition: the watchdog calls close(),
    // onclose runs (clearHeartbeatTimer is a no-op since the timer
    // already fired), then scheduleReconnect arms a single fresh timer.
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 500,
      minBackoffMs: 1000,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    // Advance past the watchdog timeout — the heartbeat fires and calls
    // ws.close(); that doesn't auto-fire onclose in our fake, so simulate
    // it manually as the browser would.
    vi.advanceTimersByTime(501);
    expect(inst.close).toHaveBeenCalledTimes(1);
    inst.onclose?.({ code: 1006 } as CloseEvent);

    // Exactly one timer pending (the reconnect timer).
    expect(vi.getTimerCount()).toBe(1);

    // Advance past the reconnect delay; a new FakeWebSocket should be
    // constructed exactly once.
    const beforeReconnect = fakeInstances.length;
    vi.runOnlyPendingTimers();
    expect(fakeInstances.length).toBe(beforeReconnect + 1);

    client.stop();
  });

  it("onerror leaves the heartbeat timer armed (recovery body fires later if onclose drops)", () => {
    // Round 1 cleared the watchdog inside onerror to avoid a redundant
    // close() on a failing socket. Round 5 reverted that, since the
    // watchdog body is what runs the dropped-onclose recovery
    // (see armHeartbeat — when errorAlreadyCounted is still true on
    // the watchdog tick, it forces the reconnect state machine).
    // This test only pins down the "armed" half of the contract; the
    // recovery itself is covered by the next test.
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 500,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));
    expect(vi.getTimerCount()).toBe(1); // heartbeat armed

    inst.onerror?.(new Event("error"));
    // Heartbeat timer survives onerror.
    expect(vi.getTimerCount()).toBe(1);

    client.stop();
  });

  it("onerror + dropped onclose: heartbeat forces reconnect after heartbeatTimeoutMs", () => {
    // The setConnected(false) inside onerror handles the UI-staleness
    // half. The other half — the client never reconnecting — was the
    // residual round-5 review found. The heartbeat watchdog now
    // recognizes "onerror fired but onclose never arrived" and forces
    // the recovery state machine.
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 500,
      minBackoffMs: 1000,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));
    const beforeReconnect = fakeInstances.length;

    // onerror fires; we deliberately do NOT fire the matching onclose
    // to simulate a proxy dropping it.
    inst.onerror?.(new Event("error"));
    expect(inst.close).toHaveBeenCalledTimes(1);

    // Watchdog fires at heartbeatTimeoutMs; recovery path arms reconnect.
    vi.advanceTimersByTime(501);

    // Reconnect timer should now be pending (1000 ms base * jitter 1.0).
    expect(vi.getTimerCount()).toBe(1);
    vi.runOnlyPendingTimers();

    // A new socket should now exist beyond the original one.
    expect(fakeInstances.length).toBe(beforeReconnect + 1);

    client.stop();
  });

  it("onerror flips connection state to disconnected immediately", () => {
    // Round-5 fix: previously onerror only triggered close() and waited
    // for onclose to update connected state. If the browser dropped
    // onclose, the UI would stay "WS Connected" forever. onerror now also
    // calls setConnected(false); a duplicate call from onclose is a no-op.
    const states: boolean[] = [];
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: (c) => states.push(c),
      heartbeatTimeoutMs: 500,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));
    expect(states).toEqual([true]);

    inst.onerror?.(new Event("error"));
    expect(states).toEqual([true, false]);

    // A subsequent onclose must not fire another connection-change event.
    inst.onclose?.({ code: 1006 } as CloseEvent);
    expect(states).toEqual([true, false]);

    client.stop();
  });

  it("WebSocket constructor throw is reported as a transport error", () => {
    // Pre-fix: a misconfigured wsUrl (invalid scheme, malformed
    // subprotocol) made the WebSocket constructor synchronously throw,
    // and the catch block silently rescheduled with no transport event,
    // no log, no counter bump. Operator never saw the 3x-error alert.
    const events: WsTransportEvent[] = [];

    // Override the FakeWebSocket installed in beforeEach with one that
    // throws on construction.
    class ThrowingWebSocket {
      constructor() {
        throw new Error("invalid url");
      }
    }
    (globalThis as unknown as { WebSocket: unknown }).WebSocket =
      ThrowingWebSocket as unknown as typeof WebSocket;

    const client = new ReconnectingWsClient({
      url: "ws://broken",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      onTransportEvent: (e) => events.push(e),
      rng: () => 0.5,
    });
    client.start();

    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe("error");
    expect(events[0].consecutiveErrors).toBe(1);
    expect(console.error).toHaveBeenCalled();

    client.stop();
  });

  it("handleMessage drops malformed JSON without throwing", () => {
    const envelopes: unknown[] = [];
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: (env) => envelopes.push(env),
      onConnectionChange: () => {},
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    inst.onmessage?.({ data: "{not valid json" } as MessageEvent);
    expect(envelopes).toEqual([]);

    // Sanity: a valid frame still flows through.
    inst.onmessage?.({
      data: '{"event":"WARNING","data":{},"timestamp_s":0,"seq":0}',
    } as MessageEvent);
    expect(envelopes).toHaveLength(1);

    client.stop();
  });

  it("seeded heartbeat (set before onopen) is honoured when the socket later opens", () => {
    // App.tsx pattern: setHeartbeatTimeoutMs is called immediately after
    // construction (before onopen) so a rebuilt client gets the cadence
    // it learned from a previous LINK_STATUS. Verify the timeout is
    // applied when onopen later arms the watchdog.
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 5000, // ignored; setHeartbeatTimeoutMs overrides
      rng: () => 0.5,
    });
    client.start();
    client.setHeartbeatTimeoutMs(200);

    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    vi.advanceTimersByTime(199);
    expect(inst.close).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(inst.close).toHaveBeenCalledTimes(1);

    client.stop();
  });

  it("heartbeat-driven close emits an abnormal_close transport event with consecutiveErrors=1", () => {
    // The heartbeat-cycle test below verifies timer side-effects; this
    // one pins the contract that operators receive the same 3x escalation
    // path for silent-stream failures as for active-error failures.
    const events: WsTransportEvent[] = [];
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      onTransportEvent: (e) => events.push(e),
      heartbeatTimeoutMs: 500,
      rng: () => 0.5,
    });
    client.start();
    const inst = lastInstance();
    inst.onopen?.(new Event("open"));

    vi.advanceTimersByTime(501);
    inst.onclose?.({ code: 1006 } as CloseEvent);

    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe("abnormal_close");
    expect(events[0].consecutiveErrors).toBe(1);

    client.stop();
  });

  it("setHeartbeatTimeoutMs after stop() does not arm a stray timer", () => {
    const client = new ReconnectingWsClient({
      url: "ws://test",
      onEnvelope: () => {},
      onConnectionChange: () => {},
      heartbeatTimeoutMs: 500,
      rng: () => 0.5,
    });
    client.start();
    lastInstance().onopen?.(new Event("open"));

    client.stop();
    expect(vi.getTimerCount()).toBe(0);

    client.setHeartbeatTimeoutMs(100);
    expect(vi.getTimerCount()).toBe(0);

    vi.advanceTimersByTime(60_000);
    expect(fakeInstances.length).toBe(1); // no new socket constructed
  });
});
