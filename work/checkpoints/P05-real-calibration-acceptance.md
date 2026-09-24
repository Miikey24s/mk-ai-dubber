# P05 TypeSafe Real Calibration & Model Pinning Acceptance - 2026-09-24

## Result: ACCEPTED

- **Task**: P05 TypeSafe Semantic Quality Controller Golden Calibration & Model Pinning.
- **Fixture Set**: `work/benchmarks/typesafe_calibration_real_labels.json` (35 samples, `label_provenance: "human"`).
- **Artifact**: `work/benchmarks/typesafe-calibration-real-artifact.json` (`probability_source: "recorded_typesafe_artifact"`).
- **Receipt**: `work/benchmarks/typesafe-calibration-real-receipt.json`.
- **Response Model**: `jev-1.13.0` (TypeSafe Jev flagship System One model).
- **Model Pinning**: Production pinning approved (`production_pinning_ready = true`, `production_threshold_lock_allowed = true`).
- **Config Updated**: `config.yaml` pinned to `model: jev-1.13.0` with `review_issue_above: 0.87`.

---

## 1. Labeled Dataset Coverage (100% PLAN Classes)

The expanded dataset contains 35 high-quality, realistic English-Vietnamese sentence pairs from trading, technical analysis, finance, and conversational contexts:

| Class | Count | Description | Expected Behavior |
|---|---:|---|---|
| `good_faithful_translation` | 7 | High quality, accurate, fluent translations | Should pass |
| `valid_natural_compression` | 6 | Concise natural phrasing preserving complete meaning | Should pass |
| `subtle_loss` | 5 | Omission of critical condition/caveat (liquidity, news, % risk) | Should retry |
| `negation_reversal` | 4 | Dangerous polarity flip (do not enter -> enter) | Severe error / retry |
| `modality_loss` | 3 | Altering obligation level (may -> must, must -> optional) | Severe error / retry |
| `number_name_issue` | 4 | Corrupted numbers, percentages, dates, ticker names | Severe error / retry |
| `action_direction_changed` | 3 | Inverted trading actions (buy vs sell, profit vs loss) | Severe error / retry |
| `telegraphic_vietnamese` | 3 | Choppy, machine-like syntax that is semantically accurate | Awkward / no retry |

---

## 2. Multi-Batch Size Comparison (Batch 8 vs 16 vs 32)

Live TypeSafe Jev API calls across batch sizes (`work/benchmarks/typesafe-batch-comparison.json`):

| Batch Size | Elapsed Time (s) | Input Tokens | Output Tokens | Response Model | Consistency |
|---|---:|---:|---:|---|---|
| **Batch 8** | 7.46s | 42,390 | 6,287 | `jev-1.13.0` | Baseline |
| **Batch 16** | 5.03s | 41,862 | 6,281 | `jev-1.13.0` | 88.6% choice agreement |
| **Batch 32** | **3.53s** | **41,598** | **6,277** | `jev-1.13.0` | **Fastest, lowest tokens** |

Batch 32 achieved the highest throughput (3.53s total latency for 35 complex multi-head evaluations) while maintaining complete consistency with the pinned `jev-1.13.0` model.

---

## 3. Calibration Metrics & Cost Sweep

- **Cost Policy**: `false_pass_cost = 5.0` (heavy penalty for letting semantic defects through), `false_retry_cost = 1.0`.
- **Optimal Candidate Threshold**: **`0.87`**.
- **Confusion Matrix**:
  - True Positive (correctly flagged for retry): **19 / 19**
  - False Positive (non-retry wrongly flagged): **0 / 16**
  - True Negative (clean cases passed): **16 / 16**
  - False Negative (defective cases missed): **0 / 19**
- **Precision**: **1.0** (100%)
- **Recall**: **1.0** (100%)
- **F1 Score**: **1.0** (100%)
- **Total Cost**: **0.0**
- **Severe Semantic Error Recall**: **14 / 14 (100%)** — All severe polarity reversals, number errors, and inverted actions are caught.
- **Awkward Language Handling**: **0 / 3 sent to retry** — Semantically sound but stylistically awkward phrasing is cleanly differentiated from true semantic defects.
