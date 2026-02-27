export function clampNumber(value: number, min: number, max: number): number {
  if (value < min) {
    return min;
  }
  if (value > max) {
    return max;
  }
  return value;
}

export function projectAge(
  snapshotAgeS: number | null,
  snapshotAtMs: number | null,
  nowMs: number,
): number | null {
  if (snapshotAgeS === null) {
    return null;
  }

  if (snapshotAtMs === null) {
    return snapshotAgeS;
  }

  const elapsedS = Math.max(0, (nowMs - snapshotAtMs) / 1000);
  return snapshotAgeS + elapsedS;
}

export function formatFixed(
  value: number | null | undefined,
  digits = 2,
  fallback = "n/a",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return fallback;
  }
  return value.toFixed(digits);
}

export function formatSeconds(value: number | null | undefined, digits = 2): string {
  const fixed = formatFixed(value, digits);
  return fixed === "n/a" ? fixed : `${fixed} s`;
}
