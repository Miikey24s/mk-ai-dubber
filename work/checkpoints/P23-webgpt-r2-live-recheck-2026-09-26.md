# P23 WebGPT R2 live recheck — 2026-09-26

Status: **provider blocker no longer reproduces in bounded live checks; P23 short A/B remains NOT ACCEPTED**.

## Scope

This recheck followed the v2.18 failure where Dedicated Dubber-WebGPT `:17850` returned malformed/non-contract output during production translation. The retained-diagnostic patch is commit `0d8d44f7eeebf871315d82afa8e2147d95d8f6a9`.

No Full harness, local tools, provider failover, account change, model switch, or global Codex/Cockpit config change was used.

## Direct Responses probe

Runtime health/catalog was live on `127.0.0.1:17850`; `chatgpt-web/gpt-5.6-sol` advertised `medium`/`high` reasoning.

One tool-free direct Responses request used:

- model `chatgpt-web/gpt-5.6-sol`;
- effort `high`;
- retry budget `0`;
- `tools=[]`, `tool_choice=none` through the existing `WebGptTranslator` direct-responses route.

Result: PASS. The requested JSON contract returned successfully and no malformed-output diagnostic was produced.

Evidence: `work/webgpt-r2-live-20260926T155155/probe-summary.json`.

## Production-path short A/B recheck

Command:

```text
uv run python scripts/benchmark_p23_short_overhead.py --transport direct-responses --repeats 1 --output-dir work/benchmarks/p23-short-overhead-r2-live-recheck-20260926
```

Both current-tree arms completed the production translation stage over 53 source segments without a malformed WebGPT response:

- baseline: `longform.enabled=false`;
- candidate: `longform.enabled=true`;
- all other resolved tuning axes matched;
- both produced output media;
- candidate stayed on the ordinary short path with no macro-chunk plan.

The prior `Local tools unavailable`/malformed-output failure therefore did not reproduce in either arm.

The benchmark still returned `gate=FAIL`:

| Check | Observed |
|---|---:|
| source similarity | `1.0` |
| translated similarity | `0.2653968253968254` |
| baseline wall | `612.492920699995 s` |
| candidate wall | `557.8834535999922 s` |
| candidate slowdown ratio | `0.910840655860003` |
| overhead gate | PASS |
| baseline QA | FAIL: `1/53` final segment flag remains after repair |
| candidate QA | FAIL: `1/53` final segment flag remains after repair |

Evidence: `work/benchmarks/p23-short-overhead-r2-live-recheck-20260926/results.json`.

## Decision

Do not run the three-trial short benchmark yet.

The old provider malformed-output blocker is no longer the current reason for stopping. The one-trial gate still fails independently because both outputs fail the existing segment-QA requirement. In addition, the harness requires `>=0.98` text similarity between two separately generated WebGPT translations; the observed difference is large enough that this oracle must be reconciled before using it as evidence for a `longform.enabled`-only performance comparison.

This checkpoint does not waive either requirement. The next step is to determine whether the fixture/QA failure represents a product-quality defect and whether cross-arm translated-text equality is a valid isolation oracle for a stochastic live provider. Only then should the repeated A/B be rerun.

P23 remains in progress. Real-media 6h+/VRAM/live-provider/final-media acceptance and the separate human listening/P14 gates remain open.
