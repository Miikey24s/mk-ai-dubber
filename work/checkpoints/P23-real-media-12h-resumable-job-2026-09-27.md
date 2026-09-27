# P23 real-media 12h resumable job — 27/09/2026

Status: **PAUSED WITH VALID INPUT-EXTRACT CHECKPOINT / P23 STILL IN PROGRESS**.

## Retained source and job

- YouTube source: `https://www.youtube.com/watch?v=IXSu0MClr34`
- Source ID: `IXSu0MClr34`
- Local source: `work/youtube/IXSu0MClr34.mp4`
- Duration: `42831.561s` (~11h 53m 52s)
- Source size: `1,879,628,959` bytes
- Source SHA-256: `8dc51f8a892aba2103728a8d2360b30f9a6319d5015ff8fd00ef4bdbc4b08ddf`
- Job: `work/job-8dc51f8a892aba21`
- Output target: `work/outputs/IXSu0MClr34_vi.mp4`
- Profile: `balanced_fast`
- Diarization: disabled for this super-long P23 run
- Translation: Dedicated Dubber-WebGPT / `chatgpt-web/gpt-5.6-sol` / high effort

The source is already downloaded. Do not re-download it and do not use `--fresh` for this retained test unless the source or checkpoint is later proven invalid.

## Durable checkpoint

`work/job-8dc51f8a892aba21/manifests/extract_audio.json` is committed as `status=complete`.

- Artifact: `original.wav`
- Artifact size: `12,335,487,510` bytes
- Artifact SHA-256: `55fa85e73c1f11faf3a15d01fd2ec036a678b02aee527afa469db6a568401b09`

The separation stage was intentionally stopped before it committed a manifest, so separation must restart on the next resume. The completed input-extract stage must be reused.

Current persisted lifecycle after reconciliation:

- status: `paused`
- stage: `separation`
- displayed progress: `8%`
- `run.lock`: absent

## Windows lease bug found and fixed

The first live run exposed a Windows-specific lease bug: the old PID probe used `os.kill(pid, 0)`. On Windows this is destructive for an external live process, so merely listing jobs could terminate the runner and then misclassify it as stale.

Fix commit: `4bef30e` (`fix: make Windows job lease probing non-destructive`).

The Windows branch now uses a read-only Win32 process query. The regression test launches a real external Python process, calls `list_job_states()`, verifies the job stays `running`, and verifies the child process remains alive.

Validation:

```powershell
uv run pytest tests/test_jobs.py tests/test_fault_contracts.py -q
```

Result: `17 passed`.

`git diff --check` also passed; Git only reported the repository's existing LF-to-CRLF working-copy warning.

## Real resume smoke proof

The same retained job was resumed with `--resume` after the lease fix.

- `original.wav` kept its original size and modification time; it was not extracted again.
- The run metrics recorded `cache_hits=1` and advanced into BS-Roformer separation.
- `uv run vi-dubber jobs --limit 1` was executed while the runner was live and reported the job as `running` without terminating it.
- The smoke run was then interrupted intentionally and reconciled back to `paused`; no live `run.lock` remains.

The smoke-run `metrics.json` has `status=failed` because the separation process was interrupted deliberately. This does not invalidate the committed `extract_audio` manifest.

## Resume later

Preferred product flow: open the saved job in the app and use **Tiếp tục từ checkpoint**.

Equivalent CLI:

```powershell
uv run vi-dubber dub "work/youtube/IXSu0MClr34.mp4" --output "work/outputs/IXSu0MClr34_vi.mp4" --profile balanced_fast --no-diarize --resume
```

Expected behavior: verify/reuse the completed input-extract checkpoint, then restart separation. The source download and 12-hour audio extraction do not need to be repeated.

Observed BS-Roformer throughput on the RTX 2070 SUPER was about `1.2 it/s` over `8113` separator iterations, roughly `1h50m` for this stage. Separation currently has no in-stage checkpoint, so interruption during that stage restarts separation itself.

This checkpoint proves retained-source/resume behavior and the Windows lease fix. It does not close the remaining P23 whole-pipeline super-long performance, live-provider, GPU/resource, progressive-preview, or final acceptance gates.
