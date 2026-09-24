# P14-P15 multi-speaker/profile agent checkpoint

Date: 2026-09-22

## Scope

- Ownership used: `src/vi_dubber/types.py`, `src/vi_dubber/profiles.py`, and `tests/test_profiles.py`.
- No edits to pipeline, config, PLAN, README, web, ASR, timing, media, TTS, or review modules.
- Production default remains `balanced_best`; no existing execution path was switched to a different profile or overlap policy.

## P14 - Multi-speaker/overlap visibility

Added a deterministic `Segment.speaker_visibility()` contract which reports:

- primary speaker;
- stable sorted speaker labels observed across segment + word metadata;
- speaker count and `multi_speaker` flag;
- overlap derived from either segment-level or word-level overlap metadata;
- `needs_review` for any overlap or multi-speaker hard case.

This is passive metadata: it makes hard cases machine-readable without silently merging speakers or changing rendering behavior. Existing segment serialization/schema remains unchanged, so this slice does not invalidate stored segment artifacts merely to expose the contract.

Pipeline/editor integration that consumes `needs_review` remains outside this agent's ownership. Therefore full P14 product acceptance is not claimed here; the visibility contract is ready for root integration.

## P15 - Resolved profile behavior contract

Replaced conditional/implicit profile-policy construction with explicit per-profile policy tables while preserving existing resolved behavior.

Each resolved profile snapshot now exposes machine-readable values for:

- translation fan-out and scope;
- translation prefit behavior;
- TypeSafe policy mode;
- TTS batch policy and resolved batch size;
- QA depth and semantic-QA enablement;
- retry budget;
- optional lip-sync;
- final full QA;
- overlap assembly policy and hard-case review policy.

Current resolved contract remains:

| Profile | Fan-out | TypeSafe | Prefit | QA | Retry | Final QA |
| --- | ---: | --- | --- | --- | ---: | --- |
| Fast | 1 / single | critical gate | off | minimal, semantic off | 1 | off |
| Balanced Best | 1 / single | verify/escalate | on | standard, semantic on | 3 | on |
| Max Quality | 2 / hard segments | strict verify/escalate | on | strict, semantic on | 4 | on |

All three currently resolve TTS batch size `1`, lip-sync off, and equal-power overlap assembly. The contract records actual current behavior instead of claiming future benchmark-driven batch settings.

`validate_config()` now rejects unknown overlap assembly policies early; accepted values match the existing media implementation: `equal_power` and `sum`. Omitting the setting still resolves to the existing `equal_power` behavior.

## Validation

- `uv run pytest -q tests/test_profiles.py` -> `6 passed`.
- `uv run pytest -q tests/test_asr_words.py tests/test_fault_contracts.py` -> `24 passed`.
- `uv run pytest -q tests/test_segmentation.py tests/test_audio_qa.py tests/test_timing.py tests/test_core.py` -> `45 passed`.
- Full regression: `uv run pytest -q` -> `189 passed in 17.43s`.

## Status

- P14 deterministic visibility/data contract: complete within owned files; consumer integration and real two-speaker overlap fixture review remain for root.
- P15 resolved behavior contract: complete within owned files with production default preserved.
