# P23 machine gates — 2026-09-26

Status: **PARTIAL ACCEPTANCE EVIDENCE; P23 REMAINS IN PROGRESS**

## Short single-chunk overhead gate

The first short-overhead harness compared the current tree against historical commit `e2133d2`. That was rejected as an invalid P23-only comparison because the historical code still pinned WebGPT to retired instance `:17842`, mixing transport changes with the scheduler/core comparison.

`scripts/benchmark_p23_short_overhead.py` now compares the **same current code, model/runtime, profile and input** with only one intentional config difference:

- baseline: `longform.enabled=false`
- candidate: `longform.enabled=true`

The fixture is below `single_chunk_threshold_seconds`, so the candidate must stay on the ordinary short path and must not create a macro-chunk plan. The harness alternates run order, uses three repeats by default, verifies resolved non-longform axes, QA, source/translation similarity and a conservative 5% slowdown ceiling. Relative glossary paths are resolved against the source config so benchmark configs under `work/benchmarks/` do not break translation.

Live evidence:

- `uv run python scripts/verify_p22_live.py` passed against Dedicated Dubber-WebGPT `127.0.0.1:17850`, model `chatgpt-web/gpt-5.6-sol`, effort `high`, wall `24.573s`, terminology gate pass and critical-token gate pass.
- A fresh short A/B baseline run then completed BS-RoFormer + WhisperX (53 source segments) but failed in production direct Responses translation with `RuntimeError: Direct WebGPT Responses trả kết quả JSON không hợp lệ.`
- The pipeline remained fail-closed. No short-overhead PASS is claimed and no tuning/default is promoted.

Receipt: `work/benchmarks/p23-short-overhead-current-v2-20260926/results.json`.

## 6h+ synthetic control-plane gate

Added `scripts/verify_p23_superlong.py` plus `tests/test_p23_superlong_acceptance.py`. The harness uses the real `BoundedExecutor` and real per-chunk manifest commit/load primitives over a synthetic 6.25h / 25-chunk timeline.

Observed receipt:

- timeline: `6.25h`, `25` chunks, contiguous/ordered;
- bounded queue: `1` running worker, `2` queued, producer demonstrably blocked at saturation;
- process RSS delta during the probe: `1,572,864` bytes;
- temp disk peak: `1,048,576` bytes; no `.part` files remain;
- crash/resume: chunks `0..11` reused, failed/orphan chunk rejected before manifest commit, chunks `12..24` recomputed, sibling hashes preserved;
- forward/reverse/interleaved completion orders produce the same deterministic assembly digest `7c25ef50cc10d3de9f8062f2d143b3213b1dae1a3115476bb39e2136242b6895`.

Receipt: `work/benchmarks/p23-superlong-synthetic-20260926.json`.

The Windows RSS probe initially failed because `GetCurrentProcess()` used ctypes' default integer return type, truncating the pseudo-handle. The probe now declares the Windows API `restype`/`argtypes`; the focused acceptance tests pass after the fix.

This synthetic gate intentionally does **not** claim:

- real 6h media throughput;
- VRAM/OOM acceptance under real WhisperX/VieNeu work;
- live WebGPT pressure/restart acceptance;
- FFmpeg final-media bitwise parity.

Those remain part of the live 6h+ P23 promotion gate.

## Validation

- `uv run pytest -q tests\test_p23_short_overhead.py tests\test_p23_superlong_acceptance.py` -> `9 passed`.
- `uv run pytest -q` -> `460 passed, 2 warnings`.
- `npm run build` in `frontend/` -> TypeScript + Vite build passed.
- `uv run vi-dubber doctor` -> RTX 2070 SUPER CUDA, WhisperX, audio-separator, VieNeu and Dedicated Dubber-WebGPT `:17850` healthy before the live benchmark wave.

## Remaining P23 promotion gates

1. Re-run the three-trial short A/B only after the live direct Responses route can complete the production translation batch reliably; keep the current fail-closed receipt as evidence of the failed attempt.
2. Do not spend the much larger three-trial 33-minute auto-tune matrix while the shorter production-path gate is failing on the same provider route.
3. Run a real/instrumented 6h+ media acceptance for RSS/VRAM/disk/queue pressure, slow downstream backpressure, mid-job restart and final-media ordering once the live provider gate is stable.

P07/P11/P12 remain separate human-listening/label gates; this checkpoint does not replace those ballots.
