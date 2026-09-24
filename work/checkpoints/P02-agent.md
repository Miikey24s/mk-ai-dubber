# P02 word-level ASR handoff

Task: `P02-WORDS`

Decision: `BUILD`

Reason: preserving WhisperX alignment metadata is project-owned data plumbing;
no external dependency is needed beyond the existing WhisperX result schema.

## Implemented

- `src/vi_dubber/asr.py`
  - added pure `_segment_from_aligned()` conversion helper;
  - preserves word text, start/end, WhisperX `score` as confidence, and word speaker;
  - preserves segment `avg_logprob` and segment speaker;
  - derives the dominant segment speaker from word speakers when the segment speaker is absent;
  - derives missing segment bounds from available word timestamps, with safe zero/start fallback;
  - `transcribe_and_align()` now uses the helper instead of discarding word metadata.
- `tests/test_asr_words.py`
  - synthetic coverage for full aligned metadata;
  - diarized word speakers and dominant-speaker fallback;
  - missing word timestamps/scores;
  - unaligned words with segment-level bounds.

No model downloads or live ASR inference were used for these tests.

## Validation

Focused:

`uv run pytest -q tests/test_asr_words.py`

Result: `4 passed in 0.04s`

Full regression at this checkpoint:

`uv run pytest -q`

Result: `55 passed in 6.75s`

## Integration contract

Existing callers still receive `list[Segment]`; each returned segment now carries
`words` and `avg_logprob` when WhisperX supplies them. Downstream segmentation can
consume these fields without rerunning ASR. Legacy serialized segments remain
compatible because `Segment.from_dict()` already treats `words`/`avg_logprob` as
optional.

## Remaining risk

WhisperX/provider schema changes beyond the currently observed `word`, `start`,
`end`, `score`, `speaker`, and `avg_logprob` keys are not live-model tested here.
The helper intentionally tolerates missing alignment fields rather than inventing
per-word timestamps.
