# Continuation checkpoint - 2026-09-22

Source task resumed from `codex://threads/01a0c7a7-01b5-77a1-8665-8e705cf4e0a5`.

## Changes completed in this continuation

- P16 real-job compatibility fixed:
  - Review source lineage now resolves smart `segments_turns.json` ids safely;
  - semantic QA index v2/v3 is accepted while translated-stage artifacts remain
    validated;
  - real `work/job-4c8236a32c685a11` loads 50 review rows successfully.
- P20 WebGPT fault handling hardened:
  - malformed/partial JSON is now a bounded retryable provider failure;
  - retry still obeys the existing retry budget/backoff and exhausts clearly;
  - hybrid mode can fall back after that bounded failure path.
- `PLAN.md` phase status was reconciled against current code, checkpoints,
  tests, and real runtime evidence instead of leaving the original TODO table.
- P11 current mix chain was re-measured without rerunning ASR/TTS by reusing the
  existing 226 s fixture stems + `voice_vi.wav`:
  - integrated loudness: `-14.45 LUFS`;
  - true peak: `-1.61 dBTP`;
  - clipping risk: false;
  - numeric loudness and true-peak gates pass.

## Runtime / browser evidence

- Real smart E2E artifact remains:
  - 50 speech turns from 53 raw ASR segments;
  - 3/50 overflow;
  - global Vietnamese Re-ASR QA 99.3%;
  - final output `work/outputs/CP2-smart-ab.mp4`.
- Review UI localhost smoke on port 7875:
  - completed job recovered automatically;
  - 50 review rows / 3 flagged overflow rows displayed;
  - segment 0 exposes source English, selected Vietnamese, speaker, tempo,
    semantic confidence and dubbed audio;
  - browser reload restores the persisted completed job and Review rows.
- `uv run vi-dubber doctor --config work/config-smart-ab.yaml` passes Python,
  FFmpeg, CUDA RTX 2070 SUPER 8 GB, WhisperX, separator, VieNeu, TorchCodec,
  Gradio, yt-dlp, WebGPT route, local Qwen, config schema and profiles. TypeSafe
  is available in shadow mode; diarization token remains optional/not present.

## Validation

- provider/core focused tests after malformed-JSON retry change: `44 passed`;
- P16 focused review/job/fault tests: `35 passed` from the P16 worker;
- current full suite after merging both lanes: `254 passed in 11.33s`.

## Current roadmap truth

`DONE`: P00, P01, P02, P15.

`PARTIAL`: P03-P14 except P15, plus P16, P17 and P20. These are held partial
where real fixture, blind listening, calibration, long-video or live fault
evidence is still missing even when implementation is present.

`OPTIONAL/BLOCKED`: P18 until P17 acceptance; P19 until explicit user opt-in for
cloud/API/cost/privacy changes.

Highest-value next acceptance work is a current-code real E2E that exercises
P12 segment QA and P16 selective rerender, followed by the missing P17 fixture
classes and human A/B gates. Do not mark these complete from unit tests alone.
