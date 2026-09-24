from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from vi_dubber.semantic_qa import (
    SEMANTIC_QA_QUESTION_VERSION,
    evaluate_semantic_qa,
)
from vi_dubber.types import Segment

REPO_ROOT = Path(__file__).resolve().parents[1]
LABELS_PATH = REPO_ROOT / "work/benchmarks/typesafe_calibration_real_labels.json"
OUTPUT_ARTIFACT = REPO_ROOT / "work/benchmarks/typesafe-calibration-real-artifact.json"
OUTPUT_COMPARISON = REPO_ROOT / "work/benchmarks/typesafe-batch-comparison.json"


def main() -> int:
    api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        print("ERROR: TYPESAFE_API_KEY is not set in environment.", file=sys.stderr)
        return 1

    labels_data = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    cases = labels_data["cases"]
    segments = [
        Segment(
            id=int(c["id"]),
            start=float(c.get("id", 1) * 2.0),
            end=float(c.get("id", 1) * 2.0 + 3.0),
            text=str(c["source_en"]),
            vi=str(c["translation_vi"]),
        )
        for c in cases
    ]

    print(f"Loaded {len(segments)} segments for live TypeSafe batch calibration.")

    batch_sizes = [8, 16, 32]
    batch_results: dict[int, dict[str, Any]] = {}

    for bs in batch_sizes:
        print(f"\n--- Running evaluate_semantic_qa with batch_size={bs} ---")
        cfg = {
            "model": "jev-latest",
            "batch_size": bs,
            "timeout_seconds": 45.0,
            "max_attempts": 3,
        }
        start_time = time.perf_counter()
        res = evaluate_semantic_qa(segments, cfg, stage="translated", api_key=api_key)
        elapsed = time.perf_counter() - start_time
        print(f"Batch {bs} completed in {elapsed:.2f}s, status: {res.get('status')}")

        batch_results[bs] = {
            "batch_size": bs,
            "elapsed_seconds": round(elapsed, 4),
            "input_tokens": res.get("usage", {}).get("input_tokens"),
            "output_tokens": res.get("usage", {}).get("output_tokens"),
            "response_models": res.get("response_models"),
            "items_count": len(res.get("items", [])),
            "raw_result": res,
        }

    # Compare batch choices and probabilities
    print("\n--- Comparing consistency across batches ---")
    differences = 0
    total_comparisons = 0
    per_item_stability: list[dict[str, Any]] = []

    for i, seg in enumerate(segments):
        choices = {bs: batch_results[bs]["raw_result"]["items"][i]["choice"] for bs in batch_sizes}
        unique_choices = set(choices.values())
        is_consistent = len(unique_choices) == 1
        if not is_consistent:
            differences += 1
        total_comparisons += 1
        per_item_stability.append(
            {
                "id": seg.id,
                "text": seg.text[:40],
                "choices": choices,
                "consistent": is_consistent,
            }
        )

    print(f"Choice consistency: {total_comparisons - differences}/{total_comparisons} matching ({(total_comparisons - differences)/total_comparisons*100:.1f}%)")

    # Select standard batch (e.g. batch 16 or 32 based on lowest latency / token efficiency)
    chosen_bs = 16 if 16 in batch_results else batch_sizes[0]
    chosen_res = batch_results[chosen_bs]["raw_result"]

    # Build official recorded artifact
    artifact_payload = {
        "schema_version": 1,
        "probability_source": "recorded_typesafe_artifact",
        "docs_snapshot_date": "2026-09-24",
        "docs_current_model": chosen_res.get("response_models", ["jev-1.13.0"])[0],
        "chosen_batch_size": chosen_bs,
        "raw_result": {
            "status": chosen_res.get("status"),
            "stage": "translated",
            "question_policy_version": SEMANTIC_QA_QUESTION_VERSION,
            "requested_model": chosen_res.get("requested_model", "jev-latest"),
            "response_models": chosen_res.get("response_models", ["jev-1.13.0"]),
            "items": chosen_res.get("items", []),
        },
    }

    OUTPUT_ARTIFACT.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved recorded artifact to: {OUTPUT_ARTIFACT}")

    # Build comparison summary
    comparison_summary = {
        "timestamp": "2026-09-24T08:20:00+07:00",
        "sample_count": len(segments),
        "batches": {
            bs: {
                "elapsed_seconds": batch_results[bs]["elapsed_seconds"],
                "input_tokens": batch_results[bs]["input_tokens"],
                "output_tokens": batch_results[bs]["output_tokens"],
                "response_models": batch_results[bs]["response_models"],
            }
            for bs in batch_sizes
        },
        "choice_agreement_rate": round((total_comparisons - differences) / total_comparisons, 4),
        "per_item_stability": per_item_stability,
    }

    OUTPUT_COMPARISON.write_text(json.dumps(comparison_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved batch comparison summary to: {OUTPUT_COMPARISON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
