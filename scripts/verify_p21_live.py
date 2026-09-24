from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
import yaml

from vi_dubber.translate import (
    WebGptTranslator,
    load_glossary,
    webgpt_model_catalog,
    webgpt_route_info,
)
from vi_dubber.types import Segment


def main() -> None:
    if sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    work_dir = Path("work/p21-live-acceptance")
    work_dir.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    trans_config = config.get("translation", {})
    glossary = load_glossary(Path("glossary.yaml"))

    print("=" * 60)
    print("P21 / P04 LIVE ACCEPTANCE TEST SUITE")
    print("=" * 60)

    # 1. Route & Catalog Check
    route = webgpt_route_info(trans_config)
    print(f"Route info: ready={route['ready']}, base_url={route.get('base_url')}, port={route.get('port')}")
    assert route["ready"], f"WebGPT route not ready: {route['reason']}"

    catalog = webgpt_model_catalog(trans_config)
    print(f"Catalog default model: {catalog.get('default_model')}")
    print(f"Catalog default effort: {catalog.get('default_effort')}")
    for m in catalog.get("models", []):
        print(f"  - Model: {m.get('id')} | Efforts: {m.get('supported_efforts')} | Default: {m.get('default_effort')}")

    results: dict = {
        "timestamp": time.time(),
        "route": route,
        "catalog": catalog,
        "runs": {},
    }

    test_segments = [
        Segment(
            id=1,
            start=0.0,
            end=4.5,
            text="Price retraces into the FVG and then sweeps liquidity below the order block to confirm the market structure.",
        ),
        Segment(
            id=2,
            start=4.5,
            end=9.0,
            text="OpenAI announced that Sam Altman spoke about GPT-4 processing 86,400 tokens with a 99.8% accuracy rate.",
        ),
        Segment(
            id=3,
            start=9.0,
            end=13.5,
            text="You must not sell immediately; you have to wait for the candle close, but you can cancel the pending order anytime.",
        ),
        Segment(
            id=4,
            start=13.5,
            end=17.5,
            text="The backtest showed the win rate was 68%, while the loss rate was 32%.",
        ),
    ]

    # Test A: chatgpt-web/gpt-5.6-sol with effort "high"
    print("\n[Test 1] Testing chatgpt-web/gpt-5.6-sol with effort='high'...")
    t_high_dir = work_dir / "high"
    t_high_dir.mkdir(parents=True, exist_ok=True)
    t_high = WebGptTranslator(
        trans_config,
        t_high_dir,
        retry_budget=2,
        model_override="chatgpt-web/gpt-5.6-sol",
        effort_override="high",
    )
    t0 = time.perf_counter()
    with t_high.running() as client:
        segs_high = [Segment(id=s.id, start=s.start, end=s.end, text=s.text) for s in test_segments]
        client.translate_segments(segs_high, glossary)
    t_high_duration = time.perf_counter() - t0
    print(f"-> Completed in {t_high_duration:.2f}s")
    for s in segs_high:
        print(f"  [{s.id}] {s.text}\n      -> {s.vi}")

    results["runs"]["gpt-5.6-sol-high"] = {
        "duration_sec": t_high_duration,
        "segments": [{"id": s.id, "source": s.text, "vi": s.vi} for s in segs_high],
        "stats": t_high.stats(),
    }

    # Test B: chatgpt-web/gpt-5.6-sol with effort "medium"
    print("\n[Test 2] Testing chatgpt-web/gpt-5.6-sol with effort='medium'...")
    t_med_dir = work_dir / "medium"
    t_med_dir.mkdir(parents=True, exist_ok=True)
    t_med = WebGptTranslator(
        trans_config,
        t_med_dir,
        retry_budget=2,
        model_override="chatgpt-web/gpt-5.6-sol",
        effort_override="medium",
    )
    t0 = time.perf_counter()
    with t_med.running() as client:
        segs_med = [Segment(id=s.id, start=s.start, end=s.end, text=s.text) for s in test_segments]
        client.translate_segments(segs_med, glossary)
    t_med_duration = time.perf_counter() - t0
    print(f"-> Completed in {t_med_duration:.2f}s")
    for s in segs_med:
        print(f"  [{s.id}] {s.text}\n      -> {s.vi}")

    results["runs"]["gpt-5.6-sol-medium"] = {
        "duration_sec": t_med_duration,
        "segments": [{"id": s.id, "source": s.text, "vi": s.vi} for s in segs_med],
        "stats": t_med.stats(),
    }

    # Test C: chatgpt-web/gpt-5.6-sol with effort "low" -> Contract check (must fail closed)
    print("\n[Test 3] Testing chatgpt-web/gpt-5.6-sol with effort='low' (Contract check)...")
    t_low_dir = work_dir / "low_sol"
    t_low_dir.mkdir(parents=True, exist_ok=True)
    t_low = WebGptTranslator(
        trans_config,
        t_low_dir,
        retry_budget=2,
        model_override="chatgpt-web/gpt-5.6-sol",
        effort_override="low",
    )
    contract_rejected = False
    contract_err_msg = ""
    try:
        with t_low.running():
            pass
    except RuntimeError as exc:
        contract_rejected = True
        contract_err_msg = str(exc)
        print(f"-> Contract properly rejected unsupported effort 'low' on gpt-5.6-sol: {exc}")

    results["runs"]["gpt-5.6-sol-low-contract"] = {
        "contract_rejected": contract_rejected,
        "error_message": contract_err_msg,
    }

    # Test D: chatgpt-web/gpt-5.6-sol-instant with effort "low"
    print("\n[Test 4] Testing chatgpt-web/gpt-5.6-sol-instant with effort='low' (Instant model)...")
    t_instant_dir = work_dir / "instant_low"
    t_instant_dir.mkdir(parents=True, exist_ok=True)
    t_instant = WebGptTranslator(
        trans_config,
        t_instant_dir,
        retry_budget=2,
        model_override="chatgpt-web/gpt-5.6-sol-instant",
        effort_override="low",
    )
    t0 = time.perf_counter()
    with t_instant.running() as client:
        segs_instant = [Segment(id=s.id, start=s.start, end=s.end, text=s.text) for s in test_segments]
        client.translate_segments(segs_instant, glossary)
    t_instant_duration = time.perf_counter() - t0
    print(f"-> Completed in {t_instant_duration:.2f}s")
    for s in segs_instant:
        print(f"  [{s.id}] {s.text}\n      -> {s.vi}")

    results["runs"]["gpt-5.6-sol-instant-low"] = {
        "duration_sec": t_instant_duration,
        "segments": [{"id": s.id, "source": s.text, "vi": s.vi} for s in segs_instant],
        "stats": t_instant.stats(),
    }

    # Save summary json
    summary_path = work_dir / "p21_live_results.json"
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved to {summary_path}")


if __name__ == "__main__":
    main()
