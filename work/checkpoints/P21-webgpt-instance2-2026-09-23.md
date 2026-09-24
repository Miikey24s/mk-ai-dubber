# P21 checkpoint - Codex WebGPT instance 2

Date: 2026-09-23
Status: PARTIAL

## Decision

Active product translation path is Codex ChatGPT Web only, locked to managed instance 2:

- base URL: `http://127.0.0.1:17842/v1`
- default model: `chatgpt-web/gpt-5.6-sol`
- UI provider choices: Codex WebGPT only
- Aurora: dormant / not active
- instance 1 `:17841`: not a fallback
- local/hybrid: retained only for backward-compatible artifacts/tests, not exposed in current product UI

Global `~/.codex/config.toml` is not modified. `WebGptTranslator` injects the `codex_local_access` provider and exact instance-2 base URL into each `codex exec` invocation.

## Live runtime evidence

Final probe:

```text
status=ok
port=17842
accepting_turns=true
provider_base_url=http://127.0.0.1:17842/v1
models=chatgpt-web/gpt-5.6-sol-instant, chatgpt-web/gpt-5.6-sol
```

`uv run vi-dubber doctor` reports:

```text
Codex WebGPT: OK - chatgpt-web/gpt-5.6-sol · instance 2 · port 17842
Translator mặc định: webgpt
Config schema: OK
```

## Changed behavior

- WebGPT health/catalog probing now targets instance 2 directly.
- Wrong WebGPT base URL is rejected instead of silently using another instance.
- Model catalog comes from instance 2 and feeds the product UI.
- Web UI exposes only Codex WebGPT in the provider selector.
- Selected WebGPT model/catalog metadata flows through pipeline state/fingerprint instead of being replaced by the old `chatgpt-web/high` value.
- Every WebGPT `codex exec` carries a per-call provider/base-URL override to `17842`.
- README and PLAN v1.8 supersede the active Aurora direction.

## Validation

Focused regression after route/UI changes:

```text
uv run pytest -q tests/test_core.py tests/test_webgpt_retry.py tests/test_preflight.py tests/test_web_review.py
66 passed in 8.46s
```

Final full suite:

```text
uv run pytest -q
330 passed in 18.45s
```

Active config/UI scan found no `provider: aurora`, `aurora_base_url`, `17841`, or `chatgpt-web/high` references in the active files checked (`config.yaml`, `README.md`, `web.py`, `pipeline.py`, `cli.py`).

## Post-change SHA256

Pre-edit hashes were not captured at the start of this resumed slice; do not infer them. Post-change hashes:

```text
config.yaml                DDD18425508E6BCCFC3DB0B706D0FA52886F1A612B3774907B08BD08BF0C17BD
README.md                  383ECE607A9B8895B7E87424C1E5F6F9CF06D03EE4AD51001EA1077CE5F0D76D
PLAN.md                    75CB6D741DA43A8BD55614ED0D2849B8731E191CD7C8A994C163B094A0C7CD85
src/vi_dubber/translate.py CDBE28250F9B134F387DFEFC187F5B93C02CEE8CCB06BDBC15AA32A5C8A3E57D
src/vi_dubber/preflight.py 73DE906ED5DE3F3DAB8046EF00BEEAEB2E0AF228FBCD29C1B9203963E597EB7E
src/vi_dubber/cli.py       49070208208466FA18E943D135A2BDFBAFD8C01B659B46C2D6969819E109CD9B
src/vi_dubber/web.py       F238C5FDE7952BDABAC3C210099AE12A92C8957953D339863AACBADABAA0B7E6
src/vi_dubber/pipeline.py  DD91206FC95C43A2134E2374EEE073FED41F4D1BD1B73FBCA8114C280362F16D
```

## Remaining P21 gates

- Representative live translation through the normal VI Dubber process.
- Quality A/B against the prior WebGPT baseline.
- Concurrency/cooldown/retry benchmark on instance 2.
- Fault injection for restart, 429/disconnect, selected-model removal, catalog failure and wrong-port configuration.
- Re-accept P04/P16/P20 after those receipts.

One direct nested `codex exec` generation smoke was attempted from inside the current Codex development task, but the host blocked nested Codex process execution by policy before the request reached instance 2. This is not counted as a WebGPT failure and is why P21 remains PARTIAL rather than COMPLETE.
