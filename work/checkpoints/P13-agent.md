# P13 ASR/separator tuning agent checkpoint

Date: 2026-09-22

## Scope

- Ownership used: `src/vi_dubber/asr.py`, `src/vi_dubber/separation.py`, and a focused P13 runtime-fallback test.
- No edits to `pipeline.py`, `config.yaml`, `PLAN.md`, or `README.md`.
- Existing `work/checkpoints/P08-P13-runtime.md` and `work/benchmarks/p08-p13-runtime-20260922.json` were used as the tuning evidence; the performance benchmark was not rerun.

## Evidence decision

The existing real 23 s WhisperX fixture measured:

| batch | wall s | RTF | transcript vs batch 4 |
| ---: | ---: | ---: | --- |
| 4 | 1.9176 | 0.0834 | baseline |
| 6 | 1.6475 | 0.0716 | identical |
| 8 | 1.7040 | 0.0741 | identical |

Batch 6 is the fastest measured candidate on that fixture, but the benchmark explicitly leaves general transcript/alignment quality parity pending. Therefore production `asr.batch_size` remains `4`; no ASR model, compute type, separator model, or separator tuning default was changed.

The installed `audio-separator` API exposes model-family-specific batch knobs, but the P08-P13 runtime artifact contains no representative separator A/B quality/performance measurement. P13 therefore does not tune separator batch/model settings yet.

## Implemented safe runtime gaps

- WhisperX transcription now retries only CUDA out-of-memory failures with lower conservative batch sizes below the configured value. For measured higher candidates this gives `6 -> 4 -> 2 -> 1` or `8 -> 4 -> 2 -> 1`; non-OOM errors propagate immediately.
- Invalid non-positive ASR batch sizes fail before model transcription.
- `transcribe_and_align()` now releases loaded WhisperX/alignment/diarization resources in `finally`, including partial-failure paths.
- Missing diarization token fails before expensive WhisperX model/runtime initialization.
- QA `transcribe_text_files()` uses the same narrow CUDA-OOM fallback without changing its normal one-model-reuse behavior.
- Source separation now runs GPU cache cleanup from `finally`, so a separator/model failure does not skip cleanup.

The ASR fallback emits a warning when it lowers the effective batch. Persisting the effective fallback batch into stage manifests would require `pipeline.py` integration and is outside this agent's ownership.

## Validation

- `python -m py_compile src/vi_dubber/asr.py src/vi_dubber/separation.py tests/test_p13_runtime_fallbacks.py` -> pass.
- `python -m pytest -q tests/test_asr_words.py tests/test_p13_runtime_fallbacks.py` -> `26 passed`.
- `python -m pytest -q tests/test_fault_contracts.py tests/test_asr_words.py tests/test_p13_runtime_fallbacks.py` -> `30 passed in 0.20s`.
- Focused tests cover CUDA-OOM retry, non-OOM propagation, invalid batch rejection, WhisperX cleanup on alignment failure, diarization fail-fast, and separator cleanup on failure.

## P13 status

- Config-independent resilience/cleanup slice: complete within owned files.
- WhisperX default batch change: **not accepted**; keep batch 4 until broader real-fixture transcript/alignment quality evidence exists.
- Separator tuning/default change: **not accepted**; representative real-fixture A/B evidence is still missing.
