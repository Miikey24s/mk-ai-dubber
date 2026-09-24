# P07/P08 voice-engine checkpoint

Date: 2026-09-22

## Scope

- Owned implementation: `src/vi_dubber/reference.py`, `src/vi_dubber/tts.py`.
- Owned tests: `tests/test_tts_metrics.py` and P07/P08 coverage in `tests/test_voice_audio_core.py`.
- No changes to pipeline, config, PLAN, README, media, or QA modules.
- No new heavy benchmark was run; existing `work/checkpoints/P08-P13-runtime.md` remains the runtime performance source.

## Open-source reconnaissance

Decision: `ADAPT` for VieNeu's native v3 Turbo API; `BUILD` for project-specific reference scoring/fallback orchestration; other dubbing stacks are `REFERENCE-ONLY`.

- VieNeu-TTS `d350c63fceb0792d7b2db9a51d61cc040b1f8efa`, Apache-2.0. Read `src/vieneu/v3turbo.py`: `precision` selects the ONNX/CPU graph, while PyTorch takes `dtype`; GPU batching is exposed by `infer_batch` and one call shares one resolved voice/reference. This matches the local installed 3.7.1 behavior already measured in the runtime checkpoint, so vi-dubber adapts the public API instead of copying engine code.
- VideoLingo `11048cc4a78e2a36e657224566305ff7bb1a1ad9`, Apache-2.0. Read `core/_9_refer_audio.py` and `core/_10_gen_audio.py`: reference audio is extracted from task windows and TTS work is orchestrated around per-item generation/concurrency. Useful workflow reference, but it does not provide a stronger acoustic selector or VieNeu-specific CUDA contract to reuse.
- pyVideoTrans `16a5a047bf1bef9c68133ab500d76c11709617e8`, GPL-3.0. Read `videotrans/tts/_base.py`: queue-based TTS, retry/error routing, clone-role validation, and threaded item execution are useful resilience references, but the GPL license and broader architecture make direct reuse inappropriate here.

No upstream code was copied.

## P07 implementation state

`reference.py` already contained deterministic acoustic scoring using duration, speech/silence ratio, clipping, approximate SNR, ASR confidence, and overlap penalty. This task kept that scoring and closed the selector contract gap in `tts.py`:

- candidate window is now strictly 3.0-8.0 s;
- overlap candidates are excluded before ranking;
- the selected canonical clip stays at least 3.0 s and at most 8.0 s;
- one canonical reference per speaker remains stable for the synthesis run;
- fallback to deterministic duration-based selection remains only for unreadable/unusual audio inputs.

Test coverage now proves the integrated selector prefers a clean 4 s candidate over a longer clipped 5 s candidate and rejects 2.9 s / 8.1 s candidates.

## P08 implementation state

- `backend=pytorch` + CUDA now passes explicit `dtype=float16` unless the caller supplies another `dtype`. This avoids VieNeu resolving `dtype=auto` to BF16 while the P08 contract says FP16.
- Existing grouping by speaker/reference and `infer_batch()` path is retained. Batch failure first retries serial inference on the same engine, so a batch-only failure does not unnecessarily drop to CPU.
- GPU/PyTorch constructor failure or per-segment GPU inference failure now falls back to VieNeu v3 Turbo `backend=onnx`, `device=cpu`, `precision=fp32`.
- CPU fallback usage is observable through the `tts_cpu_fallbacks` metrics counter when a recorder is present.
- TTS raw writes now go to a sibling `*.partial.wav` and atomically replace the target after a successful save, reducing the chance that a partial file is mistaken for a reusable raw checkpoint.
- Existing CPU/ONNX behavior is unchanged unless it already fails; config defaults were intentionally not changed in this worker's ownership.

## Validation

Focused command:

`uv run pytest -q tests/test_tts_metrics.py tests/test_voice_audio_core.py`

Result after final patch: `12 passed in 0.80s`.

Added/extended coverage includes:

- acoustic reference selection and strict 3-8 s window;
- explicit CUDA/PyTorch `dtype=float16` constructor argument;
- GPU inference failure -> CPU/ONNX FP32 fallback;
- GPU engine init failure -> CPU/ONNX FP32 fallback;
- existing batch grouping, batch failure -> serial fallback, rewrite verifier, pronunciation text mapping, and TTS metrics remain passing.

Existing runtime evidence from `P08-P13-runtime.md` remains applicable:

- RTX 2070 SUPER, VieNeu 3.7.1 PyTorch CUDA explicit FP16: load/inference pass;
- measured batch 4: 1.2513 s wall for 10.56 s audio, RTF 0.1185, peak PyTorch allocator 728.83 MiB;
- batch 4 reduced wall time about 30.3% vs four sequential calls on the measured fixture;
- CPU ONNX FP32 path passed and remains the fallback.

## Acceptance / remaining gap

Functional and runtime/performance evidence for P08 is now sufficient to integrate the CUDA FP16 + batch-4 candidate path, with CPU fallback preserved. P07 deterministic selector behavior is covered by tests.

Quality acceptance is still **pending**. No blind A/B in this task proves speaker similarity, naturalness, pronunciation clarity, noise bleed, or parity between sequential and batched stochastic output. Therefore this checkpoint does not claim full CP4/P08 completion and does not justify changing the production default by itself.

Next quality gate: representative blind A/B of legacy vs smart reference plus sequential vs batch-4 CUDA FP16, then coordinator decides whether to change the resolved/default TTS profile.

## File hashes

Final SHA256:

- `src/vi_dubber/reference.py`: `6A4D07ADCB1DE8B084660664E9EB705A34B4A0B5C338861E6C904348E4B9526C` (unchanged in this task)
- `src/vi_dubber/tts.py`: `A4292E391BFA0C0C6B082CCEBCD1174EABB7351A2744F027603ED8F1F1E58B52`
- `tests/test_tts_metrics.py`: `05CAD07B86B3B18B288AE9B2A7986ECFCD4E994F523F5B85D59BFE3CFD136E52`
- `tests/test_voice_audio_core.py`: `01AEB371DC6961937DB40944F0EF718497C7A4F8DFA3AAAF06D216DCDCAD93A4`

The pre-change hash command in this task used PowerShell table formatting, which abbreviated changed-file hashes in captured stdout. Known pre-change prefixes were `C682A6358AA3FBC88C97D9DD755E8AD86CA7D9BC06...` (`tts.py`), `F409CCA3C8F37F92F70C463D8772AE0216D48E2173...` (`test_tts_metrics.py`), and `6406DB1DF8C8160E09A5C1304BE51B09E1E89FD41A...` (`test_voice_audio_core.py`); no exact suffix is invented here.
