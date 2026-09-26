# P22 Dedicated Dubber-WebGPT — live correctness checkpoint (25/09/2026)

Status: **P22-A ACCEPTED / P22-B ACCEPTED / P22-C ACCEPTED / P22 COMPLETE**

## Runtime isolation

- Dedicated home: `D:\ANNAM\TradingWorkspace\.runtime\dubber-webgpt`.
- Dedicated listener: `127.0.0.1:17850`.
- Dedicated ChatGPT login capture completed and `storage-state.json` was created.
- `webgpt-runtime status`: login `OK`, runtime `ONLINE`.
- Live catalog advertises `chatgpt-web/gpt-5.6-sol` and `chatgpt-web/gpt-5.6-sol-instant`.
- Historical `:17842` remains retired; daily Codex/Cockpit `:17841` is not used as a fallback.

## Compatibility fixes required by current ChatGPT UI

The account was visibly logged in and showed the composer, but the core login verifier still failed. Live inspection proved the current login surface uses a `textarea` named `prompt-textarea` with `aria-label="Chat with ChatGPT"`; the older selector only recognized test-id/Lexical contenteditable variants.

The current model menu also changed its verification metadata. `GPT-5.6 Sol` remained the exact checked model row and High remained slider value `2` in range `0..3`, while `aria-describedby` now contained only effort text such as `High, 3 of 4.` and keyboard instructions. The verifier now treats absent model-family evidence in those descriptions as neutral while still rejecting explicit contradictory family/version evidence.

Core commit: `62ca664 fix: support current ChatGPT composer and model UI`.

Validation: `158 passed`, `0 failed`, TypeScript typecheck passed.

## Direct Responses contract

VI Dubber now gives each direct logical request a dedicated technical `thread_id` and `turn_id` in Responses `client_metadata` plus the current user message metadata. Retries reuse that identity; independent requests get different identities. The request remains provider-only/tool-free: `tools=[]`, `tool_choice=none`, `parallel_tool_calls=false`, `stream=false`, `store=false`.

Live direct smoke after runtime restart:

```text
{'ok': True, 'route': 'dedicated-17850'}
```

## P04 live re-acceptance

Verifier: `scripts/verify_p22_live.py`.
Receipt: `work/p22-live-acceptance/p22_p04_live_results.json`.

```text
status=passed
route=http://127.0.0.1:17850/v1
model=chatgpt-web/gpt-5.6-sol
effort=high
concurrency=1
global_context_enabled=false
wall_seconds=13.9638
terminology=true
critical=true
```

The fixture covers trading terminology plus named entities, numbers, percentages, negation/modality and directional ratios.

## P16 live re-acceptance

Focused browser/UI checks:

```text
3 passed, 29 deselected
```

The model catalog/status banner exposes the dedicated `:17850` runtime and survives page reload.

## P20 fault evidence

- Dedicated runtime restart was exercised repeatedly; direct smoke passed after restart.
- Missing dedicated login blocks start.
- Wrong base URL/model selection fail closed.
- Missing live model fails in catalog validation before prompt dispatch.
- Direct 429/capacity is classified as pressure and is not blindly retried.
- Malformed direct output obeys bounded retry.
- Direct 503/disconnect leaves `segment.vi` empty and does not commit `translations_cache.json`.
- Pipeline cancellation remains fail-closed without committing a partial stage.

Focused fault checks passed; full VI Dubber regression after transport migration: `385 passed, 2 warnings`.

## P22-C throughput acceptance

Receipt: `work/benchmarks/p22-direct-concurrency-20260925T034900Z/results.json`.

Repeated-warm benchmark used the same 6-segment representative fixture, batch size 2, GPT-5.6 Sol High, direct Responses and global context disabled:

| Mode | Repeat 1 | Repeat 2 | Median |
| --- | ---: | ---: | ---: |
| c1 | 40.2977s | 41.9804s | 41.1390s |
| c2 | 27.9629s | 33.8176s | 30.8903s |

c2 delivered `1.3318x` median wall-time speedup. Both c2 runs passed terminology and critical-token gates with `0` pressure failures, retries or request failures. Individual request p95 rose to about `22s`, but total wall time improved materially and repeatably, so production default is promoted to `webgpt_concurrency: 2`.

Global context remains disabled. Historical c3 remains benchmark-only because it previously hit model capacity.

## c2 atomicity hardening

Independent audit found one concurrency edge case: a successful sibling batch could be applied before another concurrent batch failed. The parallel path now stages and validates all sibling results first, then applies them only after the whole concurrent group succeeds.

Regression coverage now includes a mixed c2 success/failure case and verifies both `segment.vi` values remain empty and `translations_cache.json` is not committed. Focused fault tests pass, and the full VI Dubber suite is `387 passed, 2 warnings`.
