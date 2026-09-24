from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from vi_dubber.qa import critical_spoken_tokens, normalize_spoken_text
from vi_dubber.segmentation import build_speech_turns, segmentation_summary
from vi_dubber.types import Segment


MANIFEST_SCHEMA_VERSION = 1
SUMMARY_SCHEMA_VERSION = 1
COMPARISON_SCHEMA_VERSION = 1

DEFAULT_ACCEPTANCE_LIMITS = {
    "rewrite_rate": 0.30,
    "overflow_rate": 0.05,
    "tempo.p95": 1.20,
}

REQUIRED_FIXTURE_CATEGORIES = frozenset(
    {
        "clean-single-speaker-talking-head",
        "long-monologue",
        "fast-english-speech",
        "short-fragmented-subtitle-style-speech",
        "music-under-dialogue",
        "noisy-speech",
        "two-speakers",
        "overlapping-speech",
        "names-numbers-technical-terms",
        "emotional-prosody-stress",
    }
)

SOURCE_READY_FIXTURE_STATUSES = frozenset({"available", "source-ready"})

_COMPARISON_METRICS = (
    ("real_time_factor", "lower_is_better"),
    ("rewrite_rate", "lower_is_better"),
    ("overflow_rate", "lower_is_better"),
    ("tempo.p95", "lower_is_better"),
    ("tempo.max", "lower_is_better"),
    ("qa.similarity", "higher_is_better"),
    ("quality.wer", "lower_is_better"),
    ("quality.critical_token_accuracy", "higher_is_better"),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _rate(count: int | None, total: int | None) -> float | None:
    if count is None or total is None or total <= 0:
        return None
    return count / total


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * quantile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _stage_timings(metrics: Any) -> dict[str, float]:
    if not isinstance(metrics, dict):
        return {}
    stages = metrics.get("stages")
    if not isinstance(stages, dict):
        return {}

    result: dict[str, float] = {}
    for name in sorted(stages):
        item = stages[name]
        if not isinstance(item, dict):
            continue
        wall_seconds = _number(item.get("wall_seconds"))
        if wall_seconds is not None:
            result[str(name)] = wall_seconds
    return result


def _word_edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, ref_token in enumerate(reference, start=1):
        current = [row]
        for column, hyp_token in enumerate(hypothesis, start=1):
            substitution = previous[column - 1] + (ref_token != hyp_token)
            insertion = current[column - 1] + 1
            deletion = previous[column] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def summarize_segment_qa(report: dict[str, Any]) -> dict[str, Any]:
    final = report.get("final")
    if not isinstance(final, list):
        return {
            "wer": None,
            "word_errors": 0,
            "reference_words": 0,
            "critical_token_accuracy": None,
            "critical_tokens": 0,
            "critical_missing": 0,
            "segments_checked": 0,
            "failed_segments": 0,
        }

    word_errors = 0
    reference_words = 0
    critical_tokens = 0
    critical_missing = 0
    failed_segments = 0
    segments_checked = 0
    for item in final:
        if not isinstance(item, dict):
            continue
        expected = str(item.get("expected") or "")
        actual = str(item.get("actual") or "")
        reference = normalize_spoken_text(expected).split()
        hypothesis = normalize_spoken_text(actual).split()
        word_errors += _word_edit_distance(reference, hypothesis)
        reference_words += len(reference)

        explicit_terms = item.get("critical_terms")
        explicit_terms = explicit_terms if isinstance(explicit_terms, list) else []
        expected_critical = critical_spoken_tokens(expected, explicit_terms)
        missing_values = item.get("missing_critical")
        missing_values = missing_values if isinstance(missing_values, list) else []
        missing = {
            normalized
            for value in missing_values
            if (normalized := normalize_spoken_text(str(value)))
        }
        expected_critical.update(missing)
        critical_tokens += len(expected_critical)
        critical_missing += len(missing)
        failed_segments += int(item.get("passed") is False)
        segments_checked += 1

    return {
        "wer": word_errors / reference_words if reference_words else None,
        "word_errors": word_errors,
        "reference_words": reference_words,
        "critical_token_accuracy": (
            (critical_tokens - critical_missing) / critical_tokens if critical_tokens else None
        ),
        "critical_tokens": critical_tokens,
        "critical_missing": critical_missing,
        "segments_checked": segments_checked,
        "failed_segments": failed_segments,
    }


def summarize_semantic_qa(index: dict[str, Any]) -> dict[str, Any]:
    stages = index.get("stages")
    stages = stages if isinstance(stages, dict) else {}
    translated = stages.get("translated")
    translated = translated if isinstance(translated, dict) else {}
    checked = _integer(translated.get("segments_checked"))
    needs_review = _integer(translated.get("needs_review"))
    return {
        "status": str(translated.get("status")) if translated.get("status") is not None else None,
        "segments_checked": checked,
        "needs_review": needs_review,
        "review_rate": _rate(needs_review, checked),
        "stage_count": len(stages),
    }


def summarize_data(
    result: dict[str, Any],
    *,
    tts_stats: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    duration_seconds = _number(result.get("duration_seconds"))
    elapsed_seconds = _number(result.get("elapsed_seconds"))
    rtf = _number(result.get("real_time_factor"))
    if rtf is None and duration_seconds and elapsed_seconds is not None:
        rtf = elapsed_seconds / duration_seconds

    segments = _integer(result.get("segments"))
    rewritten_segments = _integer(result.get("rewritten_segments"))
    overflow_segments = _integer(result.get("overflow_segments"))

    tempos: list[float] = []
    if isinstance(tts_stats, list):
        for item in tts_stats:
            if not isinstance(item, dict):
                continue
            tempo = _number(item.get("tempo"))
            if tempo is not None:
                tempos.append(tempo)

    tempo_max = max(tempos) if tempos else _number(result.get("max_tempo"))
    qa = result.get("qa") if isinstance(result.get("qa"), dict) else {}

    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "duration_seconds": duration_seconds,
        "elapsed_seconds": elapsed_seconds,
        "real_time_factor": rtf,
        "segments": segments,
        "rewritten_segments": rewritten_segments,
        "rewrite_rate": _rate(rewritten_segments, segments),
        "overflow_segments": overflow_segments,
        "overflow_rate": _rate(overflow_segments, segments),
        "tempo": {
            "p50": _percentile(tempos, 0.50),
            "p95": _percentile(tempos, 0.95),
            "max": tempo_max,
            "samples": len(tempos),
        },
        "stage_timings_seconds": _stage_timings(metrics),
        "qa": {
            "similarity": _number(qa.get("similarity")),
            "passed": qa.get("passed") if isinstance(qa.get("passed"), bool) else None,
        },
    }

    missing: list[str] = []
    for key in (
        "duration_seconds",
        "elapsed_seconds",
        "real_time_factor",
        "segments",
        "rewritten_segments",
        "overflow_segments",
    ):
        if summary[key] is None:
            missing.append(key)
    for key in ("p50", "p95", "max"):
        if summary["tempo"][key] is None:
            missing.append(f"tempo.{key}")
    summary["missing_fields"] = missing
    return summary


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_artifacts(
    result_path: Path,
    *,
    tts_stats_path: Path | None = None,
    metrics_path: Path | None = None,
    segment_qa_path: Path | None = None,
    semantic_qa_path: Path | None = None,
) -> dict[str, Any]:
    if not result_path.is_file():
        raise FileNotFoundError(f"benchmark result artifact not found: {result_path}")

    result = _load_json(result_path)
    if not isinstance(result, dict):
        raise ValueError(f"benchmark result must be a JSON object: {result_path}")

    optional_artifacts: dict[str, dict[str, Any]] = {}

    tts_stats = None
    if tts_stats_path is not None:
        if tts_stats_path.is_file():
            loaded = _load_json(tts_stats_path)
            if not isinstance(loaded, list):
                raise ValueError(f"TTS stats must be a JSON array: {tts_stats_path}")
            tts_stats = loaded
            optional_artifacts["tts_stats"] = {"path": str(tts_stats_path), "status": "available"}
        else:
            optional_artifacts["tts_stats"] = {"path": str(tts_stats_path), "status": "missing"}

    metrics = None
    if metrics_path is not None:
        if metrics_path.is_file():
            loaded = _load_json(metrics_path)
            if not isinstance(loaded, dict):
                raise ValueError(f"metrics artifact must be a JSON object: {metrics_path}")
            metrics = loaded
            optional_artifacts["metrics"] = {"path": str(metrics_path), "status": "available"}
        else:
            optional_artifacts["metrics"] = {"path": str(metrics_path), "status": "missing"}

    summary = summarize_data(result, tts_stats=tts_stats, metrics=metrics)
    if segment_qa_path is not None:
        if segment_qa_path.is_file():
            loaded = _load_json(segment_qa_path)
            if not isinstance(loaded, dict):
                raise ValueError(f"segment QA artifact must be a JSON object: {segment_qa_path}")
            summary["quality"] = summarize_segment_qa(loaded)
            optional_artifacts["segment_qa"] = {"path": str(segment_qa_path), "status": "available"}
        else:
            optional_artifacts["segment_qa"] = {"path": str(segment_qa_path), "status": "missing"}
    if semantic_qa_path is not None:
        if semantic_qa_path.is_file():
            loaded = _load_json(semantic_qa_path)
            if not isinstance(loaded, dict):
                raise ValueError(f"semantic QA artifact must be a JSON object: {semantic_qa_path}")
            summary["semantic_qa"] = summarize_semantic_qa(loaded)
            optional_artifacts["semantic_qa"] = {"path": str(semantic_qa_path), "status": "available"}
        else:
            optional_artifacts["semantic_qa"] = {"path": str(semantic_qa_path), "status": "missing"}
    summary["artifacts"] = {
        "result": {"path": str(result_path), "status": "available"},
        **optional_artifacts,
    }
    return summary


def _nested_value(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _comparison_entry(before: Any, after: Any, *, direction: str) -> dict[str, Any]:
    before_number = _number(before)
    after_number = _number(after)
    if before_number is None or after_number is None:
        return {
            "before": before_number,
            "after": after_number,
            "delta": None,
            "relative_change": None,
            "direction": direction,
            "trend": "unavailable",
        }

    delta = after_number - before_number
    relative_change = delta / abs(before_number) if before_number != 0 else None
    if math.isclose(delta, 0.0, rel_tol=1e-12, abs_tol=1e-12):
        trend = "unchanged"
    elif direction == "lower_is_better":
        trend = "improved" if delta < 0 else "regressed"
    elif direction == "higher_is_better":
        trend = "improved" if delta > 0 else "regressed"
    else:
        trend = "changed"
    return {
        "before": before_number,
        "after": after_number,
        "delta": delta,
        "relative_change": relative_change,
        "direction": direction,
        "trend": trend,
    }


def evaluate_acceptance(
    summary: dict[str, Any],
    *,
    limits: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate deterministic release gates without inventing missing measurements."""
    selected_limits = dict(DEFAULT_ACCEPTANCE_LIMITS if limits is None else limits)
    criteria: dict[str, dict[str, Any]] = {}
    for path in sorted(selected_limits):
        maximum = _number(selected_limits[path])
        if maximum is None:
            raise ValueError(f"acceptance limit must be finite: {path}")
        value = _number(_nested_value(summary, path))
        if value is None:
            status = "unavailable"
        else:
            status = "pass" if value <= maximum else "fail"
        criteria[path] = {
            "value": value,
            "operator": "<=",
            "limit": maximum,
            "status": status,
        }

    return {
        "passed": bool(criteria) and all(item["status"] == "pass" for item in criteria.values()),
        "criteria": criteria,
    }


def compare_summaries(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    limits: dict[str, float] | None = None,
) -> dict[str, Any]:
    metrics = {
        path: _comparison_entry(
            _nested_value(before, path),
            _nested_value(after, path),
            direction=direction,
        )
        for path, direction in _COMPARISON_METRICS
    }

    before_stages = before.get("stage_timings_seconds")
    after_stages = after.get("stage_timings_seconds")
    before_stages = before_stages if isinstance(before_stages, dict) else {}
    after_stages = after_stages if isinstance(after_stages, dict) else {}
    stage_timings = {
        stage: _comparison_entry(
            before_stages.get(stage),
            after_stages.get(stage),
            direction="lower_is_better",
        )
        for stage in sorted(set(before_stages) | set(after_stages))
    }

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "metrics": metrics,
        "stage_timings_seconds": stage_timings,
        "acceptance": evaluate_acceptance(after, limits=limits),
    }


def _fixture_summary(fixture: dict[str, Any], *, root: Path) -> dict[str, Any]:
    artifacts = fixture.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(f"fixture {fixture.get('id')!r} has no artifacts object")
    result = artifacts.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("path"), str):
        raise ValueError(f"fixture {fixture.get('id')!r} is missing result.path")

    def optional_path(name: str) -> Path | None:
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            return None
        return root / Path(artifact["path"])

    return summarize_artifacts(
        root / Path(result["path"]),
        tts_stats_path=optional_path("tts_stats"),
        metrics_path=optional_path("metrics"),
        segment_qa_path=optional_path("segment_qa"),
        semantic_qa_path=optional_path("semantic_qa"),
    )


def summarize_fixture_coverage(manifest: dict[str, Any]) -> dict[str, Any]:
    fixtures = manifest.get("fixtures")
    fixtures = fixtures if isinstance(fixtures, list) else []
    benchmark_ready = [
        item for item in fixtures if isinstance(item, dict) and item.get("status") == "available"
    ]
    source_ready = [
        item
        for item in fixtures
        if isinstance(item, dict) and item.get("status") in SOURCE_READY_FIXTURE_STATUSES
    ]

    def categories(items: list[dict[str, Any]]) -> set[str]:
        return {
            category
            for item in items
            for category in item.get("categories", [])
            if isinstance(category, str) and category
        }

    benchmark_categories = categories(benchmark_ready)
    source_categories = categories(source_ready)
    missing_benchmark = sorted(REQUIRED_FIXTURE_CATEGORIES - benchmark_categories)
    missing_source = sorted(REQUIRED_FIXTURE_CATEGORIES - source_categories)
    return {
        "total_fixtures": len(fixtures),
        "available_fixtures": len(benchmark_ready),
        "source_ready_fixtures": len(source_ready),
        "required_categories": sorted(REQUIRED_FIXTURE_CATEGORIES),
        "available_categories": sorted(benchmark_categories & REQUIRED_FIXTURE_CATEGORIES),
        "missing_categories": missing_benchmark,
        "complete": not missing_benchmark,
        "source_ready_categories": sorted(source_categories & REQUIRED_FIXTURE_CATEGORIES),
        "missing_source_categories": missing_source,
        "source_complete": not missing_source,
    }


def compare_fixture_sets(
    before_manifest: dict[str, Any],
    after_manifest: dict[str, Any],
    *,
    before_root: Path,
    after_root: Path,
    limits: dict[str, float] | None = None,
) -> dict[str, Any]:
    coverage_report = {
        "before": summarize_fixture_coverage(before_manifest),
        "after": summarize_fixture_coverage(after_manifest),
    }
    before_issues = validate_manifest(before_manifest, root=before_root)
    after_issues = validate_manifest(after_manifest, root=after_root)
    if before_issues or after_issues:
        return {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "passed": False,
            "thresholds_passed": False,
            "coverage": coverage_report,
            "manifest_issues": {"before": before_issues, "after": after_issues},
            "fixtures": [],
        }

    def available_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        fixtures = manifest.get("fixtures")
        if not isinstance(fixtures, list):
            return {}
        return {
            str(item["id"]): item
            for item in fixtures
            if isinstance(item, dict)
            and item.get("status") == "available"
            and isinstance(item.get("id"), str)
        }

    before_fixtures = available_by_id(before_manifest)
    after_fixtures = available_by_id(after_manifest)
    fixture_reports: list[dict[str, Any]] = []
    for fixture_id in sorted(set(before_fixtures) | set(after_fixtures)):
        before_fixture = before_fixtures.get(fixture_id)
        after_fixture = after_fixtures.get(fixture_id)
        if before_fixture is None or after_fixture is None:
            fixture_reports.append(
                {
                    "id": fixture_id,
                    "status": "missing_before" if before_fixture is None else "missing_after",
                    "passed": False,
                }
            )
            continue
        comparison = compare_summaries(
            _fixture_summary(before_fixture, root=before_root),
            _fixture_summary(after_fixture, root=after_root),
            limits=limits,
        )
        fixture_reports.append(
            {
                "id": fixture_id,
                "status": "compared",
                "passed": comparison["acceptance"]["passed"],
                "comparison": comparison,
            }
        )

    thresholds_passed = bool(fixture_reports) and all(item["passed"] for item in fixture_reports)
    coverage_complete = bool(coverage_report["before"]["complete"]) and bool(
        coverage_report["after"]["complete"]
    )
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "passed": thresholds_passed and coverage_complete,
        "thresholds_passed": thresholds_passed,
        "coverage": coverage_report,
        "manifest_issues": {"before": [], "after": []},
        "fixtures": fixture_reports,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(manifest: dict[str, Any], *, root: Path) -> list[str]:
    issues: list[str] = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append(
            f"schema_version must be {MANIFEST_SCHEMA_VERSION}, got {manifest.get('schema_version')!r}"
        )

    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        return issues + ["fixtures must be a JSON array"]

    seen: set[str] = set()
    for index, fixture in enumerate(fixtures):
        label = f"fixtures[{index}]"
        if not isinstance(fixture, dict):
            issues.append(f"{label} must be a JSON object")
            continue

        fixture_id = fixture.get("id")
        if not isinstance(fixture_id, str) or not fixture_id:
            issues.append(f"{label}.id must be a non-empty string")
        elif fixture_id in seen:
            issues.append(f"duplicate fixture id: {fixture_id}")
        else:
            seen.add(fixture_id)

        status = fixture.get("status")
        if status not in {"available", "source-ready", "planned"}:
            issues.append(f"{label}.status must be 'available', 'source-ready', or 'planned'")
            continue
        if status == "planned":
            continue

        source = fixture.get("source")
        if status == "source-ready":
            if not isinstance(source, dict) or source.get("status") != "available":
                issues.append(f"{label}.source must be available for source-ready fixtures")
                continue
            provenance = source.get("provenance")
            if not isinstance(provenance, dict) or provenance.get("kind") not in {
                "real",
                "derived",
                "synthetic",
            }:
                issues.append(
                    f"{label}.source.provenance.kind must be real, derived, or synthetic for source-ready fixtures"
                )
            elif isinstance(provenance.get("generator"), dict):
                generator = provenance["generator"]
                generator_path_value = generator.get("path")
                if not isinstance(generator_path_value, str) or not generator_path_value:
                    issues.append(f"{label}.source.provenance.generator.path must be a non-empty string")
                else:
                    generator_path = root / Path(generator_path_value)
                    if not generator_path.is_file():
                        issues.append(
                            f"{label}.source.provenance.generator missing: {generator_path_value}"
                        )
                    else:
                        expected_hash = generator.get("sha256")
                        if (
                            isinstance(expected_hash, str)
                            and sha256_file(generator_path).lower() != expected_hash.lower()
                        ):
                            issues.append(
                                f"{label}.source.provenance.generator hash mismatch: {generator_path_value}"
                            )
            verification = fixture.get("verification")
            if not isinstance(verification, dict):
                issues.append(f"{label}.verification is required for source-ready fixtures")
            else:
                verification_path_value = verification.get("path")
                if not isinstance(verification_path_value, str) or not verification_path_value:
                    issues.append(f"{label}.verification.path must be a non-empty string")
                else:
                    verification_path = root / Path(verification_path_value)
                    if not verification_path.is_file():
                        issues.append(f"{label}.verification missing: {verification_path_value}")
                    else:
                        expected_hash = verification.get("sha256")
                        if (
                            isinstance(expected_hash, str)
                            and sha256_file(verification_path).lower() != expected_hash.lower()
                        ):
                            issues.append(f"{label}.verification hash mismatch: {verification_path_value}")

            path_value = source.get("path")
            if not isinstance(path_value, str) or not path_value:
                issues.append(f"{label}.source.path is required when source is available")
            else:
                source_path = root / Path(path_value)
                if not source_path.is_file():
                    issues.append(f"{label}.source missing: {path_value}")
                else:
                    expected_hash = source.get("sha256")
                    if isinstance(expected_hash, str) and sha256_file(source_path).lower() != expected_hash.lower():
                        issues.append(f"{label}.source hash mismatch: {path_value}")
            continue

        artifacts = fixture.get("artifacts")
        if not isinstance(artifacts, dict):
            issues.append(f"{label}.artifacts must be an object for available fixtures")
            continue
        result_artifact = artifacts.get("result")
        if not isinstance(result_artifact, dict) or not isinstance(result_artifact.get("path"), str):
            issues.append(f"{label}.artifacts.result.path is required")
            continue

        for artifact_name, artifact in artifacts.items():
            if not isinstance(artifact, dict):
                issues.append(f"{label}.artifacts.{artifact_name} must be an object")
                continue
            path_value = artifact.get("path")
            if not isinstance(path_value, str) or not path_value:
                issues.append(f"{label}.artifacts.{artifact_name}.path must be a non-empty string")
                continue
            artifact_path = root / Path(path_value)
            if not artifact_path.is_file():
                issues.append(f"{label}.artifacts.{artifact_name} missing: {path_value}")
                continue
            expected_hash = artifact.get("sha256")
            if isinstance(expected_hash, str) and sha256_file(artifact_path).lower() != expected_hash.lower():
                issues.append(f"{label}.artifacts.{artifact_name} hash mismatch: {path_value}")

        if isinstance(source, dict) and source.get("status") == "available":
            path_value = source.get("path")
            if not isinstance(path_value, str) or not path_value:
                issues.append(f"{label}.source.path is required when source is available")
            else:
                source_path = root / Path(path_value)
                if not source_path.is_file():
                    issues.append(f"{label}.source missing: {path_value}")
                else:
                    expected_hash = source.get("sha256")
                    if isinstance(expected_hash, str) and sha256_file(source_path).lower() != expected_hash.lower():
                        issues.append(f"{label}.source hash mismatch: {path_value}")

    return issues


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False))


def _command_summarize(args: argparse.Namespace) -> int:
    summary = summarize_artifacts(
        Path(args.result),
        tts_stats_path=Path(args.tts_stats) if args.tts_stats else None,
        metrics_path=Path(args.metrics) if args.metrics else None,
        segment_qa_path=Path(args.segment_qa) if args.segment_qa else None,
        semantic_qa_path=Path(args.semantic_qa) if args.semantic_qa else None,
    )
    _print_json(summary)
    return 0


def _command_validate_manifest(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest must be a JSON object: {manifest_path}")
    issues = validate_manifest(manifest, root=Path(args.root))
    _print_json({"manifest": str(manifest_path), "valid": not issues, "issues": issues})
    return 0 if not issues else 2


def _command_segmentation(args: argparse.Namespace) -> int:
    source_path = Path(args.segments)
    raw = _load_json(source_path)
    if not isinstance(raw, list):
        raise ValueError(f"segments artifact must be a JSON array: {source_path}")
    segments = [Segment.from_dict(item) for item in raw if isinstance(item, dict)]
    config = {
        "min_duration": args.min_duration,
        "target_duration": args.target_duration,
        "max_duration": args.max_duration,
        "pause_threshold": args.pause_threshold,
        "soft_pause_threshold": args.soft_pause_threshold,
        "min_words": args.min_words,
    }
    turns = build_speech_turns(segments, config)
    _print_json(
        {
            "source": str(source_path),
            "before": segmentation_summary(segments),
            "after": segmentation_summary(turns),
            "config": config,
        }
    )
    return 0


def _acceptance_limits_from_args(args: argparse.Namespace) -> dict[str, float]:
    return {
        "rewrite_rate": float(args.max_rewrite_rate),
        "overflow_rate": float(args.max_overflow_rate),
        "tempo.p95": float(args.max_tempo_p95),
    }


def _command_compare(args: argparse.Namespace) -> int:
    before = summarize_artifacts(
        Path(args.before_result),
        tts_stats_path=Path(args.before_tts_stats) if args.before_tts_stats else None,
        metrics_path=Path(args.before_metrics) if args.before_metrics else None,
        segment_qa_path=Path(args.before_segment_qa) if args.before_segment_qa else None,
        semantic_qa_path=Path(args.before_semantic_qa) if args.before_semantic_qa else None,
    )
    after = summarize_artifacts(
        Path(args.after_result),
        tts_stats_path=Path(args.after_tts_stats) if args.after_tts_stats else None,
        metrics_path=Path(args.after_metrics) if args.after_metrics else None,
        segment_qa_path=Path(args.after_segment_qa) if args.after_segment_qa else None,
        semantic_qa_path=Path(args.after_semantic_qa) if args.after_semantic_qa else None,
    )
    report = compare_summaries(before, after, limits=_acceptance_limits_from_args(args))
    _print_json(report)
    return 0 if report["acceptance"]["passed"] else 2


def _command_compare_manifests(args: argparse.Namespace) -> int:
    before_manifest = _load_json(Path(args.before_manifest))
    after_manifest = _load_json(Path(args.after_manifest))
    if not isinstance(before_manifest, dict) or not isinstance(after_manifest, dict):
        raise ValueError("benchmark manifests must be JSON objects")
    report = compare_fixture_sets(
        before_manifest,
        after_manifest,
        before_root=Path(args.before_root),
        after_root=Path(args.after_root),
        limits=_acceptance_limits_from_args(args),
    )
    _print_json(report)
    return 0 if report["passed"] else 2


def _add_acceptance_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-rewrite-rate", type=float, default=DEFAULT_ACCEPTANCE_LIMITS["rewrite_rate"])
    parser.add_argument("--max-overflow-rate", type=float, default=DEFAULT_ACCEPTANCE_LIMITS["overflow_rate"])
    parser.add_argument("--max-tempo-p95", type=float, default=DEFAULT_ACCEPTANCE_LIMITS["tempo.p95"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline vi-dubber benchmark artifact reader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summarize_parser = subparsers.add_parser("summarize", help="Summarize existing result artifacts")
    summarize_parser.add_argument("--result", required=True)
    summarize_parser.add_argument("--tts-stats")
    summarize_parser.add_argument("--metrics")
    summarize_parser.add_argument("--segment-qa")
    summarize_parser.add_argument("--semantic-qa")
    summarize_parser.set_defaults(handler=_command_summarize)

    manifest_parser = subparsers.add_parser("validate-manifest", help="Validate fixture paths and hashes")
    manifest_parser.add_argument("--manifest", default="work/benchmarks/fixtures.json")
    manifest_parser.add_argument("--root", default=".")
    manifest_parser.set_defaults(handler=_command_validate_manifest)

    segmentation_parser = subparsers.add_parser(
        "segmentation",
        help="Compare legacy segment windows with deterministic speech turns",
    )
    segmentation_parser.add_argument("--segments", required=True)
    segmentation_parser.add_argument("--min-duration", type=float, default=1.2)
    segmentation_parser.add_argument("--target-duration", type=float, default=3.6)
    segmentation_parser.add_argument("--max-duration", type=float, default=7.0)
    segmentation_parser.add_argument("--pause-threshold", type=float, default=0.8)
    segmentation_parser.add_argument("--soft-pause-threshold", type=float, default=0.28)
    segmentation_parser.add_argument("--min-words", type=int, default=3)
    segmentation_parser.set_defaults(handler=_command_segmentation)

    compare_parser = subparsers.add_parser("compare", help="Compare before/after result artifacts")
    compare_parser.add_argument("--before-result", required=True)
    compare_parser.add_argument("--after-result", required=True)
    compare_parser.add_argument("--before-tts-stats")
    compare_parser.add_argument("--after-tts-stats")
    compare_parser.add_argument("--before-metrics")
    compare_parser.add_argument("--after-metrics")
    compare_parser.add_argument("--before-segment-qa")
    compare_parser.add_argument("--after-segment-qa")
    compare_parser.add_argument("--before-semantic-qa")
    compare_parser.add_argument("--after-semantic-qa")
    _add_acceptance_args(compare_parser)
    compare_parser.set_defaults(handler=_command_compare)

    compare_manifests_parser = subparsers.add_parser(
        "compare-manifests",
        help="Compare matching available fixtures across two benchmark manifests",
    )
    compare_manifests_parser.add_argument("--before-manifest", required=True)
    compare_manifests_parser.add_argument("--after-manifest", required=True)
    compare_manifests_parser.add_argument("--before-root", default=".")
    compare_manifests_parser.add_argument("--after-root", default=".")
    _add_acceptance_args(compare_manifests_parser)
    compare_manifests_parser.set_defaults(handler=_command_compare_manifests)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
