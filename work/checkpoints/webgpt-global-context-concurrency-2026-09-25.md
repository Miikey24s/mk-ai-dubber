# WebGPT Global Context + Concurrency Checkpoint - 2026-09-25

## Scope

Implemented the next v2.5/v2.6 PLAN slice without changing the production translation model, effort, or route:

1. deterministic compact video-level translation context;
2. thread-safe WebGPT per-invocation receipt/output state;
3. bounded translation concurrency 1-3;
4. request latency telemetry;
5. fail-fast classification for model-capacity/rate-pressure errors;
6. a reusable live concurrency benchmark harness.

Production remains conservative after the first live benchmark:

```text
webgpt_concurrency: 1
global_context_enabled: false
```

The candidate path can be enabled explicitly for benchmark/A-B runs.

## Implementation

`build_global_translation_context()` builds one deterministic pack for the whole video from:

- audience/register profile from the terminology glossary;
- active approved terminology actually present in the source;
- speaker/duration/segment metadata;
- evenly distributed bounded source excerpts.

It does not add another LLM summary call, retained chat state, `previous_response_id`, tools, or MCP context.

`WebGptTranslator` now uses thread-local output paths for receipt ownership and a lock for shared counters/latency telemetry. Batch results are applied to mutable `Segment` objects and `translations_cache.json` only on the coordinator thread, so parallel workers do not mutate shared segment/cache state directly.

Receipt identity excludes the non-semantic concurrency setting while retaining model, effort, context/global-context policy and exact prompt/schema inputs.

## Live Benchmark

Run artifacts:

`work/benchmarks/webgpt-concurrency-20260924T221859Z`

Fixture: 6 trading/AI segments, batch size 2, `chatgpt-web/gpt-5.6-sol`, effort `high`, global context candidate enabled, dedicated instance 2 (`127.0.0.1:17842`).

| Concurrency | Wall time | Segments/min | Request p95 | Retry | Quality gate | Result |
|---:|---:|---:|---:|---:|---|---|
| 1 | 137.04 s | 2.63 | 50.08 s | 0 | pass | baseline |
| 2 | 122.33 s | 2.94 | 74.91 s | 0 | pass | ~1.12x vs c1 |
| 3 | n/a | n/a | n/a | outer layer retried once under old policy | not reached | failed: selected model at capacity |

Both successful runs passed deterministic terminology and critical-token gates. Concurrency 2 reduced total wall time by about 10.7%, but individual-turn p95 increased materially. Concurrency 3 is not a safe production target from this run.

The benchmark process exited before its original end-of-run JSON save when c3 failed. The successful c1/c2 translation caches and WebGPT receipts, plus c3 failure-attempt artifacts, remain in the run directory. The benchmark harness is now changed to save partial results after every completed/failed run so future failures do not lose summary state.

## Retry Policy Finding

The c3 failure exposed a policy bug: the outer VI Dubber retry loop treated `Selected model is at capacity` like a short transient and immediately retried it. Capacity/rate-pressure markers now fail fast at this layer and are counted separately as `webgpt_pressure_failures`; ordinary route/process/JSON transient failures retain the bounded retry behavior.

## Verification

- baseline before this slice: `uv run pytest -q` -> 364 passed, 2 existing warnings;
- baseline/final doctor before live benchmark: WebGPT instance 2, CUDA, VieNeu, WhisperX, separator, local Qwen and TypeSafe all healthy;
- focused translator/retry tests after implementation and pressure classification: 42 passed;
- live c1/c2 quality gates passed with zero retries;
- live c3 produced real model-capacity rejection.
- final full suite after the retry-policy/config edits: `uv run pytest -q` -> **369 passed, 2 existing warnings**;
- `tools/p04_quality_receipt.py` -> `passed: true` with retained artifact hashes, glossary, context, duration and critical-fact parity checks passing.

## Decision / Next Gate

Do not promote concurrency 2 or global context to production defaults from one short live run. The next WebGPT gate is a repeated warm/long-fixture A/B of c1 vs c2 with the same quality checks and boundary-consistency review. Concurrency 3 stays benchmark-only unless capacity behavior materially changes. Direct provider-only Responses remains a later transport A/B; it is not mixed into this slice.
