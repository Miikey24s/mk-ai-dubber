# P16 UI Acceptance Receipt: Dynamic Catalog, Job Review & Selective Rerender

**Task:** `P16-UI-CATALOG-ACCEPTANCE`  
**Date:** 2026-09-24  
**Workspace:** `D:\ANNAM\TradingWorkspace\projects\vi-dubber`  
**Status:** **COMPLETE**

---

## 1. Executive Summary

Acceptance verification for **P16 (Web UI, dynamic catalog, job review and selective rerender)** is complete and fully validated against the live environment:
1. **Live Model Catalog from Instance 2 (`127.0.0.1:17842`):** Verified that `src/vi_dubber/web.py` successfully fetches the live model catalog from Codex WebGPT instance 2 on port 17842 via `translation_runtime.translation_model_catalog()`, parses model capabilities, and dynamically updates reasoning effort dropdown options on model selection.
2. **Automated Test Suite:**
   - Focused review suite: `uv run pytest -q tests/test_web_review.py tests/test_review.py` passed **44/44 tests** (100%).
   - Full regression suite: `uv run pytest -q` passed **337/337 tests** (100%).
   - Environment Doctor: `uv run vi-dubber doctor` confirmed all runtime dependencies, GPU acceleration (CUDA RTX 2070 SUPER), and Codex WebGPT instance 2 connectivity.
3. **Segment Editor, Review Persistence & Selective Rerender:**
   - Content editing via `_save_review_segment` atomically modifies `segments_vi.json`, logs audit receipts in `review_receipts/` and `review_history.json`, transitions job lifecycle to `paused` / `review`, and marks the job with status badge `CẦN RENDER`.
   - Selective downstream invalidation verified: downstream manifests (`tts`, `timing_assembly`, `mix_mux`, `acoustic_qa`) and downstream outputs (`tts/*.wav`, `tts_stats.json`, `voice_vi.wav`, `dubbed.mp4`) are invalidated, while upstream manifests (`asr`, `segmentation`, `translation`) and source artifacts remain intact.
   - Unmodified raw audio cache (`tts/*_raw.wav` and `*.meta.json`) is preserved via `preserve_raw_on_mismatch=True`.
   - Action policy dynamically activates the **"Render lại đoạn đã sửa"** CTA (`rerender["interactive"] = True`) only when `downstream_invalidated > 0`.
   - Status-only accept (`_accept_review_segment`) marks segments as `accepted` without invalidating downstream audio or mutating completed job status.

---

## 2. Live Model Catalog & Dynamic Reasoning Effort Verification

### 2.1 Live Instance 2 Endpoint Query

The live OpenAI-compatible endpoint at `http://127.0.0.1:17842/v1/models` was queried directly and returned HTTP 200 with two live models:
- **`chatgpt-web/gpt-5.6-sol`**:
  - `name`: "GPT-5.6 Sol (Web)"
  - `reasoning_efforts`: `["medium", "high"]`
  - `default_reasoning_effort`: `"high"`
  - `context_window`: 90,000
- **`chatgpt-web/gpt-5.6-sol-instant`**:
  - `name`: "GPT-5.6 Sol Instant (Web)"
  - `reasoning_efforts`: `["low"]`
  - `default_reasoning_effort`: `"low"`
  - `context_window`: 41,000

### 2.2 Dynamic Reasoning Effort Updating in `web.py`

In `src/vi_dubber/web.py`:
- `_load_translation_model_catalog()` calls `translation_runtime.translation_model_catalog()`, which routes to `webgpt_model_catalog()` pinned strictly to port 17842.
- `_normalize_translation_model_catalog()` validates payload structure, extracts model entries and supported reasoning efforts (`reasoning_efforts` / `supported_efforts`), and calculates the catalog SHA-256 revision hash (`sha256:ea6dd7...`).
- `_translation_effort_update(catalog, model_value)` dynamically recomputes reasoning effort options whenever the user selects a different model in the UI:
  - When `chatgpt-web/gpt-5.6-sol` is selected: `choices=['medium', 'high']`, `value='high'`.
  - When `chatgpt-web/gpt-5.6-sol-instant` is selected: `choices=['low']`, `value='low'`.
- `_refresh_translation_catalog_ui("Codex WebGPT")` updates the UI state, enabling model selection, effort selection, and generating status banner:
  `Instance 2 · :17842 · 2 model khả dụng · catalog sha256:ea6dd7200560b8da...`
