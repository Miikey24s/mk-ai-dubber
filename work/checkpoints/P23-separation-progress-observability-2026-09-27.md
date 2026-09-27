# P23 separation progress observability — 27/09/2026

Status: **PATCH READY IN ISOLATED WORKTREE / NOT YET APPLIED TO THE LIVE 12H RUN**.

## Trigger

The retained ~12h real-media job `job-8dc51f8a892aba21` is healthy and running
BS-Roformer separation, but persisted job progress stays at the stage boundary
(`8%`) while `audio-separator` exposes detailed internal tqdm progress such as
`542/8113`. This makes a long GPU stage look static in the dashboard even when
work is advancing.

## Change

- `separate_dialogue()` accepts an optional progress callback.
- VI Dubber adapts the current `audio-separator` MDXC/Roformer module-level
  tqdm loop without modifying the installed dependency.
- Separation progress is persisted across the existing `8% -> 19%` pipeline
  interval; ASR still starts at `20%`.
- Callback/reporting failures fail open to normal separation rather than
  failing the media job.
- The dependency tqdm symbol is restored after success or failure.
- Local separation ownership is serialized inside one process while the
  module-level adapter is installed, matching P23's conservative GPU-heavy
  concurrency=1 policy and preventing progress leakage between jobs.

## Validation

Focused + job/fault/pipeline/core regression using the repo's real portable
FFmpeg bundle and the isolated worktree source:

```text
88 passed in 9.98s
git diff --check: pass
```

The first worktree regression attempts failed only because ignored portable
FFmpeg binaries are not materialized in a Git worktree. Rerunning with the test
process pointed at the main repo's existing `tools` bundle passed the complete
selected suite.

## Integration boundary

The currently running 12h process imported the pre-patch code and is deliberately
left untouched. This patch should be integrated only for subsequent process
starts/resumes; it is not evidence that the already-running separator can gain
mid-process dashboard progress retroactively.
