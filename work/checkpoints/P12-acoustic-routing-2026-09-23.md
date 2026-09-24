# P12 acoustic QA routing checkpoint - 2026-09-23

## Result

- Fixed a routing bug in segment-level acoustic QA: a missing critical token detected by re-ASR is an acoustic/rendering failure, so it now routes to `pronunciation_retry` rather than semantic `text_repair`.
- Critical-token failures remain highest priority in the selective repair queue.
- Unified the segment-QA cache policy version at `SEGMENT_QA_POLICY_VERSION = 4`; `pipeline.py` now uses the single source from `qa.py` instead of shadowing it with a second constant.
- Focused regression after integration: `54 passed` across `test_audio_qa.py`, `test_voice_audio_core.py`, and `test_pipeline_foundation.py`.

## Isolated real E2E receipt

Source: `work/benchmarks/CP2-short-smart-source.mp4`.

Isolated work root: `work/p12-routing-e2e-20260923-0945/`.

The run used `balanced_best`, WebGPT translation, resume from an isolated clone of the retained job, then recomputed the changed downstream work. Final receipt:

- 53 speech segments.
- Global Vietnamese re-ASR similarity: `0.9948832035595107` (99.49%) vs threshold 0.78.
- Initial segment failures: 1.
- Selective repairs attempted/completed: 1 / 1.
- Final segment failures: 1.
- Missing-critical-token failures after the routing fix: 0.
- Remaining failure: segment 9 timing overflow only.
- Remaining overflow rate: `1 / 53 = 1.89%`, below the PLAN target `<3%` and acceptance `<5%`.
- Remaining segment 9 timing ratio after repair: `1.2443953048509113`; it stays visible/fail-closed rather than being silently accepted.
- Final mix: -14.16 LUFS, -1.76 dBTP, no clipping risk.
- End-to-end RTF for this run: `4.781942632507805`.

Behavioral artifact:

- `work/p12-routing-e2e-20260923-0945/job-4c8236a32c685a11/segment_qa.json`
- `work/p12-routing-e2e-20260923-0945/job-4c8236a32c685a11/result.json`

The E2E run was produced immediately before the policy-version constant was unified from the duplicated value to version 4. The routing logic exercised by the run is unchanged; the version bump only guarantees future cache invalidation for the new policy.

## Remaining P12 gap

- The one remaining timing overflow is correctly detected and is within the global overflow gate; fixing it further is a P09 timing/prosody optimization rather than an acoustic semantic repair.
- Broader real-human false-positive/listening evidence is still required before claiming human acceptance complete.

