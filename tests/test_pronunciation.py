from __future__ import annotations

import json
from pathlib import Path

import pytest

from vi_dubber.pronunciation import normalize_pronunciation


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "pronunciation_golden.json"


@pytest.mark.parametrize(
    "fixture",
    json.loads(GOLDEN_PATH.read_text(encoding="utf-8")),
    ids=lambda fixture: fixture["id"],
)
def test_pronunciation_golden_set(fixture: dict[str, object]) -> None:
    source = str(fixture["source"])
    result = normalize_pronunciation(
        source,
        fixture["pronunciation_map"],  # type: ignore[arg-type]
    )

    assert result.display_text == source
    assert result.tts_text == fixture["expected_tts"]
    assert [replacement.category for replacement in result.replacements] == fixture[
        "expected_categories"
    ]


@pytest.mark.parametrize(
    "source",
    [
        "Ngày 01/02/03",
        "Số 1,234",
        "Giá $12,50",
        "Phiên bản 1.2.3",
        "Ngày 2026-02-30",
        "Giờ 24:00",
        "Mã GPT-4 và SHA-256",
        "Mã 0012",
    ],
)
def test_ambiguous_or_invalid_formats_are_not_interpreted(source: str) -> None:
    result = normalize_pronunciation(source)

    assert result.display_text == source
    assert result.tts_text == source
    assert result.replacements == ()


def test_explicit_mapping_is_the_only_name_and_acronym_policy() -> None:
    source = "OpenAI dùng API, còn XYZ giữ nguyên."
    result = normalize_pronunciation(
        source,
        {"OpenAI": "ô pần ây ai", "API": "ây pi ai"},
    )

    assert result.display_text == source
    assert result.tts_text == "ô pần ây ai dùng ây pi ai, còn XYZ giữ nguyên."
    assert [item.original for item in result.replacements] == ["OpenAI", "API"]


def test_empty_pronunciation_override_cannot_delete_spoken_content() -> None:
    source = "Giữ API trong câu."
    result = normalize_pronunciation(source, {"API": ""})

    assert result.display_text == source
    assert result.tts_text == source
    assert result.replacements == ()
