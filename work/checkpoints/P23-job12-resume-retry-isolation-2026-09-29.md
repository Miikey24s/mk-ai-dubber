# P23 retained Job12 resume after retry isolation — 2026-09-29

Status: **RUNNING; translation is progressing; final media acceptance is still pending.**

## Change and validation

- The narrow production fix is commit `11793e0` (`fix: isolate direct WebGPT retries`).
- Each direct Responses attempt now creates a fresh native thread, turn ID, and `prompt_cache_key`. This prevents a malformed response from being replayed into its own retry through the bridge's latest-thread state.
- Focused validation passed:
  - `uv run pytest -q tests/test_webgpt_retry.py` — 24 passed.
  - `uv run pytest -q tests/test_core.py -k "webgpt or translation_receipt or json"` — 14 passed, 16 deselected.
  - `git diff --check` — passed.
- A bounded live direct-Responses canary on `chatgpt-web/gpt-5.6-sol` with effort `high` returned two valid Vietnamese translations; `attempts=1`, `retry_attempts=0`, `failures=0`.

## Resume command and preflight

The retained job was resumed without `--fresh`, using one WebGPT worker to limit provider pressure:

```text
uv run vi-dubber dub work/youtube/IXSu0MClr34.mp4 --output work/outputs/IXSu0MClr34_vi.mp4 --config work/job-8dc51f8a892aba21/job12-resume-stable.yaml --profile balanced_fast --no-diarize --resume
```

At launch: runtime `ONLINE`, login `OK`, core revision `3bde59b`, no competing Job12 worker, no output MP4, no stale `run.lock`, and about `90.66 GiB` free on D:. The active lease is the single retained-job lease.

## Verified live checkpoint

At the latest snapshot (`2026-09-29 06:58:40 +07`):

- `state.json`: `running`, stage `translation`, callback `2592/4277`, whole-pipeline progress `0.4369651624970774` (**43.70%**).
- `run.lock` is present and the Job12 process tree is alive; the dedicated WebGPT runtime is healthy and accepting turns.
- `167` valid translation receipt files are present; the three old malformed diagnostics remain historical and no new malformed diagnostic has appeared during this resume.
- Unique receipt IDs remain `3,808/4,277` because the worker is currently re-translating earlier context-sensitive batches. The missing ranges `3712..3743` and `3840..4276` have not been reached in this snapshot.
- Some provider receipts contain an extra adjacent ID beyond the requested batch. The validator applies only IDs in the requested batch, so this has not changed the current segment assignment; it is an audit item for the post-translation receipt review.

## Guardrails and open gates

Keep the worker single-lease. Do not use `--fresh`, delete receipts/cache/locks, create a duplicate worker, switch provider/model, or claim final output before the actual MP4, TTS/mix/mux artifacts, deterministic QA receipts, and owner listening are present. This checkpoint does not claim translation completion, TTS completion, final MP4, media QA, or whole-pipeline acceptance.
