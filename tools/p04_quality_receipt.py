from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from vi_dubber.translate import build_translation_payload, duration_fit_hint
from vi_dubber.types import Segment


SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("đ", "d").replace("Đ", "D")
    return " ".join(text.casefold().split())


def evaluate_text_checks(text: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = _normalized(text)
    failures: list[dict[str, Any]] = []
    categories: list[str] = []
    for check in checks:
        category = str(check["category"])
        categories.append(category)
        missing = [str(value) for value in check.get("all_of", []) if _normalized(value) not in normalized]
        alternatives = [str(value) for value in check.get("any_of", [])]
        if alternatives and not any(_normalized(value) in normalized for value in alternatives):
            missing.append("any_of:" + "|".join(alternatives))
        for value, minimum in dict(check.get("min_occurrences", {})).items():
            actual = normalized.count(_normalized(value))
            if actual < int(minimum):
                missing.append(f"{value}:expected>={int(minimum)},actual={actual}")
        if missing:
            failures.append({"category": category, "missing": missing})
    return {
        "passed": not failures,
        "categories": sorted(set(categories)),
        "failures": failures,
    }


def _safe_project_path(project_root: Path, relative_path: str) -> Path:
    root = project_root.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Path escapes project root: {relative_path}")
    return path


def _context_receipts(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for case in cases:
        segments = [Segment.from_dict(item) for item in case["segments"]]
        payload = build_translation_payload(
            segments,
            offset=int(case["offset"]),
            count=int(case["count"]),
            context_window=int(case["context_window"]),
        )
        actual = []
        for item in payload:
            actual.append(
                {
                    "id": item["id"],
                    "speaker": item["speaker"],
                    "target_duration_sec": item["target_duration_sec"],
                    "before_ids": [entry["id"] for entry in item["context_before"]],
                    "after_ids": [entry["id"] for entry in item["context_after"]],
                    "before_translation_vi": [
                        entry["translation_vi"]
                        for entry in item["context_before"]
                        if "translation_vi" in entry
                    ],
                }
            )
        receipts.append({"id": case["id"], "passed": actual == case["expected"], "actual": actual})
    return receipts


def _duration_receipts(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for case in cases:
        hint = duration_fit_hint(str(case["text"]), float(case["target_seconds"]))
        receipts.append(
            {
                "id": case["id"],
                "passed": hint["fit"] == case["expected_fit"],
                "expected_fit": case["expected_fit"],
                "actual": hint,
            }
        )
    return receipts


def _glossary_receipt(project_root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    path = _safe_project_path(project_root, str(contract["path"]))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms = raw.get("terms", raw)
    expected = dict(contract["expected_terms"])
    mismatches = {
        key: {"expected": value, "actual": terms.get(key)}
        for key, value in expected.items()
        if _normalized(terms.get(key)) != _normalized(value)
    }
    return {
        "path": path.relative_to(project_root.resolve()).as_posix(),
        "sha256": sha256_file(path),
        "terms_checked": len(expected),
        "passed": not mismatches,
        "mismatches": mismatches,
    }


def _quality_receipts(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for case in cases:
        evaluation = evaluate_text_checks(str(case["candidate_vi"]), list(case["checks"]))
        expected_pass = bool(case["expected_pass"])
        receipts.append(
            {
                "id": case["id"],
                "expected_pass": expected_pass,
                "actual_pass": evaluation["passed"],
                "conforms": evaluation["passed"] is expected_pass,
                "categories": evaluation["categories"],
                "failures": evaluation["failures"],
            }
        )
    return receipts


def _retained_artifact_receipts(
    project_root: Path,
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for artifact in artifacts:
        path = _safe_project_path(project_root, str(artifact["path"]))
        expected_hash = str(artifact["sha256"]).lower()
        actual_hash = sha256_file(path) if path.is_file() else None
        artifact_failures: list[str] = []
        case_receipts: list[dict[str, Any]] = []
        segments_by_id: dict[int, dict[str, Any]] = {}
        if actual_hash != expected_hash:
            artifact_failures.append("sha256_mismatch")
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                segments_by_id = {
                    int(item["id"]): item
                    for item in payload
                    if isinstance(item, dict) and "id" in item
                }
            else:
                artifact_failures.append("artifact_not_segment_list")
        else:
            artifact_failures.append("artifact_missing")

        for case in artifact["cases"]:
            segment_id = int(case["segment_id"])
            segment = segments_by_id.get(segment_id)
            failures: list[str] = []
            evaluation = {"passed": False, "categories": [], "failures": []}
            if segment is None:
                failures.append("segment_missing")
            else:
                if _normalized(segment.get("text")) != _normalized(case["source"]):
                    failures.append("source_mismatch")
                if _normalized(segment.get("vi")) != _normalized(case["vi"]):
                    failures.append("translation_mismatch")
                evaluation = evaluate_text_checks(str(segment.get("vi") or ""), list(case["checks"]))
                if not evaluation["passed"]:
                    failures.append("labeled_check_failed")
            case_receipts.append(
                {
                    "segment_id": segment_id,
                    "passed": not failures,
                    "categories": evaluation["categories"],
                    "failures": failures + evaluation["failures"],
                }
            )

        receipts.append(
            {
                "id": artifact["id"],
                "path": path.relative_to(project_root.resolve()).as_posix(),
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
                "passed": not artifact_failures and all(case["passed"] for case in case_receipts),
                "failures": artifact_failures,
                "cases": case_receipts,
            }
        )
    return receipts


def _ab_receipts(
    artifacts: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    artifacts_by_id = {str(item["id"]): item for item in artifacts}
    receipts: list[dict[str, Any]] = []
    for comparison in comparisons:
        required = set(map(str, comparison["required_categories"]))
        sides: dict[str, Any] = {}
        for side in ("before", "after"):
            artifact_id = str(comparison[f"{side}_artifact_id"])
            segment_ids = {int(value) for value in comparison[f"{side}_segment_ids"]}
            artifact = artifacts_by_id.get(artifact_id)
            selected = (
                [case for case in artifact["cases"] if int(case["segment_id"]) in segment_ids]
                if artifact is not None
                else []
            )
            categories = {
                category
                for case in selected
                for category in case["categories"]
            }
            failures: list[str] = []
            if artifact is None:
                failures.append("artifact_missing")
            elif not artifact["passed"]:
                failures.append("artifact_failed")
            if {int(case["segment_id"]) for case in selected} != segment_ids:
                failures.append("labeled_segments_missing")
            missing_categories = sorted(required - categories)
            if missing_categories:
                failures.append("categories_missing:" + ",".join(missing_categories))
            if any(not case["passed"] for case in selected):
                failures.append("labeled_case_failed")
            sides[side] = {
                "artifact_id": artifact_id,
                "segment_ids": sorted(segment_ids),
                "categories": sorted(categories),
                "passed": not failures,
                "failures": failures,
            }
        receipts.append(
            {
                "id": comparison["id"],
                "claim": "labeled_critical_fact_parity_only",
                "required_categories": sorted(required),
                "passed": sides["before"]["passed"] and sides["after"]["passed"],
                "before": sides["before"],
                "after": sides["after"],
            }
        )
    return receipts


def build_receipt(fixture_path: Path, project_root: Path) -> dict[str, Any]:
    fixture_path = fixture_path.resolve()
    project_root = project_root.resolve()
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported quality contract schema: {fixture.get('schema_version')!r}")

    scope = fixture.get("scope")
    required_scope = {
        "machine_verifiable_only": True,
        "naturalness_assessed": False,
        "semantic_equivalence_assessed": False,
        "human_ab_required": True,
    }
    if scope != required_scope:
        raise ValueError("Quality contract scope must explicitly keep naturalness and semantic A/B open")

    context = _context_receipts(list(fixture["context_cases"]))
    duration = _duration_receipts(list(fixture["duration_fit_cases"]))
    glossary = _glossary_receipt(project_root, dict(fixture["glossary"]))
    quality = _quality_receipts(list(fixture["quality_cases"]))
    retained = _retained_artifact_receipts(project_root, list(fixture["retained_artifacts"]))
    ab_comparisons = _ab_receipts(retained, list(fixture["ab_comparisons"]))

    category_counts: Counter[str] = Counter()
    for case in quality:
        category_counts.update(case["categories"])
    for artifact in retained:
        for case in artifact["cases"]:
            category_counts.update(case["categories"])

    sections = {
        "context": all(item["passed"] for item in context),
        "duration_fit": all(item["passed"] for item in duration),
        "glossary_snapshot": glossary["passed"],
        "golden_checker_conformance": all(item["conforms"] for item in quality),
        "retained_artifacts": all(item["passed"] for item in retained),
        "retained_ab_critical_fact_parity": all(item["passed"] for item in ab_comparisons),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_id": fixture["contract_id"],
        "fixture_sha256": sha256_file(fixture_path),
        "scope": scope,
        "passed": all(sections.values()),
        "sections": sections,
        "category_case_counts": dict(sorted(category_counts.items())),
        "context_cases": context,
        "duration_fit_cases": duration,
        "glossary": glossary,
        "quality_cases": quality,
        "retained_artifacts": retained,
        "ab_comparisons": ab_comparisons,
        "limitations": [
            "Labeled substring checks do not establish full semantic equivalence.",
            "The retained A/B receipt establishes labeled critical-fact parity, not translation superiority.",
            "Duration-fit output is a deterministic proxy with provisional calibration.",
            "Naturalness and listening quality require human blind A/B evidence.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the offline P04 quality-contract receipt.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/p04_quality_contract.json"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    receipt = build_receipt(args.fixture, args.root)
    rendered = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
