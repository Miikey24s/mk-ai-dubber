# P21 WebGPT fault + effort contract - 2026-09-23

Status: focused implementation and regression slice complete; P21 overall remains PARTIAL.

## Behavior

- `WebGptTranslator` accepts explicit model/effort overrides and `build_translator()` forwards them for WebGPT.
- Every explicit WebGPT effort is carried into the Codex invocation as a per-call `model_reasoning_effort` override, so VI Dubber does not silently inherit the machine-wide Codex effort.
- `webgpt_model_catalog()` preserves live `reasoning_efforts` / `default_reasoning_effort` as well as the older supported-effort shapes.
- `running()` checks that the selected model still exists in the live instance-2 catalog and rejects an unsupported explicit effort. It does not substitute another model.
- The exact WebGPT base URL remains locked to `http://127.0.0.1:17842/v1`; retry remains bounded by the existing retry budget.

## Ownership / decision

Decision: `BUILD` / project-specific control-plane hardening. No external implementation or dependency was added.

Changed files:

- `src/vi_dubber/translate.py`
- `tests/test_webgpt_retry.py`
- this checkpoint

No changes were made to `pipeline.py`, `web.py`, `config.yaml`, `PLAN.md`, or `README.md` in this slice.

## Validation

```text
uv run pytest -q tests/test_webgpt_retry.py tests/test_core.py tests/test_pipeline_foundation.py tests/test_web_review.py
83 passed in 7.58s
```

Live catalog probe after the patch reports:

```text
chatgpt-web/gpt-5.6-sol-instant -> efforts [low], default low
chatgpt-web/gpt-5.6-sol         -> efforts [medium, high], default high
default model                   -> chatgpt-web/gpt-5.6-sol
default effort                  -> high
```

P21 remains PARTIAL until representative normal-process translation, concurrency/cooldown evidence, quality A/B, and the remaining service-fault receipts are accepted.
