# P23 auto-tune + live auth gate checkpoint

Date: 2026-09-26

## What is verified

- Existing dirty P23 WIP contains chunked TTS manifests, chunk QA, progressive MP4 preview publication, preview API endpoints, stale invalidation after edits, and the React preview rail.
- Focused long-form/TTS/preview/API tests: `69 passed`.
- Full Python suite before the harness hardening below: `436 passed, 2 warnings`.
- `python -m compileall -q src\\vi_dubber`: pass.
- Frontend `npm run build`: pass.
- `git diff --check`: no whitespace errors after removing two pre-existing trailing-space lines.

## Whole-job P23 evidence

Fixture: `work/benchmarks/p23-long-source-2x.mp4`

- duration: `1987.202s` (~33m 07s)
- SHA-256: `82dfc2d8af30d2214bbc2ad0e3689ff673382eddc44d163e0ef0e0c85d607b5d`
- RTX 2070 SUPER / CUDA path: available
- Dedicated Dubber-WebGPT route: `http://127.0.0.1:17850/v1`

First live baseline run receipt: `work/benchmarks/p23-autotune-20260925T213753Z/results.json`.

Observed before the external auth failure:

- BS-Roformer separation completed in `00:15:57`.
- Windowed ASR completed and produced `178` speech segments with diarization disabled, matching the current P23 safety boundary.
- ASR persisted long-form state and reported about `9.49x` media-seconds processed per ASR wall-second for this run.
- Translation then failed at total wall `1374.1s` because the dedicated ChatGPT Web session was expired / Temporary Chat was unavailable.
- TTS, preview and final whole-job gates were therefore not reached live and are not accepted from this run.

No candidate was promoted and `config.yaml` was not auto-modified.

## Harness bugs found and fixed

### Windows console encoding

The first auto-tune attempt failed before useful work with `UnicodeEncodeError` on Vietnamese console output. `scripts/benchmark_p23_autotune.py` now reconfigures stdout/stderr to UTF-8 so the benchmark does not depend on the Windows terminal code page.

### Cache-only P22 verifier false positive

`scripts/verify_p22_live.py` previously reused the persistent `work/p22-live-acceptance/p04-high` cache and could report `passed` with `webgpt_attempts=0` in ~0.03s.

It now:

- creates a unique run directory for every verification;
- requires at least one real WebGPT attempt;
- fails closed when the live request cannot authenticate.

The hardened verifier now reproduces the current expired-session error instead of returning a cache-only pass.

### Auto-tune baseline did not exercise multi-chunk preview

The ~33 minute fixture was paired with `max_seconds >= 2400`, so the planner correctly collapsed the baseline into one macro chunk while `_preview_gate` required at least two chunks.

Benchmark configs now bound `max_seconds` to `1.2 * target_seconds` for the tuning fixture. The default 25 minute target therefore uses `max_seconds=1800`, which forces the 1987 second fixture through the multi-chunk path without changing the product default config.

### Expensive auth failure happened after separation

The benchmark used to discover an expired WebGPT session only after ~16 minutes of separation plus ASR. It now sends one uncached direct WebGPT translation request before any heavy variant run. With the current expired session, the harness blocks immediately before separator work.

Focused validation after these harness changes: `4 passed`; both scripts compile successfully.

## Current blocker

`vi-dubber webgpt-runtime status` still reports process/catalog/storage-state as online, but a unique uncached direct Responses smoke request fails with:

```text
ChatGPT web login is expired or the Temporary Chat surface is unavailable
```

Restarting the dedicated runtime does not refresh that browser authentication.

## Next gate

Human action required: refresh the dedicated ChatGPT Web login with:

```powershell
uv run vi-dubber webgpt-runtime login
```

After the login completes, continue in this order:

1. `uv run vi-dubber webgpt-runtime start`
2. `uv run python scripts\\verify_p22_live.py` — must show a real WebGPT attempt
3. rerun `uv run python scripts\\benchmark_p23_autotune.py`
4. only if baseline + candidates clear quality/fault/preview gates, consider a manual tuning promotion
5. then close live chunked-TTS/progressive-preview/UI acceptance and proceed to long/super-long crash-resume/fault matrix

