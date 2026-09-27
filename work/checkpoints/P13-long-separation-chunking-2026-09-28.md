# P13 long-input separation hardening — 2026-09-28

## Problem

The recovered Job12 source is about 11h53m. The previous long-input fix moved
the extracted timeline from WAV to FLAC, but `audio-separator` still materialized
one full-track RoFormer overlap-add buffer and attempted a 32.9 GB allocation.

## Change

`separate_dialogue` now keeps the existing one-pass path for short inputs and
uses a bounded long-input path when the probed duration exceeds 30 minutes:

- one separator/model instance is reused;
- 15-minute FLAC windows are created atomically;
- each window is separated in its own output directory;
- vocals and instrumental windows are concatenated to final FLAC files with
  FFmpeg and atomic replacement;
- the separation policy is versioned as `3`, so old full-track manifests and
  truncated stems cannot be reused on resume;
- the pipeline still validates both final stems against the source duration.

The chunk path is provider-free and has no broker or execution capability. It
does not claim a live or quality acceptance result; boundary quality remains a
media QA concern after the long run completes.

## Verification

- `uv run pytest -q tests/test_p13_runtime_fallbacks.py tests/test_pipeline_foundation.py tests/test_web_review.py` — 81 passed
- `uv run pytest -q tests/test_fault_contracts.py tests/test_audio_qa.py tests/test_longform.py` — 58 passed
- `uv run python -m compileall -q src` — pass
- `git diff --check` — pass
- short and long extraction smoke test with FFmpeg/FFprobe/soundfile — pass

The repository-wide Ruff invocation reports pre-existing violations in files
outside this slice; the modified separation file has no import-order errors.
