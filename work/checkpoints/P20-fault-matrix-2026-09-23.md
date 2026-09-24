# P20 fault-matrix receipt - 2026-09-23

## Deterministic automated coverage

| Failure case | Evidence | Status |
|---|---|---|
| WebGPT transient execution/network-style failure | `tests/test_webgpt_retry.py::test_webgpt_retries_transient_execution_failure_then_succeeds` | PASS |
| WebGPT partial/malformed result | `tests/test_webgpt_retry.py::test_webgpt_malformed_result_is_retried_then_succeeds` + bounded exhaustion test | PASS |
| WebGPT route stays down | `tests/test_webgpt_retry.py::test_hybrid_falls_back_to_local_after_webgpt_retry_budget_is_exhausted` | PASS |
| TypeSafe timeout | `tests/test_semantic_cache.py::test_typesafe_timeout_retries_then_succeeds` | PASS |
| TypeSafe 429/5xx | `tests/test_semantic_cache.py` bounded retry tests + localhost transport test in `tests/test_language_quality.py` | PASS |
| Process termination while a stage owns the job | `tests/test_fault_contracts.py::test_terminated_process_preserves_committed_checkpoint_and_reclaims_stale_lease` | PASS for TTS-stage interruption boundary; not repeated independently for every ASR/translation/mix implementation |
| Power-loss style termination during atomic JSON write | `tests/test_fault_contracts.py::test_terminated_process_during_atomic_write_preserves_previous_committed_json` | PASS; old committed JSON remains intact before `os.replace` |
| TTS crash at segment N | `tests/test_voice_audio_core.py::test_tts_crash_mid_run_preserves_completed_raw_and_resume_skips_it` | PASS |
| CUDA OOM | `tests/test_p13_runtime_fallbacks.py::test_cuda_oom_retries_with_conservative_batch_fallbacks` | PASS |
| Disk-space failure | `tests/test_fault_contracts.py::test_live_disk_preflight_fails_clean_without_filling_the_drive` | PASS |
| Input moved with identical content | `tests/test_web_review.py::test_run_persisted_job_recovers_legacy_input_by_verified_identity` | PASS |
| Input content changed before resume | `tests/test_web_review.py::test_run_persisted_job_rejects_changed_input_identity` | PASS, resume fails closed |
| Config/model/glossary/reference changes | stage-fingerprint tests in `tests/test_artifacts.py` and `tests/test_fault_contracts.py` | PASS for relevant downstream invalidation contracts |
| Browser refresh/reopen | real browser receipt in `work/checkpoints/continuation-2026-09-23-p10-p16.md` plus persisted-state reload tests | PASS for completed/stale-running attach; broader UI-state acceptance remains P16 work |
| Duplicate Start/Retry | lease tests plus `tests/test_web_review.py::test_duplicate_start_guard_blocks_same_source_while_live_job_is_running` | PASS |
| Cancel mid-stage | `tests/test_pipeline_foundation.py::test_cancelled_pipeline_persists_cancelled_state_without_committing_partial_stage` | PASS for lifecycle/commit boundary |
| Corrupt/missing cache artifact | manifest/cache tests in `tests/test_artifacts.py`, `tests/test_fault_contracts.py`, `tests/test_semantic_cache.py` | PASS |

## Validation receipt

- New provider-fault slice: `27 passed in 3.92s`.
- Lifecycle/UI slice after cancel-state additions: `41 passed in 6.05s`.
- Fault/lifecycle slice after atomic-loss, changed-input and duplicate-start additions: `44 passed in 9.84s`.
- Atomic process-termination test was rerun twice after a Windows handle-release cleanup fix: `2 passed`, then `1 passed`.
- Full suite after all changes: `281 passed in 14.88s`.
- `uv run vi-dubber doctor`: healthy; CUDA RTX 2070 SUPER, WebGPT `chatgpt-web/high`, local Qwen and TypeSafe shadow key all visible.
- `tools/benchmark.py validate-manifest`: `valid: true`, no issues.

## Remaining P20 acceptance gaps

- Fresh-install smoke has not been reproduced in a clean disposable environment in this receipt; current-env doctor/setup is healthy.
- The real process-kill test proves the lease/checkpoint contract at a TTS-stage interruption boundary, but does not separately kill every ASR/translation/mix implementation.
- External provider outages are tested deterministically with mocked/local transport rather than intentionally breaking the user's real Internet/WebGPT/TypeSafe services.
- P16 still owns broader failed/loading/disabled interaction and YouTube UI end-to-end acceptance.

These gaps keep P20 `PARTIAL`; the automated reliability core is substantially covered without destructive machine-level fault injection.
