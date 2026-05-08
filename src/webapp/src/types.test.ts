import { describe, expect, it } from "vitest";
import {
  ControlMode,
  TrackingState,
  asTelUpdate,
  asVisUpdate,
  asWarningPayload,
  formatNumber,
  parseTelUpdate,
  parseVisUpdate,
} from "./types";

describe("parseTelUpdate", () => {
  it("accepts a fully populated valid TEL_UPDATE", () => {
    const result = parseTelUpdate({
      type: "TEL",
      seq: 42,
      timestamp_s: 1234.5,
      control_mode: ControlMode.Tracking,
      tracking_state: TrackingState.Tracking,
      target_x: 0.5,
      target_y: -0.25,
      bound_w: 0.3,
      bound_h: 0.2,
      confidence: 0.9,
      cmd_age_s: 0.05,
    });
    expect(result.mismatch).toBeNull();
    expect(result.value?.confidence).toBe(0.9);
    expect(result.value?.target_x).toBe(0.5);
    expect(result.value?.tracking_state).toBe(TrackingState.Tracking);
  });

  it("returns null+null for non-record input (silent skip, not a mismatch)", () => {
    expect(parseTelUpdate(null)).toEqual({ value: null, mismatch: null });
    expect(parseTelUpdate("not-an-object")).toEqual({ value: null, mismatch: null });
    expect(parseTelUpdate([1, 2, 3])).toEqual({ value: null, mismatch: null });
  });

  it("rejects out-of-range confidence", () => {
    const result = parseTelUpdate({ confidence: 1.5 });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("rejects out-of-range target_x", () => {
    const result = parseTelUpdate({ target_x: 2.0 });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("rejects negative bound_w", () => {
    const result = parseTelUpdate({ bound_w: -0.1 });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("rejects NaN confidence as out-of-range (readNumber filters it)", () => {
    const result = parseTelUpdate({ confidence: Number.NaN });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("rejects Infinity in target_y", () => {
    const result = parseTelUpdate({ target_y: Number.POSITIVE_INFINITY });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("rejects unknown tracking_state enum values", () => {
    const result = parseTelUpdate({ tracking_state: 99 });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("rejects unknown control_mode enum values", () => {
    const result = parseTelUpdate({ control_mode: 99 });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("treats missing optional fields as undefined (not a mismatch)", () => {
    const result = parseTelUpdate({});
    expect(result.mismatch).toBeNull();
    expect(result.value).not.toBeNull();
    expect(result.value?.confidence).toBeUndefined();
    expect(result.value?.tracking_state).toBeUndefined();
  });
});

describe("parseTelUpdate — boundary + edge cases", () => {
  it("accepts target_x at the inclusive bounds (-1, 0, 1)", () => {
    for (const x of [-1, -0, 0, 0.5, 1]) {
      const result = parseTelUpdate({ target_x: x });
      expect(result.mismatch).toBeNull();
      expect(result.value?.target_x).toBe(x);
    }
  });

  it("accepts confidence at the inclusive bounds (0 and 1)", () => {
    for (const c of [0, 0.5, 1]) {
      const result = parseTelUpdate({ confidence: c });
      expect(result.mismatch).toBeNull();
      expect(result.value?.confidence).toBe(c);
    }
  });

  it("rejects a string-typed numeric (no implicit coercion)", () => {
    const result = parseTelUpdate({ confidence: "0.5" });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/confidence/);
  });

  it("rejects an array in a numeric slot", () => {
    const result = parseTelUpdate({ target_x: [0.5] });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/target_x/);
  });

  it("treats null as a mismatch for non-nullable numeric fields", () => {
    // Wire spec says these fields are present-but-zero, not null. null
    // could mask a sensor failure if accepted.
    const result = parseTelUpdate({ confidence: null });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/confidence/);
  });

  it("strips wire-extra fields (closed-shape contract)", () => {
    const result = parseTelUpdate({
      confidence: 0.5,
      // A future server-side field the webapp does not yet know about
      // must NOT flow through into the validated record.
      injected_unknown_field: { malicious: "payload" },
    });
    expect(result.mismatch).toBeNull();
    const validated = result.value as Record<string, unknown> | null;
    expect(validated).not.toBeNull();
    expect(validated!).not.toHaveProperty("injected_unknown_field");
  });

  it("mismatch message names the offending field", () => {
    const result = parseTelUpdate({ bound_w: 1.5 });
    expect(result.mismatch).toContain("TEL_UPDATE.bound_w");
  });
});

describe("parseVisUpdate", () => {
  it("accepts a valid VIS_UPDATE", () => {
    const result = parseVisUpdate({
      type: "VIS",
      seq: 1,
      timestamp_s: 0.0,
      tracking_state: TrackingState.Tracking,
      loc_x: 0.1,
      loc_y: -0.1,
      bound_w: 0.2,
      bound_h: 0.3,
      confidence: 0.85,
    });
    expect(result.mismatch).toBeNull();
    expect(result.value?.confidence).toBe(0.85);
    expect(result.value?.loc_x).toBe(0.1);
  });

  it("rejects loc_x out of [-1, 1]", () => {
    const result = parseVisUpdate({ loc_x: -1.5 });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("rejects bound_h > 1", () => {
    const result = parseVisUpdate({ bound_h: 1.01 });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });

  it("rejects string-typed numerics", () => {
    const result = parseVisUpdate({ confidence: "0.5" });
    expect(result.value).toBeNull();
    expect(result.mismatch).toMatch(/out of range/);
  });
});

describe("asTelUpdate / asVisUpdate (legacy wrappers)", () => {
  it("asTelUpdate returns null on non-record input", () => {
    expect(asTelUpdate(null)).toBeNull();
    expect(asTelUpdate("x")).toBeNull();
  });

  it("asTelUpdate returns null on schema mismatch (sheds the reason)", () => {
    expect(asTelUpdate({ confidence: 1.5 })).toBeNull();
  });

  it("asTelUpdate returns the validated value on a clean record", () => {
    const result = asTelUpdate({ confidence: 0.5 });
    expect(result).not.toBeNull();
    expect(result?.confidence).toBe(0.5);
  });

  it("asVisUpdate matches the same contract", () => {
    expect(asVisUpdate(null)).toBeNull();
    expect(asVisUpdate({ loc_x: 2.0 })).toBeNull();
    expect(asVisUpdate({ loc_x: 0.5 })?.loc_x).toBe(0.5);
  });
});

describe("asWarningPayload", () => {
  it("accepts a valid info-severity warning", () => {
    const result = asWarningPayload({
      kind: "tracking_blocked",
      detail: "Vision stale",
      severity: "info",
    });
    expect(result).toEqual({
      kind: "tracking_blocked",
      detail: "Vision stale",
      severity: "info",
    });
  });

  it("rejects non-record input", () => {
    expect(asWarningPayload(null)).toBeNull();
    expect(asWarningPayload("not-a-warning")).toBeNull();
  });

  it("rejects when kind is missing", () => {
    expect(asWarningPayload({ detail: "x", severity: "warn" })).toBeNull();
  });

  it("rejects when detail is missing", () => {
    expect(asWarningPayload({ kind: "x", severity: "warn" })).toBeNull();
  });

  it("rejects unknown severity values (and is case-sensitive)", () => {
    expect(
      asWarningPayload({ kind: "k", detail: "d", severity: "FATAL" }),
    ).toBeNull();
    expect(
      asWarningPayload({ kind: "k", detail: "d", severity: "Warn" }),
    ).toBeNull();
    expect(
      asWarningPayload({ kind: "k", detail: "d", severity: "warning" }),
    ).toBeNull();
  });

  it("accepts each documented severity", () => {
    for (const severity of ["info", "warn", "error"] as const) {
      expect(
        asWarningPayload({ kind: "k", detail: "d", severity }),
      ).toEqual({ kind: "k", detail: "d", severity });
    }
  });
});

describe("formatNumber non-finite handling", () => {
  it("renders n/a for null, undefined, NaN", () => {
    expect(formatNumber(null, 2, " s")).toBe("n/a");
    expect(formatNumber(undefined, 2, " s")).toBe("n/a");
    expect(formatNumber(Number.NaN, 2, " s")).toBe("n/a");
  });

  it("renders n/a for +Infinity and -Infinity (regression: was 'Infinity m')", () => {
    expect(formatNumber(Number.POSITIVE_INFINITY, 2, " m")).toBe("n/a");
    expect(formatNumber(Number.NEGATIVE_INFINITY, 2, " m")).toBe("n/a");
  });

  it("formats finite numbers with the suffix", () => {
    expect(formatNumber(0.5, 2, " s")).toBe("0.50 s");
    expect(formatNumber(-1.234, 3)).toBe("-1.234");
  });
});
