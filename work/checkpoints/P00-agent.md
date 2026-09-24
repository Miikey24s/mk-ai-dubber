# P00 profiler foundation handoff

Task: `P00-PROFILER-A`

Decision: `BUILD`

Reason: this is project-owned control-plane instrumentation. Pulling an external
profiler dependency would add coupling without improving the narrow wall-time,
counter, run/cache metadata, and CUDA-memory contract required by P00.

## Implemented

- `src/vi_dubber/metrics.py`
  - `MetricsRecorder.stage(name)` aggregates wall time, calls, and failed calls.
  - Standard counters are initialized to zero and custom integer counters are allowed.
  - `run.kind` distinguishes `cold`, `warm`, and `resume` benchmark runs.
  - `run.cache` and `run.metadata` retain JSON-safe benchmark context.
  - `finish()` freezes total elapsed time for a stable final snapshot.
  - `cuda_memory_snapshot()` lazily imports torch and reports current/peak
    allocated and reserved bytes when CUDA is available.
  - Module import itself has no torch/CUDA side effects.
- `tests/test_metrics.py`
  - deterministic fake-clock coverage for timing and aggregate calls;
  - failure timing preserves the original exception;
  - frozen elapsed time after `finish()`;
  - standard/custom counters and type validation;
  - CUDA disabled/missing-torch no-op paths;
  - strict JSON serialization smoke.

## Validation

Focused:

`uv run pytest -q tests/test_metrics.py`

Result: `7 passed in 0.05s`

Full regression at this checkpoint:

`uv run pytest -q`

Result: `26 passed in 4.34s`

CUDA smoke on this machine reported `NVIDIA GeForce RTX 2070 SUPER`, device 0,
with integer current/peak allocated and reserved byte fields. No dubbing/model
benchmark was run in this worker task.

## Integration contract

Pipeline integration should construct one recorder per job, wrap stage bodies
with `with metrics.stage("stage_name")`, increment counters at the ownership
site, call `finish()` once the job is complete, and persist `metrics.snapshot()`
as the machine-readable P00 benchmark artifact.

This worker intentionally did not edit `pipeline.py`, runtime/config, TTS,
translation, or TypeSafe behavior.

## Remaining risk

CUDA peak values are process/global torch allocator peaks unless another owner
resets them. Integration should decide whether benchmark runs need an explicit
peak reset boundary before claiming per-stage GPU peaks.
