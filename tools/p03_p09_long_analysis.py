from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from vi_dubber.timing import (
    TIMING_POLICY_VERSION,
    allocate_timing_windows,
    rebalance_timing_windows,
    timing_action,
)
from vi_dubber.types import Segment


SCHEMA_VERSION = 1


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "samples": len(values),
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": max(values) if values else None,
    }


def _rate(count: int, total: int) -> float | None:
    return count / total if total else None


def _duration_bucket(duration: float) -> str:
    if duration < 1.0:
        return "lt_1s"
    if duration < 2.0:
        return "1_to_lt_2s"
    if duration < 4.0:
        return "2_to_lt_4s"
    if duration < 7.0:
        return "4_to_lt_7s"
    return "gte_7s"


def _timing_config(config: dict[str, Any]) -> dict[str, float]:
    timing = config.get("timing") if isinstance(config.get("timing"), dict) else {}
    return {
        "max_speedup": float(timing.get("max_speedup", 1.25)),
        "max_slowdown": float(timing.get("max_slowdown", 0.92)),
        "rewrite_threshold": float(timing.get("rewrite_threshold", 1.25)),
        "segment_pad_seconds": float(timing.get("segment_pad_ms", 35.0)) / 1000.0,
        "max_borrow_seconds": float(timing.get("max_borrow_seconds", 0.35)),
        "preferred_tempo": 1.20,
    }


