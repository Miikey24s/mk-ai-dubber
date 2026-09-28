# P23 Dedicated WebGPT canary — post-fix 2026-09-28

Status: **PASS for provider completion and payload shape; real Job12 resume pending.**

## Scope

- One bounded synthetic canary was sent through the retained Dedicated Dubber-WebGPT route at `http://127.0.0.1:17850/v1` using the existing `chatgpt-web/gpt-5.6-sol` model and `high` effort.
- The request used no tools, no media, no Job12 input, and did not change the model/provider or global Codex configuration.
- Runtime source revision was `3bde59b375e450a25b0d9a365d778b2c8ade55df`.

## Evidence

| Check | Result |
|---|---|
| Dedicated runtime | **PASS** — `ONLINE`, login `OK`, port `17850`, source revision `3bde59b` |
| Browser completion | **PASS** — trace `a345a230e305`, diagnostic checkpoint `16-turn-completed` |
| HTTP/Responses result | **PASS** — HTTP `200`, status `completed`, response id recorded below |
| Production parser contract | **PASS** — `_extract_json(..., array=False)` returned `{translations:[{id:int,vi:string}]}` with one item and id `0` |
| Sanitized response receipt | `resp_87ff254b642345339286fa6d4e779595`; assistant text `3821` chars; SHA-256 `8a0d6ff5e43c0cf136795833ac15e44a7658f269b9b8de9ec12fcb8224f1c9b6` |
| Synthetic marker | The parsed value was `CANARY\\_OK` rather than the literal `CANARY_OK`, because the ChatGPT Web wrapper escaped the underscore. This does not invalidate the object shape; real translation text still needs validation during Job12 resume. |
| Job12 impact | **PASS / unchanged** — no cache, receipt, lock, output or state mutation |

Sanitized diagnostic evidence:

- `.runtime/dubber-webgpt/diagnostics/browser-turns/a345a230e305-5c1a9979/16-turn-completed.json`
- `.runtime/dubber-webgpt/responses-state.json` (used only to derive response id/thread id and the redacted hash/shape above; raw prompt/output is not copied here)

## Decision

The provider gate is open for the next single-lease action. Retained Job12 remains terminal failed at translation (`0.3711246200607903`, `28/28` ASR chunks, `864` cached unique translation ids, `49` receipt files, no output MP4). Resume only through the supported retained-job command after a fresh idle-health and lease check. Do not use `--fresh`, delete receipts or locks, create a duplicate worker, or silently switch model/provider.

This receipt does not claim whole-pipeline completion, final media quality, or the owner listening/QA gates.
