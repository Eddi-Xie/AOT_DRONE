export interface NormalizedBox {
  centerX: number;
  centerY: number;
  boundW: number;
  boundH: number;
}

export interface CanvasRect {
  x: number;
  y: number;
  width: number;
  height: number;
  centerX: number;
  centerY: number;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function clamp(value: number, min: number, max: number): number {
  if (value < min) {
    return min;
  }
  if (value > max) {
    return max;
  }
  return value;
}

export function normalizedToCanvasRect(
  box: NormalizedBox,
  canvasWidth: number,
  canvasHeight: number,
): CanvasRect | null {
  if (!isFiniteNumber(canvasWidth) || !isFiniteNumber(canvasHeight)) {
    return null;
  }

  if (canvasWidth <= 0 || canvasHeight <= 0) {
    return null;
  }

  if (
    !isFiniteNumber(box.centerX) ||
    !isFiniteNumber(box.centerY) ||
    !isFiniteNumber(box.boundW) ||
    !isFiniteNumber(box.boundH)
  ) {
    return null;
  }

  const centerXNorm = clamp(box.centerX, -1, 1);
  const centerYNorm = clamp(box.centerY, -1, 1);
  const boundWNorm = clamp(box.boundW, 0, 1);
  const boundHNorm = clamp(box.boundH, 0, 1);

  const cxNorm01 = (centerXNorm + 1) / 2;
  const cyNorm01 = 1 - (centerYNorm + 1) / 2;

  const centerX = cxNorm01 * canvasWidth;
  const centerY = cyNorm01 * canvasHeight;

  const widthRaw = boundWNorm * canvasWidth;
  const heightRaw = boundHNorm * canvasHeight;

  const leftRaw = centerX - widthRaw / 2;
  const topRaw = centerY - heightRaw / 2;
  const rightRaw = leftRaw + widthRaw;
  const bottomRaw = topRaw + heightRaw;

  const left = clamp(leftRaw, 0, canvasWidth);
  const top = clamp(topRaw, 0, canvasHeight);
  const right = clamp(rightRaw, 0, canvasWidth);
  const bottom = clamp(bottomRaw, 0, canvasHeight);

  return {
    x: left,
    y: top,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
    centerX: clamp(centerX, 0, canvasWidth),
    centerY: clamp(centerY, 0, canvasHeight),
  };
}
