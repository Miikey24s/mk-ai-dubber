# P16 Human review/editor UI checkpoint

Date: 2026-09-22

Status: deterministic UI/review slice PASS; full P16 acceptance remains blocked by backend manual-override/selective-regeneration support and browser visual acceptance.

## Scope

Changed only the assigned P16 ownership:

- `src/vi_dubber/review.py`
- `src/vi_dubber/web.py`
- `tests/test_review.py`
- `tests/test_web_review.py`

No edits were made to pipeline, config, PLAN, README, shared UI systems, or external services.

Open-source gate: `BUILD`. This slice extends the existing Gradio control plane and persisted project artifacts; no new dependency or external UI/codebase was needed.

## Behavior changed

- Review status-only updates (`reviewed` / `accepted`) no longer invalidate TTS, timing assembly, mix/mux, or acoustic QA. Content changes (Vietnamese text or speaker) still invalidate only downstream render stages.
- No-op review updates are rejected, preventing useless receipts and accidental invalidation.
- Added compact deterministic review summary counts for total/flagged/rewrite/overflow/semantic/reviewed/accepted/stale-render state.
- Web runs now persist non-secret resume metadata in `state.json`: input path, source mode/YouTube URL, provider, profile, diarization, voice-reference path, and output path. HF token is intentionally not persisted.
- Added persisted-job selector/history reload. Selecting an existing job restores lifecycle status, progress, result paths, telemetry, and review workspace where artifacts are valid.
- Resume now uses persisted job metadata instead of requiring the browser form to still contain the original file/options.
- Added dense Gradio review workspace with review/speaker filters, segment table, English/baseline/selected Vietnamese, editable speaker/status, diagnostics, current dubbed segment audio, save, accept, and downstream-render action.
- Saving a content edit persists it atomically, invalidates downstream artifacts, and marks the job `paused/review` so stale final output is not presented as current.
- Accepting a segment is metadata-only and leaves completed output valid.
- If a manual content edit is pending, persisted resume/rerender is fail-closed. The current pipeline does not consume `review_history.json` / manual overrides, so allowing resume would regenerate from prefit/baseline text and could overwrite the user's edit. UI reports this instead of claiming a successful rerender.

## Main data/state flow

1. New web job resolves/downloads the input and writes non-secret run metadata to the existing persisted job `state.json`.
2. `run_pipeline()` remains the source of truth for processing and lifecycle updates.
3. Completed job review reads `segments_source.json`, `segments_translated.json`, `segments_vi.json`, TTS stats, and semantic QA into normalized rows.
4. Status-only review writes `segments_vi.json` + durable review receipt/history without invalidating render artifacts.
5. Text/speaker edit writes the selected segment + durable receipt/history, removes downstream manifests/artifacts, preserves upstream ASR/segmentation/translation artifacts, and moves persisted job state to `paused/review`.
6. Reopen/job selection reconstructs the UI from persisted `state.json` and review artifacts; stale output is suppressed when downstream has been invalidated.

## Trade-off / blocker

P16 asks for "regenerate only selected segment" and rerun downstream after an edit. That cannot be claimed from this slice because the current pipeline does not import/apply `load_review_overrides()` and has no selected-segment regeneration contract. Implementing a parallel TTS/mix pipeline inside `web.py`/`review.py` would duplicate core pipeline behavior and risk inconsistent manifests/cache semantics, so this slice deliberately blocks rerender after manual content edits rather than silently losing the edit.

Required backend follow-up before full P16 acceptance:

- pipeline applies durable manual review overrides after translation/prefit and before TTS;
- selected-segment TTS regeneration updates the selected audio/stat/manifests safely;
- downstream timing/mix/QA rerun consumes the regenerated segment while upstream caches remain valid.

## Validation

Focused review/web/job/control regression:

`uv run pytest -q tests/test_review.py tests/test_web_review.py tests/test_jobs.py tests/test_fault_contracts.py`

Result: `26 passed in 6.16s`.

Full suite:

`uv run pytest -q`

Result: `190 passed in 10.86s`.

Syntax/import check:

`uv run python -m py_compile src/vi_dubber/review.py src/vi_dubber/web.py`

Result: pass.

Gradio runtime smoke:

- launched local app on `http://127.0.0.1:7873` with browser opening disabled;
- HTTP GET returned `200`;
- server was then shut down cleanly.

## Visual/browser acceptance still missing

- No Playwright/browser screenshot pass was performed in this slice.
- No desktop-width visual inspection of the new review accordion/table/editor has been recorded.
- No narrow/mobile visual acceptance has been recorded; P16 is desktop-first, but responsive overflow still needs a browser check.
- No real completed job was manually clicked through end-to-end in a browser for filter -> edit -> accept -> reopen persistence.
- No real source-slice playback exists yet; the editor exposes the current dubbed per-segment WAV when available. Source timing is shown, but source-slice audio generation/playback still needs a backend/UI contract.

## SHA256

