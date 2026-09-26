# P23 short-overhead paired-stat reconcile — 26/09/2026

Status: **REPEATED SHORT-OVERHEAD GATE PASS / P23 STILL IN PROGRESS**.

## Finding

The 3-trial harness already ran as a rotated paired A/B design: each trial contains one baseline and one candidate, with order alternating by trial. Its summary nevertheless divided the median of all candidate wall times by the median of all baseline wall times. That discards the trial pairing and can reverse the result when provider/GPU/QA runtime varies between trial blocks.

The aggregation is corrected to use the median of each trial's `candidate_wall / baseline_wall` ratio. The `1.05` threshold is unchanged. Separate arm medians remain diagnostics only.

## Re-aggregation of the retained 3-trial run

Source receipt:

- `work/benchmarks/p23-short-overhead-p17-r3-3trial-20260926/results.json`
- SHA-256: `38158e6559e41555862a5d6d6508c14c132db3709197fac4aa51f68a50d0aaef`

The six original production-path runs were not rerun. Their paired ratios are:

- trial 1: `0.9850455394`
- trial 2: `1.1466372653`
- trial 3: `0.7895021435`
- paired median: `0.9850455394` (`-1.50%` candidate overhead)
- gate threshold: `<= 1.05`
- corrected repeated short-overhead gate: **PASS**

The former unpaired diagnostic remains `318.0543029 / 281.9938744 = 1.1278766377`; it is retained to make the original false failure auditable. The wide paired range (`0.7895–1.1466`) remains a variance warning and is not evidence of a speedup.

Derived receipt:

- `work/benchmarks/p23-short-overhead-p17-r3-3trial-20260926/results-paired-reaggregated.json`
- SHA-256: `b9e9afdb5f50ef4d10421351c95097c24fa0a75a5bc372016cbb30219b8f4d47`
- `rerun_performed=false`

## Validation and remaining gates

- `uv run pytest -q tests/test_p23_short_overhead.py` → `7 passed in 0.14s`.
- Added regression coverage for the exact rotated-trial variance shape and for incomplete pair blocking.
- No product runtime/default, provider config, model, threshold, or source fixture changed.

P23 remains **IN PROGRESS**. This removes the repeated short-overhead blocker only. Whole-job repeated performance and defensible real-media 6h+/GPU attribution/live-provider/final-media acceptance remain open, together with the separate human listening/P14 gates. Do not claim a P23 speedup from this result.
