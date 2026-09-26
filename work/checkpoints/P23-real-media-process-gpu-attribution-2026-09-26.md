# P23 real-media process GPU attribution — 26/09/2026

Status: **INSTRUMENTATION PASS / FULL 6H RERUN STILL REQUIRED**.

## Problem

The first 6.01h final-media run reported `system_gpu_delta_mib=6884`, but that value came from system-wide `nvidia-smi memory.used`. It included unrelated desktop/browser/Codex/Figma GPU consumers and could not be attributed to the VI Dubber mux process tree. On this Windows/WDDM machine, `nvidia-smi --query-compute-apps ... used_gpu_memory` also returns `[N/A]`, so NVML-style per-process memory is not a defensible replacement here.

## Change

`scripts/verify_p23_real_media.py` now samples Windows PDH `GPU Process Memory(*)\\Dedicated Usage`, parses the PID-bearing instances, and sums only PIDs belonging to the monitored mux helper + descendants. The harness still records system GPU usage as diagnostic context, but the acceptance gate now uses peak process-tree dedicated GPU memory only.

If process attribution is unavailable or errors during the run, the GPU gate fails closed. The old system-wide delta no longer decides product acceptance.

The harness remains scoped to **final-media/FFmpeg assembly**. It still does not claim whole-pipeline 6h ASR/separation/TTS/translation VRAM or throughput acceptance.

## Validation

- `uv run pytest -q tests/test_p23_real_media_acceptance.py` → `2 passed in 4.29s`.
- Production-mux smoke command used the same harness path with a ~5 second real H264/AAC fixture.
- Smoke receipt: `work/checkpoints/P23-real-media-process-gpu-smoke-2026-09-26.json`.
- Smoke receipt SHA-256: `fb9be2535082a2a9b791e1a2f33cb7091ad85246a69b304339ea2484bc8e58f1`.
- PDH source: `windows-pdh-dedicated-usage`.
- Observed mux process-tree PIDs: 3.
- Peak process-tree dedicated GPU memory: `0.0 MiB`.
- System GPU before/peak stayed around `1212 MiB`; this is now diagnostic only.
- Media, duration, loudness, RSS and workspace-growth gates passed in the smoke.
- Overall smoke status remained `failed` exactly because a 5 second fixture is not representative-superlong; the harness did not self-promote the short smoke.

## Remaining gate

The historical 6.01h result is not retroactively converted to PASS because it did not collect process-attributed GPU samples. A fresh representative 6h+ final-media run with this instrumentation is still required before closing that gate. The previous run took about 25.5 minutes for mux alone, so it should be rerun when that evidence will actually be consumed; no synthetic or short-run inference replaces it.