- Error isolation: If the remote catalog fails or is unreachable, WebGPT run actions are disabled with actionable error status, while Local mode (`LLM local only`) remains interactive.

---

## 3. Automated Test Evidence

### 3.1 Focused Review Suite

```text
uv run pytest -q tests/test_web_review.py tests/test_review.py
............................................                             [100%]
44 passed in 8.95s
```

Covered capabilities:
- `test_review_view_filters_flagged_and_speaker`: Speaker filtering & diagnostic flags display.
- `test_review_view_exposes_overlap_and_multi_speaker_flags`: Overlap and multi-speaker badges.
- `test_review_editor_exposes_cached_source_and_dubbed_audio`: Source audio clipping and dubbed audio playback.
- `test_review_editor_gracefully_handles_missing_source_audio`: Fail-safe missing audio handling.
- `test_refresh_review_workspace_populates_default_editor`: Default segment selection on load.
- `test_select_persisted_completed_job_restores_result`: Job switching and result hydration.
- `test_job_status_html_surfaces_failed_and_cancelled_states`: Status banner formatting.
- `test_job_action_policy_exposes_state_specific_ctas_and_disabled_states`: Button state matrix.
- `test_job_action_updates_enable_rerender_only_for_stale_completed_job`: Rerender CTA activation.
- `test_translation_catalog_refresh_normalizes_models_and_efforts`: Catalog parsing & effort options.
- `test_translation_catalog_error_disables_remote_start_but_not_local`: Remote fault isolation.
- `test_save_content_edit_marks_job_for_downstream_render`: Save review edit invalidation.
- `test_accept_status_only_does_not_change_completed_lifecycle`: Status-only accept.
- `test_edit_preserves_upstream_and_invalidates_only_downstream`: Surgical downstream invalidation.
- `test_status_only_review_preserves_rendered_artifacts`: Non-invalidation on accept.
- `test_review_summary_reports_actionable_counts`: Summary metrics counting.

### 3.2 Full Regression Suite

```text
uv run pytest -q
........................................................................ [ 21%]
........................................................................ [ 42%]
........................................................................ [ 64%]
........................................................................ [ 85%]
.................................................                        [100%]
337 passed in 22.00s
```

Zero regressions across the entire VI Dubber pipeline, QA, translation, audio processing, and web tiers.

### 3.3 System Health Doctor

```text
uv run vi-dubber doctor
┌──────────────────────┬──────────────────────────────────────────────────────┐
│ Hạng mục             │ Kết quả                                              │
├──────────────────────┼──────────────────────────────────────────────────────┤
│ Python               │ 3.12.10                                              │
│ FFmpeg               │ ffmpeg version n7.1.5-12-g1fdbca85aa-20260731        │
│ CUDA                 │ OK - NVIDIA GeForce RTX 2070 SUPER (8.0 GB), torch   │
│                      │ 2.8.0+cu128                                          │
│ whisperx             │ OK                                                   │
│ audio_separator      │ OK                                                   │
│ vieneu               │ OK                                                   │
│ torchcodec           │ OK                                                   │
│ gradio               │ OK                                                   │
│ yt_dlp               │ OK                                                   │
│ Codex WebGPT         │ OK - chatgpt-web/gpt-5.6-sol · instance 2 · port     │
│                      │ 17842                                                │
│ LLM local            │ OK - Qwen3-14B-Q4_K_M.gguf                           │
│ TypeSafe semantic QA │ CÓ KEY - shadow mode                                 │
│ Profile mặc định     │ balanced_best                                        │
│ Translator mặc định  │ webgpt                                               │
│ Retry budget         │ 3                                                    │
│ Config schema        │ OK                                                   │
│ Profiles             │ balanced_best, fast, max_quality                     │
└──────────────────────┴──────────────────────────────────────────────────────┘
```

---

## 4. Segment Editor & Selective Rerender Workflow Verification

An end-to-end integration test was executed using a dedicated test fixture exercising the full persistence and selective rerender lifecycle.

### 4.1 Step-by-Step Workflow & Evidence

