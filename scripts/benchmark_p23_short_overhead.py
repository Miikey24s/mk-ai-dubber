from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "work" / "benchmarks" / "CP2-short-smart-source.mp4"


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
    if not path.is_file():
        return ""
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


def _resolved_axes(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": config.get("profile"),
        "asr": {
            key: config.get("asr", {}).get(key)
            for key in ("model", "device", "compute_type", "batch_size")
        },
        "tts": {
            key: config.get("tts", {}).get(key)
            for key in ("backend", "device", "precision", "batch_size")
        },
        "translation": {
            key: config.get("translation", {}).get(key)
            for key in (
                "webgpt_base_url",
                "webgpt_transport",
                "webgpt_model",
                "webgpt_concurrency",
                "codex_segments_per_batch",
            )
        },
        "longform": {
            key: config.get("longform", {}).get(key)
            for key in ("enabled", "single_chunk_threshold_seconds", "target_seconds")
        },
    }


def _worker(args: argparse.Namespace) -> int:
    main_root = Path(args.main_root).resolve()
    trial_root = Path(args.trial_root).resolve()
    result_path = Path(args.worker_result).resolve()
    trial_root.mkdir(parents=True, exist_ok=True)

    try:
        from vi_dubber import asr, pipeline, runtime, translate

        runtime.PROJECT_ROOT = main_root
        runtime.TOOLS_DIR = main_root / "tools"
        runtime.MODELS_DIR = main_root / "models"
        runtime.WORK_DIR = trial_root / "jobs"
        pipeline.MODELS_DIR = runtime.MODELS_DIR
        pipeline.WORK_DIR = runtime.WORK_DIR
        asr.MODELS_DIR = runtime.MODELS_DIR
        translate.MODELS_DIR = runtime.MODELS_DIR
        translate.PROJECT_ROOT = main_root

        config_path = Path(args.config).resolve()
        resolved = pipeline.load_config(config_path)
        started = time.perf_counter()
        result = pipeline.run_pipeline(
            Path(args.input).resolve(),
            trial_root / "output.mp4",
            config_path,
            translation_provider="webgpt",
            diarize_override=False,
            resume=False,
        )
        wall = time.perf_counter() - started
        job_dir = Path(result["work_dir"])
        metrics = _read_json(job_dir / "metrics.json") if (job_dir / "metrics.json").is_file() else {}
        qa = result.get("qa") if isinstance(result.get("qa"), dict) else {}
        terminology = (
            _read_json(job_dir / "terminology_qa.json")
            if (job_dir / "terminology_qa.json").is_file()
            else None
        )
        counters = metrics.get("counters") if isinstance(metrics, dict) else {}
        counters = counters if isinstance(counters, dict) else {}
        payload = {
            "status": "passed",
            "label": args.label,
            "wall_seconds": wall,
            "result_elapsed_seconds": result.get("elapsed_seconds"),
            "real_time_factor": result.get("real_time_factor"),
            "job_dir": str(job_dir),
            "output": str(result.get("output") or ""),
            "output_exists": Path(str(result.get("output") or "")).is_file(),
            "qa_passed": bool(qa.get("passed", False)),
            "qa": qa,
            "terminology_qa": terminology,
            "resolved_axes": _resolved_axes(resolved),
            "metrics": metrics,
            "macro_chunks": int(counters.get("macro_chunks") or 0),
            "chunk_plan_exists": (job_dir / "chunks" / "plan.json").is_file(),
            "source_script": _normalized_script(job_dir / "segments_source.json", "text"),
            "translated_script": _normalized_script(job_dir / "segments_vi.json", "vi"),
        }
        _write_json(result_path, payload)
        return 0
    except BaseException as exc:
        _write_json(
            result_path,
            {
                "status": "failed",
                "label": args.label,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            },
        )
        return 2


