import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "work" / "benchmarks" / "fixtures.json"
BENCHMARK_TOOL_PATH = REPO_ROOT / "tools" / "benchmark.py"

_SPEC = importlib.util.spec_from_file_location("vi_dubber_benchmark_tool", BENCHMARK_TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BENCHMARK_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCHMARK_TOOL)

summarize_artifacts = _BENCHMARK_TOOL.summarize_artifacts
summarize_data = _BENCHMARK_TOOL.summarize_data
summarize_fixture_coverage = _BENCHMARK_TOOL.summarize_fixture_coverage
validate_manifest = _BENCHMARK_TOOL.validate_manifest
REQUIRED_FIXTURE_CATEGORIES = _BENCHMARK_TOOL.REQUIRED_FIXTURE_CATEGORIES


def test_summarize_data_computes_rates_percentiles_and_stage_timings() -> None:
    summary = summarize_data(
        {
            "duration_seconds": 10.0,
            "elapsed_seconds": 15.0,
            "segments": 4,
            "rewritten_segments": 2,
            "overflow_segments": 1,
            "qa": {"similarity": 0.9, "passed": True},
        },
        tts_stats=[
            {"tempo": 1.0},
            {"tempo": 1.1},
            {"tempo": 1.2},
            {"tempo": 1.4},
        ],
        metrics={
            "stages": {
                "tts": {"wall_seconds": 4.5},
                "translation": {"wall_seconds": 2.0},
                "ignored": {"wall_seconds": None},
            }
        },
    )

    assert summary["real_time_factor"] == pytest.approx(1.5)
    assert summary["rewrite_rate"] == pytest.approx(0.5)
    assert summary["overflow_rate"] == pytest.approx(0.25)
    assert summary["tempo"]["p50"] == pytest.approx(1.15)
    assert summary["tempo"]["p95"] == pytest.approx(1.37)
    assert summary["tempo"]["max"] == pytest.approx(1.4)
    assert summary["stage_timings_seconds"] == {"translation": 2.0, "tts": 4.5}
    assert summary["missing_fields"] == []


def test_summarize_data_degrades_cleanly_when_optional_fields_are_missing() -> None:
    summary = summarize_data({"segments": 0})

    assert summary["real_time_factor"] is None
    assert summary["rewrite_rate"] is None
    assert summary["overflow_rate"] is None
    assert summary["tempo"] == {"p50": None, "p95": None, "max": None, "samples": 0}
    assert "duration_seconds" in summary["missing_fields"]
    assert "tempo.p95" in summary["missing_fields"]


def test_summarize_artifacts_marks_missing_optional_artifact(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "duration_seconds": 2,
                "elapsed_seconds": 4,
                "segments": 1,
                "rewritten_segments": 0,
                "overflow_segments": 0,
                "max_tempo": 1.0,
            }
        ),
        encoding="utf-8",
    )
    missing_stats = tmp_path / "tts_stats.json"

    summary = summarize_artifacts(result_path, tts_stats_path=missing_stats)

    assert summary["real_time_factor"] == pytest.approx(2.0)
    assert summary["artifacts"]["tts_stats"]["status"] == "missing"
    assert summary["tempo"]["max"] == pytest.approx(1.0)
    assert summary["tempo"]["p50"] is None


def test_summarize_artifacts_requires_result_artifact(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="benchmark result artifact not found"):
        summarize_artifacts(tmp_path / "missing-result.json")


def test_real_fixture_manifest_schema_paths_and_hashes_are_valid() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    issues = validate_manifest(manifest, root=REPO_ROOT)

    assert issues == []
    available = [fixture for fixture in manifest["fixtures"] if fixture["status"] == "available"]
    source_ready = [fixture for fixture in manifest["fixtures"] if fixture["status"] == "source-ready"]
    available_roles = {fixture["role"] for fixture in available}
    available_categories = {
        category
        for fixture in available
        for category in fixture.get("categories", [])
    }
    all_categories = {
        category
        for fixture in manifest["fixtures"]
        for category in fixture.get("categories", [])
    }
    assert {"short-real-baseline", "long-real-baseline"} <= available_roles
    assert "clean-talking-head-real-regression" in available_roles
    assert "technical-terms-real-regression" in available_roles
    assert "fast-english-real-regression" in available_roles
    assert "clean-single-speaker-talking-head" in available_categories
    assert "fast-english-speech" in available_categories
    assert "names-numbers-technical-terms" in available_categories
    assert REQUIRED_FIXTURE_CATEGORIES <= all_categories
    assert len(source_ready) == 0

    coverage = summarize_fixture_coverage(manifest)
    assert len(coverage["available_categories"]) == 10
    assert coverage["complete"] is True
    assert len(coverage["missing_categories"]) == 0
    assert coverage["source_complete"] is True
    assert coverage["missing_source_categories"] == []
    assert set(coverage["available_categories"]) == REQUIRED_FIXTURE_CATEGORIES


