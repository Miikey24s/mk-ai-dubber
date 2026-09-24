import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "p03_p09_long_analysis.py"

_SPEC = importlib.util.spec_from_file_location("p03_p09_long_analysis", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_TOOL)

analyze_long_artifacts = _TOOL.analyze_long_artifacts


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _config(path: Path) -> Path:
    path.write_text(
        """timing:
  max_speedup: 1.25
  max_slowdown: 0.92
  rewrite_threshold: 1.25
  segment_pad_ms: 35
  max_borrow_seconds: 0.35
""",
        encoding="utf-8",
    )
    return path


def test_long_analysis_is_fail_closed_without_word_timing_or_smart_runtime(tmp_path: Path) -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 0.8, "text": "a", "speaker": "S"},
        {"id": 1, "start": 1.0, "end": 3.0, "text": "b", "speaker": "S"},
    ]
    stats = [
        {
            "segment_id": 0,
            "target_duration": 0.8,
            "generated_duration": 1.2,
            "final_duration": 0.96,
            "tempo": 1.25,
            "rewrites": 1,
        },
        {
            "segment_id": 1,
            "target_duration": 2.0,
            "generated_duration": 1.8,
            "final_duration": 1.8,
            "tempo": 1.0,
            "rewrites": 0,
        },
    ]
    result = {
        "duration_seconds": 3.0,
        "segments": 2,
        "rewritten_segments": 1,
        "overflow_segments": 1,
    }

    report = analyze_long_artifacts(
        segments_path=_write_json(tmp_path / "segments.json", segments),
        tts_stats_path=_write_json(tmp_path / "tts.json", stats),
        result_path=_write_json(tmp_path / "result.json", result),
        config_path=_config(tmp_path / "config.yaml"),
    )

    assert report["baseline"]["word_tokens"] == 0
    assert report["baseline"]["rewrite"]["segments"] == 1
    assert report["baseline"]["overflow"]["segments"] == 1
    assert report["baseline"]["segment_duration_seconds"]["lt_1s"] == 1
    assert report["smart_long_ab"]["status"] == "blocked"
    assert report["acceptance"]["p03_can_move_from_partial"] is False
    assert report["acceptance"]["p09_can_move_from_partial"] is False
    assert report["current_timing_counterfactual"]["acceptance_evidence"] is False


def test_long_analysis_rejects_baseline_count_mismatch(tmp_path: Path) -> None:
    segments_path = _write_json(
        tmp_path / "segments.json",
        [{"id": 0, "start": 0.0, "end": 1.0, "text": "a", "speaker": "S"}],
    )
    stats_path = _write_json(tmp_path / "tts.json", [])
    result_path = _write_json(
        tmp_path / "result.json",
        {"segments": 1, "rewritten_segments": 0, "overflow_segments": 0},
    )

    with pytest.raises(ValueError, match="baseline segment/tts count mismatch"):
        analyze_long_artifacts(
            segments_path=segments_path,
            tts_stats_path=stats_path,
            result_path=result_path,
            config_path=_config(tmp_path / "config.yaml"),
        )


def test_long_analysis_marks_complete_smart_set_available_but_requires_words(tmp_path: Path) -> None:
    baseline_segments = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "a", "speaker": "S"}
    ]
    baseline_stats = [
        {
            "segment_id": 0,
            "target_duration": 1.0,
            "generated_duration": 1.0,
            "final_duration": 1.0,
            "tempo": 1.0,
            "rewrites": 0,
        }
    ]
    baseline_result = {
        "duration_seconds": 1.0,
        "segments": 1,
        "rewritten_segments": 0,
        "overflow_segments": 0,
    }
    smart_segments = [
        {
            "id": 0,
            "start": 0.0,
            "end": 1.0,
            "text": "a",
            "speaker": "S",
            "words": [{"text": "a", "start": 0.0, "end": 1.0}],
        }
    ]

    report = analyze_long_artifacts(
        segments_path=_write_json(tmp_path / "segments.json", baseline_segments),
        tts_stats_path=_write_json(tmp_path / "tts.json", baseline_stats),
        result_path=_write_json(tmp_path / "result.json", baseline_result),
        config_path=_config(tmp_path / "config.yaml"),
        smart_segments_path=_write_json(tmp_path / "smart-segments.json", smart_segments),
        smart_tts_stats_path=_write_json(tmp_path / "smart-tts.json", baseline_stats),
        smart_result_path=_write_json(tmp_path / "smart-result.json", baseline_result),
    )

    assert report["smart_long_ab"]["status"] == "available"
    assert report["smart_long_ab"]["machine_verifiable"] is True
    assert report["smart_long_ab"]["runtime"]["word_tokens"] == 1
