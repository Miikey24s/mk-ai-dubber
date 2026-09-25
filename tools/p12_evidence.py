from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("work/benchmarks/fixtures.json")
DEFAULT_SUPPLEMENTALS = (
    (
        "canonical-short-50",
        Path("work/job-4c8236a32c685a11/segment_qa.json"),
    ),
    (
        "p12-routing-isolated-53",
        Path(
            "work/p12-routing-e2e-20260923-0945/"
            "job-4c8236a32c685a11/segment_qa.json"
        ),
    ),
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _final_flags(report: dict[str, Any]) -> list[dict[str, Any]]:
    flagged: list[dict[str, Any]] = []
    for row in report.get("final", []):
        if bool(row.get("passed", False)):
            continue
        flagged.append(
            {
                "segment_id": int(row["segment_id"]),
                "action": str(row.get("action", "")),
                "reasons": list(row.get("reasons", [])),
                "missing_critical": list(row.get("missing_critical", [])),
                "similarity": float(row.get("similarity", 0.0)),
                "timing_ratio": float(row.get("timing_ratio", 0.0)),
                "expected": str(row.get("expected", "")),
                "actual": str(row.get("actual", "")),
            }
        )
    return flagged


def _summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    final = list(report.get("final", []))
    flags = _final_flags(report)
    passed = [row for row in final if bool(row.get("passed", False))]
    variant_passes = [
        row
        for row in passed
        if float(row.get("similarity", 0.0)) < 0.999999
    ]
    reason_counts = Counter(
        reason
        for row in flags
        for reason in row.get("reasons", [])
    )
    action_counts = Counter(row.get("action", "") for row in flags)
    summary = dict(report.get("summary", {}))
    return {
        "segments_checked": int(summary.get("segments_checked", len(final))),
        "initial_failed": int(summary.get("initial_failed", 0)),
        "repairs_attempted": int(summary.get("repairs_attempted", 0)),
        "repairs_completed": int(summary.get("repairs_completed", 0)),
        "final_failed": int(summary.get("final_failed", len(flags))),
        "passed_segments": len(passed),
        "variant_passes": len(variant_passes),
        "reason_counts": dict(sorted(reason_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "flags": flags,
    }


def analyze(
    root: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    supplementals: tuple[tuple[str, Path], ...] = DEFAULT_SUPPLEMENTALS,
) -> dict[str, Any]:
    root = root.resolve()
    manifest_abs = (root / manifest_path).resolve()
    manifest = _load_json(manifest_abs)

    p17_runs: list[dict[str, Any]] = []
    known_defects: list[dict[str, Any]] = []
    missing_artifacts: list[str] = []
    hash_mismatches: list[dict[str, str]] = []

    for fixture in manifest.get("fixtures", []):
        if fixture.get("status") != "available":
            continue
        segment_artifact = fixture.get("artifacts", {}).get("segment_qa")
        if not segment_artifact:
            continue
        rel_path = Path(segment_artifact["path"])
        artifact_path = (root / rel_path).resolve()
        if not artifact_path.exists():
            missing_artifacts.append(str(rel_path).replace("\\", "/"))
            continue

        actual_sha = _sha256(artifact_path)
        expected_sha = str(segment_artifact.get("sha256", ""))
        if expected_sha and actual_sha != expected_sha:
            hash_mismatches.append(
                {
                    "fixture_id": str(fixture.get("id", "")),
                    "path": str(rel_path).replace("\\", "/"),
                    "expected": expected_sha,
                    "actual": actual_sha,
                }
            )

        report = _load_json(artifact_path)
        run = {
            "fixture_id": str(fixture.get("id", "")),
            "role": str(fixture.get("role", "")),
            "categories": list(fixture.get("categories", [])),
            "source_kind": str(
                fixture.get("source", {}).get("provenance", {}).get("kind", "real")
            ),
            "path": str(rel_path).replace("\\", "/"),
            "sha256": actual_sha,
            **_summarize_report(report),
        }
        p17_runs.append(run)

        declared_failed = fixture.get("quality_receipt", {}).get("segment_qa_failed")
        if declared_failed is not None:
            run["declared_segment_qa_failed"] = int(declared_failed)
            run["declared_failure_count_matches"] = (
                int(declared_failed) == run["final_failed"]
            )

        defect = fixture.get("quality_receipt", {}).get("known_detected_defect")
        if defect:
            defect_segment_id = int(defect["segment_id"])
            matching_flags = [
                row for row in run["flags"] if row["segment_id"] == defect_segment_id
            ]
            known_defects.append(
                {
                    "fixture_id": run["fixture_id"],
                    "segment_id": defect_segment_id,
                    "expected": str(defect.get("expected", "")),
                    "actual": str(defect.get("actual", "")),
                    "missing_critical": str(defect.get("missing_critical", "")),
                    "caught_by_final_segment_qa": bool(matching_flags),
                    "observed_flag": matching_flags[0] if matching_flags else None,
                }
            )

    supplemental_runs: list[dict[str, Any]] = []
    for label, rel_path in supplementals:
        artifact_path = (root / rel_path).resolve()
        if not artifact_path.exists():
            continue
        report = _load_json(artifact_path)
        supplemental_runs.append(
            {
                "label": label,
                "path": str(rel_path).replace("\\", "/"),
                "sha256": _sha256(artifact_path),
                **_summarize_report(report),
            }
        )

    totals = {
        "p17_fixture_runs": len(p17_runs),
        "p17_segments_checked": sum(run["segments_checked"] for run in p17_runs),
        "p17_final_failed": sum(run["final_failed"] for run in p17_runs),
        "p17_passed_segments": sum(run["passed_segments"] for run in p17_runs),
        "p17_variant_passes": sum(run["variant_passes"] for run in p17_runs),
        "p17_zero_flag_runs": sum(run["final_failed"] == 0 for run in p17_runs),
        "supplemental_runs": len(supplemental_runs),
        "supplemental_segments_checked": sum(
            run["segments_checked"] for run in supplemental_runs
        ),
        "supplemental_final_failed": sum(
            run["final_failed"] for run in supplemental_runs
        ),
    }

    return {
        "schema_version": 1,
        "manifest": str(manifest_path).replace("\\", "/"),
        "integrity": {
            "missing_artifacts": missing_artifacts,
            "hash_mismatches": hash_mismatches,
        },
        "totals": totals,
        "known_detected_defects": known_defects,
        "p17_runs": p17_runs,
        "supplemental_runs": supplemental_runs,
        "interpretation": {
            "variant_passes_definition": (
                "Final segment passed while transcript similarity was below 1.0; "
                "this is evidence that benign ASR variation is not automatically flagged."
            ),
            "false_positive_limit": (
                "Machine artifacts cannot establish an audible false-positive rate. "
                "Flagged segments without independent listening labels remain unresolved."
            ),
        },
    }


def _print_report(report: dict[str, Any]) -> None:
    totals = report["totals"]
    print(
        "P17: "
        f"{totals['p17_fixture_runs']} runs, "
        f"{totals['p17_segments_checked']} segments, "
        f"{totals['p17_final_failed']} final flags, "
        f"{totals['p17_variant_passes']} non-exact ASR variants passed."
    )
    print(
        "Supplemental: "
        f"{totals['supplemental_runs']} runs, "
        f"{totals['supplemental_segments_checked']} segments, "
        f"{totals['supplemental_final_failed']} final flags."
    )
    for run in report["p17_runs"]:
        if not run["flags"]:
            continue
        print(f"{run['fixture_id']}: {run['final_failed']} flag(s)")
        for row in run["flags"]:
            print(
                "  "
                f"seg {row['segment_id']} {row['action']} "
                f"reasons={','.join(row['reasons']) or '-'} "
                f"missing={','.join(row['missing_critical']) or '-'} "
                f"sim={row['similarity']:.4f} timing={row['timing_ratio']:.4f}"
            )
    for run in report["supplemental_runs"]:
        if not run["flags"]:
            continue
        print(f"{run['label']}: {run['final_failed']} flag(s)")
        for row in run["flags"]:
            print(
                "  "
                f"seg {row['segment_id']} {row['action']} "
                f"reasons={','.join(row['reasons']) or '-'} "
                f"missing={','.join(row['missing_critical']) or '-'} "
                f"sim={row['similarity']:.4f} timing={row['timing_ratio']:.4f}"
            )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Summarize existing P12 segment-QA evidence without rerunning models."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = analyze(args.root, manifest_path=args.manifest)
    _print_report(report)
    if args.output:
        output_path = args.output
        if not output_path.is_absolute():
            output_path = args.root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    integrity = report["integrity"]
    if integrity["missing_artifacts"] or integrity["hash_mismatches"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
