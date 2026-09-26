# P23 auto-tune harness hardening — 2026-09-26

Status: **CODE/REGRESSION READY; LIVE PROMOTION GATE OPEN**

## What changed

- `balanced_fast` no longer overwrites `tts.batch_size` with `1`. The profile now inherits the benchmarked production value from `config.yaml` (`4`) and allows P23 variants such as `tts8` to actually reach the TTS engine.
- `scripts/benchmark_p23_autotune.py` verifies the resolved ASR batch, TTS batch, WebGPT concurrency and long-form chunk target before an expensive run. A profile/config override now fails closed instead of silently producing a fake A/B.
- Default candidates are now `baseline`, `chunk20m` and `tts8`. `asr8` was removed from the default matrix because P13 already rejected increasing WhisperX batch above the production batch-4 baseline on this RTX 2070 SUPER / current runtime.
- Promotion now requires at least three trials. Trial order rotates across variants and recommendation uses median whole-job wall time; a one-run screen cannot be promoted from a 3% timing difference.
- Receipts persist `resolved_tuning` so the exact knobs that reached the resolved runtime config are auditable.

## Current live evidence

- Dedicated Dubber-WebGPT `127.0.0.1:17850` is online and doctor reports the direct Responses route healthy.
- The retained 1987.202 s (~33 min) baseline job was resumed to completion after the earlier transport interruption: QA passed, both macro-chunk previews are valid, WebGPT recorded zero request/pressure failures, and final mix gates passed.
- That resumed job is **correctness evidence only**. Its timing includes cache/resume behavior and is not a valid cold whole-job speed baseline, so no P23 speedup is claimed from it.
- A fresh cold rerun was intentionally stopped during invariant BS-RoFormer separation after confirming it would repeat roughly the same expensive upstream work. The hardened harness is ready for the next deliberate repeated run.

## Validation

- Focused P23/profile/QA regression after hardening: `82 passed`.
- Full suite: `451 passed, 2 warnings`.
- `uv run vi-dubber doctor`: CUDA RTX 2070 SUPER, WhisperX, audio-separator, VieNeu, Dedicated Dubber-WebGPT `:17850`, local Qwen, config schema and profiles all healthy.
- Dry-run receipt: `work/benchmarks/p23-autotune-dryrun-repeated-20260926/results.json`.
- Dry-run resolves exactly:
  - baseline: ASR 4 / TTS 4 / WebGPT c2 / chunk 1500 s;
  - chunk20m: ASR 4 / TTS 4 / WebGPT c2 / chunk 1200 s;
  - tts8: ASR 4 / TTS 8 / WebGPT c2 / chunk 1500 s.

## Remaining promotion gates

1. Short single-chunk A/B to quantify scheduler/chunk overhead.
2. Three-trial rotated whole-job benchmark on the same 1987.202 s fixture/hardware; compare medians and require all quality/fault gates to pass.
3. 6h+ instrumented resource/fault acceptance covering bounded RSS/VRAM/disk/queue depth, slow downstream backpressure, mid-job kill/resume and deterministic ordering/final assembly.

Until those gates pass, P23 remains **IN PROGRESS** and no performance default beyond already accepted P08/P13/P22 settings is promoted.
