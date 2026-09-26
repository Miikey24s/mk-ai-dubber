from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml


for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from vi_dubber import pipeline
from vi_dubber.artifacts import load_stage_manifest
from vi_dubber.media import media_duration
from vi_dubber.translate import WebGptTranslator, load_glossary, webgpt_route_info
from vi_dubber.types import Segment


DEFAULT_INPUT = PROJECT_ROOT / "work" / "benchmarks" / "p23-long-source-2x.mp4"
DEFAULT_VARIANTS = (
    "baseline:asr=4,tts=4,translation=2,chunk=1500",
    "chunk20m:asr=4,tts=4,translation=2,chunk=1200",
    "tts8:asr=4,tts=8,translation=2,chunk=1500",
)
MIN_PROMOTION_TRIALS = 3


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    asr_batch: int
    tts_batch: int
    translation_concurrency: int
    chunk_target_seconds: int


def parse_variant(raw: str) -> Variant:
    name, separator, payload = raw.partition(":")
    if not separator or not name.strip():
        raise ValueError(f"invalid variant: {raw!r}")
    values: dict[str, int] = {}
    for item in payload.split(","):
        key, equals, value = item.partition("=")
        if not equals:
            raise ValueError(f"invalid variant field: {item!r}")
        values[key.strip()] = int(value)
    required = {"asr", "tts", "translation", "chunk"}
    missing = required - set(values)
    if missing:
        raise ValueError(f"variant {name!r} missing fields: {sorted(missing)}")
    if any(values[key] <= 0 for key in required):
        raise ValueError("all variant values must be positive")
    return Variant(
        name=name.strip(),
        asr_batch=values["asr"],
        tts_batch=values["tts"],
        translation_concurrency=values["translation"],
        chunk_target_seconds=values["chunk"],
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalized_script(path: Path, field: str) -> str:
    raw = _read_json(path)
    if not isinstance(raw, list):
        return ""
    text = " ".join(str(item.get(field, "")) for item in raw if isinstance(item, dict))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _similarity(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _write_variant_config(base_path: Path, target: Path, variant: Variant) -> None:
    raw = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"invalid config: {base_path}")
    config = copy.deepcopy(raw)

    asr = dict(config.get("asr") or {})
    asr["batch_size"] = variant.asr_batch
    config["asr"] = asr

    tts = dict(config.get("tts") or {})
    tts["batch_size"] = variant.tts_batch
    config["tts"] = tts

    translation = dict(config.get("translation") or {})
    translation["webgpt_concurrency"] = variant.translation_concurrency
    glossary = Path(str(translation.get("glossary") or "glossary.yaml"))
    if not glossary.is_absolute():
        glossary = (base_path.parent / glossary).resolve()
    translation["glossary"] = str(glossary)
    config["translation"] = translation

    config["longform"] = {
        **dict(config.get("longform") or {}),
        "enabled": True,
        "target_seconds": variant.chunk_target_seconds,
        "min_seconds": min(900, max(300, int(variant.chunk_target_seconds * 0.6))),
        # This harness uses a ~33 minute fixture. Keep max below that duration so
        # every default variant actually exercises the multi-chunk preview path.
        "max_seconds": max(
            variant.chunk_target_seconds,
            int(variant.chunk_target_seconds * 1.2),
        ),
        "boundary_search_seconds": 60.0,
        "context_seconds": 2.0,
        "single_chunk_threshold_seconds": 900,
        "chunked_tts_enabled": True,
        "progressive_preview_enabled": True,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _assert_variant_resolved_config(config_path: Path, variant: Variant) -> dict[str, int]:
    """Fail closed when profile resolution masks one of the requested tuning axes."""
    resolved = pipeline.load_config(config_path)
    actual = {
        "asr": int(resolved.get("asr", {}).get("batch_size", 0)),
        "tts": int(resolved.get("tts", {}).get("batch_size", 0)),
        "translation": int(resolved.get("translation", {}).get("webgpt_concurrency", 0)),
        "chunk": int(resolved.get("longform", {}).get("target_seconds", 0)),
    }
    expected = {
        "asr": variant.asr_batch,
        "tts": variant.tts_batch,
        "translation": variant.translation_concurrency,
        "chunk": variant.chunk_target_seconds,
    }
    mismatches = [
        f"{key}={actual[key]} (requested {expected[key]})"
        for key in expected
        if actual[key] != expected[key]
    ]
    if mismatches:
        raise RuntimeError(
            f"variant {variant.name!r} bị profile/config override: " + ", ".join(mismatches)
        )
    return actual


def _live_webgpt_preflight(base_path: Path, root: Path) -> dict[str, Any]:
    raw = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"invalid config: {base_path}")
    translation = dict(raw.get("translation") or {})
    translation.update(
        {
            "webgpt_transport": "direct-responses",
            "webgpt_concurrency": 1,
            "global_context_enabled": False,
        }
    )
    glossary_path = Path(str(translation.get("glossary") or "glossary.yaml"))
    if not glossary_path.is_absolute():
        glossary_path = (base_path.parent / glossary_path).resolve()
    glossary = load_glossary(glossary_path)
    segment = Segment(
        id=1,
        start=0.0,
        end=3.0,
        text="Keep the FVG visible and do not move the stop loss yet.",
        speaker="SPEAKER_00",
    )
    work_dir = root / "_webgpt_preflight"
    translator = WebGptTranslator(translation, work_dir, retry_budget=0)
    started = time.perf_counter()
    with translator.running() as client:
        client.translate_segments([segment], glossary)
    wall = time.perf_counter() - started
    stats = translator.stats()
    attempts = int(stats.get("webgpt_attempts") or 0)
    if attempts < 1:
        raise RuntimeError("WebGPT preflight did not issue a live request")
    return {
        "passed": True,
        "wall_seconds": wall,
        "webgpt_attempts": attempts,
        "model": stats.get("model"),
        "transport": stats.get("transport"),
    }


def _preview_gate(job_dir: Path) -> dict[str, Any]:
    plan_path = job_dir / "chunks" / "plan.json"
    if not plan_path.is_file():
        return {"passed": False, "reason": "missing_chunk_plan", "total_chunks": 0, "valid_previews": 0}
    plan = _read_json(plan_path)
    chunks = plan.get("chunks") if isinstance(plan, dict) else None
    if not isinstance(chunks, list) or len(chunks) < 2:
        return {
            "passed": False,
            "reason": "fixture_did_not_exercise_multi_chunk_path",
            "total_chunks": len(chunks or []),
            "valid_previews": 0,
        }
    valid = 0
    invalid: list[str] = []
    for item in chunks:
        chunk_id = str(item.get("chunk_id") or "") if isinstance(item, dict) else ""
        manifest = load_stage_manifest(
            job_dir / "chunks" / chunk_id / "manifests" / "preview.json",
            job_dir=job_dir,
            expected_stage="chunk_preview",
            verify_artifacts=True,
        )
        if manifest is None:
            invalid.append(chunk_id)
        else:
            valid += 1
    return {
        "passed": not invalid,
        "reason": None if not invalid else "missing_or_invalid_preview",
        "total_chunks": len(chunks),
        "valid_previews": valid,
        "invalid_chunks": invalid,
    }


def _run_variant(
    variant: Variant,
    *,
    input_path: Path,
    base_config: Path,
    root: Path,
) -> dict[str, Any]:
    variant_root = root / variant.name
    config_path = variant_root / "config.yaml"
    _write_variant_config(base_config, config_path, variant)
    resolved_tuning = _assert_variant_resolved_config(config_path, variant)
    jobs_root = variant_root / "jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)
    original_work_dir = pipeline.WORK_DIR
    pipeline.WORK_DIR = jobs_root
    started = time.perf_counter()
    try:
        result = pipeline.run_pipeline(
            input_path,
            variant_root / "output.mp4",
            config_path,
            translation_provider="webgpt",
            diarize_override=False,
            resume=False,
        )
    except Exception as exc:
        return {
            "variant": asdict(variant),
            "status": "failed",
            "wall_seconds": time.perf_counter() - started,
            "error": f"{type(exc).__name__}: {exc}",
            "quality_gate": False,
            "fault_gate": False,
            "resolved_tuning": resolved_tuning,
        }
    finally:
        pipeline.WORK_DIR = original_work_dir

    wall = time.perf_counter() - started
    job_dir = Path(result["work_dir"])
    metrics = _read_json(job_dir / "metrics.json")
    counters = metrics.get("counters") if isinstance(metrics, dict) else {}
    counters = counters if isinstance(counters, dict) else {}
    qa = result.get("qa") if isinstance(result.get("qa"), dict) else {}
    preview = _preview_gate(job_dir)
    pressure_failures = int(counters.get("webgpt_pressure_failures") or 0)
    cpu_fallbacks = int(counters.get("tts_cpu_fallbacks") or 0)
    output_ok = Path(str(result.get("output") or "")).is_file()
    qa_passed = bool(qa.get("passed", False))
    fault_gate = bool(output_ok and preview["passed"] and pressure_failures == 0 and cpu_fallbacks == 0)
    quality_gate = bool(qa_passed and preview["passed"])
    return {
        "variant": asdict(variant),
        "status": "passed" if quality_gate and fault_gate else "gate_failed",
        "wall_seconds": wall,
        "result_elapsed_seconds": result.get("elapsed_seconds"),
        "real_time_factor": result.get("real_time_factor"),
        "job_dir": str(job_dir),
        "quality_gate": quality_gate,
        "fault_gate": fault_gate,
        "qa_passed": qa_passed,
        "preview_gate": preview,
        "webgpt_pressure_failures": pressure_failures,
        "tts_cpu_fallbacks": cpu_fallbacks,
        "resolved_tuning": resolved_tuning,
        "source_script": _normalized_script(job_dir / "segments_source.json", "text"),
        "translated_script": _normalized_script(job_dir / "segments_vi.json", "vi"),
    }


def _select_recommendation(
    runs: list[dict[str, Any]],
    *,
    required_trials: int = 1,
) -> dict[str, Any]:
    if not runs:
        return {"eligible_for_manual_promotion": False, "reason": "no_runs"}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        name = str(run.get("variant", {}).get("name") or "")
        grouped.setdefault(name, []).append(run)
    baseline_runs = grouped.get("baseline") or [runs[0]]
    if any(run.get("status") != "passed" for run in baseline_runs):
        return {"eligible_for_manual_promotion": False, "reason": "baseline_gate_failed"}
    if len(baseline_runs) < required_trials:
        return {
            "eligible_for_manual_promotion": False,
            "reason": "insufficient_repeated_trials",
            "required_trials": required_trials,
            "baseline_trials": len(baseline_runs),
        }
    baseline_by_trial = {
        int(run.get("trial") or index + 1): run
        for index, run in enumerate(baseline_runs)
    }
    baseline_wall = statistics.median(float(run["wall_seconds"]) for run in baseline_runs)
    candidates: list[dict[str, Any]] = []
    for name, candidate_runs in grouped.items():
        if name == "baseline" or len(candidate_runs) < required_trials:
            continue
        if any(run.get("status") != "passed" for run in candidate_runs):
            continue
        source_similarities: list[float] = []
        translated_similarities: list[float] = []
        for index, run in enumerate(candidate_runs):
            trial = int(run.get("trial") or index + 1)
            baseline = baseline_by_trial.get(trial, baseline_runs[min(index, len(baseline_runs) - 1)])
            source_similarities.append(
                _similarity(
                    str(baseline.get("source_script") or ""),
                    str(run.get("source_script") or ""),
                )
            )
            translated_similarities.append(
                _similarity(
                    str(baseline.get("translated_script") or ""),
                    str(run.get("translated_script") or ""),
                )
            )
        candidate_wall = statistics.median(float(run["wall_seconds"]) for run in candidate_runs)
        speedup = baseline_wall / max(0.001, candidate_wall)
        source_similarity = min(source_similarities)
        translated_similarity = min(translated_similarities)
        evaluated = {
            "name": name,
            "speedup_vs_baseline": speedup,
            "baseline_median_wall_seconds": baseline_wall,
            "candidate_median_wall_seconds": candidate_wall,
            "trials": len(candidate_runs),
            "source_similarity": source_similarity,
            "translated_similarity": translated_similarity,
            "quality_equivalent": source_similarity >= 0.995 and translated_similarity >= 0.98,
        }
        if evaluated["quality_equivalent"] and speedup >= 1.03:
            candidates.append(evaluated)
    if not candidates:
        return {
            "eligible_for_manual_promotion": False,
            "reason": "no_candidate_cleared_quality_fault_and_speedup_gates",
        }
    winner = max(candidates, key=lambda item: item["speedup_vs_baseline"])
    return {
        "eligible_for_manual_promotion": True,
        "reason": "candidate_cleared_all_gates",
        "candidate": winner,
        "auto_applied": False,
    }


def _baseline_gate_failed(variant: Variant, run: dict[str, Any]) -> bool:
    return variant.name == "baseline" and run.get("status") != "passed"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P23 same-fixture whole-job auto-tune harness. Never edits config.yaml.",
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    parser.add_argument("--variant", action="append", default=[])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--repeats", type=int, default=MIN_PROMOTION_TRIALS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1:
        raise ValueError("--repeats must be >= 1")

    input_path = args.input.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    base_config = args.config.resolve()
    variants = [parse_variant(item) for item in (args.variant or DEFAULT_VARIANTS)]
    if len({item.name for item in variants}) != len(variants):
        raise ValueError("variant names must be unique")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = (args.output_dir or PROJECT_ROOT / "work" / "benchmarks" / f"p23-autotune-{stamp}").resolve()
    root.mkdir(parents=True, exist_ok=True)
    duration = media_duration(input_path)
    route = webgpt_route_info(yaml.safe_load(base_config.read_text(encoding="utf-8"))["translation"])
    manifest = {
        "schema_version": 1,
        "kind": "p23_whole_job_autotune",
        "created_at": datetime.now(UTC).isoformat(),
        "input": str(input_path),
        "input_sha256": _sha256(input_path),
        "duration_seconds": duration,
        "same_fixture_required": True,
        "auto_apply": False,
        "repeats": args.repeats,
        "minimum_promotion_trials": MIN_PROMOTION_TRIALS,
        "variants": [asdict(item) for item in variants],
        "route": route,
    }
    _write_json(root / "manifest.json", manifest)
    if duration <= 1800:
        _write_json(root / "results.json", {**manifest, "status": "blocked", "reason": "fixture_must_exceed_30_minutes"})
        print("BLOCKED: fixture must exceed 30 minutes to exercise multi-chunk preview")
        return 2
    if args.dry_run:
        resolved_variants: list[dict[str, Any]] = []
        for variant in variants:
            config_path = root / variant.name / "config.yaml"
            _write_variant_config(base_config, config_path, variant)
            resolved_variants.append(
                {
                    "variant": asdict(variant),
                    "resolved_tuning": _assert_variant_resolved_config(config_path, variant),
                }
            )
        _write_json(
            root / "results.json",
            {
                **manifest,
                "status": "dry_run_ready",
                "resolved_variants": resolved_variants,
                "run_order": [
                    [item.name for item in variants[offset:] + variants[:offset]]
                    for offset in (index % len(variants) for index in range(args.repeats))
                ],
            },
        )
        print(f"dry-run receipt={root / 'results.json'}")
        return 0
    if not route.get("ready"):
        _write_json(root / "results.json", {**manifest, "status": "blocked", "reason": route.get("reason")})
        print(f"BLOCKED: Dedicated Dubber-WebGPT not ready: {route.get('reason')}")
        return 2

    try:
        preflight = _live_webgpt_preflight(base_config, root)
    except Exception as exc:
        failure = {
            **manifest,
            "status": "blocked",
            "reason": "webgpt_live_preflight_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        _write_json(root / "results.json", failure)
        print(f"BLOCKED: live WebGPT preflight failed: {exc}")
        return 2
    manifest = {**manifest, "webgpt_live_preflight": preflight}
    _write_json(root / "manifest.json", manifest)

    runs: list[dict[str, Any]] = []
    for trial_index in range(args.repeats):
        trial = trial_index + 1
        offset = trial_index % len(variants)
        ordered = variants[offset:] + variants[:offset]
        for variant in ordered:
            print(
                f"[trial {trial}/{args.repeats} · {variant.name}] "
                f"asr={variant.asr_batch} tts={variant.tts_batch} "
                f"c={variant.translation_concurrency} chunk={variant.chunk_target_seconds}s"
            )
            run = _run_variant(
                variant,
                input_path=input_path,
                base_config=base_config,
                root=root / f"trial-{trial:02d}",
            )
            run["trial"] = trial
            runs.append(run)
            print(f"  status={run['status']} wall={float(run['wall_seconds']):.1f}s")

            # Candidate comparisons are meaningless when any baseline trial
            # fails its quality/fault gate. Stop before spending more compute.
            if _baseline_gate_failed(variant, run):
                recommendation = _select_recommendation(
                    runs,
                    required_trials=MIN_PROMOTION_TRIALS,
                )
                result = {
                    **manifest,
                    "status": "blocked",
                    "runs": runs,
                    "recommendation": recommendation,
                }
                _write_json(root / "results.json", result)
                print(f"BLOCKED: baseline gate failed; receipt={root / 'results.json'}")
                return 1

    recommendation = _select_recommendation(runs, required_trials=MIN_PROMOTION_TRIALS)
    result = {**manifest, "status": "complete", "runs": runs, "recommendation": recommendation}
    _write_json(root / "results.json", result)
    print(f"receipt={root / 'results.json'}")
    print(json.dumps(recommendation, ensure_ascii=False, indent=2))
    return 0 if all(run.get("status") == "passed" for run in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
