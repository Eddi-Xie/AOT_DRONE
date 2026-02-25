import { normalizedToCanvasRect } from "./overlayMath";

let hasRun = false;

function assert(condition: boolean, message: string): void {
  if (!condition) {
    throw new Error(`overlayMath assertion failed: ${message}`);
  }
}

function assertPresent<T>(value: T | null, message: string): T {
  if (value === null) {
    throw new Error(`overlayMath assertion failed: ${message}`);
  }
  return value;
}

function approxEqual(actual: number, expected: number, epsilon = 1e-6): boolean {
  return Math.abs(actual - expected) <= epsilon;
}

export function runOverlayMathDevAssertions(): void {
  if (hasRun) {
    return;
  }
  hasRun = true;

  const centerRect = normalizedToCanvasRect(
    {
      centerX: 0,
      centerY: 0,
      boundW: 0.2,
      boundH: 0.4,
    },
    200,
    100,
  );
  const center = assertPresent(centerRect, "center rect should exist");
  assert(approxEqual(center.centerX, 100), "centerX=0 should map to W/2");
  assert(approxEqual(center.centerY, 50), "centerY=0 should map to H/2");

  const rightEdge = normalizedToCanvasRect(
    {
      centerX: 1,
      centerY: 0,
      boundW: 0,
      boundH: 0,
    },
    320,
    180,
  );
  const right = assertPresent(rightEdge, "right-edge rect should exist");
  assert(approxEqual(right.centerX, 320), "centerX=1 should map to right edge");

  const topEdge = normalizedToCanvasRect(
    {
      centerX: 0,
      centerY: 1,
      boundW: 0,
      boundH: 0,
    },
    320,
    180,
  );
  const top = assertPresent(topEdge, "top-edge rect should exist");
  assert(approxEqual(top.centerY, 0), "centerY=1 should map to top edge");

  const scaled = normalizedToCanvasRect(
    {
      centerX: 0,
      centerY: 0,
      boundW: 0.5,
      boundH: 0.25,
    },
    400,
    200,
  );
  const scaledRect = assertPresent(scaled, "scaled rect should exist");
  assert(approxEqual(scaledRect.width, 200), "bound_w should scale by canvas width");
  assert(approxEqual(scaledRect.height, 50), "bound_h should scale by canvas height");

  const clamped = normalizedToCanvasRect(
    {
      centerX: 1,
      centerY: -1,
      boundW: 2,
      boundH: 2,
    },
    100,
    50,
  );
  const clampedRect = assertPresent(clamped, "clamped rect should exist");
  assert(clampedRect.x >= 0 && clampedRect.x <= 100, "x must stay within [0,W]");
  assert(clampedRect.y >= 0 && clampedRect.y <= 50, "y must stay within [0,H]");
  assert(clampedRect.x + clampedRect.width <= 100, "x+width must stay within W");
  assert(clampedRect.y + clampedRect.height <= 50, "y+height must stay within H");
  assert(Number.isFinite(clampedRect.x), "x must be finite");
  assert(Number.isFinite(clampedRect.y), "y must be finite");
  assert(Number.isFinite(clampedRect.width), "width must be finite");
  assert(Number.isFinite(clampedRect.height), "height must be finite");
}
