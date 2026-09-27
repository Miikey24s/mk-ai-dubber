export type NullableNumber = number | null;

export function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function nonNegativeFinite(value: unknown): value is number {
  return finiteNumber(value) && value >= 0;
}

export function positiveFinite(value: unknown): value is number {
  return finiteNumber(value) && value > 0;
}

export function normalizeProgress(value: unknown): NullableNumber {
  return finiteNumber(value) && value >= 0 && value <= 1 ? value : null;
}

export interface EtaTelemetryInput {
  status?: string;
  progress?: unknown;
  elapsedSeconds?: unknown;
  durationSeconds?: unknown;
  realTimeFactor?: unknown;
  nowMs?: unknown;
}

export interface EtaTelemetryState {
  progress: NullableNumber;
  elapsedSeconds: NullableNumber;
  durationSeconds: NullableNumber;
  realTimeFactor: NullableNumber;
  remainingSeconds: NullableNumber;
  completionAtMs: NullableNumber;
}

/**
 * Derive ETA only from persisted, finite telemetry. A missing or invalid
 * input stays unknown instead of becoming a plausible-looking estimate.
 */
export function deriveEtaTelemetry(input: EtaTelemetryInput): EtaTelemetryState {
  const progress = normalizeProgress(input.progress);
  const elapsedSeconds = nonNegativeFinite(input.elapsedSeconds) ? input.elapsedSeconds : null;
  const durationSeconds = positiveFinite(input.durationSeconds) ? input.durationSeconds : null;
  const realTimeFactor = positiveFinite(input.realTimeFactor) ? input.realTimeFactor : null;
  const isCompleted = input.status === 'completed';

  let remainingSeconds: NullableNumber = null;
  if (isCompleted) {
    remainingSeconds = 0;
  } else if (
    progress !== null &&
    elapsedSeconds !== null &&
    durationSeconds !== null &&
    realTimeFactor !== null
  ) {
    const estimate = progress > 0 && progress < 1
      ? elapsedSeconds > 0
        ? (elapsedSeconds / progress) - elapsedSeconds
        : null
      : progress === 0
        ? durationSeconds * realTimeFactor
        : null;
    if (estimate !== null && finiteNumber(estimate)) {
      remainingSeconds = Math.max(0, estimate);
    }
  }

  const nowMs = finiteNumber(input.nowMs) ? input.nowMs : Date.now();
  const completionAtMs = remainingSeconds !== null && finiteNumber(nowMs)
    ? nowMs + remainingSeconds * 1000
    : null;

  return {
    progress,
    elapsedSeconds,
    durationSeconds,
    realTimeFactor,
    remainingSeconds,
    completionAtMs,
  };
}
