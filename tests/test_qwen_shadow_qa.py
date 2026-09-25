from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from vi_dubber.qwen_shadow_qa import (
    QwenShadowCandidate,
    _transcribe_qwen_files,
    run_qwen_shadow_qa,
    select_qwen_shadow_candidates,
)


def test_select_shadow_candidates_prioritizes_acoustic_risk_and_skips_timing_only(
    tmp_path: Path,
) -> None:
    report = {
        "final": [
            {
                "segment_id": 1,
                "expected": "Bạn không bán BTC",
                "actual": "Bạn bán BTC",
                "similarity": 0.82,
                "passed": False,
                "reasons": ["missing_critical"],
                "actual_duration": 1.0,
                "critical_terms": ["BTC"],
            },
            {
                "segment_id": 2,
                "expected": "Một câu hơi khó nghe",
                "actual": "Một câu hơi khó nghe",
                "similarity": 0.86,
                "passed": True,
                "reasons": [],
                "actual_duration": 1.0,
            },
            {
                "segment_id": 3,
                "expected": "Câu dài",
                "actual": "Câu dài",
                "similarity": 1.0,
                "passed": False,
                "reasons": ["timing_overflow"],
                "actual_duration": 2.0,
            },
        ]
    }
    audio = {index: tmp_path / f"{index}.wav" for index in (1, 2, 3)}

    selected = select_qwen_shadow_candidates(report, audio, borderline_similarity=0.90)

    assert [item.segment_id for item in selected] == [1, 2]
    assert selected[0].critical_terms == ("BTC",)


def test_run_shadow_qa_reports_confirmation_and_disagreement(tmp_path: Path) -> None:
    candidates = [
        QwenShadowCandidate(
            segment_id=1,
            audio_path=tmp_path / "1.wav",
            expected="Bạn không bán BTC",
            primary_actual="Bạn bán BTC",
            primary_similarity=0.80,
            primary_passed=False,
            primary_reasons=("missing_critical",),
            actual_duration=1.0,
            critical_terms=("BTC",),
        ),
        QwenShadowCandidate(
            segment_id=2,
            audio_path=tmp_path / "2.wav",
            expected="Giữ vị thế",
            primary_actual="Giữ vị thế",
            primary_similarity=0.85,
            primary_passed=True,
            primary_reasons=(),
            actual_duration=1.0,
        ),
    ]

    report = run_qwen_shadow_qa(
        candidates,
        transcriber=lambda paths, config: ["Bạn không bán BTC", "Đóng vị thế"],
    )

    assert report["blocking"] is False
    assert report["mode"] == "shadow"
    assert [item["disposition"] for item in report["items"]] == [
        "disputed_primary_failure",
        "shadow_only_failure",
    ]
    assert report["summary"] == {
        "segments_checked": 2,
        "confirmed_primary_failures": 0,
        "disputed_primary_failures": 1,
        "shadow_only_failures": 1,
    }


def test_qwen_transcriber_uses_optional_offline_package_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class FakeResult:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeModel:
        @classmethod
        def from_pretrained(cls, model_name: str, **kwargs):
            calls["model_name"] = model_name
            calls["load"] = kwargs
            return cls()

        def transcribe(self, **kwargs):
            calls["transcribe"] = kwargs
            return [FakeResult("xin chào"), FakeResult("thế giới")]

    fake_qwen = types.ModuleType("qwen_asr")
    fake_qwen.Qwen3ASRModel = FakeModel
    monkeypatch.setitem(sys.modules, "qwen_asr", fake_qwen)

    texts = _transcribe_qwen_files(
        [tmp_path / "a.wav", tmp_path / "b.wav"],
        {
            "model": "Qwen/Qwen3-ASR-0.6B",
            "device": "cpu",
            "dtype": "float32",
            "batch_size": 2,
            "language": "Vietnamese",
        },
    )

    assert texts == ["xin chào", "thế giới"]
    assert calls["model_name"] == "Qwen/Qwen3-ASR-0.6B"
    assert calls["transcribe"] == {
        "audio": [str(tmp_path / "a.wav"), str(tmp_path / "b.wav")],
        "language": ["Vietnamese", "Vietnamese"],
    }


def test_shadow_qa_empty_selection_does_not_import_qwen() -> None:
    report = run_qwen_shadow_qa([])

    assert report["items"] == []
    assert report["summary"]["segments_checked"] == 0
