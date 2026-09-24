# P17 benchmark skeleton checkpoint

Status: foundation complete for Wave 0; full P17 quality suite remains future work.

## Scope completed

- Added `tools/benchmark.py`, an offline deterministic reader for existing `result.json`, `tts_stats.json`, and future P00 metrics artifacts.
- Added `work/benchmarks/fixtures.json` with two real baselines plus planned golden fixture categories from PLAN P17.
- Added `tests/test_benchmark_manifest.py` for summary math, missing-field behavior, required-artifact failure, and real manifest path/hash validation.
- No production source, config, PLAN, model, or network behavior was changed.

## Open-source gate

Decision: `BUILD`.

Rationale: this is control-plane glue over project-owned JSON formats. External benchmark frameworks would add dependency and schema coupling without improving the current baseline freeze. Re-evaluate only when P17 grows into audio perceptual scoring or a standard eval format becomes useful.

## Real fixtures frozen

### Short baseline

- Job: `work/PWKWNb550Cw-819968d7da`
- Source: `work/youtube/PWKWNb550Cw.mp4` (hash recorded in manifest)
- Duration: `226.081 s`
- Elapsed: `772.5222396 s`
- RTF: `3.4170153157`
- Segments: `53`
- Rewritten: `24` (`45.283%`)
- Overflow: `11` (`20.755%`)
- Tempo p50/p95/max: `1.09790 / 1.25 / 1.25`

### Long baseline

- Job: `work/Trading_Strategies_That_Work__full_training_-7a8ee35de0`
- Original source video path is not recorded by `result.json`; manifest deliberately marks it `unresolved` instead of guessing.
- Duration: `3238.672812 s`
- Elapsed: `5652.5999912 s`
- RTF: `1.7453445653`
- Segments: `734`
- Rewritten: `523` (`71.253%`)
- Overflow: `199` (`27.112%`)
- Tempo p50/p95/max: `1.10800 / 1.25 / 1.25`

## Commands / receipts

Focused test:

`uv run pytest -q tests/test_benchmark_manifest.py`

Manifest validation:

`uv run python tools/benchmark.py validate-manifest --manifest work/benchmarks/fixtures.json --root .`

Baseline summary example:

`uv run python tools/benchmark.py summarize --result work/PWKWNb550Cw-819968d7da/result.json --tts-stats work/PWKWNb550Cw-819968d7da/tts_stats.json`

## Known gaps / next P17 work

- P00 stage-timing metrics did not exist in these legacy jobs, so stage timing is intentionally empty; the reader will ingest it when future metrics artifacts exist.
- Remaining planned fixtures need curated real/golden clips before they can become release gates.
- Full before/after comparison report, critical-token/WER metrics, TypeSafe eval metrics, and human A/B sheet belong to later P17 waves.