- `src/vi_dubber/review.py`: `16CB9654BEB83EF97DC45E97CFF515B2088D23D7275FF265DB0A7CCA8CBC852E`
- `src/vi_dubber/web.py`: `9DAF453B0DA858C4D87C431960347A6591CB01B43C5F9129098DE05017FB1132`
- `tests/test_review.py`: `1AA148C0D6615670ACD03A3A56D83A52F2F55ACB6FCAC87D05F5506C00E51A90`
- `tests/test_web_review.py`: `D56879433D8739E5C40925E18D3144275D23171A5F471CF6D3F38D47C73AC0A2`

## Real-job alignment follow-up - 2026-09-22

The completed job `work/job-4c8236a32c685a11` exposed the real smart-segmentation lineage that the original review contract must preserve:

- `segments_source.json`: 53 ASR segments, ids `0..52`;
- `segments_turns.json`: 50 post-segmentation speech turns, ids `0..49`;
- `segments_translated.json`, `segments_vi.json`, and `tts_stats.json`: 50 rows, ids `0..49`;
- translated semantic QA: 50 items, ids `0..49`.

The Review/Editor identity is therefore the post-segmentation speech-turn lineage, not raw ASR segmentation. The loader now resolves the review source against the translated/final id set: it prefers `segments_turns.json` when those ids align, falls back to `segments_source.json` for legacy/non-segmented jobs only when that id set aligns, and otherwise fails closed with counts that identify the conflicting artifacts. This also avoids trusting a stray/stale turn artifact merely because the file exists.

Regression coverage now includes both the 3-source -> 2-turn smart-segmentation shape and a stale-turn fallback/no-valid-candidate failure case.

The same real-job smoke then exposed a second compatibility issue: production `semantic_qa.json` is index version 3, while the original Review loader accepted only version 2. Version 3 keeps the translated-stage contract used by Review (`status`, `artifact`, `thresholds`) and adds additional QA stages, so Review now accepts semantic QA index versions 2 and 3 while continuing to validate the translated artifact schema itself. A v3 regression case is included.

The backend blocker remains unchanged: selective rerender/manual overrides still require pipeline-owned consumption of durable review overrides and selected-segment TTS regeneration. This review-layer fix does not claim that missing pipeline capability.

Follow-up validation:

- `tests/test_review.py tests/test_web_review.py`: `25 passed in 5.39s`;
- `tests/test_review.py tests/test_web_review.py tests/test_jobs.py tests/test_fault_contracts.py`: `35 passed in 5.77s`;
- full suite: `254 passed in 10.61s`;
- real job `load_review_rows()` / `review_summary()`: 50 rows, ids `0..49`, review summary generated successfully;
- real job `web._review_view()`: 50 table rows, 50 segment choices, 2 speaker-filter choices.

Follow-up hashes before this checkpoint append:

- `src/vi_dubber/review.py`: `6AF819C9C07418AE8F04AF1167860C1251C2B4C2BCBBEAA1990810BE7280EA5E`
- `tests/test_review.py`: `3D4F24AD7B448F5EBA41B01005A3723EE2A1C89A4E1FDBBB88F4D6399CEF8068`

## Root integration correction - 2026-09-22 20:xx

The earlier backend-blocker note is superseded by the current root `pipeline.py`.
The coordinator verified that the pipeline now:

- loads durable `review_history.json` edits through `load_review_overrides()`;
- applies text/speaker overrides after translation/prefit and before reference/TTS;
- includes review overrides in the TTS request identity;
- locks manually reviewed segment ids against automatic rewrite;
- preserves identity-guarded raw TTS artifacts where safe and reruns downstream
  manifests from the changed TTS state.

Browser smoke on the real completed smart-segmentation job at
`http://127.0.0.1:7875` now loads the persisted job and Review/Editor without an
artifact-id or semantic-schema error. It shows 50 rows, 3 flagged overflow
segments, the selected segment English/Vietnamese/speaker/semantic/timing data,
and its dubbed WAV. A full browser reload restores the same completed job and
review rows, which is real refresh/reopen evidence rather than only a unit test.

P16 remains PARTIAL because the destructive/mutating browser path
edit -> save -> selected rerender -> reopen has not been exercised against the
real completed job in this pass; source-slice playback and final visual polish
also remain acceptance work.

## Browser selective-rerender follow-up - 2026-09-23

The previous mutating-path blocker is superseded by the isolated-clone browser receipt at `work/p16-e2e-clone-20260923-0300/job-4c8236a32c685a11`.

- The browser/manual edit -> selective-rerender path was exercised on the clone rather than the canonical completed job.
- `original-baseline.json` records the pre-run files and SHA256 values.
- A post-run audit found no changed pre-existing hashed artifact.
- Only `tts/00022_raw.wav` and `tts/00022_raw.meta.json` were newly created.

This closes the earlier "mutating path not exercised" gap for the isolated selective-rerender slice. It does not close all P16 acceptance: source-slice playback, local + YouTube happy paths, failure/retry/cancel UI behavior, and broader empty/loading/disabled/visual-state coverage still need receipts. P16 therefore remains PARTIAL.
