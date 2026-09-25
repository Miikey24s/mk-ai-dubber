# P23 local fault/resume gate checkpoint

Date: 2026-09-26

## Live WebGPT status

- Dedicated Dubber-WebGPT `127.0.0.1:17850` reports runtime/catalog/login state online.
- A unique uncached direct Responses probe was run with `webgpt_concurrency=1`, `global_context_enabled=false`, `retry_budget=0` and a 90 second client timeout.
- After a runtime stop/start recovery cycle, the probe still failed after `72.863s` with one real WebGPT attempt:

```text
ChatGPT model controls are unavailable. Reload ChatGPT and retry the task.
code=server_is_overloaded
```

- Because this is an external browser/runtime control-surface failure, the 33 minute P23 whole-job auto-tune benchmark was not rerun. The live gate remains fail-closed and no tuning default was promoted.

## Local P23 fault/resume progress

Added a regression for the atomic chunk commit boundary:

- simulate a crash after a TTS chunk artifact is written but before its manifest is committed;
- verify resume ignores the orphaned artifact;
- commit the matching manifest;
- verify the same artifact becomes reusable only after that commit.

This complements existing local P23 coverage for:

- 6h+ macro-chunk timeline coverage without timestamp drift;
- changed/tampered chunk artifact fail-closed behavior;
- sibling chunk failure isolation;
- dependency-aware downstream invalidation;
- atomic preview publication and preservation of the previous preview on failure;
- TTS per-segment checkpointing before a later synthesis failure;
- API preview stale invalidation after a segment edit.

## Validation

Focused command:

```powershell
uv run pytest -q tests\test_longform.py tests\test_longform_state.py tests\test_media_preview.py tests\test_tts_metrics.py tests\test_api.py
```

Result: `45 passed, 2 warnings in 5.84s`.

`git diff --check -- tests\test_longform_state.py` also passed; the only output was Git's existing LF-to-CRLF working-copy warning.

## Remaining gate

P23 is still **IN PROGRESS**. Local crash/resume correctness has stronger evidence, but whole-job long-form performance, live chunked TTS/progressive preview, and final UI acceptance still require a working Dedicated Dubber-WebGPT browser session. No speedup or full P23 acceptance is claimed from this checkpoint.
