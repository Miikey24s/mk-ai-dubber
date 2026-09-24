from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_TOOL_PATH = REPO_ROOT / "tools" / "benchmark.py"

_SPEC = importlib.util.spec_from_file_location("vi_dubber_regression_benchmark_tool", BENCHMARK_TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BENCHMARK_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCHMARK_TOOL)

compare_fixture_sets = _BENCHMARK_TOOL.compare_fixture_sets
compare_summaries = _BENCHMARK_TOOL.compare_summaries
evaluate_acceptance = _BENCHMARK_TOOL.evaluate_acceptance
REQUIRED_FIXTURE_CATEGORIES = _BENCHMARK_TOOL.REQUIRED_FIXTURE_CATEGORIES
sha256_file = _BENCHMARK_TOOL.sha256_file
summarize_data = _BENCHMARK_TOOL.summarize_data
summarize_segment_qa = _BENCHMARK_TOOL.summarize_segment_qa
summarize_semantic_qa = _BENCHMARK_TOOL.summarize_semantic_qa


def _summary(*, rtf: float, rewrite: int, overflow: int, tempos: list[float]) -> dict:
    return summarize_data(
        {
            "duration_seconds": 100.0,
            "elapsed_seconds": 100.0 * rtf,
            "segments": 100,
            "rewritten_segments": rewrite,
            "overflow_segments": overflow,
            "qa": {"similarity": 0.95, "passed": True},
        },
        tts_stats=[{"tempo": value} for value in tempos],
        metrics={"stages": {"translation": {"wall_seconds": 10.0 * rtf}}},
    )


def test_comparison_report_tracks_directional_regressions_and_release_acceptance() -> None:
    before = _summary(rtf=2.0, rewrite=45, overflow=12, tempos=[1.0, 1.25, 1.30])
    after = _summary(rtf=1.7, rewrite=20, overflow=4, tempos=[1.0, 1.10, 1.18])

    report = compare_summaries(before, after)

    assert report["schema_version"] == 1
    assert report["metrics"]["real_time_factor"]["trend"] == "improved"
    assert report["metrics"]["rewrite_rate"]["trend"] == "improved"
    assert report["metrics"]["overflow_rate"]["trend"] == "improved"
    assert report["stage_timings_seconds"]["translation"]["trend"] == "improved"
    assert report["acceptance"]["passed"] is True
    assert report["acceptance"]["criteria"]["rewrite_rate"]["status"] == "pass"
    assert report["acceptance"]["criteria"]["overflow_rate"]["status"] == "pass"
    assert report["acceptance"]["criteria"]["tempo.p95"]["status"] == "pass"


def test_acceptance_fails_closed_when_required_measurement_is_missing() -> None:
    incomplete = summarize_data(
        {
            "duration_seconds": 10.0,
            "elapsed_seconds": 10.0,
            "segments": 10,
            "rewritten_segments": 1,
            "overflow_segments": 0,
        }
    )

    acceptance = evaluate_acceptance(incomplete)

    assert acceptance["passed"] is False
    assert acceptance["criteria"]["tempo.p95"]["status"] == "unavailable"


def test_segment_qa_summary_reports_micro_wer_and_critical_token_accuracy() -> None:
    summary = summarize_segment_qa(
        {
            "final": [
                {
                    "expected": "Bạn không được bỏ qua hai mươi lăm phần trăm.",
                    "actual": "Bạn không được bỏ qua hai mươi lăm phần trăm.",
                    "critical_terms": ["hai mươi lăm"],
                    "missing_critical": [],
                    "passed": True,
                },
                {
                    "expected": "Tôi sẽ mua ba hợp đồng.",
                    "actual": "Tôi mua hợp đồng.",
                    "critical_terms": ["ba"],
                    "missing_critical": ["sẽ", "ba"],
                    "passed": False,
                },
            ]
        }
    )

    assert summary["segments_checked"] == 2
    assert summary["failed_segments"] == 1
    assert summary["reference_words"] == 16
    assert summary["word_errors"] == 2
    assert summary["wer"] == pytest.approx(2 / 16)
    assert summary["critical_tokens"] == 5
    assert summary["critical_missing"] == 2
    assert summary["critical_token_accuracy"] == pytest.approx(0.6)


def test_semantic_qa_summary_uses_final_translated_stage_without_claiming_calibration() -> None:
    summary = summarize_semantic_qa(
        {
            "stages": {
                "translated": {"status": "ok", "segments_checked": 20, "needs_review": 3},
                "prefit_candidate_00001": {"status": "ok", "segments_checked": 1, "needs_review": 1},
            }
        }
    )

    assert summary == {
        "status": "ok",
        "segments_checked": 20,
        "needs_review": 3,
        "review_rate": pytest.approx(0.15),
        "stage_count": 2,
    }


