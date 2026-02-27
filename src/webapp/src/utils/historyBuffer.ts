export const DEFAULT_HISTORY_LIMIT = 60;

function sanitizeLimit(limit: number): number {
  if (!Number.isFinite(limit) || limit < 1) {
    return DEFAULT_HISTORY_LIMIT;
  }
  return Math.trunc(limit);
}

function sanitizeSample(sample: number): number {
  if (!Number.isFinite(sample)) {
    return 0;
  }
  return sample;
}

export function pushHistorySample(
  history: readonly number[],
  sample: number,
  limit = DEFAULT_HISTORY_LIMIT,
): number[] {
  const normalizedLimit = sanitizeLimit(limit);
  const next = history.length >= normalizedLimit ? history.slice(1) : history.slice();
  next.push(sanitizeSample(sample));
  return next;
}
