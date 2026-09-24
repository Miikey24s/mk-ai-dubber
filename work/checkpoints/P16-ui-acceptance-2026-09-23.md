# P16 UI acceptance receipt - 2026-09-23

Task: `P16-UI-ACCEPT`

Status: **PARTIAL**. Local UI happy path, lifecycle controls, failure/retry states, persisted paused/cancelled rendering, source-mode interaction, and desktop/narrow layout are verified. A live YouTube URL was not stored in the project fixtures, so YouTube end-to-end through download -> pipeline -> final output remains open. The YouTube controller branch has automated coverage only.

## Behavior changed

- Job controls are state-aware instead of always interactive:
  - `running`: Pause + Cancel enabled.
  - `paused`: `Tiếp tục từ checkpoint` enabled.
  - `failed`: `Thử lại từ checkpoint` enabled.
  - `cancelled`: Pause/Cancel/Resume disabled; status explains cache vs fresh restart.
  - `completed`: run controls disabled; downstream rerender becomes enabled only when review data reports invalidated downstream artifacts.
- While a pause/cancel request is pending, lifecycle controls are disabled so the user cannot stack conflicting requests.
- A failed/paused/cancelled result emitted by `run_web_job()` now surfaces the persisted job state and actionable guidance instead of replacing it with a generic failure banner.
- Initial load and persisted-job selection re-evaluate lifecycle controls from durable job state.

Main data/state flow: `run_web_job()` / persisted state -> `reconcile_job_state()` -> `_job_action_policy()` -> Gradio component updates. Review invalidation is read only to decide whether `Render downstream` is enabled.

Trade-off: control availability is derived from persisted status, while `run_persisted_job()` remains the final validator for whether an old source path can actually be recovered. This keeps job selection cheap and preserves the existing resume validation boundary.

## Automated evidence

Focused:

```text
uv run pytest -q tests/test_web_review.py tests/test_jobs.py
29 passed in 5.91s
```

Full suite:

```text
uv run pytest -q
291 passed in 14.97s
```

Doctor:

```text
uv run vi-dubber doctor
Python 3.12.10; FFmpeg OK; CUDA RTX 2070 SUPER OK; whisperx/audio_separator/vieneu/torchcodec/gradio/yt_dlp OK;
Codex WebGPT OK; local Qwen3-14B-Q4_K_M OK; config schema/profiles OK.
```

New focused coverage includes lifecycle CTA/disabled policy, pending/loading controls, stale completed rerender, local controller happy path, and YouTube controller happy path with a fake downloader/pipeline. Existing `tests/test_jobs.py` continues to verify fail-closed pause/cancel checkpoint handling and stale-running recovery.

## Real local browser happy path

Isolated source: `work/p16-ui-accept/source-6s.mp4` (6.04 s, SHA-256 prefix `61920e6d`; unique hash, so it did not reuse the canonical completed job).

Browser flow on isolated server `127.0.0.1:7881`:

1. Upload local file.
2. Select `LLM local only`, `Fast`, diarization `Tắt`.
3. Start from UI.
4. Browser showed `ĐANG CHẠY`, 14% at audio separation, with Pause + Cancel enabled.
5. Durable state advanced through ASR -> local Qwen translation -> reference selection -> TTS.
6. Terminal state became `completed`, `complete`, 100%.
7. Browser then showed `XONG`, preview `0:00 / 0:06`, final MP4 and SRT download actions, and lifecycle controls disabled again.

Artifacts:

- `work/job-61920e6df888b4ca/state.json`
- `work/outputs/source-6s_vi.mp4`
- `work/outputs/source-6s_vi.vi.srt`

Observed UI telemetry: source duration `0:06.0`, processing time `4:19.5`, one speech segment, one speaker. Fast profile skipped final QA for this tiny acceptance clip; final output generation itself completed successfully.

## Failure / retry / cancel / pause UI evidence

Real failure fixture: `work/p16-ui-accept/no-audio.mp4` is a valid MP4 with no audio stream. Running it from the UI created `work/job-ae52ebe8061574cf` and failed at `input_extract` (4%) with the FFmpeg no-stream error. Browser evidence after failure:

- banner `Job dừng do lỗi` / `LỖI`;
- explicit guidance to retry from checkpoint or select the source again and disable Resume for a fresh run;
- `Thử lại từ checkpoint` enabled;
- Pause + Cancel disabled.

Persisted state fixtures created only for UI rendering verified:

- `cancelled`: banner `Job đã hủy` / `ĐÃ HỦY`, guidance for cache-vs-fresh restart, Pause/Cancel disabled, resume control disabled and relabeled `Job đã hủy`;
- `paused`: banner `Job đang tạm dừng` / `TẠM DỪNG`, `Tiếp tục từ checkpoint` enabled, Pause/Cancel disabled.

