# P23 fresh 6h final-media rerun reconcile — 27/09/2026

Status: **PASS / FINAL-ASSEMBLY 6H RESOURCE+GPU SUB-GATE CLOSED**.

The required fresh representative rerun already existed in the workspace but had not been reconciled into the PLAN/outer RESUME. It was created at `2026-09-26 23:03:48 +07:00`, after the PDH attribution commit `cadba20` (`2026-09-26 18:27:37 +07:00`). The harness/test files are unchanged from that attribution commit through the current HEAD used for this review.

Evidence receipt: `work/checkpoints/P23-real-media-final-assembly-6h-rerun-2026-09-26.json`.

- SHA-256: `8a33687e7899cca6707732c33bf33d68f901c9b99098a949eba6d5d88c87e7d1`
- requested/source duration: `21636s` = `6.01h`
- receipt status: `passed`
- `representative_superlong=true`
- overall `p23_real_media_final_assembly=true`
- PDH source: `windows-pdh-dedicated-usage`
- observed mux process-tree PIDs: `4`
- peak process-tree dedicated GPU: `0.0 MiB`
- media validity, loudness, bounded RSS and bounded workspace-growth gates: pass
- mux wall time: `1164.7899545s`

Current focused regression was rerun during reconciliation:

`uv run pytest -q tests/test_p23_real_media_acceptance.py` → `2 passed in 4.68s`.

This closes only the real-media **final assembly** 6h resource/GPU sub-gate. It does not claim 6h ASR/separation/TTS/translation throughput, whole-pipeline CUDA VRAM/OOM acceptance, live WebGPT pressure/restart acceptance or whole-job speedup.
