# P23 Dedicated WebGPT canary — 2026-09-28

Status: **FAILED / provider gate remains closed**.

## Scope

- One bounded synthetic canary prompt was sent through the retained Dedicated Dubber-WebGPT route at `http://127.0.0.1:17850/v1` using `chatgpt-web/gpt-5.6-sol`, with no tools, no media, no Job12 input, and no model/provider change.
- Three browser-turn traces were observed during the bounded check: `74ed27e7ae97`, `482d8a6f8c8f`, and `e3bda6bd84a1`.
- Each turn reached browser submission acceptance and rendered an assistant block, but each stopped at `response-stalled-60s`; none emitted `response.completed`, a schema-valid `output_text` object, or a persisted Responses receipt.

## Evidence

| Check | Result |
|---|---|
| Dedicated runtime | `ONLINE`, browser-only, port `17850`, accepting turns after cleanup |
| Login/catalog | Login marker `OK`; catalog returned the two advertised models |
| Browser submission | Accepted (`generation_running` or `user_turn`) in all three traces |
| Assistant completion | **FAIL**: visible assistant block only; no stable completion event |
| Payload contract | **FAIL**: no parsed `{translations:[...]}` object and no `responses-state` update |
| Cleanup | `service cancel-turns` cancelled `3` browser turns; final health was `active_http_turns=0`, `active_browser_turns=0` |
| Job12 impact | No Job12 process, lease, cache, receipt or output was changed |

Evidence paths (sanitized diagnostic captures; they contain no raw prompt, cookie or token):

- `.runtime/dubber-webgpt/diagnostics/browser-turns/74ed27e7ae97-379b87ec/16-response-stalled-60s.json`
- `.runtime/dubber-webgpt/diagnostics/browser-turns/482d8a6f8c8f-fb57e502/16-response-stalled-60s.json`
- `.runtime/dubber-webgpt/diagnostics/browser-turns/e3bda6bd84a1-ebc53677/16-response-stalled-60s.json`

## Decision

Do not close the provider gate and do not resume retained Job12. Keep the job at terminal translation failure (`37.11246200607903%`, `28/28` ASR chunks, `864` cached translation IDs, no MP4). Do not use `--fresh`, delete receipts/locks, create a duplicate worker, or silently switch model/provider. The next action is to diagnose or repair completion detection/provider surface, then rerun one bounded canary only after that change is validated.
