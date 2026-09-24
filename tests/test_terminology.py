from __future__ import annotations

import json
from pathlib import Path

from vi_dubber.terminology import (
    TerminologyGlossary,
    glossary_prompt_json,
    load_terminology_glossary,
    merge_pronunciation_map,
    validate_terminology_candidate,
    validate_terminology_segments,
)
from vi_dubber.translate import load_glossary
from vi_dubber.types import Segment


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _segment(source: str, translated: str) -> Segment:
    return Segment(id=7, start=0.0, end=2.0, text=source, vi=translated)


def test_real_glossary_keeps_legacy_mapping_and_loads_policy_metadata() -> None:
    glossary = load_glossary(PROJECT_ROOT / "glossary.yaml")

    assert isinstance(glossary, TerminologyGlossary)
    assert glossary["FVG"] == "FVG"
    assert glossary["liquidity sweep"] == "quét thanh khoản"
    assert glossary["bullish engulfing"] == "Bullish Engulfing"
    assert glossary.profile["register"] == "conversational/explanatory"
    policies = {entry.source: entry.policy for entry in glossary.entries}
    assert policies["bullish engulfing"] == "KEEP_EN"
    assert policies["order block"] == "PREFER_EN"
    assert policies["market structure"] == "VI"


def test_glossary_prompt_exposes_audience_and_policy_without_losing_display_map() -> None:
    glossary = load_terminology_glossary(PROJECT_ROOT / "glossary.yaml")
    payload = json.loads(glossary_prompt_json(glossary))

    assert payload["display_map"]["stop loss"] == "stop loss"
    assert "Vietnamese viewers familiar with trading" in payload["audience_profile"]["audience"]
    policy = {item["source"]: item for item in payload["terminology"]}
    assert policy["FVG"]["spoken"] == "ép vi gi"
    assert "nến nhấn chìm tăng giá" in policy["bullish engulfing"]["rejected"]


def test_keep_en_and_vi_policies_fail_closed_on_rejected_translation() -> None:
    glossary = load_terminology_glossary(PROJECT_ROOT / "glossary.yaml")

    rejected = validate_terminology_candidate(
        _segment(
            "This bullish engulfing confirms the setup.",
            "Cây nến nhấn chìm tăng giá này xác nhận setup.",
        ),
        "Cây nến nhấn chìm tăng giá này xác nhận setup.",
        glossary,
    )
    accepted = validate_terminology_candidate(
        _segment(
            "This bullish engulfing confirms the setup.",
            "Cây Bullish Engulfing này xác nhận setup.",
        ),
        "Cây Bullish Engulfing này xác nhận setup.",
        glossary,
    )
    translated_vi = validate_terminology_candidate(
        _segment("Wait for a liquidity sweep.", "Chờ một cú quét thanh khoản."),
        "Chờ một cú quét thanh khoản.",
        glossary,
    )

    assert rejected["passed"] is False
    assert {item["code"] for item in rejected["violations"]} == {
        "missing_approved_display",
        "rejected_form",
    }
    assert accepted["passed"] is True
    assert translated_vi["passed"] is True


def test_segment_gate_checks_only_terms_present_in_source() -> None:
    glossary = load_terminology_glossary(PROJECT_ROOT / "glossary.yaml")
    receipt = validate_terminology_segments(
        [
            _segment("Move the stop loss to breakeven.", "Dời stop loss về breakeven."),
            _segment("A generic sentence.", "Một câu bình thường."),
        ],
        glossary,
    )

    assert receipt["passed"] is True
    assert receipt["terms_checked"] == 2
    assert receipt["failed_segments"] == 0


def test_terminology_spoken_forms_feed_tts_without_changing_display() -> None:
    glossary = load_terminology_glossary(PROJECT_ROOT / "glossary.yaml")
    spoken = merge_pronunciation_map({"API": "a pi ai custom"}, glossary)

    assert spoken["FVG"] == "ép vi gi"
    assert spoken["BOS"] == "bi ô ét"
    assert spoken["API"] == "a pi ai custom"


def test_legacy_glossary_file_remains_supported(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text("FVG: ép vi gi\norder block: order block\n", encoding="utf-8")

    glossary = load_glossary(path)

    assert dict(glossary) == {"FVG": "ép vi gi", "order block": "order block"}
    assert isinstance(glossary, TerminologyGlossary)
    assert glossary.entries == ()


def test_code_switch_golden_set_enforces_all_approved_domain_terms() -> None:
    fixture = json.loads(
        (PROJECT_ROOT / "tests/fixtures/terminology_golden.json").read_text(encoding="utf-8")
    )
    glossary = load_terminology_glossary(PROJECT_ROOT / "glossary.yaml")

    assert fixture["schema_version"] == 1
    assert len(fixture["cases"]) >= 16
    for index, case in enumerate(fixture["cases"], start=1):
        accepted_segment = Segment(
            id=index,
            start=float(index),
            end=float(index + 2),
            text=case["source"],
            vi=case["accepted"],
        )
        accepted = validate_terminology_candidate(
            accepted_segment,
            case["accepted"],
            glossary,
        )
        rejected = validate_terminology_candidate(
            accepted_segment,
            case["rejected"],
            glossary,
        )
        assert accepted["passed"] is True, case["id"]
        assert rejected["passed"] is False, case["id"]
