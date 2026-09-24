from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "p04_quality_contract.json"
TOOL_PATH = REPO_ROOT / "tools" / "p04_quality_receipt.py"

_SPEC = importlib.util.spec_from_file_location("vi_dubber_p04_quality_receipt", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_TOOL)


def test_quality_contract_receipt_passes_without_claiming_human_quality() -> None:
    receipt = _TOOL.build_receipt(FIXTURE_PATH, REPO_ROOT)

    assert receipt["passed"] is True
    assert receipt["sections"] == {
        "context": True,
        "duration_fit": True,
        "glossary_snapshot": True,
        "golden_checker_conformance": True,
        "retained_artifacts": True,
        "retained_ab_critical_fact_parity": True,
    }
    assert receipt["scope"] == {
        "machine_verifiable_only": True,
        "naturalness_assessed": False,
        "semantic_equivalence_assessed": False,
        "human_ab_required": True,
    }
    assert set(receipt["category_case_counts"]) == {
        "critical_fact",
        "glossary",
        "modality",
        "name",
        "negation",
        "number",
    }


def test_golden_negative_cases_prove_each_critical_gate_fails_closed() -> None:
    receipt = _TOOL.build_receipt(FIXTURE_PATH, REPO_ROOT)
    cases = {item["id"]: item for item in receipt["quality_cases"]}

    for case_id in (
        "glossary-missing-negative",
        "name-missing-negative",
        "number-missing-negative",
        "negation-dropped-negative",
        "modality-dropped-negative",
        "critical-direction-swapped-negative",
    ):
        assert cases[case_id]["expected_pass"] is False
        assert cases[case_id]["actual_pass"] is False
        assert cases[case_id]["conforms"] is True
        assert cases[case_id]["failures"]


def test_retained_artifacts_are_hash_locked_and_cover_labeled_facts() -> None:
    receipt = _TOOL.build_receipt(FIXTURE_PATH, REPO_ROOT)

    assert len(receipt["retained_artifacts"]) == 5
    assert all(item["actual_sha256"] == item["expected_sha256"] for item in receipt["retained_artifacts"])
    assert sum(len(item["cases"]) for item in receipt["retained_artifacts"]) == 20
    assert all(item["passed"] for item in receipt["retained_artifacts"])

    comparison = receipt["ab_comparisons"][0]
    assert comparison["claim"] == "labeled_critical_fact_parity_only"
    assert comparison["passed"] is True
    assert comparison["before"]["categories"] == ["modality", "name", "negation", "number"]
    assert comparison["after"]["categories"] == ["modality", "name", "negation", "number"]


def test_retained_artifact_hash_mismatch_fails_receipt(tmp_path: Path) -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture["retained_artifacts"][0]["sha256"] = "0" * 64
    changed_fixture = tmp_path / "changed-contract.json"
    changed_fixture.write_text(json.dumps(fixture), encoding="utf-8")

    receipt = _TOOL.build_receipt(changed_fixture, REPO_ROOT)

    assert receipt["passed"] is False
    assert receipt["sections"]["retained_artifacts"] is False
    assert receipt["retained_artifacts"][0]["failures"] == ["sha256_mismatch"]


def test_context_and_duration_cases_exercise_production_contract() -> None:
    receipt = _TOOL.build_receipt(FIXTURE_PATH, REPO_ROOT)

    context = receipt["context_cases"][0]
    assert context["actual"][0]["before_translation_vi"] == ["Cau dau"]
    assert context["actual"][1]["before_translation_vi"] == []
    assert [item["actual"]["fit"] for item in receipt["duration_fit_cases"]] == [
        "unknown",
        "comfortable",
        "tight",
        "likely_overflow",
    ]
