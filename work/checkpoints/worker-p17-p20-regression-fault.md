# Worker P17/P20 regression + fault harness

Date: 2026-09-22

Scope owned by this worker:

- `tools/benchmark.py`
- new `tests/test_regression_*.py` / `tests/test_fault_*.py`
- this checkpoint only

## Implemented

- Added deterministic before/after benchmark comparison helpers for RTF, rewrite rate, overflow rate, tempo, QA similarity, and per-stage timings.
- Added fail-closed acceptance evaluation using PLAN v1 thresholds currently measurable from summary artifacts: rewrite rate `<= 0.30`, overflow rate `<= 0.05`, tempo p95 `<= 1.20`.
- Added fixture-set comparison across matching available fixture IDs, including manifest path/hash validation before comparison.
- Added CLI commands `compare` and `compare-manifests`; non-passing acceptance returns exit code 2.
- Added fault/regression coverage for corrupt/missing cached artifact rejection, duplicate/stale/corrupt job lease behavior, deterministic profile snapshots with stage-relevant cache keys, and persisted job state discovery.

## Validation

- New focused tests: `8 passed`.
- Full project suite: `108 passed in 8.75s`.
- `compare-manifests` smoke-tested against the real fixture manifest with intentionally relaxed limits; both current available fixtures were discovered, validated, and compared with unchanged metrics.

## CP7 gaps not simulated here

Do not mark the full P20 matrix or CP7 complete from this worker alone. Stable automated simulation was intentionally not invented for:

- Internet/WebGPT disconnect, partial response, route-down fallback;
- TypeSafe timeout/429/5xx;
- process kill/reboot/power-loss during live pipeline work;
- CUDA OOM and real GPU fallback;
- disk-full behavior;
- browser reconnect against a live background job;
- TTS crash at a real segment boundary.

Those cases still need production-path automated fixtures where deterministic, otherwise manual receipts as required by PLAN P20.