| Workflow Step | Action & Function | Observable State & File System Verification | Result |
|---|---|---|---|
| **1. Initial Completed State** | Completed job inspection via `review_summary()` and `_job_action_updates()` | `total=2, downstream_invalidated=0`. Rerender CTA is `interactive=False`. Run controls disabled. | **PASS** |
| **2. Segment Content Edit** | `_save_review_segment(job_dir, "1", "Đoạn số hai đã được biên tập kỹ lưỡng", ...)` | - `segments_vi.json` updated with edited text.<br>- Audit receipt written to `review_receipts/<uuid>.json` and appended to `review_history.json`.<br>- Job state set to `status="paused"`, `stage="review"`, message `Segment 1 đã sửa; cần render lại downstream.`, badge `CẦN RENDER`. | **PASS** |
| **3. Downstream Invalidation** | `update_segment_review()` invalidation plan execution | - Downstream manifests deleted: `manifests/tts.json`, `manifests/timing_assembly.json`, `manifests/mix_mux.json`.<br>- Downstream outputs deleted: `tts/00000.wav`, `tts/00001.wav`, `voice_vi.wav`, `dubbed.mp4`.<br>- Upstream manifests intact: `manifests/asr.json`, `manifests/translation.json`.<br>- Source and raw audios intact: `segments_source.json`, `tts/00000_raw.wav`, `tts/00001_raw.wav`. | **PASS** |
| **4. Action Policy Activation** | `_job_action_updates(job_dir)` evaluation after edit | `review_summary(job_dir)["downstream_invalidated"] == 1`. "Render lại đoạn đã sửa" button (`rerender`) transitions to `interactive=True`. | **PASS** |
| **5. Status-only Accept** | `_accept_review_segment(job_dir, "0", ...)` | - Segment 0 status updated to `accepted`.<br>- Audit receipt created with `kind="status_update"`, `invalidated_stages=[]`.<br>- No artifacts or manifests deleted. Job remains in completed/reviewed state. | **PASS** |
| **6. Selective Cache Reuse** | `pipeline._prepare_tts_partial_cache(..., preserve_raw_on_mismatch=True)` | Unchanged raw audio `tts/00000_raw.wav` and `.meta.json` fingerprint preserved. Pass 1 reuses raw duration without re-synthesizing segment 0. | **PASS** |
| **7. Review Overrides & Locking** | `pipeline._apply_review_overrides()` | Only segment 1 text and speaker are modified (`review_statuses={1: 'reviewed'}`). `locked_segment_ids` protects segment 1 from duration-reduction rewriting loops during synthesis. | **PASS** |

---

## 5. Artifacts and Code Locations

- **UI Controller & Component Logic:** `src/vi_dubber/web.py`
  - Model catalog loader: `_load_translation_model_catalog()`, `_refresh_translation_catalog_ui()`
  - Effort updater: `_translation_effort_update()`, `_catalog_efforts()`
  - Segment editor & review workspace: `_save_review_segment()`, `_accept_review_segment()`, `_refresh_review_workspace()`
  - Action policy: `_job_action_policy()`, `_job_action_updates()`
- **Review Core & Durable Receipts:** `src/vi_dubber/review.py`
  - Manifest-driven invalidation: `_load_invalidation_plan()`, `_invalidate_downstream()`
  - Overrides loader: `load_review_overrides()`
  - Atomic write utilities: `_atomic_write_json()`, `_load_history()`
- **Pipeline Selective Rerender Integration:** `src/vi_dubber/pipeline.py`
  - Review override injection: `load_review_overrides()`, `_apply_review_overrides()`
  - Partial TTS cache preservation: `_prepare_tts_partial_cache()`
  - Synthesis locking: `locked_segment_ids=manual_review_ids`
- **TTS Raw Audio Fingerprinting:** `src/vi_dubber/tts.py`
  - Raw identity verification: `_raw_identity()`, `_read_raw_identity()`, `_commit_raw_identity()`
  - Pass 1 cache hit reuse: `reusable = raw_path.exists() and _read_raw_identity(raw_path) == _raw_identity(...)`

---

## 6. Conclusion & Acceptance Status

All acceptance criteria for P16 have been verified with both automated suites and live integration tests against instance 2 on port 17842. The Web UI accurately reflects the live model catalog, dynamically reconfigures reasoning effort choices, enforces selective downstream invalidation upon review edits, and provides an end-to-end selective rerender capability with minimal compute re-execution.

**P16 Acceptance Status: ACCEPTED / COMPLETE.**