def _write_comparable_config(
    source: Path,
    target: Path,
    profile: str,
    transport: str,
    *,
    longform_enabled: bool,
) -> None:
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"invalid config: {source}")
    raw["profile"] = profile
    translation = dict(raw.get("translation") or {})
    translation["webgpt_transport"] = transport
    glossary = Path(str(translation.get("glossary") or "glossary.yaml"))
    if not glossary.is_absolute():
        glossary = (source.parent / glossary).resolve()
    translation["glossary"] = str(glossary)
    raw["translation"] = translation
    longform = dict(raw.get("longform") or {})
    longform["enabled"] = longform_enabled
    raw["longform"] = longform
    target.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _run_one(
    *,
    main_root: Path,
    label: str,
    trial: int,
    input_path: Path,
    config_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    trial_root = output_root / f"trial-{trial:02d}" / label
    result_path = trial_root / "worker-result.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(main_root / "src")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--main-root",
        str(main_root),
        "--label",
        label,
        "--trial-root",
        str(trial_root),
        "--worker-result",
        str(result_path),
        "--input",
        str(input_path),
        "--config",
        str(config_path),
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=main_root, env=env, check=False)
    outer_wall = time.perf_counter() - started
    if not result_path.is_file():
        return {
            "status": "failed",
            "label": label,
            "trial": trial,
            "outer_wall_seconds": outer_wall,
            "error": f"worker exited {completed.returncode} without a result artifact",
        }
    result = _read_json(result_path)
    result["trial"] = trial
    result["outer_wall_seconds"] = outer_wall
    result["worker_exit_code"] = completed.returncode
    return result


