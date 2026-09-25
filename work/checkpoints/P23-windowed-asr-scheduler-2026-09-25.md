# P23 windowed ASR + bounded scheduler checkpoint

Date: 2026-09-25

## Implemented

- FFmpeg `silencedetect` streams over the vocals stem to derive speech/silence ranges without loading the full waveform into Python memory.
- Long non-diarized inputs use the P23 macro-chunk planner and context windows.
- WhisperX and align models stay loaded across missing chunks; chunk timestamps are shifted back to the global timeline.
- Context overlap uses midpoint ownership so the same aligned segment is not emitted by two neighboring chunks.
- Every completed ASR chunk commits its own verified manifest and `segments_source.json`, so a later crash can resume only missing/invalid chunks.
- `BoundedExecutor` / `ResourceScheduler` provide hard queue backpressure instead of unbounded executor queues.
- ASR prefetch overlaps at most one FFmpeg audio-window extraction with GPU ASR work; GPU ASR concurrency remains 1.
- Persisted job state records macro-chunk count, current chunk, throughput and rolling ETA.

## Deliberate safety boundary

Windowed ASR is disabled when diarization is active. Running pyannote independently per chunk can renumber speakers and break cross-chunk identity. Multi-speaker stays on the full-file ASR path until speaker reconciliation is implemented and accepted.

## Validation

- Focused P23/ASR/scheduler/pipeline tests: `70 passed`.
- Full suite: `425 passed, 2 warnings`.
- `python -m compileall -q src\\vi_dubber`: pass.
- `git diff --check` on changed P23 files: pass.

## Next incomplete gate

Chunked TTS with a persistent engine runtime, per-chunk TTS manifests and progressive preview publication. Translation remains global/bounded WebGPT concurrency to preserve whole-video context and terminology consistency.
