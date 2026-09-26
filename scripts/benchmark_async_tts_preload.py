from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.benchmark import compare_summaries, summarize_artifacts
from vi_dubber import pipeline
from vi_dubber import tts as tts_module
from vi_dubber.pipeline import load_config
from vi_dubber.translate import webgpt_route_info


DEFAULT_INPUT = (
    PROJECT_ROOT
    / "work"
    / "benchmarks"
    / "p17-synthetic-missing-classes"
    / "two-speakers"
    / "source.mp4"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return data


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_variant_config(base_config: Path, target: Path, *, preload: bool) -> None:
    raw = yaml.safe_load(base_config.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"Invalid config: {base_config}")
    config = copy.deepcopy(raw)
    translation = dict(config.get("translation") or {})
    glossary = Path(str(translation.get("glossary") or "glossary.yaml"))
    if not glossary.is_absolute():
        glossary = (base_config.parent / glossary).resolve()
    translation["glossary"] = str(glossary)
    config["translation"] = translation
    tts = dict(config.get("tts") or {})
    tts["async_preload_enabled"] = preload
    config["tts"] = tts
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _artifact_snapshot(job_dir: Path, target: Path) -> dict[str, str]:
    target.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    for name in (
        "result.json",
        "metrics.json",
        "tts_stats.json",
        "segments_translated.json",
        "segments_vi.json",
        "segment_qa.json",
        "semantic_qa.json",
        "translation_meta.json",
        "tts_translation_meta.json",
    ):
        source = job_dir / name
        if not source.is_file():
            continue
        destination = target / name
        shutil.copy2(source, destination)
        copied[name] = _sha256(destination)
    return copied


def _cuda_reset_peak() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def _stage_wall(metrics: dict[str, Any], name: str) -> float | None:
    stages = metrics.get("stages")
    if not isinstance(stages, dict):
        return None
    item = stages.get(name)
    if not isinstance(item, dict):
        return None
    value = item.get("wall_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _peak_reserved_mib(metrics: dict[str, Any]) -> float | None:
    resources = metrics.get("resources")
    if not isinstance(resources, dict):
        return None
    cuda = resources.get("torch_cuda_allocator")
    if not isinstance(cuda, dict) or not cuda.get("available"):
        return None
    value = cuda.get("peak_reserved_bytes")
    return float(value) / (1024 * 1024) if isinstance(value, (int, float)) else None


def _install_timing_probes() -> tuple[dict[str, Any], Callable[[], None]]:
    events: dict[str, Any] = {"engine_loads": [], "progress": []}
    original_load = tts_module.load_tts_engine
    original_synthesize = pipeline.synthesize_segments

    def timed_load(config: Any):
        started = time.perf_counter()
        record = {"started": started, "finished": None, "wall_seconds": None, "failed": False}
        events["engine_loads"].append(record)
        try:
            return original_load(config)
        except BaseException:
            record["failed"] = True
            raise
        finally:
            finished = time.perf_counter()
            record["finished"] = finished
            record["wall_seconds"] = finished - started

    def timed_synthesize(*args: Any, **kwargs: Any):
        started = time.perf_counter()
        events["tts_call_started"] = started
        original_progress = kwargs.get("progress_callback")

        def progress(value: float, message: str) -> None:
            if "tts_first_output" not in events:
                events["tts_first_output"] = time.perf_counter()
            if original_progress is not None:
                original_progress(value, message)

        if original_progress is not None:
            kwargs["progress_callback"] = progress
        try:
            return original_synthesize(*args, **kwargs)
        finally:
            events["tts_call_finished"] = time.perf_counter()

    tts_module.load_tts_engine = timed_load
    pipeline.synthesize_segments = timed_synthesize

    def restore() -> None:
        tts_module.load_tts_engine = original_load
        pipeline.synthesize_segments = original_synthesize

    return events, restore


def _run_once(
    *,
    label: str,
    input_path: Path,
    config_path: Path,
    variant_root: Path,
    resume: bool,
) -> dict[str, Any]:
    job_root = variant_root / "jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    original_work_dir = pipeline.WORK_DIR
    pipeline.WORK_DIR = job_root
    events, restore_probes = _install_timing_probes()
    started = time.perf_counter()

    def progress(value: float, message: str) -> None:
        events["progress"].append({"at": time.perf_counter(), "value": value, "message": message})

    try:
        _cuda_reset_peak()
        result = pipeline.run_pipeline(
            input_path,
            variant_root / "output.mp4",
            config_path,
            translation_provider="webgpt",
            resume=resume,
            progress_callback=progress,
        )
    finally:
        restore_probes()
        pipeline.WORK_DIR = original_work_dir

    observed_wall = time.perf_counter() - started
    job_dir = Path(result["work_dir"])
    metrics = _read_json(job_dir / "metrics.json")
    progress_rows = list(events.get("progress") or [])
    translation_start = next(
        (row["at"] for row in progress_rows if "Đang dịch bằng" in str(row.get("message") or "")),
        None,
    )
    translation_wall = _stage_wall(metrics, "translation")
    translation_end = (
        float(translation_start) + float(translation_wall)
        if translation_start is not None and translation_wall is not None
        else None
    )
    first_engine = (events.get("engine_loads") or [None])[0]
    overlap = None
    if isinstance(first_engine, dict) and translation_start is not None and translation_end is not None:
        engine_start = float(first_engine["started"])
        engine_end = float(first_engine["finished"])
        overlap = max(0.0, min(engine_end, translation_end) - max(engine_start, float(translation_start)))
    tts_call_started = events.get("tts_call_started")
    tts_first_output = events.get("tts_first_output")
    time_to_first_output = (
        float(tts_first_output) - float(tts_call_started)
        if tts_call_started is not None and tts_first_output is not None
        else None
    )
    fresh_or_resume = variant_root / ("resume" if resume else "fresh")
    hashes = _artifact_snapshot(job_dir, fresh_or_resume)
    summary = summarize_artifacts(
        fresh_or_resume / "result.json",
        tts_stats_path=fresh_or_resume / "tts_stats.json",
        metrics_path=fresh_or_resume / "metrics.json",
        segment_qa_path=fresh_or_resume / "segment_qa.json",
        semantic_qa_path=fresh_or_resume / "semantic_qa.json",
    )
    counters = metrics.get("counters") if isinstance(metrics.get("counters"), dict) else {}
    return {
        "label": label,
        "resume": resume,
        "observed_wall_seconds": observed_wall,
        "result_elapsed_seconds": result.get("elapsed_seconds"),
        "translation_wall_seconds": translation_wall,
        "tts_pass_1_wall_seconds": _stage_wall(metrics, "tts_pass_1"),
        "tts_call_wall_seconds": (
            float(events["tts_call_finished"]) - float(tts_call_started)
            if tts_call_started is not None and events.get("tts_call_finished") is not None
            else None
        ),
        "tts_time_to_first_output_seconds": time_to_first_output,
        "engine_loads": events.get("engine_loads") or [],
        "preload_translation_overlap_seconds": overlap,
        "peak_reserved_mib": _peak_reserved_mib(metrics),
        "cache_hits": counters.get("cache_hits"),
        "cache_misses": counters.get("cache_misses"),
        "tts_inferences": counters.get("tts_inferences"),
        "tts_batches": counters.get("tts_batches"),
        "artifact_sha256": hashes,
        "summary": summary,
    }


def _parity(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    b_hash = baseline.get("artifact_sha256") if isinstance(baseline.get("artifact_sha256"), dict) else {}
    c_hash = candidate.get("artifact_sha256") if isinstance(candidate.get("artifact_sha256"), dict) else {}
    names = sorted(set(b_hash) & set(c_hash))
    exact = {name: b_hash[name] == c_hash[name] for name in names}
    comparison = compare_summaries(baseline["summary"], candidate["summary"])
    return {
        "exact_artifact_match": exact,
        "segments_translated_exact": exact.get("segments_translated.json"),
        "segments_vi_exact": exact.get("segments_vi.json"),
        "summary_comparison": comparison,
    }


def _dry_run(input_path: Path, base_config: Path, root: Path) -> int:
    baseline_config = root / "baseline.yaml"
    candidate_config = root / "candidate.yaml"
    _write_variant_config(base_config, baseline_config, preload=False)
    _write_variant_config(base_config, candidate_config, preload=True)
    baseline = load_config(baseline_config)
    candidate = load_config(candidate_config)
    route = webgpt_route_info(candidate["translation"])
    payload = {
        "input": str(input_path),
        "input_sha256": _sha256(input_path),
        "route_ready": route.get("ready"),
        "route_reason": route.get("reason"),
        "baseline_preload": baseline["tts"].get("async_preload_enabled"),
        "candidate_preload": candidate["tts"].get("async_preload_enabled"),
        "same_glossary": baseline["translation"].get("glossary") == candidate["translation"].get("glossary"),
    }
    _write_json(root / "dry-run.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if route.get("ready") else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Whole-job A/B for experimental async VieNeu preload.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-resume-check", action="store_true")
    args = parser.parse_args()

    input_path = args.input.resolve()
    base_config = args.config.resolve()
    if not input_path.is_file():
        raise SystemExit(f"Input not found: {input_path}")
    if not base_config.is_file():
        raise SystemExit(f"Config not found: {base_config}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = (args.output_dir or PROJECT_ROOT / "work" / "benchmarks" / f"async-tts-preload-{stamp}").resolve()
    root.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        return _dry_run(input_path, base_config, root)

    configs = {
        "baseline": root / "baseline" / "config.yaml",
        "candidate": root / "candidate" / "config.yaml",
    }
    _write_variant_config(base_config, configs["baseline"], preload=False)
    _write_variant_config(base_config, configs["candidate"], preload=True)
    candidate_loaded = load_config(configs["candidate"])
    route = webgpt_route_info(candidate_loaded["translation"])
    if not route.get("ready"):
        raise SystemExit(f"Dedicated WebGPT route is not ready: {route.get('reason')}")

    fresh: dict[str, dict[str, Any]] = {}
    resume_runs: dict[str, dict[str, Any]] = {}
    for label in ("baseline", "candidate"):
        variant_root = root / label
        fresh[label] = _run_once(
            label=label,
            input_path=input_path,
            config_path=configs[label],
            variant_root=variant_root,
            resume=False,
        )
        _write_json(variant_root / "fresh-run.json", fresh[label])
        if not args.skip_resume_check:
            resume_runs[label] = _run_once(
                label=label,
                input_path=input_path,
                config_path=configs[label],
                variant_root=variant_root,
                resume=True,
            )
            _write_json(variant_root / "resume-run.json", resume_runs[label])

    b_wall = float(fresh["baseline"]["observed_wall_seconds"])
    c_wall = float(fresh["candidate"]["observed_wall_seconds"])
    summary = {
        "schema_version": 1,
        "kind": "async-tts-preload-whole-job-ab",
        "timestamp_utc": stamp,
        "input": {"path": str(input_path), "sha256": _sha256(input_path)},
        "route": route,
        "baseline": fresh["baseline"],
        "candidate": fresh["candidate"],
        "resume": resume_runs,
        "wall_delta_seconds": c_wall - b_wall,
        "wall_delta_pct": ((c_wall / b_wall) - 1.0) * 100.0 if b_wall else None,
        "speedup": b_wall / c_wall if c_wall else None,
        "parity": _parity(fresh["baseline"], fresh["candidate"]),
    }
    output = root / "results.json"
    _write_json(output, summary)
    print(
        json.dumps(
            {
                "results": str(output),
                "baseline_wall_s": b_wall,
                "candidate_wall_s": c_wall,
                "speedup": summary["speedup"],
                "wall_delta_pct": summary["wall_delta_pct"],
                "translation_exact": summary["parity"]["segments_translated_exact"],
                "final_text_exact": summary["parity"]["segments_vi_exact"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