These synthetic persisted states were moved out of the top-level `work/job-*` scan after verification and retained under `work/p16-ui-state-fixtures/` for reproducibility. They do not replace the existing backend control tests; no claim is made here that a browser-driven cancel action itself was completed.

## Desktop / narrow / interaction evidence

Desktop viewport: `1280 x 720`, document `scrollWidth=1265`, so no horizontal overflow. Two-column control/studio layout rendered without overlap; restored persisted completed job showed preview, 100% stepper, KPIs, review table, and source/dubbed segment audio.

Narrow viewport: `390 x 844`, document `scrollWidth=375`, so no horizontal overflow. Layout collapsed to one column with source/provider/profile controls fitting cleanly. Switching `Tệp trên máy` -> `YouTube` removed the upload control and exposed the URL input in the browser accessibility tree.

## Remaining P16 gaps

- Live YouTube end-to-end acceptance is still open. The repository contains the benchmark ID/file `PWKWNb550Cw` but no stored safe URL fixture; no URL was invented for this acceptance run. Automated coverage verifies only the YouTube web-controller branch with a fake downloader.
- Browser-driven cancellation itself was not completed in this run. Cancelled rendering/disabled controls are browser-verified from persisted fixture state, while cancel checkpoint semantics remain covered by `tests/test_jobs.py`.
- P16 should therefore remain **PARTIAL**, not COMPLETE.

Files changed by this task:

- `src/vi_dubber/web.py`
- `tests/test_web_review.py`
- `work/checkpoints/P16-ui-acceptance-2026-09-23.md`

`tests/test_jobs.py` was read/tested but not modified. `pipeline.py`, `PLAN.md`, `config.yaml`, `semantic_qa.py`, and `work/benchmarks/fixtures.json` were kept read-only.

## Bounded UI/runtime acceptance refresh - 2026-09-23 19:28 +07:00

This follow-up was evidence-only. The app was launched with `start-web.ps1 --port 7882`; no production source, config, PLAN, README, or tests were edited.

Fresh browser/runtime evidence on `http://127.0.0.1:7882`:

- **Completed local persisted job restore verified:** selecting `COMPLETED · source-83s.mp4 · complete · 100%` rendered `XONG`, 100%, final preview `0:00 / 1:23`, MP4/SRT download actions, 20 review rows, and disabled Pause/Cancel/Resume lifecycle controls. This is a persisted completed-job restore, not a new local upload/run in this follow-up.
- **Source-slice playback gap is now closed in current runtime:** the selected completed job exposed both `Source segment` and `Dubbed segment` audio controls for segment `#0000`, each with playable/downloadable ~3 s audio. The older note that source-slice playback was missing is therefore superseded for current runtime behavior.
- **Real failed state verified:** selecting `FAILED · no-audio.mp4 · input_extract · 4%` rendered `Job dừng do lỗi` / `LỖI`, the real FFmpeg no-stream error, and enabled `Thử lại từ checkpoint` while Pause/Cancel remained disabled.
- **Real cancelled persisted state verified:** `work/job-004e3f0997f86384/state.json` is `status=cancelled`, `stage=reference_selection`, `progress=0.52`, input `cancel-8s.mp4`, with `PipelineCancelled: Job đã được yêu cầu hủy`. Selecting it in the browser rendered `Job đã hủy` / `ĐÃ HỦY`; resume was disabled and relabeled `Job đã hủy`. This proves a durable real cancelled job and its browser rendering, but the available evidence does not prove that the original cancel request itself was issued by a browser click, so no browser-click cancellation claim is added.
- **Loading/reconnect verified:** a full browser reload first exposed the Gradio pre-hydration/loading skeleton (controls present without normal labels/content). The local HTTP endpoint remained healthy (`200`, 299150 bytes). A subsequent browser read showed the fully hydrated UI again with `Đã kết nối Codex WebGPT`, the persisted selector, and the paused job restored with `Tiếp tục từ checkpoint` enabled while Pause/Cancel were disabled.
- **YouTube interaction surface verified again:** switching source mode to `YouTube` removed the local upload surface and exposed the `URL YouTube` entry. A benchmark ID/file (`PWKWNb550Cw`) exists in the workspace, but the current browser-control safety layer blocked entering the derived public URL before any request was submitted. No live download/pipeline run occurred, so live YouTube E2E remains unverified.

Current P16 conclusion remains **PARTIAL**. The new runtime evidence closes the source-slice playback gap and strengthens failed/cancelled/loading/reconnect acceptance. The remaining material acceptance gap is live YouTube end-to-end; browser-origin provenance for the real cancel request is also still unproven.
