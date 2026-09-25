from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "p12_evidence.py"
    spec = importlib.util.spec_from_file_location("p12_evidence_tool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_analyze_counts_flags_variants_and_known_defect(tmp_path: Path) -> None:
    tool = _load_tool()
    qa_path = tmp_path / "qa.json"
    qa_path.write_text(
        json.dumps(
            {
                "summary": {
                    "segments_checked": 3,
                    "initial_failed": 1,
                    "repairs_attempted": 1,
                    "repairs_completed": 0,
                    "final_failed": 1,
                },
                "final": [
                    {
                        "segment_id": 0,
                        "passed": True,
                        "action": "pass",
                        "similarity": 1.0,
                        "timing_ratio": 0.9,
                    },
                    {
                        "segment_id": 1,
                        "passed": True,
                        "action": "pass",
                        "similarity": 0.93,
                        "timing_ratio": 0.9,
                    },
                    {
                        "segment_id": 2,
                        "passed": False,
                        "action": "pronunciation_retry",
                        "reasons": ["missing_critical"],
                        "missing_critical": ["33"],
                        "similarity": 0.88,
                        "timing_ratio": 0.95,
                        "expected": "thua 33 phần trăm",
                        "actual": "thua 53 phần trăm",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    digest = tool._sha256(qa_path)
    manifest_path = tmp_path / "fixtures.json"
    manifest_path.write_text(
        json.dumps(
            {
                "fixtures": [
                    {
                        "id": "numeric",
                        "role": "real-regression",
                        "status": "available",
                        "categories": ["numbers"],
                        "source": {"status": "available"},
                        "artifacts": {
                            "segment_qa": {
                                "path": "qa.json",
                                "sha256": digest,
                            }
                        },
                        "quality_receipt": {
                            "segment_qa_failed": 1,
                            "known_detected_defect": {
                                "segment_id": 2,
                                "missing_critical": "33",
                                "expected": "thua 33 phần trăm",
                                "actual": "thua 53 phần trăm",
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = tool.analyze(
        tmp_path,
        manifest_path=Path("fixtures.json"),
        supplementals=(),
    )

    assert report["integrity"] == {
        "missing_artifacts": [],
        "hash_mismatches": [],
    }
    assert report["totals"]["p17_segments_checked"] == 3
    assert report["totals"]["p17_final_failed"] == 1
    assert report["totals"]["p17_variant_passes"] == 1
    assert report["p17_runs"][0]["declared_failure_count_matches"] is True
    assert report["known_detected_defects"][0]["caught_by_final_segment_qa"] is True
