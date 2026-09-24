# Continuation checkpoint - 2026-09-23

## Resume/config recovery

- Legacy persisted snapshot `work/job-4c8236a32c685a11/resolved_config.json` originally stored `translation.glossary: ../glossary.yaml` without its source config base.
- Pipeline now preserves `_config_origin.base_dir` in resolved snapshots and performs a bounded legacy recovery only when the direct relative glossary path is missing and the legacy parent-base path actually exists.
- Regression coverage added for both the legacy recovery and explicit persisted origin.
- Focused config-origin tests: `2 passed`.

## Real resume receipt

Command resumed the existing content-addressed job with its persisted config snapshot and original output path. The run completed instead of failing at glossary resolution.

- job: `work/job-4c8236a32c685a11`
- source: `work/benchmarks/CP2-short-smart-source.mp4`
- output: `work/outputs/CP2-p12-current.mp4`
- final state: `completed / complete / 100%`
- elapsed: `366.21 s`
- RTF: `1.6198`
- global re-ASR similarity: `98.42%`
- segment QA: `3/50` final flags, `1` repair completed
- remaining flags are all actionable on this fixture:
  - segment 8: timing overflow
  - segment 21: missing critical token `cần`
  - segment 25: missing critical token `cần`

This run also rewrote the legacy snapshot with `_config_origin.base_dir = .../work`, so subsequent resumes no longer need the legacy heuristic for this job.

## P17 current quality receipt

`tools/benchmark.py summarize` over the completed job reports:

- WER: `0.0962567` (`54 / 561` word errors/reference words)
- critical-token accuracy: `0.945946` (`2 / 37` missing)
- semantic QA: `50` segments checked, `0` need review
- overflow rate: `0.02` (`1/50`)
- rewrite rate: `0.0`
- tempo p95: `1.20`

P17 remains PARTIAL because fixture coverage is still only 2/10 required classes and human listening / TypeSafe calibration evidence is still missing.

## Validation

- focused resume/config regression: `2 passed`
- focused P16/P01/P12/P17 suite before the live run: `60 passed`
- full suite after adding config-origin regression: `264 passed in 12.91s`

## Next acceptance work

1. Add representative P17 fixtures, starting from the existing local `qOHGZ6vCI5Q.mp4` clean talking-head source instead of fabricating coverage.
2. Complete P16 browser/manual selective-rerender acceptance against the current persisted job.
3. Add deterministic P20 live fault receipts where safe; keep reboot/power-loss and similar destructive cases manual/non-destructive.
4. Keep P18 blocked until P17 core acceptance and keep P19 blocked until explicit user opt-in for cloud/API/cost/privacy.

## Follow-up - P16 selective rerender receipt

The isolated clone `work/p16-e2e-clone-20260923-0300/job-4c8236a32c685a11` is retained for the browser/manual selective-rerender acceptance run.

- Browser edit/rerender path completed on the isolated clone rather than mutating the canonical real job.
- A post-run audit compared every file recorded in `original-baseline.json` against its pre-run SHA256.
- No pre-existing hashed artifact changed.
- The only new render artifacts were `tts/00022_raw.wav` and `tts/00022_raw.meta.json`.

This is narrow evidence that the selected raw TTS unit can be generated without rewriting the previously frozen upstream/downstream artifact set. P16 remains PARTIAL because the complete acceptance list still includes local + YouTube UI happy paths, source-slice playback, failed/interrupted CTA behavior, cancel semantics, and broader empty/loading/disabled/visual-state coverage.

## Follow-up - P17 clean talking-head fixture

The real 60-second talking-head source is now an `available` fixture in `work/benchmarks/fixtures.json`.

Cold legacy -> smart comparison (`work/benchmarks/p17-clean-talking-head/comparison-cold.json`):

- WER: `9.09% -> 7.07%`;
- critical-token accuracy: `100% -> 100%`;
- global re-ASR similarity: `97.90% -> 98.21%`;
- overflow: `0 -> 0`;
- smart segment QA: `0/13` failures;
- semantic shadow: `2/13` need review;
- tempo p95: `1.042 -> 1.097`;
- RTF: `9.33 -> 9.73`.

Quality improved on this fixture, but tempo and RTF regressed, so this receipt does not support a speed-win claim.

## Follow-up - P17 numeric / technical fixture

A second real fixture was derived without guessing the unresolved original-video path of the long job:

- the retained final video and `work/Trading_Strategies_That_Work__full_training_-7a8ee35de0/original.wav` were verified to differ by less than `0.01 s` over the full `3238.67 s` duration;
- a `90 s` slice beginning at `843.4 s` was remuxed with the original English audio into `work/benchmarks/p17-technical-terms/source-90s.mp4`;
- the slice contains dense trading numbers/terms such as `2,834%`, annual returns, win/loss percentages and drawdown.

Fresh smart run receipt:

- `20` segments;
- WER `33.21%` (`88 / 265` word errors/reference words);
- critical-token accuracy `96.30%` (`1 / 27` missing);
- global re-ASR similarity `97.19%`;
- overflow `0/20`;
- rewrite `1/20`;
- semantic shadow `1/20` need review;
- RTF `8.51`;
- tempo p95 `1.034`.

The remaining segment-QA failure is deliberately retained as a regression target: segment `9` expected "Thắng sáu mươi sáu phần trăm, thua ba mươi ba phần trăm." but re-ASR produced "thẳng 66%, thua 53%." and flagged the missing critical token `ba mươi ba phần trăm`. This is useful evidence that global similarity alone can hide a serious numeric error.

The fixture registry now covers `4/10` required P17 classes. Missing: fast English speech, music under dialogue, noisy speech, two speakers, overlapping speech, emotional/prosody stress.

## Follow-up - P20 safe fault injection

`tests/test_fault_contracts.py` now contains two stronger production-path receipts:

1. A real child process claims a job lease, persists `running/tts`, leaves a raw TTS partial file and is terminated by the parent. Recovery preserves the already committed translation manifest, never creates/accepts a TTS manifest for the partial file, reconciles the job to `paused`, and reclaims the stale lease.
2. Disk preflight is run against the live filesystem with an intentionally impossible minimum-free-space policy. It returns an actionable `disk` error and fails before pipeline work without actually filling the drive.

Focused fault-contract result: `6 passed`.

## Follow-up - P17 fail-closed gate

Manifest validation reports no path/hash issues. A relaxed-threshold self-comparison over all four available fixtures reports `thresholds_passed=true` but returns exit code `2` and `passed=false` because required fixture coverage is incomplete. This is intentional release behavior, not a benchmark failure.

Current missing fixture classes: `6/10`.
