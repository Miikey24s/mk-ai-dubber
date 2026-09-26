# P23 short-overhead correctness reconcile — 2026-09-26

Status: **correctness RECONCILED / repeated short-overhead gate BLOCKED by performance**.

## Why the previous one-trial gate was misleading

The previous short A/B used `work/benchmarks/CP2-short-smart-source.mp4`. That retained short fixture is useful for routing/performance coverage, but it is not a clean segment-QA oracle: both arms independently finished with one final segment-QA flag. The P17 retained real clean-talking-head fixture (`work/benchmarks/p17-clean-talking-head/source-60s.mp4`, SHA-256 `4fce48270d1f328cfcabf2d0bcfc95bb6a1f89db82232ac2965dac25ee160c87`) has a frozen clean segment-QA receipt and is now the default short-overhead fixture.

The old harness also required `>=0.98` literal translated-text similarity between two independently generated WebGPT translations. That is not a valid correctness condition for a stochastic provider. Cross-arm translated-text similarity is now diagnostic only. Each arm must independently clear product QA and terminology QA, while source-script parity, resolved-axis parity, long-form enablement and short-path bypass remain hard correctness gates.

The same rule applies to the P23 auto-tune selector: a run is considered only when its own `status=passed`, which already requires its product quality and fault gates to pass. Source parity remains required; translated-text similarity is retained only as a diagnostic.

## Validation

Focused regression:

```text
uv run pytest -q tests/test_p23_short_overhead.py tests/test_p23_autotune.py
14 passed in 1.11s
```

One valid production-path A/B on the P17 60s fixture:

```text
uv run python scripts/benchmark_p23_short_overhead.py --transport direct-responses --repeats 1 --output-dir work/benchmarks/p23-short-overhead-p17-r3-live-20260926
```

- gate: `PASS`
- baseline quality: PASS, candidate quality: PASS
- source similarity: `1.0`
- translated similarity: `0.7436399217221135` (diagnostic only)
- candidate short path: no macro-chunk plan
- baseline wall: `367.75440580000577 s`
- candidate wall: `297.92673659999855 s`
- candidate/base: `0.8101241804347511`

Because correctness was valid, the required three-trial rotated-order run was executed:

```text
uv run python scripts/benchmark_p23_short_overhead.py --transport direct-responses --repeats 3 --output-dir work/benchmarks/p23-short-overhead-p17-r3-3trial-20260926
```

Final summary:

| Gate | Result |
|---|---:|
| correctness | PASS |
| baseline quality | PASS |
| candidate quality | PASS |
| source parity | `1.0` |
| translated similarity | `0.48620236530880423` diagnostic only |
| short-path bypass | PASS |
| baseline median | `281.9938744000101 s` |
| candidate median | `318.0543028999964 s` |
| candidate/base | `1.1278766376635343` |
| overhead threshold | `<= 1.05` |
| overhead | **FAIL (+12.7877%)** |

Per-trial wall times show material live-provider/runtime variance, but the promoted gate is the frozen three-trial median above. No speedup is claimed.

## Decision

The P23 short correctness blocker is resolved. The repeated short-overhead gate is **BLOCKED by performance**, not by fixture QA or the old literal-translation oracle. Do not promote P23 or claim a short-path speedup from this result.

This checkpoint does not close repeated whole-job 33-minute acceptance, real-media 6h+ resource/VRAM/live-provider/final-media acceptance, P14, or human listening gates.
