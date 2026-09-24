# P03/P09 timing-core checkpoint

Date: 2026-09-22

## Status

- P03 smart-segmentation implementation/short-fixture gate: PASS.
- P09 elastic-timing foundation: PASS for deterministic allocation/action contracts; real E2E acceptance remains PENDING.
- CP2 must not be marked complete from this checkpoint alone.

## Scope

Changed only the assigned implementation/test surface:

- `src/vi_dubber/segmentation.py`
- `src/vi_dubber/timing.py`
- `tests/test_segmentation.py`
- `tests/test_timing.py` (new)

No pipeline/config/PLAN/README edits were made.

## P03 findings and changes

Initial dry-run of the existing smart builder on the real short source artifact
`work/job-cafbd0adb862d5ae/segments_source.json` exposed a gate failure:

- legacy/source: 53 segments, 3 `<1s` (5.66%), max 10.664 s;
- pre-fix smart builder: 59 turns, 9 `<1s` (15.25%), max 6.606 s.

Root cause was deterministic fragmentation: a pause just above the configured
pause threshold could orphan a sub-minimum leading word, and a natural split
could leave a short tail even when rejoining it was safe.

Fixes:

- preserve word-level overlap provenance (`segment.overlap OR word.overlap`);
- protect transitions into/out of word-level overlap;
- do not hard-split a sub-minimum group on a moderate pause unless the pause is
  also larger than `min_duration`;
- compact short groups after the first pass only when the neighboring boundary
  is safe: same speaker, no overlap/scene boundary, bounded pause, bounded
  `max_duration`, and bounded `max_words`;
- prefer rejoining a group that shares source-segment provenance;
- bump `SEGMENTATION_POLICY_VERSION` from 1 to 2 so the existing segmentation
  stage fingerprint invalidates stale smart-turn artifacts.

Post-fix dry-run on the same source artifact:

- 50 turns;
- 0 `<1s` turns (0%);
- mean 3.275 s, p50 3.221 s, p90 4.982 s;
- max 6.606 s.

Focused tests cover text order, max-word splitting, missing word timestamps,
speaker boundaries, overlap, scene boundaries, moderate-pause orphaning, and
short-tail compaction.

## P09 findings and changes

The current real short E2E artifact still reports the coordinator acceptance
gap supplied for this task:

- overflow: 23/53 = 43.4%;
- tempo p95: 1.25.

That run used 53 source segments and 53 speech turns, so it was produced with
legacy segmentation and is not a P03+P09 A/B acceptance run.

Timing foundation changes:

- added explicit `TIMING_POLICY_VERSION = 2` for downstream cache integration;
- clamp negative borrow/padding inputs instead of allowing negative slack;
- keep adjacent-silence allocation conservative and collision-free;
- extend `timing_action()` with the configured mild-slowdown behavior:
  `slowdown` for a near-target short render, `preserve_pause` when filling the
  whole slot would require excessive slowdown, `speedup` for bounded excess,
  and `rewrite` for large excess.

Real-artifact dry-run evidence for the existing allocator:

- 53 timing windows;
- 0 window collisions;
- 31.628 s total adjacent silence borrowed across both sides;
- maximum combined borrow for one segment: 0.700 s.

Applying the new action classifier to the existing 53 generated/target duration
pairs gives: 24 `rewrite`, 17 `speedup`, 4 `slowdown`, 8 `preserve_pause`.
This is diagnostic routing evidence only, not proof that rewrites or slowdown
were executed in that prior E2E run.

## Validation

- focused current surface:
  `.venv\\Scripts\\python.exe -m pytest -q tests\\test_segmentation.py tests\\test_timing.py tests\\test_voice_audio_core.py`
  -> `28 passed in 1.11s`.
- full suite:
  `.venv\\Scripts\\python.exe -m pytest -q`
  -> `160 passed in 12.33s`.

## Remaining gaps / root integration

1. `config.yaml` still selects `segmentation.mode=legacy`; this worker was not
   allowed to change config. A controlled smart-vs-legacy E2E A/B is still
   required before changing the default.
2. `timing_action()` is a tested policy helper but the current TTS fitting path
   does not consume it, so the new `slowdown` route does not yet change runtime
   audio by itself.
3. `TIMING_POLICY_VERSION` is not yet included in the TTS stage fingerprint;
   `pipeline.py` currently owns that version map and was outside this worker's
   write scope. Root integration should wire the timing policy version before
   relying on resume/cache correctness for timing behavior changes.
4. P09 acceptance still needs a real P03+P09 run showing lower overflow and
   tempo p95 without sync drift, plus listening evidence. Do not infer that from
   the deterministic helper tests.
5. P03 still needs the planned larger/734-segment benchmark and blind listening
   before roadmap-level acceptance, even though the short real fixture now
   passes the `<1s` distribution gate strongly.
