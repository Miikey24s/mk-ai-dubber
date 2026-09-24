# P10-P12 audio/QA agent checkpoint

Date: 2026-09-22

## Scope

- Ownership used: `src/vi_dubber/media.py`, `src/vi_dubber/qa.py`, focused tests.
- No edits to `pipeline.py`, `config.yaml`, `PLAN.md`, `README.md`, TTS, or reference selection.

## P10 - Assembly/boundary/overlap

Implemented deterministic core behavior:

- voice track now preserves the requested timeline length to the nearest sample instead of appending a hidden 0.5 s tail;
- boundary fade behavior remains deterministic;
- overlap is explicit with `equal_power` (default) and `sum` policies;
- equal-power overlap prevents simple additive collisions from doubling level before the existing peak guard;
- unknown overlap policies fail fast instead of silently overwriting/mixing.

Integration note: the pipeline's `timing_assembly` cache policy version still belongs to root. Because assembly semantics changed, root should bump that fingerprint policy before relying on resume caches.

Long-video memory acceptance is not claimed. Assembly still uses an in-memory timeline buffer; PLAN only requires chunk/stream replacement if benchmark evidence justifies it.

## P11 - Mix/master

Implemented deterministic helpers:

- optional sidechain background ducking in the FFmpeg mix graph using a split dialogue key;
- `measure_mix_metrics()` runs FFmpeg loudnorm analysis and reports integrated LUFS, true peak, LRA, threshold, target offset, loudness delta, target pass flags, and clipping risk;
- existing pipeline behavior stays conservative because ducking is opt-in and root owns runtime/config integration.

Not accepted yet:

- the current mux path still uses single-pass loudnorm;
- no real representative-fixture listening A/B was performed;
- no claim yet that final output holds `-14 LUFS +/-0.5`, `<= -1.5 dBTP`, zero clipping, natural ducking, or music continuity on production fixtures.

## P12 - Segment QA/selective repair

Implemented deterministic helpers:

- critical-token matching now uses normalized whole phrases instead of substring matching;
- decimal numbers and multi-word modality terms normalize consistently against ASR text;
- each segment QA result records machine-readable repair reasons;
- `selective_repair_plan()` produces a stable priority-ordered repair list with segment id, action, reasons, similarity, missing critical tokens, and timing ratio;
- existing `selective_repair_ids()` remains compatible.

Pipeline integration of segment-level re-ASR/repair remains with root because `pipeline.py` is outside this agent's ownership. The current pipeline global Re-ASR therefore does not yet make P12 acceptance pass by itself.

## Validation

- Focused: `python -m pytest -q tests/test_audio_qa.py tests/test_voice_audio_core.py` -> 12 passed.
- Full suite: `python -m pytest -q` -> 143 passed in 13.70 s.
- Sidechain FFmpeg smoke: synthetic video + background sine + dialogue sine through the generated ducking/loudnorm graph -> exit code 0.
- `measure_mix_metrics()` test uses the bundled FFmpeg on a real generated WAV and returns finite LUFS/true-peak values.

## Status

- P10 deterministic code core: complete within owned files; cache-version integration and long-memory benchmark remain.
- P11 deterministic mix/metric helpers: complete within owned files; real two-pass/loudness/listening acceptance remains.
- P12 deterministic segment QA/repair helpers: complete within owned files; segment-level pipeline execution and real bad-example/false-positive acceptance remain.
