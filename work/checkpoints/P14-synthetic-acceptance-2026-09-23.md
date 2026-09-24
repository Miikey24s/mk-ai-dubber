# P14 synthetic acceptance checkpoint - 2026-09-23

## Result

- The deterministic synthetic P14 contract harness passes: **11/11 checks**.
- This closes the machine-verifiable contract slice for the retained `two-speakers` and `overlapping-speech` source-ready fixtures.
- **P14 remains PARTIAL overall.** No real-human diarization, conversational crosstalk quality, voice identity listening, or natural overlap rendering claim is made.
- No production source, default config, `PLAN.md`, or `work/benchmarks/fixtures.json` was changed.

## Evidence scope

The new ground-truth probe recreates the four original Windows SAPI utterances with the same voices, text, order, FFmpeg resampling, concat, delay, and mix operations used by the P17 fixture generator. Both reconstructed WAVs are byte-identical to the retained fixture WAVs:

| Fixture | Retained/reconstructed WAV SHA-256 | Ground truth |
|---|---|---|
| `two-speakers` | `8dcb2fd76e82d18c0d62ca3d676161526520f54eb3d64ee8db5886782e77440a` | 4 alternating turns, `SPEAKER_00/01/00/01`, designed overlap `0.0 s` |
| `overlapping-speech` | `b8f86150fc77b0a999a892def9a02f33166377346068da73e3d5e620fbad4fec` | speaker 2 starts at `1.2 s`; exact overlap span `1.2-6.473424 s`, rounded designed overlap `5.273 s` |

The MP4 and verification hashes also match the unchanged P17 manifest:

- `two-speakers` MP4: `245fef386fd06143aa78266a24c8de34fcbbe9ef29b9aebe476e207f4415699d`
- `two-speakers` verification: `dec029f004912e955d836bdd581e250553f4abe7dd326050fd3ec64189fb748b`
- `overlapping-speech` MP4: `818d0c0945f5909845799e21f8d518963b2e128ec6a44e7b6af2d488274d4232`
- `overlapping-speech` verification: `6880eb9d05d49c999b2f89986e3d927ab652f084868c93c0099b33a0cb013983`

## P14 acceptance mapping

| P14 criterion | Machine-verifiable result | Boundary |
|---|---|---|
| At least 2 speakers + overlap sample | PASS on the two retained synthetic fixtures and byte-identical ground truth | Synthetic source coverage only |
| Speaker identity stable | PASS for validated `num_speakers=2`, four protected speaker turns, and stable `00/01/00/01` sequence | Does not prove pyannote accuracy on real voices |
| Never silently merge speakers | PASS: production segmentation preserves all four speaker boundaries | Depends on upstream speaker labels being correct |
| Canonical ref per speaker | PASS: production selection emitted one isolated reference per speaker from matching ground-truth turns | Does not replace listening for cross-speaker leakage or voice similarity |
| No cross-reference contamination | PASS at timeline/selection-contract level: selected reference windows stay within a single matching ground-truth speaker turn | Acoustic/human contamination judgment remains open |
| Overlap failure visible | PASS: exact distinct-speaker overlap is marked at segment/word level; visibility reports `multi_speaker=true`, `overlap=true`, `needs_review=true` | UI/human action quality is not claimed |
| Preserve overlap downstream | PASS: production equal-power assembly retained timeline and measured overlap sample `0.565674` vs expected `0.565685` | Synthetic constant-tone assembly probe, not naturalness |
| Fail closed | PASS: missing HF token fails before runtime/model initialization; conflicting speaker hints fail before inference | Live model quality was not exercised |

## Runtime probe

- `whisperx` importable: yes.
- `pyannote.audio` importable: yes.
- `HUGGINGFACE_TOKEN` present: no.
- Project `models/pyannote` cached files: `0`.
- Live diarization was intentionally not invoked. The production path requires an accepted pyannote model agreement and an authorized Hugging Face read token; faking or silently bypassing that gate would not produce valid evidence.

## Added artifacts

- `tools/p14_fixture_ground_truth.ps1`
  - SHA-256: `cb7646ebf85ed5015128c5bc0941b0aadb4cb34faea7a52c7b676cbc4db4c862`
- `tools/p14_synthetic_acceptance.py`
  - SHA-256: `db4e6d214d0f85ae8c66e98bffc3c254fdac01cca59c14a2f4d313de29681e04`
- `tests/test_p14_synthetic_acceptance.py`
  - SHA-256: `86bbb57b8ec910890223af2e8370b4ff823a60e04cde8b48443e961e6f88d169`
- `work/benchmarks/p14-synthetic-acceptance/fixture-ground-truth.json`
  - SHA-256: `25e850c67bdc41005c361c18179760e17c369a2f96c811c46f125f197dc81b06`
- `work/benchmarks/p14-synthetic-acceptance/acceptance-report.json`
  - SHA-256: `faffdcf59634776d81a217f5530c6d0991637cc4f08aa8bebd99d110305ba38a`

The acceptance report also records hashes for every production contract file it exercised, so the receipt is tied to the exact implementation under test.

## Validation

- `powershell -File tools/p14_fixture_ground_truth.ps1` -> pass; byte-identical reconstruction for both WAV fixtures.
- Ground-truth generator run twice -> identical receipt hash `25e850c...b06`.
- `uv run python tools/p14_synthetic_acceptance.py` -> exit `0`, `passed=true`, 11/11 checks.
- Acceptance harness run twice -> identical report hash `faffdcf...38a`.
- `uv run pytest -q tests/test_p14_synthetic_acceptance.py tests/test_benchmark_manifest.py tests/test_asr_words.py tests/test_segmentation.py tests/test_voice_audio_core.py tests/test_audio_qa.py tests/test_review.py tests/test_profiles.py tests/test_p13_runtime_fallbacks.py` -> **106 passed in 6.27 s**.

## Remaining real-human/listening gates

1. Run pyannote/WhisperX diarization on a consented real two-speaker interview after the model terms are accepted and an authorized HF read token is available.
2. Run a real conversational crosstalk fixture, not synthetic delayed speech.
3. Listen for speaker identity continuity and cross-reference contamination across multiple turns.
4. Listen to overlap rendering for intelligibility and naturalness, and verify the review action is usable when both voices cannot be rendered naturally.
5. Keep P14 `PARTIAL` until those gates pass; the synthetic report must not be promoted to real-human quality evidence.
