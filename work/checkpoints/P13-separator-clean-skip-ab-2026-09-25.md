# P13 separator clean-speech skip A/B - 2026-09-25

## Scope

- Production baseline: `audio-separator 0.47.0` with `model_bs_roformer_ep_317_sdr_12.9755.ckpt`.
- Candidate: oracle-labeled clean-speech skip on the frozen real P17 fixture `golden-clean-single-speaker-talking-head`.
- The candidate reuses the extracted original audio as the vocal track and synthesizes a silent background stem.
- This is an upper-bound skip experiment only. It does not implement an automatic clean-speech detector and does not change production configuration.

Machine-readable receipt: `work/benchmarks/p13-separator-clean-skip-20260925.json`.

## Why batch 2 was not benchmarked

PLAN P13 permits separator batch 2 only when the library/model supports it. The installed `audio-separator 0.47.0` accepts an MDXC batch parameter, but its RoFormer path explicitly states that `batch_size` is currently not utilized for RoFormer inference. The active model YAML has `inference.batch_size: 1`, so changing it to 2 would not be a meaningful A/B for this production BS-RoFormer model.

## Result

| Metric | BS-RoFormer baseline | Oracle clean skip |
| --- | ---: | ---: |
| Separator/prep wall | 33.7434 s | 0.8749 s |
| Stage speed gain | - | 97.41% |
| ASR + alignment wall | 31.4040 s | 120.9292 s |
| ASR segments | 7 | 11 |
| Aligned words | 209 | 206 |
| Baseline separator peak CUDA allocated | 1587.216 MiB | - |
| Baseline separator peak CUDA reserved | 2348 MiB | - |

The receipt's strict normalized-string similarity is `0.903882`, below the predeclared `0.995` quality gate. A punctuation-insensitive token audit was also run because the string metric visibly over-penalizes punctuation and numeric formatting. It still scores only `0.979118`, with three substantive token-format differences:

- `two thousand` -> `2000` (twice)
- `ten dollars` -> `10`

Because the aligned word sequence differs, the <=30 ms exact timing parity gate cannot pass.

The skip candidate also replaces a non-silent separated background (`RMS 0.0304815`, peak `0.3905741`) with digital silence. That is not background-stem parity, even on this clean talking-head fixture.

## Decision

**REJECT oracle clean-speech skip on the current P13 gate. Keep production BS-RoFormer unchanged.**

The candidate easily wins separator-stage speed but does not meet downstream transcript/alignment parity and does not preserve the separated background stem. Since even the oracle-labeled clean fixture fails, there is no reason in this P13 slice to implement a detector or test false positives on non-clean fixtures.

No new separator model, extra fixture, UI change, PLAN change, or production default change was made.

## Verification

- Real benchmark completed successfully on the frozen clean P17 source and wrote the JSON receipt.
- `uv run python -m py_compile tools/benchmark_separator_clean_skip.py` passes.
- `uv run pytest -q tests/test_p13_runtime_fallbacks.py` -> `6 passed in 0.09s`.

