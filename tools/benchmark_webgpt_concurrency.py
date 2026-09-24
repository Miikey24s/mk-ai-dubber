from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from vi_dubber.terminology import validate_terminology_segments
from vi_dubber.translate import WebGptTranslator, load_glossary, webgpt_route_info
from vi_dubber.types import Segment


FIXTURE = [
    Segment(
        id=1,
        start=0.0,
        end=4.0,
        text="This Bullish Engulfing candle forms above the order block after a liquidity sweep.",
    ),
    Segment(
        id=2,
        start=4.0,
        end=8.0,
        text="Keep the FVG visible and wait for the market structure to confirm the breakout.",
    ),
    Segment(
        id=3,
        start=8.0,
        end=12.5,
        text="OpenAI announced that Sam Altman discussed GPT-4 processing 86,400 tokens at 99.8% accuracy.",
    ),
    Segment(
        id=4,
        start=12.5,
        end=17.0,
        text="You must not sell immediately; wait for the candle close, but you can cancel the pending order.",
    ),
    Segment(
        id=5,
        start=17.0,
        end=21.0,
        text="Move the stop loss to breakeven after price makes the pullback, then set take profit at 1.5R.",
    ),
    Segment(
        id=6,
        start=21.0,
        end=25.0,
        text="The backtest wins 66% of the time and loses 33%, so do not reverse those numbers.",
    ),
]


def _clone_fixture() -> list[Segment]:
    return [Segment.from_dict(item.to_dict()) for item in FIXTURE]


def _critical_gate(segments: list[Segment]) -> dict[str, Any]:
    joined = "\n".join(item.vi for item in segments)
    groups = {
        "OpenAI": ("OpenAI",),
        "Sam Altman": ("Sam Altman",),
        "GPT-4": ("GPT-4",),
        "86400": ("86.400", "86,400", "86400"),
        "99.8%": ("99,8%", "99.8%"),
        "1.5R": ("1,5R", "1.5R", "1,5 R", "1.5 R"),
        "66%": ("66%",),
        "33%": ("33%",),
    }
    checks = {
        name: any(candidate.casefold() in joined.casefold() for candidate in candidates)
        for name, candidates in groups.items()
    }
    return {"passed": all(checks.values()), "checks": checks}


def run_once(
    base_config: dict[str, Any],
    glossary: dict[str, str],
    work_root: Path,
    *,
    concurrency: int,
    batch_size: int,
    model: str,
    effort: str,
) -> dict[str, Any]:
    run_config = dict(base_config)
    run_config.update(
        {
            "codex_segments_per_batch": batch_size,
            "webgpt_concurrency": concurrency,
            "global_context_enabled": True,
        }
    )
    run_dir = work_root / f"c{concurrency}"
    run_dir.mkdir(parents=True, exist_ok=True)
    translator = WebGptTranslator(
        run_config,
        run_dir,
        retry_budget=1,
        model_override=model,
        effort_override=effort,
    )
    segments = _clone_fixture()
    started = time.perf_counter()
    with translator.running() as client:
        client.translate_segments(segments, glossary)
    wall_seconds = time.perf_counter() - started
    terminology = validate_terminology_segments(segments, glossary)
    critical = _critical_gate(segments)
    return {
        "concurrency": concurrency,
        "batch_size": batch_size,
        "wall_seconds": round(wall_seconds, 4),
        "segments_per_minute": round(len(segments) / wall_seconds * 60.0, 4),
        "stats": translator.stats(),
        "terminology_gate": {
            "passed": terminology["passed"],
            "terms_checked": terminology["terms_checked"],
            "failed_segments": terminology["failed_segments"],
        },
        "critical_gate": critical,
        "translations": [item.to_dict() for item in segments],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark WebGPT translation concurrency 1/2/3.")
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--model", default="chatgpt-web/gpt-5.6-sol")
    parser.add_argument("--effort", default="high")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((project_root / "config.yaml").read_text(encoding="utf-8"))
    translation_config = dict(config.get("translation") or {})
    glossary = load_glossary(project_root / "glossary.yaml")
    route = webgpt_route_info({**translation_config, "webgpt_model": args.model})
    if not route.get("ready"):
        raise RuntimeError(f"WebGPT route is not ready: {route.get('reason')}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work_root = project_root / "work" / "benchmarks" / f"webgpt-concurrency-{stamp}"
    work_root.mkdir(parents=True, exist_ok=True)
    results = {
        "schema_version": 1,
        "timestamp_utc": stamp,
        "model": args.model,
        "effort": args.effort,
        "batch_size": args.batch_size,
        "global_context_enabled": True,
        "route": route,
        "runs": [],
    }

    output_path = work_root / "results.json"

    def save_results() -> None:
        successful = [item for item in results["runs"] if item.get("status") == "passed"]
        baseline = next((item for item in successful if item["concurrency"] == 1), None)
        if baseline is not None:
            for run in successful:
                run["speedup_vs_c1"] = round(baseline["wall_seconds"] / run["wall_seconds"], 4)
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for concurrency in args.concurrency:
        if concurrency not in {1, 2, 3}:
            raise ValueError("concurrency must be 1, 2, or 3")
        print(f"Running concurrency={concurrency} ...", flush=True)
        try:
            run = run_once(
                translation_config,
                glossary,
                work_root,
                concurrency=concurrency,
                batch_size=args.batch_size,
                model=args.model,
                effort=args.effort,
            )
        except Exception as exc:
            failed_run = {
                "status": "failed",
                "concurrency": concurrency,
                "batch_size": args.batch_size,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            results["runs"].append(failed_run)
            save_results()
            print(f"  failed: {type(exc).__name__}: {exc}", flush=True)
            break
        run["status"] = "passed"
        results["runs"].append(run)
        save_results()
        print(
            f"  wall={run['wall_seconds']:.2f}s, segments/min={run['segments_per_minute']:.2f}, "
            f"p95={run['stats']['request_latency_seconds']['p95']}, "
            f"retries={run['stats']['webgpt_retry_attempts']}, "
            f"quality={run['terminology_gate']['passed'] and run['critical_gate']['passed']}",
            flush=True,
        )
    save_results()
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