def test_source_ready_fixture_receipts_prove_the_designed_stress_conditions() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    fixtures = {
        fixture["categories"][0]: fixture
        for fixture in manifest["fixtures"]
        if fixture.get("role") == "synthetic-source-regression"
    }

    assert set(fixtures) == {
        "music-under-dialogue",
        "noisy-speech",
        "two-speakers",
        "overlapping-speech",
        "emotional-prosody-stress",
    }

    receipts = {}
    for category, fixture in fixtures.items():
        receipt = json.loads((REPO_ROOT / fixture["verification"]["path"]).read_text(encoding="utf-8-sig"))
        assert receipt["fixture_class"] == category
        assert receipt["generation_kind"] == "synthetic"
        assert receipt["source_sha256"] == fixture["source"]["sha256"]
        assert receipt["duration_seconds"] > 5.0
        receipts[category] = receipt["behavior_probe"]

    assert receipts["music-under-dialogue"]["simultaneous_background"] is True
    assert receipts["music-under-dialogue"]["music_frequencies_hz"] == [220, 330]
    assert receipts["noisy-speech"]["noise_kind"] == "pink"
    assert receipts["noisy-speech"]["noise_amplitude"] > 0
    assert receipts["two-speakers"]["distinct_synthetic_speakers"] == 2
    assert receipts["two-speakers"]["alternating_turns"] == 4
    assert receipts["two-speakers"]["designed_overlap_seconds"] == 0
    assert receipts["overlapping-speech"]["distinct_synthetic_speakers"] == 2
    assert receipts["overlapping-speech"]["designed_overlap_seconds"] > 1.0
    assert receipts["emotional-prosody-stress"]["rate_sequence"] == [-4, 0, 4]
    assert receipts["emotional-prosody-stress"]["prosody_sections"] == 3


def test_real_fixture_baseline_numbers_match_current_artifacts() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    for fixture in manifest["fixtures"]:
        if fixture["status"] != "available":
            continue
        artifacts = fixture["artifacts"]
        summary = summarize_artifacts(
            REPO_ROOT / artifacts["result"]["path"],
            tts_stats_path=REPO_ROOT / artifacts["tts_stats"]["path"],
        )
        baseline = fixture["baseline"]
        assert baseline["duration_seconds"] == pytest.approx(summary["duration_seconds"])
        assert baseline["elapsed_seconds"] == pytest.approx(summary["elapsed_seconds"])
        assert baseline["real_time_factor"] == pytest.approx(summary["real_time_factor"])
        assert baseline["segments"] == summary["segments"]
        assert baseline["rewritten_segments"] == summary["rewritten_segments"]
        assert baseline["rewrite_rate"] == pytest.approx(summary["rewrite_rate"])
        assert baseline["overflow_segments"] == summary["overflow_segments"]
        assert baseline["overflow_rate"] == pytest.approx(summary["overflow_rate"])
        assert baseline["tempo_p50"] == pytest.approx(summary["tempo"]["p50"])
        assert baseline["tempo_p95"] == pytest.approx(summary["tempo"]["p95"])
        assert baseline["max_tempo"] == pytest.approx(summary["tempo"]["max"])


def test_manifest_validation_reports_missing_required_artifact(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "fixtures": [
            {
                "id": "broken",
                "status": "available",
                "artifacts": {"result": {"path": "missing/result.json"}},
            }
        ],
    }

    issues = validate_manifest(manifest, root=tmp_path)

    assert issues == ["fixtures[0].artifacts.result missing: missing/result.json"]


def test_manifest_validation_rejects_source_ready_fixture_without_provenance(tmp_path: Path) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"fixture")
    verification_path = tmp_path / "verification.json"
    verification_path.write_text("{}", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "fixtures": [
            {
                "id": "source-only",
                "status": "source-ready",
                "categories": ["noisy-speech"],
                "source": {"status": "available", "path": "source.mp4"},
                "verification": {"path": "verification.json"},
            }
        ],
    }

    issues = validate_manifest(manifest, root=tmp_path)

    assert issues == [
        "fixtures[0].source.provenance.kind must be real, derived, or synthetic for source-ready fixtures"
    ]
