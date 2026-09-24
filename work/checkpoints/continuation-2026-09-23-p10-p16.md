# Continuation checkpoint - P06/P10/P16/P17 - 2026-09-23

## P10 streaming acceptance

- `assemble_voice_track()` now renders bounded blocks instead of allocating the full timeline plus overlap-count arrays.
- Focused P10 regression: `tests/test_audio_qa.py tests/test_voice_audio_core.py` included in the 84-test focused run below.
- Real long-timeline benchmark used `3238.672812 s`, `48 kHz`, `30 s` blocks.
- Peak process RSS: `45.293 MiB`; baseline RSS: `28.605 MiB`; peak delta: `16.688 MiB`.
- The prior full-buffer shape would require about `889.528 MiB` for only the float32 mix buffer plus uint16 overlap-count buffer.
- Rendered output had `155,456,295` frames and duration `3238.6728125 s`; the temporary `310,912,634` byte WAV was deleted after validation.
- Durable numeric receipt: `work/benchmarks/p10-memory-streaming-20260923.json`.
- P10 acceptance is now considered complete.

## P06 pronunciation acceptance

- `tests/fixtures/pronunciation_golden.json` covers explicit acronym/name/technical overrides, ISO date, clock time, USD, signed percent and units.
- Ambiguous/invalid formats such as `01/02/03`, `$12,50`, `1.2.3`, invalid dates/times, mixed technical IDs and leading-zero numeric IDs remain verbatim.
- Display subtitle text stays separate from TTS-safe spoken text.
- P06 acceptance is now considered complete.

## P16 browser receipt

- Existing local web server `127.0.0.1:7877` was inspected through a real browser tab.
- Before reload, a completed `source-83s.mp4` job exposed 20 review rows, source English/Vietnamese, semantic/timing diagnostics, `Source segment` audio and `Dubbed segment` audio.
- Browser reload briefly showed the expected loading state, then restored the same persisted completed job automatically.
- After reload, the editor again exposed the same segment data plus both source and dubbed segment audio without manual re-selection.
- This closes the source-slice playback and refresh/reopen evidence gap. P16 remains PARTIAL because local + YouTube happy-path acceptance, failed/interrupted CTA behavior and broader empty/loading/disabled/visual-state coverage are not all closed yet.

## P17 current coverage

- Fixture manifest validation remains clean.
- Available P17 classes are now `5/10`: short fragmented speech, long monologue, clean talking head, fast English speech, names/numbers/technical terms.
- Do not relabel existing single-speaker artifacts as music/noisy/two-speaker/overlap/emotional merely to satisfy coverage.
- Remaining classes: music under dialogue, noisy speech, two speakers, overlapping speech, emotional/prosody stress.

## Validation

- Focused P06/P10/P17/P20 regression: `84 passed in 6.32s`.
- Full suite: `271 passed in 14.36s`.
- `uv run vi-dubber doctor`: pass; Python 3.12.10, FFmpeg 7.1.5, RTX 2070 SUPER CUDA, WhisperX, separator, VieNeu, torchcodec, Gradio, yt-dlp, WebGPT `chatgpt-web/high`, local Qwen and config/profile checks all healthy; TypeSafe key present in shadow mode.

## Next gate

Continue machine-verifiable acceptance in this order: P16 local/YouTube/failure-state UI receipts, safe P20 provider/runtime fault receipts, then add only genuinely representative missing P17 fixture classes. Human listening A/B and TypeSafe labeled calibration remain explicit non-automated acceptance gates.
