# P13 full-P17 WhisperX batch A/B - 2026-09-25

## Scope

- Benchmark WhisperX `large-v3` CUDA FP16 batch `4` (production baseline) vs batch `6`.
- Cover all 10 required P17 categories with the same local machine and model.
- Measure end-to-end ASR + WhisperX alignment wall time, CUDA allocator peaks, transcript parity, raw segment timing parity and aligned-word parity.
- Do not change `config.yaml` unless the broader fixture set shows a clear win without quality/timing regression.

Machine-readable receipt: `work/benchmarks/p13-whisperx-full-p17.json`.

The P17 manifest still has an unresolved original video path for the historical long-monologue job. For this P13 ASR-only benchmark, the run explicitly supplied its retained `work/Trading_Strategies_That_Work__full_training_-7a8ee35de0/original.wav` as the `long-monologue` source. This is an explicit benchmark override, not a guessed video-source mapping.

## Result

| Batch | Fixtures | Audio | Wall | RTF | Peak allocated | Peak reserved | Errors |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 10 | 3764.949 s | 248.8939 s | 0.066108 | 752.07 MiB | 1760 MiB | 0 |
| 6 | 10 | 3764.949 s | 286.9923 s | 0.076227 | 769.98 MiB | 1840 MiB | 0 |

Batch 6 is about **15.3% slower overall** than batch 4 on the full fixture set. The long-monologue fixture dominates this reversal: batch 4 took `204.7318 s`, while batch 6 took `254.0469 s` (about 24.1% slower). This supersedes the earlier 23-second micro-benchmark where batch 6 was about 14% faster.

No CUDA OOM occurred at either batch size.

## Parity

- 9/10 fixtures: normalized transcript, raw segment timing and aligned-word signatures are identical within the 30 ms timing tolerance.
- Long monologue: transcript similarity is `0.999569`; only `5/146` raw segments differ in text, mainly small wording/contraction choices such as `want to` vs `wanna`.
- Long monologue: `0/146` raw segment timestamps differ by more than 30 ms.
- Aligned word counts are `10075` at batch 4 vs `10069` at batch 6, so the strict exact-parity gate intentionally fails on this fixture.

This is a very small language-output drift, not evidence of a major quality loss. It is still enough to reject an exact-parity claim, and there is no throughput benefit to compensate for it.

## Decision

**KEEP `asr.batch_size: 4`. Reject batch 6 as the production default on the current RTX 2070 SUPER / P17 corpus.**

- No production config change.
- Do not promote batch 6 from the earlier short-fixture result.
- Broad batch 8 testing is not required for this gate because the intended batch-6 candidate already loses on the representative long fixture; revisit only if the ASR runtime/model/hardware changes materially.
- P13 remains `PARTIAL` only because representative separator tuning/A-B evidence is still open.

## Verification

- `uv run pytest -q tests/test_benchmark_whisper_batches.py tests/test_p13_runtime_fallbacks.py tests/test_asr_words.py` -> `29 passed`.
- Benchmark command:

```powershell
uv run python tools/benchmark_whisper_batches.py `
  --batch 4 `
  --batch 6 `
  --category-source "long-monologue=work/Trading_Strategies_That_Work__full_training_-7a8ee35de0/original.wav"
```

- Benchmark completed all 10 selected fixtures for both batches with zero runtime errors and wrote the machine-readable receipt above.
