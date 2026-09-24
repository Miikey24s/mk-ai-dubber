from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vi_dubber.semantic_qa import (
    DEFAULT_FALSE_PASS_COST,
    DEFAULT_FALSE_RETRY_COST,
    SEMANTIC_QA_QUESTION_VERSION,
    select_semantic_retry_threshold,
    semantic_model_pin_status,
    semantic_retry_risk_score,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABELS = ROOT / "tests" / "fixtures" / "semantic_calibration_labels.json"
DEFAULT_PROBABILITIES = ROOT / "tests" / "fixtures" / "semantic_calibration_probabilities.json"


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _raw_result(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("raw_result", data)
    if not isinstance(raw, dict):
        raise ValueError("semantic probability input has no raw_result object")
    if raw.get("status") != "ok":
        raise ValueError("semantic probability input must contain an ok raw result")
    items = raw.get("items")
    if not isinstance(items, list):
        raise ValueError("semantic probability input has no items list")
    return raw


def _merge_samples(labels: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    cases = labels.get("cases")
    items = raw.get("items")
    if not isinstance(cases, list) or not isinstance(items, list):
        raise ValueError("labels or semantic raw result is missing cases/items")
    by_id: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or isinstance(item.get("id"), bool) or not isinstance(item.get("id"), int):
            raise ValueError("semantic probability item has invalid id")
        item_id = int(item["id"])
        if item_id in by_id:
            raise ValueError(f"duplicate semantic probability id: {item_id}")
        by_id[item_id] = item

    samples: list[dict[str, Any]] = []
    label_ids: set[int] = set()
    for case in cases:
        if not isinstance(case, dict) or isinstance(case.get("id"), bool) or not isinstance(case.get("id"), int):
            raise ValueError("calibration label has invalid id")
        case_id = int(case["id"])
        if case_id in label_ids:
            raise ValueError(f"duplicate calibration label id: {case_id}")
        label_ids.add(case_id)
        if case_id not in by_id:
            raise ValueError(f"missing semantic probabilities for label id {case_id}")
        samples.append({**case, **by_id[case_id]})

    extras = sorted(set(by_id) - label_ids)
    if extras:
        raise ValueError(f"semantic probabilities contain unlabeled ids: {extras}")
    return samples


def _coverage(labels: dict[str, Any]) -> dict[str, Any]:
    required = labels.get("required_plan_classes")
    cases = labels.get("cases")
    if not isinstance(required, list) or not isinstance(cases, list):
        raise ValueError("labels must contain required_plan_classes and cases")
    present = sorted(
        {
            str(case.get("class"))
            for case in cases
            if isinstance(case, dict) and case.get("class") is not None
        }
    )
    missing = sorted(set(map(str, required)) - set(present))
    return {"required": list(map(str, required)), "present": present, "missing": missing}


def build_receipt(
    labels: dict[str, Any],
    probability_data: dict[str, Any],
    *,
    probability_source: str,
    false_pass_cost: float,
    false_retry_cost: float,
) -> dict[str, Any]:
    raw = _raw_result(probability_data)
    samples = _merge_samples(labels, raw)
    coverage = _coverage(labels)
    if coverage["missing"]:
        raise ValueError(f"golden set is missing PLAN classes: {coverage['missing']}")
    question_version = int(raw.get("question_policy_version") or 0)
    if question_version != SEMANTIC_QA_QUESTION_VERSION:
        raise ValueError(
            "semantic question policy mismatch: "
            f"artifact={question_version}, code={SEMANTIC_QA_QUESTION_VERSION}"
        )

    threshold = select_semantic_retry_threshold(
        samples,
        false_pass_cost=false_pass_cost,
        false_retry_cost=false_retry_cost,
    )
    selected_threshold = float(threshold["selected"]["threshold"])
    severe = [sample for sample in samples if bool(sample.get("severe_semantic_error"))]
    awkward = [sample for sample in samples if bool(sample.get("awkward_but_semantically_correct"))]
    severe_caught = sum(semantic_retry_risk_score(sample) >= selected_threshold for sample in severe)
    awkward_retried = sum(semantic_retry_risk_score(sample) >= selected_threshold for sample in awkward)
    label_provenance = str(labels.get("label_provenance") or "unknown")
    pin = semantic_model_pin_status(
        raw,
        label_provenance=label_provenance,
        probability_source=probability_source,
    )
    production_threshold_lock_allowed = bool(
        pin["production_pinning_ready"] and not coverage["missing"]
    )

    return {
        "fixture_set": str(labels.get("fixture_set") or "unknown"),
        "sample_count": len(samples),
        "label_provenance": label_provenance,
        "probability_source": probability_source,
        "question_policy_version": question_version,
        "coverage": coverage,
        "cost_policy": threshold["cost_policy"],
        "offline_candidate_threshold": selected_threshold,
        "selected_metrics": threshold["selected"],
        "severe_semantic_error": {
            "count": len(severe),
            "caught": severe_caught,
            "recall": severe_caught / len(severe) if severe else 1.0,
        },
        "awkward_but_semantically_correct": {
            "count": len(awkward),
            "sent_to_retry": awkward_retried,
        },
        "model_pinning": pin,
        "production_threshold_lock_allowed": production_threshold_lock_allowed,
        "production_note": (
            "Offline/synthetic calibration validates the harness only; keep production thresholds and "
            "model defaults unchanged until human-labeled recorded TypeSafe evidence passes this contract."
            if not production_threshold_lock_allowed
            else "Recorded human-labeled evidence is eligible for production threshold/model review."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline TypeSafe semantic-QA calibration. This command never calls the TypeSafe API."
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--artifact", type=Path, help="Recorded semantic QA stage/raw artifact")
    source.add_argument("--probabilities", type=Path, default=DEFAULT_PROBABILITIES)
    parser.add_argument("--false-pass-cost", type=float, default=DEFAULT_FALSE_PASS_COST)
    parser.add_argument("--false-retry-cost", type=float, default=DEFAULT_FALSE_RETRY_COST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    labels = _load_json(args.labels)
    probability_path = args.artifact or args.probabilities
    if probability_path is None:
        raise ValueError("provide --artifact or --probabilities")
    probability_data = _load_json(probability_path)
    probability_source = (
        "recorded_typesafe_artifact"
        if args.artifact is not None
        else str(probability_data.get("probability_source") or "fixture_probabilities")
    )
    receipt = build_receipt(
        labels,
        probability_data,
        probability_source=probability_source,
        false_pass_cost=args.false_pass_cost,
        false_retry_cost=args.false_retry_cost,
    )
    rendered = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
