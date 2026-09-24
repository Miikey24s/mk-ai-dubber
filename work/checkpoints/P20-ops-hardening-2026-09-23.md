# P20 OPS hardening receipt - 2026-09-23

Task: `P20-OPS`

## Result

- Fresh-install/setup smoke: PASS in a disposable `%TEMP%` copy.
- Process-termination matrix: PASS for `asr`, `translation`, `tts`, and `mix_mux` checkpoint boundaries.
- Production source changes: none.
- Main project `.venv`: unchanged by the fresh-install smoke (verified by `pyvenv.cfg` SHA-256 before/after).

## Fresh-install smoke

Reusable harness: `tools/p20_fresh_install_smoke.ps1`.

The harness copies only the install/config/source surface to a new temp directory, prepends a temp-only `winget.cmd` shim, sets `UV_OFFLINE=1`, and then runs the real `setup.ps1`. The shim feeds the already-cached FFmpeg portable archive from `tools/downloads`, so `Ensure-FFmpeg`, archive extraction, `uv sync --dev`, compileall, doctor, and the CLI entrypoint are exercised without Internet access or system configuration changes. The sandbox is removed after the run.

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\p20_fresh_install_smoke.ps1
```

Receipt:

- new temp `.venv` created with Python `3.12.10`;
- `163` packages installed from cache;
- FFmpeg doctor check: `n7.1.5-12-g1fdbca85aa-20260731`;
- CUDA doctor check: RTX 2070 SUPER, torch `2.8.0+cu128`;
- `whisperx`, `audio_separator`, `vieneu`, `torchcodec`, `gradio`, `yt_dlp`: OK;
- Codex WebGPT route: OK for `chatgpt-web/high`;
- config schema/profile/retry budget: OK;
- CLI `vi-dubber --help`: PASS;
- generated temp sandbox was removed after completion.

Fresh-install limitation found: `setup.ps1` does not provision the local Qwen GGUF, so the clean doctor reported `LLM local: CHƯA CÓ MODEL`. The default WebGPT path is usable, but a truly fresh `local`/`hybrid` fallback installation still needs an explicit model-provisioning step. This task did not change setup/runtime/README ownership to address that policy/product decision.

## Process-kill matrix

`tests/test_fault_contracts.py::test_terminated_process_preserves_committed_checkpoint_and_reclaims_stale_lease` is now parameterized across:

| Interrupted stage | Last committed checkpoint | Uncommitted artifact boundary |
|---|---|---|
| `asr` | `separation` | `segments_raw.partial.json` |
| `translation` | `asr` | `segments_translated.partial.json` |
| `tts` | `translation` | `tts/00001_raw.partial.wav` |
| `mix_mux` | `timing_assembly` | `dubbed.partial.mp4` |

For every row a real child Python process:

1. persists `running` state with the target stage/progress;
2. acquires the real job lease;
3. writes an uncommitted partial artifact;
4. is terminated by the parent process;
5. leaves the prior committed manifest valid and no complete manifest for the interrupted stage;
6. is reconciled to `paused` with the interrupted stage/progress retained;
7. leaves a stale lease that the next `claim_job` reclaims successfully.

This is deterministic contract-level fault injection. It deliberately does not kill live WhisperX/WebGPT/FFmpeg/GPU work, so it proves the shared checkpoint/lease/commit contract without destructive network/GPU/system fault injection.

## Validation

Focused kill/preflight slice:

```powershell
uv run pytest -q tests/test_fault_contracts.py::test_terminated_process_preserves_committed_checkpoint_and_reclaims_stale_lease tests/test_fault_contracts.py::test_terminated_process_during_atomic_write_preserves_previous_committed_json tests/test_preflight.py
```

Result: `11 passed in 4.54s`.

Full owned fault/preflight files:

```powershell
uv run pytest -q tests/test_fault_contracts.py tests/test_preflight.py
```

Result: `16 passed in 4.85s`.

Workspace regression + doctor:

```powershell
uv run pytest -q
uv run vi-dubber doctor
```

Result: `291 passed in 16.03s`; doctor healthy on the existing workspace, including FFmpeg, CUDA, WebGPT, local Qwen, TypeSafe shadow key, config schema and profiles. The suite count is higher than the supplied `281` baseline because other concurrent workspace changes added tests; none were reverted here.

## Remaining P20 gaps

- External provider outages remain deterministic mocked/local-transport tests; real Internet/WebGPT/TypeSafe services were not intentionally disrupted.
- The kill matrix validates the shared process/checkpoint contract for each major stage boundary, not an in-flight kill inside the real ASR model, WebGPT call, or FFmpeg mux executable.
- Fresh setup does not provision the local Qwen model, so `local`/`hybrid` cannot be called fully fresh-install ready until model provisioning policy is explicit.
- Broader P16 browser/loading/disabled/YouTube end-to-end acceptance remains outside this task.