def analyze_long_artifacts(
    *,
    segments_path: Path,
    tts_stats_path: Path,
    result_path: Path,
    config_path: Path,
    smart_segments_path: Path | None = None,
    smart_tts_stats_path: Path | None = None,
    smart_result_path: Path | None = None,
) -> dict[str, Any]:
    raw_segments = _load_json(segments_path)
    tts_stats = _load_json(tts_stats_path)
    result = _load_json(result_path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_segments, list) or not all(isinstance(item, dict) for item in raw_segments):
        raise ValueError("segments artifact must be a JSON array of objects")
    if not isinstance(tts_stats, list) or not all(isinstance(item, dict) for item in tts_stats):
        raise ValueError("tts stats artifact must be a JSON array of objects")
    if not isinstance(result, dict):
        raise ValueError("result artifact must be a JSON object")
    if len(raw_segments) != len(tts_stats):
        raise ValueError(
            f"baseline segment/tts count mismatch: {len(raw_segments)} != {len(tts_stats)}"
        )

    segments = [Segment.from_dict(item) for item in raw_segments]
    stats_by_id = {int(item["segment_id"]): item for item in tts_stats}
    if set(stats_by_id) != {segment.id for segment in segments}:
        raise ValueError("baseline segment ids do not match tts_stats segment ids")

    durations = [max(0.0, segment.end - segment.start) for segment in segments]
    word_count = sum(len(segment.words) for segment in segments)
    word_timed_segments = sum(bool(segment.words) for segment in segments)
    rewritten_ids = {
        int(item["segment_id"])
        for item in tts_stats
        if int(item.get("rewrites") or 0) > 0
    }
    overflow_ids = {
        int(item["segment_id"])
        for item in tts_stats
        if float(item.get("final_duration") or 0.0)
        > float(item.get("target_duration") or 0.0) * 1.03 + 1e-9
    }
    tempos = [float(item["tempo"]) for item in tts_stats if item.get("tempo") is not None]
    generated_ratios = [
        float(item["generated_duration"]) / float(item["target_duration"])
        for item in tts_stats
        if float(item.get("target_duration") or 0.0) > 0.0
    ]

    expected_rewritten = int(result.get("rewritten_segments", -1))
    expected_overflow = int(result.get("overflow_segments", -1))
    consistency_issues: list[str] = []
    if expected_rewritten != len(rewritten_ids):
        consistency_issues.append(
            f"rewritten_segments result={expected_rewritten} derived={len(rewritten_ids)}"
        )
    if expected_overflow != len(overflow_ids):
        consistency_issues.append(
            f"overflow_segments result={expected_overflow} derived={len(overflow_ids)}"
        )

    buckets: dict[str, dict[str, Any]] = {}
    for bucket_name in ("lt_1s", "1_to_lt_2s", "2_to_lt_4s", "4_to_lt_7s", "gte_7s"):
        bucket_segments = [
            segment
            for segment in segments
            if _duration_bucket(max(0.0, segment.end - segment.start)) == bucket_name
        ]
        ids = {segment.id for segment in bucket_segments}
        bucket_tempos = [
            float(stats_by_id[segment_id]["tempo"])
            for segment_id in ids
            if stats_by_id[segment_id].get("tempo") is not None
        ]
        rewritten = len(ids & rewritten_ids)
        overflow = len(ids & overflow_ids)
        buckets[bucket_name] = {
            "segments": len(ids),
            "share": _rate(len(ids), len(segments)),
            "rewritten_segments": rewritten,
            "rewrite_rate": _rate(rewritten, len(ids)),
            "overflow_segments": overflow,
            "overflow_rate": _rate(overflow, len(ids)),
            "tempo_p95": _percentile(bucket_tempos, 0.95),
        }

    timing_cfg = _timing_config(config)
    baseline_windows = allocate_timing_windows(
        segments,
        pad_seconds=timing_cfg["segment_pad_seconds"],
        max_borrow_seconds=timing_cfg["max_borrow_seconds"],
    )
    measured_durations = {
        segment_id: float(item.get("generated_duration") or 0.0)
        for segment_id, item in stats_by_id.items()
    }
    current_windows = rebalance_timing_windows(
        segments,
        baseline_windows,
        measured_durations,
        preferred_tempo=timing_cfg["preferred_tempo"],
        max_extra_borrow_seconds=timing_cfg["max_borrow_seconds"],
    )
    actions = Counter()
    current_ratios: list[float] = []
    for segment in segments:
        generated = measured_durations[segment.id]
        target = current_windows[segment.id].target_duration
        current_ratios.append(generated / target if target > 0.0 else 0.0)
        actions[
            timing_action(
                generated,
                target,
                max_speedup=timing_cfg["max_speedup"],
                rewrite_threshold=timing_cfg["rewrite_threshold"],
                max_slowdown=timing_cfg["max_slowdown"],
            )
        ] += 1

    ordered = sorted(segments, key=lambda item: (item.start, item.end, item.id))
    timing_collisions = sum(
        current_windows[left.id].end > current_windows[right.id].start + 1e-9
        for left, right in zip(ordered, ordered[1:])
        if left.end <= right.start
    )
    initial_borrow = sum(
        window.borrowed_before + window.borrowed_after for window in baseline_windows.values()
    )
    current_borrow = sum(
        window.borrowed_before + window.borrowed_after for window in current_windows.values()
    )

    smart_paths = (smart_segments_path, smart_tts_stats_path, smart_result_path)
    smart_all_present = all(path is not None and path.is_file() for path in smart_paths)
    if smart_all_present:
        smart_raw = _load_json(smart_segments_path)  # type: ignore[arg-type]
        smart_stats = _load_json(smart_tts_stats_path)  # type: ignore[arg-type]
        smart_result = _load_json(smart_result_path)  # type: ignore[arg-type]
        smart_word_count = sum(len(item.get("words") or []) for item in smart_raw if isinstance(item, dict))
        smart_runtime = {
            "segments": len(smart_raw),
            "word_tokens": smart_word_count,
            "rewritten_segments": smart_result.get("rewritten_segments"),
            "overflow_segments": smart_result.get("overflow_segments"),
            "tempo": _distribution(
                [float(item["tempo"]) for item in smart_stats if item.get("tempo") is not None]
            ),
        }
        smart_ab = {
            "status": "available",
            "machine_verifiable": smart_word_count > 0,
            "runtime": smart_runtime,
            "reason": None if smart_word_count > 0 else "smart artifact has no retained word timing",
        }
    else:
        missing = [
            name
            for name, path in zip(
                ("smart_segments", "smart_tts_stats", "smart_result"),
                smart_paths,
            )
            if path is None or not path.is_file()
        ]
        smart_ab = {
            "status": "blocked",
            "machine_verifiable": False,
            "missing_artifacts": missing,
            "reason": "no complete retained long smart runtime artifact set",
        }

    p03_blockers: list[str] = []
    if word_count == 0:
        p03_blockers.append("legacy long source has no retained word timing")
    if not smart_ab["machine_verifiable"]:
        p03_blockers.append("no machine-verifiable long smart segmentation/runtime A/B")
    p09_blockers = [
        "timing-only counterfactual reuses legacy generated durations; it is not an executed P03+P09 runtime",
        "no long smart runtime overflow/tempo receipt",
        "human timing/naturalness listening A/B remains required",
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "artifacts": {
            "segments_source": {"path": str(segments_path), "sha256": _sha256(segments_path)},
            "tts_stats": {"path": str(tts_stats_path), "sha256": _sha256(tts_stats_path)},
            "result": {"path": str(result_path), "sha256": _sha256(result_path)},
            "config": {"path": str(config_path), "sha256": _sha256(config_path)},
        },
        "baseline": {
            "duration_seconds": result.get("duration_seconds"),
            "segments": len(segments),
            "word_tokens": word_count,
            "segments_with_word_timing": word_timed_segments,
            "segment_duration_seconds": {
                **_distribution(durations),
                "lt_1s": sum(duration < 1.0 for duration in durations),
                "lt_1_2s": sum(duration < 1.2 for duration in durations),
            },
            "rewrite": {
                "segments": len(rewritten_ids),
                "rate": _rate(len(rewritten_ids), len(segments)),
                "total_attempts": sum(int(item.get("rewrites") or 0) for item in tts_stats),
                "attempt_distribution": dict(
                    sorted(Counter(int(item.get("rewrites") or 0) for item in tts_stats).items())
                ),
            },
            "overflow": {
                "segments": len(overflow_ids),
                "rate": _rate(len(overflow_ids), len(segments)),
            },
            "tempo": {
                **_distribution(tempos),
                "gt_1_15": sum(value > 1.15 + 1e-9 for value in tempos),
                "gt_1_20": sum(value > 1.20 + 1e-9 for value in tempos),
                "at_max_1_25": sum(abs(value - 1.25) <= 1e-9 for value in tempos),
            },
            "generated_to_target_ratio": _distribution(generated_ratios),
            "by_source_duration_bucket": buckets,
            "consistency_issues": consistency_issues,
        },
        "current_timing_counterfactual": {
            "policy_version": TIMING_POLICY_VERSION,
            "config": timing_cfg,
            "basis": "legacy source windows plus retained post-rewrite generated_duration values",
            "initial_borrow_seconds": initial_borrow,
            "rebalanced_borrow_seconds": current_borrow,
            "collisions": timing_collisions,
            "actions": dict(sorted(actions.items())),
            "generated_to_rebalanced_target_ratio": _distribution(current_ratios),
            "acceptance_evidence": False,
        },
        "smart_long_ab": smart_ab,
        "acceptance": {
            "p03_can_move_from_partial": False,
            "p09_can_move_from_partial": False,
            "p03_blockers": p03_blockers,
            "p09_blockers": p09_blockers,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze retained P03/P09 long-run evidence")
    parser.add_argument("--segments", required=True, type=Path)
    parser.add_argument("--tts-stats", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--smart-segments", type=Path)
    parser.add_argument("--smart-tts-stats", type=Path)
    parser.add_argument("--smart-result", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = analyze_long_artifacts(
        segments_path=args.segments,
        tts_stats_path=args.tts_stats,
        result_path=args.result,
        config_path=args.config,
        smart_segments_path=args.smart_segments,
        smart_tts_stats_path=args.smart_tts_stats,
        smart_result_path=args.smart_result,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not report["baseline"]["consistency_issues"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