def _write_fixture_run(root: Path, name: str, *, rewrite: int, overflow: int, tempos: list[float]) -> dict:
    run_dir = root / name
    run_dir.mkdir(parents=True)
    result_path = run_dir / "result.json"
    tts_path = run_dir / "tts_stats.json"
    result_path.write_text(
        json.dumps(
            {
                "duration_seconds": 20.0,
                "elapsed_seconds": 30.0,
                "segments": 100,
                "rewritten_segments": rewrite,
                "overflow_segments": overflow,
            }
        ),
        encoding="utf-8",
    )
    tts_path.write_text(json.dumps([{"tempo": value} for value in tempos]), encoding="utf-8")
    return {
        "result": {"path": f"{name}/result.json", "sha256": sha256_file(result_path)},
        "tts_stats": {"path": f"{name}/tts_stats.json", "sha256": sha256_file(tts_path)},
    }


def _write_source_ready_fixture(root: Path, name: str, *, categories: list[str]) -> dict:
    source_path = root / f"{name}.mp4"
    verification_path = root / f"{name}-verification.json"
    source_path.write_bytes(b"synthetic-source")
    verification_path.write_text(
        json.dumps({"fixture_class": categories[0], "generation_kind": "synthetic"}),
        encoding="utf-8",
    )
    return {
        "id": name,
        "status": "source-ready",
        "categories": categories,
        "source": {
            "status": "available",
            "path": source_path.name,
            "sha256": sha256_file(source_path),
            "provenance": {"kind": "synthetic"},
        },
        "verification": {
            "path": verification_path.name,
            "sha256": sha256_file(verification_path),
        },
    }


def test_fixture_set_report_compares_matching_fixture_ids_and_aggregates_acceptance(tmp_path: Path) -> None:
    before_root = tmp_path / "before"
    after_root = tmp_path / "after"
    before_artifacts = _write_fixture_run(
        before_root,
        "fixture-a",
        rewrite=50,
        overflow=20,
        tempos=[1.10, 1.25, 1.30],
    )
    after_artifacts = _write_fixture_run(
        after_root,
        "fixture-a",
        rewrite=20,
        overflow=4,
        tempos=[1.00, 1.10, 1.18],
    )
    before_manifest = {
        "schema_version": 1,
        "fixtures": [
            {
                "id": "fixture-a",
                "status": "available",
                "categories": sorted(REQUIRED_FIXTURE_CATEGORIES),
                "artifacts": before_artifacts,
            }
        ],
    }
    after_manifest = {
        "schema_version": 1,
        "fixtures": [
            {
                "id": "fixture-a",
                "status": "available",
                "categories": sorted(REQUIRED_FIXTURE_CATEGORIES),
                "artifacts": after_artifacts,
            }
        ],
    }

    report = compare_fixture_sets(
        before_manifest,
        after_manifest,
        before_root=before_root,
        after_root=after_root,
    )

    assert report["passed"] is True
    assert report["thresholds_passed"] is True
    assert report["coverage"]["after"]["complete"] is True
    assert report["manifest_issues"] == {"before": [], "after": []}
    assert report["fixtures"][0]["id"] == "fixture-a"
    assert report["fixtures"][0]["comparison"]["metrics"]["rewrite_rate"]["trend"] == "improved"


def test_fixture_set_report_rejects_corrupt_manifest_artifact_before_comparison(tmp_path: Path) -> None:
    artifacts = _write_fixture_run(
        tmp_path,
        "fixture-a",
        rewrite=20,
        overflow=4,
        tempos=[1.00, 1.10, 1.18],
    )
    artifacts["result"]["sha256"] = "0" * 64
    manifest = {
        "schema_version": 1,
        "fixtures": [{"id": "fixture-a", "status": "available", "artifacts": artifacts}],
    }

    report = compare_fixture_sets(
        manifest,
        manifest,
        before_root=tmp_path,
        after_root=tmp_path,
    )

    assert report["passed"] is False
    assert any("hash mismatch" in issue for issue in report["manifest_issues"]["before"])
    assert report["fixtures"] == []


def test_fixture_set_report_fails_release_gate_when_required_categories_are_missing(tmp_path: Path) -> None:
    artifacts = _write_fixture_run(
        tmp_path,
        "fixture-a",
        rewrite=20,
        overflow=4,
        tempos=[1.00, 1.10, 1.18],
    )
    source_ready = _write_source_ready_fixture(
        tmp_path,
        "source-ready-rest",
        categories=sorted(REQUIRED_FIXTURE_CATEGORIES - {"long-monologue"}),
    )
    manifest = {
        "schema_version": 1,
        "fixtures": [
            {
                "id": "fixture-a",
                "status": "available",
                "categories": ["long-monologue"],
                "artifacts": artifacts,
            },
            source_ready,
        ],
    }

    report = compare_fixture_sets(
        manifest,
        manifest,
        before_root=tmp_path,
        after_root=tmp_path,
    )

    assert report["thresholds_passed"] is True
    assert report["coverage"]["after"]["complete"] is False
    assert report["coverage"]["after"]["source_complete"] is True
    assert report["coverage"]["after"]["missing_source_categories"] == []
    assert "fast-english-speech" in report["coverage"]["after"]["missing_categories"]
    assert report["passed"] is False
