from __future__ import annotations

from copy import deepcopy

import pytest

from vi_dubber.profiles import DEFAULT_PROFILE, resolve_profile, validate_config
from vi_dubber.types import Segment, WordToken


def _base_config() -> dict:
    return {
        "profile": "balanced_best",
        "asr": {"batch_size": 4},
        "separation": {"enabled": True},
        "segmentation": {"mode": "legacy"},
        "translation": {},
        "tts": {},
        "timing": {
            "max_speedup": 1.25,
            "max_slowdown": 0.92,
            "rewrite_threshold": 1.25,
            "aggressive_rewrite_threshold": 1.4,
        },
        "mix": {"final_lufs": -14.0, "final_true_peak_db": -1.5},
        "qa": {"enabled": True, "semantic": {"max_attempts": 3}},
        "diarization": {},
    }


def test_profile_resolution_is_deterministic_and_does_not_mutate_source() -> None:
    source = _base_config()
    before = deepcopy(source)

    fast = resolve_profile(source, "fast")
    sweet = resolve_profile(source, "balanced")
    maximum = resolve_profile(source, "max")

    assert source == before
    assert fast["profile"] == "fast"
    assert fast["qa"]["enabled"] is False
    assert fast["profile_policy"]["retry_budget"] == 1
    assert sweet["profile"] == "balanced_fast"
    assert sweet["qa"]["segment_scope"] == "risk"
    assert sweet["reliability"]["final_full_qa"] is False
    assert maximum["profile"] == "max_quality"
    assert maximum["qa"]["semantic"]["review_critical_below"] == pytest.approx(0.82)


def test_balanced_fast_keeps_benchmarked_tts_batch_from_base_config() -> None:
    source = _base_config()
    source["tts"]["batch_size"] = 4

    resolved = resolve_profile(source, "balanced_fast")

    assert resolved["tts"]["batch_size"] == 4
    assert resolved["profile_policy"]["tts_batch_size"] == 4


def test_resolved_profile_behavior_contract_is_explicit_and_deterministic() -> None:
    source = _base_config()
    expected = {
        "fast": {
            "translation_fanout": 1,
            "translation_fanout_scope": "single",
            "translation_prefit": False,
            "typesafe_policy": "critical_gate",
            "tts_batch_policy": "throughput",
            "tts_batch_size": 1,
            "qa_depth": "minimal",
            "semantic_qa_enabled": False,
            "segment_qa_scope": "all",
            "retry_budget": 1,
            "final_full_qa": False,
        },
        "balanced_fast": {
            "translation_fanout": 1,
            "translation_fanout_scope": "single",
            "translation_prefit": True,
            "typesafe_policy": "verify_escalate",
            "tts_batch_policy": "quality_safe",
            "tts_batch_size": 1,
            "qa_depth": "risk",
            "semantic_qa_enabled": True,
            "segment_qa_scope": "risk",
            "retry_budget": 2,
            "final_full_qa": False,
        },
        "balanced_best": {
            "translation_fanout": 1,
            "translation_fanout_scope": "single",
            "translation_prefit": True,
            "typesafe_policy": "verify_escalate",
            "tts_batch_policy": "quality_safe",
            "tts_batch_size": 1,
            "qa_depth": "standard",
            "semantic_qa_enabled": True,
            "segment_qa_scope": "all",
            "retry_budget": 3,
            "final_full_qa": True,
        },
        "max_quality": {
            "translation_fanout": 2,
            "translation_fanout_scope": "hard_segments",
            "translation_prefit": True,
            "typesafe_policy": "strict_verify_escalate",
            "tts_batch_policy": "quality_safe",
            "tts_batch_size": 1,
            "qa_depth": "strict",
            "semantic_qa_enabled": True,
            "segment_qa_scope": "all",
            "retry_budget": 4,
            "final_full_qa": True,
        },
    }

    assert DEFAULT_PROFILE == "balanced_fast"
    for profile, contract in expected.items():
        first = resolve_profile(source, profile)["profile_policy"]
        second = resolve_profile(source, profile)["profile_policy"]
        assert first == second
        assert first["contract_version"] == 1
        assert first["name"] == profile
        assert first["optional_lip_sync"] is False
        assert first["overlap_assembly_policy"] == "equal_power"
        assert first["hard_case_review"] == "overlap_or_multi_speaker"
        for key, value in contract.items():
            assert first[key] == value


def test_segment_speaker_visibility_surfaces_word_level_multi_speaker_overlap() -> None:
    segment = Segment(
        id=7,
        start=0.0,
        end=1.2,
        text="hello there",
        speaker="SPEAKER_01",
        words=[
            WordToken("hello", 0.0, 0.5, speaker="SPEAKER_01"),
            WordToken("there", 0.4, 1.0, speaker="SPEAKER_02", overlap=True),
        ],
    )

    assert segment.speaker_visibility() == {
        "primary_speaker": "SPEAKER_01",
        "speakers": ["SPEAKER_01", "SPEAKER_02"],
        "speaker_count": 2,
        "multi_speaker": True,
        "overlap": True,
        "needs_review": True,
    }


def test_segment_speaker_visibility_is_stable_for_single_speaker_roundtrip() -> None:
    original = Segment(
        id=8,
        start=1.0,
        end=2.0,
        text="single speaker",
        speaker="SPEAKER_03",
        words=[WordToken("single", 1.0, 1.4, speaker="SPEAKER_03")],
    )
    restored = Segment.from_dict(original.to_dict())

    assert restored.speaker_visibility() == {
        "primary_speaker": "SPEAKER_03",
        "speakers": ["SPEAKER_03"],
        "speaker_count": 1,
        "multi_speaker": False,
        "overlap": False,
        "needs_review": False,
    }


def test_validate_config_rejects_invalid_ranges() -> None:
    config = resolve_profile(_base_config())
    validate_config(config)

    config["timing"]["max_speedup"] = 0.9
    with pytest.raises(ValueError, match="max_speedup"):
        validate_config(config)


def test_validate_config_rejects_unknown_overlap_policy() -> None:
    config = resolve_profile(_base_config())
    config["timing"]["overlap_policy"] = "overwrite"

    with pytest.raises(ValueError, match="overlap_policy"):
        validate_config(config)
