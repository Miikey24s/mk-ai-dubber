import importlib.util
from pathlib import Path
import sys

import pytest
import yaml


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_p23_autotune.py"
SPEC = importlib.util.spec_from_file_location("benchmark_p23_autotune", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark_p23_autotune = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark_p23_autotune
SPEC.loader.exec_module(benchmark_p23_autotune)
_select_recommendation = benchmark_p23_autotune._select_recommendation
_baseline_gate_failed = benchmark_p23_autotune._baseline_gate_failed
_assert_variant_resolved_config = benchmark_p23_autotune._assert_variant_resolved_config
_write_variant_config = benchmark_p23_autotune._write_variant_config
parse_variant = benchmark_p23_autotune.parse_variant


def _run(name: str, wall: float, *, status: str = "passed", source: str = "same source", vi: str = "cùng bản dịch") -> dict:
    return {
        "variant": {"name": name},
        "status": status,
        "wall_seconds": wall,
        "source_script": source,
        "translated_script": vi,
    }


def test_parse_variant_requires_all_tuning_axes() -> None:
    variant = parse_variant("candidate:asr=8,tts=4,translation=2,chunk=1200")
    assert variant.name == "candidate"
    assert variant.asr_batch == 8
    assert variant.tts_batch == 4
    assert variant.translation_concurrency == 2
    assert variant.chunk_target_seconds == 1200


def test_variant_config_forces_default_fixture_through_multi_chunk_path(tmp_path: Path) -> None:
    base = tmp_path / "config.yaml"
    base.write_text(
        yaml.safe_dump(
            {
                "asr": {"batch_size": 4},
                "tts": {"batch_size": 4},
                "translation": {"glossary": "glossary.yaml", "webgpt_concurrency": 2},
                "longform": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    target = tmp_path / "candidate.yaml"
    variant = parse_variant("baseline:asr=4,tts=4,translation=2,chunk=1500")

    _write_variant_config(base, target, variant)

    config = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert config["longform"]["target_seconds"] == 1500
    assert config["longform"]["max_seconds"] == 1800
    assert config["longform"]["max_seconds"] < 1987.202


def test_variant_tuning_axes_survive_profile_resolution(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    target = tmp_path / "candidate.yaml"
    variant = parse_variant("candidate:asr=8,tts=8,translation=3,chunk=1200")

    _write_variant_config(project_root / "config.yaml", target, variant)
    assert _assert_variant_resolved_config(target, variant) == {
        "asr": 8,
        "tts": 8,
        "translation": 3,
        "chunk": 1200,
    }


def test_autotune_recommends_only_quality_equivalent_speedup() -> None:
    recommendation = _select_recommendation(
        [
            _run("baseline", 100.0),
            _run("small-win", 99.0),
            _run("quality-drift", 70.0, source="different transcript"),
            _run("winner", 80.0),
        ]
    )

    assert recommendation["eligible_for_manual_promotion"] is True
    assert recommendation["candidate"]["name"] == "winner"
    assert recommendation["auto_applied"] is False


def test_autotune_fails_closed_when_baseline_or_candidate_gate_fails() -> None:
    assert _select_recommendation([_run("baseline", 100.0, status="gate_failed")]) == {
        "eligible_for_manual_promotion": False,
        "reason": "baseline_gate_failed",
    }
    result = _select_recommendation(
        [
            _run("baseline", 100.0),
            _run("fast-but-failed", 50.0, status="gate_failed"),
        ]
    )
    assert result["eligible_for_manual_promotion"] is False
    assert result["reason"] == "no_candidate_cleared_quality_fault_and_speedup_gates"


def test_autotune_aborts_expensive_candidates_after_baseline_failure() -> None:
    baseline = parse_variant("baseline:asr=4,tts=4,translation=2,chunk=1500")
    candidate = parse_variant("asr8:asr=8,tts=4,translation=2,chunk=1500")

    assert _baseline_gate_failed(baseline, _run("baseline", 100.0, status="failed")) is True
    assert _baseline_gate_failed(baseline, _run("baseline", 100.0)) is False
    assert _baseline_gate_failed(candidate, _run("asr8", 80.0, status="failed")) is False


def test_autotune_requires_repeated_trials_before_promotion() -> None:
    result = _select_recommendation(
        [
            {**_run("baseline", 100.0), "trial": 1},
            {**_run("winner", 80.0), "trial": 1},
        ],
        required_trials=3,
    )

    assert result["eligible_for_manual_promotion"] is False
    assert result["reason"] == "insufficient_repeated_trials"


def test_autotune_uses_median_across_paired_trials() -> None:
    runs = []
    for trial, baseline_wall, candidate_wall in (
        (1, 100.0, 80.0),
        (2, 130.0, 82.0),
        (3, 102.0, 81.0),
    ):
        runs.append({**_run("baseline", baseline_wall), "trial": trial})
        runs.append({**_run("winner", candidate_wall), "trial": trial})

    result = _select_recommendation(runs, required_trials=3)

    assert result["eligible_for_manual_promotion"] is True
    assert result["candidate"]["trials"] == 3
    assert result["candidate"]["baseline_median_wall_seconds"] == 102.0
    assert result["candidate"]["candidate_median_wall_seconds"] == 81.0
    assert result["candidate"]["speedup_vs_baseline"] == pytest.approx(102.0 / 81.0)
