import { describe, expect, it } from "vitest";
import {
  ControlMode,
  TrackingState,
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
