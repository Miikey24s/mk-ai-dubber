import assert from 'node:assert/strict';
import { deriveEtaTelemetry } from '../src/lib/telemetry.ts';

const at = 1_700_000_000_000;

const missing = deriveEtaTelemetry({
  status: 'running',
  progress: 0.25,
  durationSeconds: 100,
  realTimeFactor: 0.8,
  nowMs: at,
});
assert.equal(missing.remainingSeconds, null, 'missing persisted elapsed must stay unavailable');
assert.equal(missing.completionAtMs, null, 'missing persisted elapsed must not produce a clock');

const nonFinite = deriveEtaTelemetry({
  status: 'running',
  progress: 0.25,
  elapsedSeconds: 10,
  durationSeconds: Number.NaN,
  realTimeFactor: 0.8,
  nowMs: at,
});
assert.equal(nonFinite.remainingSeconds, null, 'non-finite duration must stay unavailable');

const zeroTotal = deriveEtaTelemetry({
  status: 'running',
  progress: 0,
  elapsedSeconds: 0,
  durationSeconds: 0,
  realTimeFactor: 0.8,
  nowMs: at,
});
assert.equal(zeroTotal.remainingSeconds, null, 'zero duration must stay unavailable');

const partial = deriveEtaTelemetry({
  status: 'running',
  progress: 0.5,
  elapsedSeconds: 10,
  durationSeconds: 100,
  realTimeFactor: 0.8,
  nowMs: at,
});
assert.equal(partial.remainingSeconds, 10, 'partial progress uses persisted elapsed time');
assert.equal(partial.completionAtMs, at + 10_000, 'completion clock follows finite ETA');

const initial = deriveEtaTelemetry({
  status: 'running',
  progress: 0,
  elapsedSeconds: 0,
  durationSeconds: 100,
  realTimeFactor: 0.8,
  nowMs: at,
});
assert.equal(initial.remainingSeconds, 80, 'zero progress may estimate from persisted duration and RTF');

console.log(JSON.stringify({ status: 'PASS', cases: 5 }));
