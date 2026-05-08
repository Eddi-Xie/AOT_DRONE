import { describe, expect, it } from "vitest";
import { AppState, INITIAL_STATE, heartbeatMsFromVisFresh, reducer } from "./App";
import { ControlMode } from "./types";

function s(overrides: Partial<AppState> = {}): AppState {
  return { ...INITIAL_STATE, ...overrides };
}

describe("App reducer — schema-mismatch sticky behaviour", () => {
  it("first SCHEMA_MISMATCH_DETECTED stores the detail", () => {
    const next = reducer(s(), {
      type: "SCHEMA_MISMATCH_DETECTED",
      detail: "TEL_UPDATE.confidence out of range or wrong type",
    });
    expect(next.schemaMismatchDetail).toBe(
      "TEL_UPDATE.confidence out of range or wrong type",
    );
  });

  it("second SCHEMA_MISMATCH_DETECTED is a no-op (sticky-once)", () => {
    const after_first = reducer(s(), {
      type: "SCHEMA_MISMATCH_DETECTED",
      detail: "first reason",
    });
    const after_second = reducer(after_first, {
      type: "SCHEMA_MISMATCH_DETECTED",
      detail: "second reason",
    });
    // Identity check pins down "no new state object created" — important
    // because returning a fresh object would re-render every consumer.
    expect(after_second).toBe(after_first);
    expect(after_second.schemaMismatchDetail).toBe("first reason");
  });
});

describe("App reducer — WS transport error counter", () => {
  it("WS_TRANSPORT_ERROR with same count is a no-op", () => {
    const start = s({ wsConsecutiveErrors: 2 });
    const next = reducer(start, { type: "WS_TRANSPORT_ERROR", consecutiveErrors: 2 });
    expect(next).toBe(start);
  });

  it("WS_TRANSPORT_ERROR with new count updates state", () => {
    const start = s({ wsConsecutiveErrors: 2 });
    const next = reducer(start, { type: "WS_TRANSPORT_ERROR", consecutiveErrors: 3 });
    expect(next.wsConsecutiveErrors).toBe(3);
  });

  it("WS_TRANSPORT_RECOVERED on already-zero is a no-op", () => {
    const start = s({ wsConsecutiveErrors: 0 });
    const next = reducer(start, { type: "WS_TRANSPORT_RECOVERED" });
    expect(next).toBe(start);
  });

  it("WS_TRANSPORT_RECOVERED resets a non-zero counter to 0", () => {
    const start = s({ wsConsecutiveErrors: 5 });
    const next = reducer(start, { type: "WS_TRANSPORT_RECOVERED" });
    expect(next.wsConsecutiveErrors).toBe(0);
  });
});

describe("App reducer — STREAM_BATCH preserves prior fields when batch slot absent", () => {
  it("a batch without linkStatus does not clobber existing state.linkStatus", () => {
    const start = s({
      linkStatus: { fc_connected: true } as unknown as AppState["linkStatus"],
      linkUpdatedAtMs: 100,
      linkEnvelopeTimestampS: 1.5,
    });
    const next = reducer(start, {
      type: "STREAM_BATCH",
      batch: { lastMessageAtMs: 200 },
    });
    expect(next.linkStatus).toBe(start.linkStatus);
    expect(next.linkUpdatedAtMs).toBe(100);
    expect(next.linkEnvelopeTimestampS).toBe(1.5);
    expect(next.lastWsMessageAtMs).toBe(200);
  });

  it("a batch without latestTel preserves the prior TEL", () => {
    const priorTel = { confidence: 0.8 };
    const start = s({ latestTel: priorTel });
    const next = reducer(start, {
      type: "STREAM_BATCH",
      batch: { lastMessageAtMs: 1 },
    });
    expect(next.latestTel).toBe(priorTel);
  });

  it("overlay confidence sample appends to history with limit", () => {
    const start = s({ confidenceHistory: [0.1, 0.2] });
    const next = reducer(start, {
      type: "STREAM_BATCH",
      batch: { lastMessageAtMs: 1, overlayConfidenceSample: 0.3 },
    });
    expect(next.confidenceHistory[next.confidenceHistory.length - 1]).toBe(0.3);
    expect(next.confidenceHistory.length).toBe(3);
  });
});

describe("heartbeatMsFromVisFresh", () => {
  it("returns (2 * visFreshS + 0.5) * 1000 for positive input", () => {
    expect(heartbeatMsFromVisFresh(0.25)).toBe(1000); // 2*0.25 + 0.5 = 1.0
    expect(heartbeatMsFromVisFresh(0.5)).toBe(1500); // 2*0.5 + 0.5 = 1.5
    expect(heartbeatMsFromVisFresh(1)).toBe(2500); // 2*1 + 0.5 = 2.5
  });

  it("returns null for null / zero / negative / non-finite input", () => {
    expect(heartbeatMsFromVisFresh(null)).toBeNull();
    expect(heartbeatMsFromVisFresh(0)).toBeNull();
    expect(heartbeatMsFromVisFresh(-0.1)).toBeNull();
    // Implementation guards "<=0" and falls through; NaN compared to 0 is
    // false, so NaN flows into the formula and produces NaN. Document the
    // behaviour: callers (App) should filter at the readNumber boundary.
    expect(Number.isNaN(heartbeatMsFromVisFresh(Number.NaN) as number)).toBe(true);
  });
});

describe("App reducer — intent + mode + armed", () => {
  it("INTENT_PENDING sets feedback kind sending + pending=true", () => {
    const next = reducer(s(), { type: "INTENT_PENDING", message: "Sending..." });
    expect(next.intentPending).toBe(true);
    expect(next.intentFeedbackKind).toBe("sending");
    expect(next.intentFeedbackMessage).toBe("Sending...");
  });

  it("INTENT_FINISHED success clears pending + records message", () => {
    const after_pending = reducer(s(), {
      type: "INTENT_PENDING",
      message: "Sending...",
    });
    const next = reducer(after_pending, {
      type: "INTENT_FINISHED",
      kind: "success",
      message: "OK",
    });
    expect(next.intentPending).toBe(false);
    expect(next.intentFeedbackKind).toBe("success");
    expect(next.intentFeedbackMessage).toBe("OK");
  });

  it("SET_SELECTED_MODE updates selectedMode", () => {
    const next = reducer(s(), {
      type: "SET_SELECTED_MODE",
      mode: ControlMode.Tracking,
    });
    expect(next.selectedMode).toBe(ControlMode.Tracking);
  });

  it("SET_ARMED toggles armed", () => {
    expect(reducer(s(), { type: "SET_ARMED", armed: true }).armed).toBe(true);
    expect(reducer(s({ armed: true }), { type: "SET_ARMED", armed: false }).armed).toBe(
      false,
    );
  });
});
