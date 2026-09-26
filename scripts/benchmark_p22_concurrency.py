from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.benchmark_webgpt_concurrency import FIXTURE, _critical_gate
from vi_dubber.terminology import validate_terminology_segments
from vi_dubber.translate import WebGptTranslator, load_glossary, webgpt_route_info
from vi_dubber.types import Segment


def run_once(base: dict[str, Any], glossary: dict[str, str], root: Path, concurrency: int, repeat: int) -> dict[str, Any]:
    cfg = dict(base)
    cfg.update({
        "webgpt_transport": "direct-responses",
        "webgpt_concurrency": concurrency,
        "global_context_enabled": False,
        "codex_segments_per_batch": 2,
    })
    model = str(cfg.get("webgpt_model") or "chatgpt-web/gpt-5.6-sol")
    effort = str(cfg.get("webgpt_effort") or cfg.get("reasoning_effort") or "high")
    translator = WebGptTranslator(
        cfg,
        root / f"c{concurrency}-r{repeat}",
        retry_budget=1,
        model_override=model,
        effort_override=effort,
    )
    segments = [Segment.from_dict(item.to_dict()) for item in FIXTURE]
    started = time.perf_counter()
    try:
        with translator.running() as client:
            client.translate_segments(segments, glossary)
    except Exception as exc:
        return {
            "status": "failed",
            "concurrency": concurrency,
            "repeat": repeat,
            "wall_seconds": round(time.perf_counter() - started, 4),
            "error": str(exc),
            "stats": translator.stats(),
        }
    wall = time.perf_counter() - started
    terminology = validate_terminology_segments(segments, glossary)
    critical = _critical_gate(segments)
    quality = bool(terminology["passed"] and critical["passed"])
    return {
        "status": "passed" if quality else "quality_failed",
        "concurrency": concurrency,
        "repeat": repeat,
        "wall_seconds": round(wall, 4),
        "segments_per_minute": round(len(segments) / wall * 60.0, 4),
        "stats": translator.stats(),
        "terminology_passed": terminology["passed"],
        "critical_passed": critical["passed"],
        "translations": [item.to_dict() for item in segments],
    }


def main() -> None:
    config = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
    base = dict(config.get("translation") or {})
    base.update({"webgpt_transport": "direct-responses", "global_context_enabled": False})
    route = webgpt_route_info(base)
    if not route.get("ready"):
        raise RuntimeError(f"Dedicated route is not ready: {route.get('reason')}")
    glossary = load_glossary(PROJECT_ROOT / "glossary.yaml")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = PROJECT_ROOT / "work" / "benchmarks" / f"p22-direct-concurrency-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for repeat in (1, 2):
        for concurrency in (1, 2):
            result = run_once(base, glossary, root, concurrency, repeat)
            results.append(result)
            print(f"c{concurrency} r{repeat}: status={result['status']} wall={result['wall_seconds']}s")
    passed = {c: [r for r in results if r["concurrency"] == c and r["status"] == "passed"] for c in (1, 2)}
    summary: dict[str, Any] = {
        "schema_version": 1,
        "timestamp_utc": stamp,
        "route": route,
        "model": base.get("webgpt_model") or "chatgpt-web/gpt-5.6-sol",
        "effort": base.get("webgpt_effort") or base.get("reasoning_effort") or "high",
        "batch_size": 2,
        "global_context_enabled": False,
        "repeats": 2,
        "runs": results,
    }
    if all(len(passed[c]) == 2 for c in (1, 2)):
        m1 = statistics.median(r["wall_seconds"] for r in passed[1])
        m2 = statistics.median(r["wall_seconds"] for r in passed[2])
        summary["median_wall_seconds"] = {"c1": round(m1, 4), "c2": round(m2, 4)}
        summary["c2_speedup_vs_c1"] = round(m1 / m2, 4) if m2 else None
        summary["c2_all_quality_passed"] = True
        summary["c2_pressure_failures"] = sum(int(r.get("stats", {}).get("webgpt_pressure_failures", 0)) for r in passed[2])
    else:
        summary["c2_all_quality_passed"] = False
    output = root / "results.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"receipt={output}")
    if "c2_speedup_vs_c1" in summary:
        print(f"median_c1={summary['median_wall_seconds']['c1']} median_c2={summary['median_wall_seconds']['c2']} speedup={summary['c2_speedup_vs_c1']}x")
    if any(r["status"] != "passed" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
