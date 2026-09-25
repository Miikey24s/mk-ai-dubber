# P11 controlled listening evidence

Date: 2026-09-25

## Scope

- P11 mix/master only.
- Reused the retained real CP2 production fixture and exact background/voice tracks from `job-4c8236a32c685a11`.
- No changes to `PLAN.md`, `pipeline.py`, `config.yaml`, `types.py`, or runtime defaults.

## Controlled A/B pair

The first retained P11 pair was not fully controlled: its baseline encoded AAC at 96 kHz while the current candidate encoded at 48 kHz. The replacement harness `tools/p11_listening_evidence.py` now renders both candidates as AAC 192k, 48 kHz, stereo and holds source video, stems, gains, loudness target, true-peak target and `duck_background=false` constant.

Only the P11 policy differs:

- baseline: single-pass loudnorm, no limiter;
- candidate: current production `mux_dubbed_video()` two-pass loudnorm plus AAC true-peak-headroom limiter.

Evidence package:

- `work/benchmarks/p11-blind-ab-20260925-v2/pair-provenance.json`
- `work/benchmarks/p11-blind-ab-20260925-v2/packet.json`
- `work/benchmarks/p11-blind-ab-20260925-v2/ballot-template.json`
- `work/benchmarks/p11-blind-ab-20260925-v2/evaluation-no-votes.json`
- blinded media aliases under `work/benchmarks/p11-blind-ab-20260925-v2/media/`

## Objective results

Baseline single-pass/no-limiter:

- integrated loudness: `-14.72 LUFS` -> outside the `-14 ±0.5 LU` gate;
- true peak: `-1.11 dBTP` -> fails the `<= -1.5 dBTP` gate;
- decoded sample clipping: `0` samples at or above 0 dBFS.

Current two-pass + limiter candidate:

- integrated loudness: `-14.40 LUFS` -> pass;
- true peak: `-1.62 dBTP` -> pass;
- decoded sample clipping: `0` samples at or above 0 dBFS -> pass;
- decoded sample count matches the controlled baseline: `21,692,416` samples each.

The generated packet passes media-integrity verification and intentionally remains `pending_human_votes`. The harness never selects a subjective winner automatically.

## Human gate still open

P11 cannot be marked fully closed yet because the acceptance criteria also require subjective evidence that dialogue clarity improves without audible pumping, background discontinuity, clipping or distracting level jumps. The blind packet requires 3 completed votes plus an explicit human-attested decision.

This fixture uses the production `duck_background=false` path, so it does not prove a benefit for optional sidechain ducking. It validates the currently used two-pass loudnorm + limiter policy against the previous single-pass/no-limiter baseline.

## Validation

- `uv run pytest -q tests/test_p11_listening_evidence.py tests/test_listening_ab.py` -> `11 passed`.
- Real 226-second controlled pair rendered successfully with the bundled FFmpeg.
- `evaluation-no-votes.json` reports `media_integrity.passed=true`, `status=pending_human_votes`, `gate_passed=false`.
