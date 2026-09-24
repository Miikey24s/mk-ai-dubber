from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools/p14_synthetic_acceptance.py"
GROUND_TRUTH = REPO_ROOT / "work/benchmarks/p14-synthetic-acceptance/fixture-ground-truth.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location("p14_synthetic_acceptance", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p14_synthetic_acceptance_closes_machine_verifiable_contracts() -> None:
    tool = _load_tool()

    report = tool.run_acceptance(REPO_ROOT, ground_truth_path=GROUND_TRUTH, hf_token=None)

    assert report["passed"] is True
    assert report["acceptance_scope"] == "machine-verifiable synthetic contract"
    assert report["real_human_quality_claimed"] is False
    checks = {item["id"]: item for item in report["checks"]}
    assert all(item["passed"] for item in checks.values())
    assert checks["two_speakers.identity_and_no_silent_merge"]["detail"]["speaker_sequence"] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "SPEAKER_00",
        "SPEAKER_01",
    ]
    visibility = checks["overlap.visibility_and_review_contract"]["detail"]["speaker_visibility"]
    assert visibility["overlap"] is True
    assert visibility["multi_speaker"] is True
    assert visibility["needs_review"] is True
    assert report["runtime_probe"]["live_diarization_attempted"] is False


def test_p14_fixture_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    tool = _load_tool()
    manifest = json.loads((REPO_ROOT / "work/benchmarks/fixtures.json").read_text(encoding="utf-8"))
    target = next(
        fixture
        for fixture in manifest["fixtures"]
        if "two-speakers" in fixture.get("categories", [])
    )
    target["source"]["sha256"] = "0" * 64
    altered = tmp_path / "fixtures.json"
    altered.write_text(json.dumps(manifest), encoding="utf-8")

    report = tool.run_acceptance(
        REPO_ROOT,
        manifest_path=altered,
        ground_truth_path=GROUND_TRUTH,
        hf_token=None,
    )

    assert report["passed"] is False
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["fixture.two-speakers.provenance"]["passed"] is False


def test_p14_missing_ground_truth_fails_closed(tmp_path: Path) -> None:
    tool = _load_tool()

    report = tool.run_acceptance(
        REPO_ROOT,
        ground_truth_path=tmp_path / "missing.json",
        hf_token=None,
    )

    assert report["passed"] is False
    assert report["checks"] == []
    assert report["errors"] and "FileNotFoundError" in report["errors"][0]