def _summarize(runs: list[dict[str, Any]], max_slowdown_ratio: float) -> dict[str, Any]:
    baseline = [item for item in runs if item.get("label") == "baseline" and item.get("status") == "passed"]
    candidate = [item for item in runs if item.get("label") == "candidate" and item.get("status") == "passed"]
    if not baseline or not candidate:
        return {"gate": "BLOCKED", "reason": "missing_successful_ab_run"}
    baseline_axes = dict(baseline[0].get("resolved_axes") or {})
    candidate_axes = dict(candidate[0].get("resolved_axes") or {})
    baseline_longform = dict(baseline_axes.pop("longform", {}) or {})
    candidate_longform = dict(candidate_axes.pop("longform", {}) or {})
    axes_equal = baseline_axes == candidate_axes
    base_wall = statistics.median(float(item["wall_seconds"]) for item in baseline)
    cand_wall = statistics.median(float(item["wall_seconds"]) for item in candidate)
    slowdown_ratio = cand_wall / max(0.001, base_wall)
    source_similarity = min(
        _similarity(str(left.get("source_script") or ""), str(right.get("source_script") or ""))
        for left, right in zip(baseline, candidate)
    )
    translated_similarity = min(
        _similarity(str(left.get("translated_script") or ""), str(right.get("translated_script") or ""))
        for left, right in zip(baseline, candidate)
    )
    correctness = bool(
        axes_equal
        and baseline_longform.get("enabled") is False
        and candidate_longform.get("enabled") is True
        and all(item.get("output_exists") and item.get("qa_passed") for item in baseline + candidate)
        and source_similarity >= 0.995
        and translated_similarity >= 0.98
        and all(not item.get("chunk_plan_exists") and int(item.get("macro_chunks") or 0) == 0 for item in candidate)
    )
    overhead = slowdown_ratio <= max_slowdown_ratio
    return {
        "gate": "PASS" if correctness and overhead else "FAIL",
        "correctness_passed": correctness,
        "overhead_passed": overhead,
        "max_slowdown_ratio": max_slowdown_ratio,
        "baseline_median_wall_seconds": base_wall,
        "candidate_median_wall_seconds": cand_wall,
        "candidate_slowdown_ratio": slowdown_ratio,
        "candidate_overhead_percent": (slowdown_ratio - 1.0) * 100.0,
        "resolved_axes_equal": axes_equal,
        "baseline_longform_enabled": baseline_longform.get("enabled"),
        "candidate_longform_enabled": candidate_longform.get("enabled"),
        "source_similarity_min": source_similarity,
        "translated_similarity_min": translated_similarity,
        "candidate_short_path_bypassed_chunking": all(
            not item.get("chunk_plan_exists") and int(item.get("macro_chunks") or 0) == 0
            for item in candidate
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="P23 short single-chunk baseline vs current-core A/B benchmark")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    parser.add_argument("--profile", default="balanced_fast")
    parser.add_argument("--transport", default="direct-responses", choices=("codex-exec", "direct-responses"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-slowdown-ratio", type=float, default=1.05)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--main-root")
    parser.add_argument("--label")
    parser.add_argument("--trial-root")
    parser.add_argument("--worker-result")
    args = parser.parse_args()
    if args.worker:
        return _worker(args)
    if args.repeats < 1:
        raise ValueError("--repeats must be >= 1")

    input_path = args.input.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_root = (args.output_dir or PROJECT_ROOT / "work" / "benchmarks" / f"p23-short-overhead-{stamp}").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    baseline_config = output_root / "baseline-config.yaml"
    candidate_config = output_root / "candidate-config.yaml"
    _write_comparable_config(
        args.config.resolve(),
        baseline_config,
        args.profile,
        args.transport,
        longform_enabled=False,
    )
    _write_comparable_config(
        args.config.resolve(),
        candidate_config,
        args.profile,
        args.transport,
        longform_enabled=True,
    )
    runs: list[dict[str, Any]] = []
    for index in range(args.repeats):
        trial = index + 1
        order = (
            [("baseline", baseline_config), ("candidate", candidate_config)]
            if index % 2 == 0
            else [("candidate", candidate_config), ("baseline", baseline_config)]
        )
        for label, config_path in order:
            print(f"[{trial}/{args.repeats}] {label}", flush=True)
            run = _run_one(
                main_root=PROJECT_ROOT,
                label=label,
                trial=trial,
                input_path=input_path,
                config_path=config_path,
                output_root=output_root,
            )
            runs.append(run)
            _write_json(output_root / "results.partial.json", {"runs": runs})
            if run.get("status") != "passed":
                report = {
                    "schema": "vi-dubber-p23-short-overhead-v2",
                    "status": "blocked",
                    "input": str(input_path),
                    "input_sha256": _sha256(input_path),
                    "baseline": "current-working-tree with longform.enabled=false",
                    "candidate": "current-working-tree with longform.enabled=true",
                    "profile": args.profile,
                    "transport": args.transport,
                    "runs": runs,
                    "summary": {"gate": "BLOCKED", "reason": f"{label}_run_failed"},
                }
                _write_json(output_root / "results.json", report)
                return 2
    summary = _summarize(runs, args.max_slowdown_ratio)
    report = {
        "schema": "vi-dubber-p23-short-overhead-v2",
        "status": "complete",
        "created_at": datetime.now(UTC).isoformat(),
        "input": str(input_path),
        "input_sha256": _sha256(input_path),
        "baseline": "current-working-tree with longform.enabled=false",
        "candidate": "current-working-tree with longform.enabled=true",
        "profile": args.profile,
        "transport": args.transport,
        "repeats": args.repeats,
        "runs": runs,
        "summary": summary,
        "notes": [
            "The 5% slowdown threshold is a harness-local operationalization of PLAN wording 'không làm short fixture chậm đáng kể'; PLAN itself does not specify a numeric percentage.",
            "Both sides use the same current code, profile, models and Dedicated WebGPT runtime. The only intentional config difference is longform.enabled.",
            "The fixture is shorter than single_chunk_threshold_seconds, so the enabled side must bypass macro-chunk planning and stay on the ordinary short path.",
            "This receipt is a P23 short-path benchmark gate only and is not whole-product acceptance.",
        ],
    }
    _write_json(output_root / "results.json", report)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"receipt={output_root / 'results.json'}")
    return 0 if summary.get("gate") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
