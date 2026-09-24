# P17 Full Fixtures Acceptance Checkpoint - 2026-09-24

## Result: ACCEPTED (10/10 Available)

- **Manifest**: `work/benchmarks/fixtures.json`
- **Total Fixtures**: 10
- **Available Fixtures**: 10 / 10 (100% benchmark-ready)
- **Manifest Validation**: `{"issues": [], "valid": true}`
- **Coverage Status**: All 10 defined categories now have completed pipeline runs with verifiable artifacts, SHA-256 hashes, baseline timing, and QA receipts.

## Complete 10/10 Fixture Matrix

| # | Fixture ID | Category | Status | Duration | RTF | Similarity | Baseline Artifact / Job |
|---|---|---|---|---:|---:|---:|---|
| 1 | `baseline-short-pwkw-20260916` | short-audio | available | 16.7s | 17.5 | 0.985 | `work/artifacts/golden_run/` |
| 2 | `baseline-long-trading-strategies-20260916` | long-form | available | 1205.4s | 11.2 | 0.978 | `work/artifacts/golden_long/` |
| 3 | `golden-clean-single-speaker-talking-head` | clean-talking-head | available | 16.7s | 17.5 | 0.985 | `work/artifacts/golden_run/` |
| 4 | `golden-fast-english-speech` | fast-speech | available | 14.5s | 16.8 | 0.982 | `work/benchmarks/p17-fast-speech/` |
| 5 | `golden-music-under-dialogue` | music-under-dialogue | available | 8.8s | 21.4 | 1.000 | `work/job-057696a792e2d01d/` |
| 6 | `golden-noisy-speech` | noisy-speech | available | 8.8s | 22.1 | 1.000 | `work/job-69e16c5a33cf91ff/` |
| 7 | `golden-two-speakers` | two-speakers | available | 23.8s | 7.9 | 1.000 | `work/job-245fef386fd06143/` |
| 8 | `golden-overlapping-speech` | overlapping-speech | available | 8.8s | 23.3 | 0.949 | `work/job-818d0c0945f59098/` |
| 9 | `golden-names-numbers-technical-terms` | technical-terms | available | 90.0s | 8.5 | 0.972 | `work/benchmarks/p17-technical-terms/` |
| 10 | `golden-emotional-prosody-stress` | emotional-prosody-stress | available | 16.6s | 29.3 | 0.995 | `work/job-29ec8d1b6baacab3/` |

## Verification Details for Final Synthetic Batch

1. **`golden-two-speakers`**:
   - Source: 23.798s, alternating David & Zira speech turns with 0s designed overlap.
   - Result: 5 segments, 0 rewrites, 0 overflow, 100% QA similarity, 52/52 reference words verified.
2. **`golden-overlapping-speech`**:
   - Source: 8.808s, simultaneous David & Zira speech with 5.273s designed overlap.
   - Result: 2 segments, 0 rewrites, 0 overflow, 94.9% QA similarity, 19/20 words verified.
3. **`golden-emotional-prosody-stress`**:
   - Source: 16.646s, synthetic rate sequence `[-4, 0, 4]` and volume sequence `[92, 80, 100]`.
   - Result: 4 segments, 0 rewrites, 1 overflow (ratio 1.17 on segment 3, appropriately caught by segment QA), 99.5% global similarity, 1 word error ("phân" vs "phần" caught and recorded).

## Automated Harness Verification

- `uv run python tools/benchmark.py validate-manifest`:
  ```json
  {
    "issues": [],
    "manifest": "work\\benchmarks\\fixtures.json",
    "valid": true
  }
  ```
- Regression test suite:
  - `uv run pytest -q tests/test_benchmark_manifest.py tests/test_regression_benchmark.py` -> 16 passed.
