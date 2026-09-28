# P23 retained Job12 resume — post-canary 2026-09-28

Status: **RUNNING; provider path is progressing; final media acceptance pending.**

## Resume authorization and preflight

- Provider gate was opened by the post-fix canary in [P23-webgpt-canary-post-fix-2026-09-28](P23-webgpt-canary-post-fix-2026-09-28.md).
- The retained job was resumed through the supported command shape:

```text
uv run vi-dubber dub work/youtube/IXSu0MClr34.mp4 --output work/outputs/IXSu0MClr34_vi.mp4 --profile balanced_fast --no-diarize --resume
```

- The single-lease guard found one already-claimed retained worker, so no second worker was started. The claim is `work/job-8dc51f8a892aba21/run.lock`, PID `31688`, with parent PID `19844`, claimed at `2026-09-28T16:04:31.386887+00:00`.
- Preflight at the handoff: runtime `ONLINE`, login `OK`, core revision `3bde59b`, active provider turns `0`, no output MP4, no competing retained-job worker, and about `90.66 GiB` free on D:.

## Runtime evidence

- Job state advanced from terminal `failed/translation` to `running`, then through `preflight`, `input_extract`, `asr`, and back to `translation` without an error.
- The retained cache reached `832/4277` source segments (`0.3711246200607903`) before the first uncached provider work.
- New provider traces reached `turn-completed` repeatedly: `42e204134a1b`, `49ff5dfc90bf`, `c694cf2e6384`, `c9422b4a4c18`, `6f7d795ad990`, and `520809ecd843`. The two newest batches were still `response-visible` at the last snapshot; no `response-stalled-60s` occurred in this resume window.
- At the latest snapshot, runtime health remained `accepting_turns=true`, with `active_http_turns=2` and `active_browser_turns=2`; the worker and its parent were alive. The state file was still `running/translation` with `error=null`.

## Handoff and guardrails

Monitor `work/job-8dc51f8a892aba21/state.json`, the tail of `events.jsonl`, `run.lock`, and the output path. Do not kill the worker, use `--fresh`, delete existing receipts/cache/locks, create a duplicate worker, or switch model/provider. This checkpoint does not claim translation completion, TTS completion, output MP4, media QA, owner listening, or whole-pipeline acceptance.

## Live follow-up — 2026-09-28 16:11Z

- The single worker remains alive under `run.lock` PID `31688`; runtime health is still `accepting_turns=true` with two active HTTP/browser turns and no provider error.
- Translation receipts written by this PID now cover a contiguous unique ID range `0..1055` (`1,056` IDs, latest receipt sequence `0015`). The newest receipt files are `translate-1790611827581-31688-0014.json` and `translate-1790611861896-31688-0015.json`.
- `state.json` still reports the last journaled progress `832/4277` while these receipts are being flushed; treat the receipt range as the more recent progress evidence. No terminal state, output MP4, or final QA result exists yet.

## Live follow-up — final snapshot for this turn

- A fresh read of the retained WebGPT receipts shows a contiguous global range `0..1311` (`1,312` unique IDs, no gaps under the maximum). PID `31688` remains alive and the job remains `running/translation` with `error=null`.
- The worker is intentionally left running in the background. This is a progress receipt only; it does not claim the remaining translation, TTS, mux, output MP4, or QA gates.

## Live follow-up — terminal result 2026-09-29

The retained worker is now terminal again:

- `state.json`: `failed`, stage `translation`, updated `2026-09-28T23:47:06.590557+07:00`.
- Error: `Direct WebGPT Responses trả kết quả JSON không hợp lệ.`
- Runtime is idle and healthy after the failure: `ONLINE`, login `OK`, `active_http_turns=0`, `active_browser_turns=0`.
- There is no retained-job lock, no live Job12 process, and no output MP4.
- Translation receipts contain `3,808 / 4,277` unique IDs (**89.0%** by source-segment ID). IDs `0..3839` were reached except `3712..3743`; the remaining tail `3840..4276` was not attempted. The job's official persisted progress remains `0.3711246200607903` because the journal did not advance after the first cached replay checkpoint.
- This supersedes the earlier `RUNNING` snapshots in this checkpoint. Do not resume blindly or use `--fresh`; the next safe slice is a narrow malformed-JSON diagnosis/recovery for the missing translation batch, followed by a bounded validation before another retained-job resume.
