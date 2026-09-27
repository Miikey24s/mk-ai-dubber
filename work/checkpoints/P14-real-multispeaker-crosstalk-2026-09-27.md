# P14 real multi-speaker/crosstalk checkpoint - 2026-09-27

Status: **MACHINE EVIDENCE READY / OWNER LISTENING REQUIRED**

## Real fixture and runtime

- Fixture: `work/benchmarks/P14-real-crosstalk-2026-09-27/source.webm`.
- Production path ran with `balanced_best --diarize --resume`.
- Authorized `pyannote/speaker-diarization-community-1` access was exercised successfully; this is no longer a token/model-access blocker.
- Final content-addressed job: `work/job-c287eb1e5bb1d72f`.
- Final dubbed output: `work/benchmarks/P14-real-crosstalk-2026-09-27/dubbed.mp4`.

## Speaker and overlap evidence

- Final pipeline segmentation: **63 segments / 3 speaker IDs**.
- Primary segment counts:
  - `SPEAKER_01`: 55
  - `SPEAKER_02`: 5
  - `SPEAKER_00`: 3
- Final pipeline marks **9 segments** with segment- or word-level overlap:
  - segment 2: 30.16-39.40 s
  - segment 10: 87.12-104.69 s
  - segment 14: 120.29-123.21 s
  - segment 29: 156.81-157.59 s
  - segment 36: 198.22-206.46 s
  - segment 37: 206.70-209.44 s
  - segment 38: 209.92-224.17 s
  - segment 40: 229.71-231.72 s
  - segment 41: 232.50-234.20 s

This satisfies the machine-side requirement for a real multi-speaker fixture with real crosstalk/overlap visibility.

## Canonical references and contamination boundary

- `SPEAKER_01`: isolated canonical reference selected from segment 3, start 40.284 s, duration 5.942 s.
- `SPEAKER_02`: isolated canonical reference selected from segment 61, start 355.919 s, duration 5.444 s.
- `SPEAKER_00`: no eligible clean 3-8 second non-overlap candidate. Reference selection records `no_eligible_3_to_8_second_non_overlap_candidate` and emits no canonical reference.
- TTS reference lookup is keyed by speaker ID. Because `SPEAKER_00` has no reference, those three short turns use the non-cloned voice path instead of borrowing another speaker's reference. This avoids silent cross-reference contamination, but audible identity continuity for those turns remains a human-review item.

## Final output QA

- Full-track ASR similarity: **96.54%**, gate **78%** -> PASS.
- Final mix after mux hardening:
  - integrated loudness: **-14.40 LUFS** -> PASS
  - true peak: **-2.74 dBTP** against target `<= -1.5 dBTP` -> PASS
  - clipping risk: **false**
- Voice track clipped samples: **0**.
- Segment QA still exposes **11/63** review flags after **3 repairs**.
- Timing reports **2 overflow segments** after rewrite/tempo limits.

These remaining flags are deliberately visible and are not converted into a machine acceptance of perceptual quality.

## Production bug found by the real fixture

The first final mux exposed two production issues:

1. WebM/VP8 video was always stream-copied into MP4, which FFmpeg correctly rejected.
2. The retained 0.75 dB AAC headroom was insufficient for this fixture; the first encoded result measured **+0.88 dBTP**.

Commit `ab8f85c fix: harden final mp4 muxing` fixes both:

- MP4 mux keeps compatible video codecs on the fast stream-copy path and transcodes incompatible codecs such as VP8 to H.264.
- AAC output is measured after encoding; only a true-peak failure triggers bounded additional headroom and a retry.
- `mix_mux` cache policy was bumped so stale pre-fix final media cannot be silently reused.

Validation:

- `uv run pytest -q tests/test_audio_qa.py` -> **35 passed**.
- Python compile check for `media.py` and `pipeline.py` -> PASS.
- `git diff --check` -> PASS before commit.
- Real P14 resume reached 6/6 and produced the final PASS metrics above.

## Owner listening packet

Review clips are cut from the final corrected dubbed output:

1. `work/benchmarks/P14-real-crosstalk-2026-09-27/review/01-speaker00-plus-overlap.mp4`
   - short `SPEAKER_00` turn plus nearby overlap; checks the no-reference fallback and local continuity.
2. `work/benchmarks/P14-real-crosstalk-2026-09-27/review/02-crosstalk-cluster.mp4`
   - dense overlap cluster around 196-238 s; checks intelligibility/naturalness when crosstalk is present.
3. `work/benchmarks/P14-real-crosstalk-2026-09-27/review/03-speaker02-switch.mp4`
   - main-speaker to `SPEAKER_02` transition around 320-358 s; checks identity continuity and cross-reference leakage.

## Remaining P14 gate

P14 stays **PARTIAL / OWNER LISTENING REQUIRED** until the owner confirms:

- speaker identity is acceptably stable through the reviewed switches;
- no audible voice/reference leakage crosses speaker identities;
- overlap remains intelligible enough, or any failure is clearly audible/reviewable rather than silently wrong.

Machine evidence alone must not close this perceptual gate.
