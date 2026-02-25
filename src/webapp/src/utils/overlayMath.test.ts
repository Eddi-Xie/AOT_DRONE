// Test-ready scaffold for future Vitest wiring.
// Enable by installing Vitest and uncommenting this block:
//
// import { describe, expect, it } from "vitest";
// import { normalizedToCanvasRect } from "./overlayMath";
//
// describe("normalizedToCanvasRect", () => {
//   it("maps center (0,0) to canvas center", () => {
//     const rect = normalizedToCanvasRect(
//       { centerX: 0, centerY: 0, boundW: 0.2, boundH: 0.4 },
//       200,
//       100,
//     );
//     expect(rect?.centerX).toBeCloseTo(100, 6);
//     expect(rect?.centerY).toBeCloseTo(50, 6);
//   });
//
//   it("maps loc_x=1 to right edge center", () => {
//     const rect = normalizedToCanvasRect(
//       { centerX: 1, centerY: 0, boundW: 0, boundH: 0 },
//       320,
//       180,
//     );
//     expect(rect?.centerX).toBeCloseTo(320, 6);
//     expect(rect?.centerY).toBeCloseTo(90, 6);
//   });
//
//   it("maps loc_y=1 to top edge center", () => {
//     const rect = normalizedToCanvasRect(
//       { centerX: 0, centerY: 1, boundW: 0, boundH: 0 },
//       320,
//       180,
//     );
//     expect(rect?.centerY).toBeCloseTo(0, 6);
//   });
//
//   it("scales bound_w/bound_h and clamps safely", () => {
//     const rect = normalizedToCanvasRect(
//       { centerX: 1, centerY: -1, boundW: 2, boundH: 2 },
//       100,
//       50,
//     );
//     expect(rect).toBeTruthy();
//     expect((rect?.x ?? 0) + (rect?.width ?? 0)).toBeLessThanOrEqual(100);
//     expect((rect?.y ?? 0) + (rect?.height ?? 0)).toBeLessThanOrEqual(50);
//   });
// });

export {};
