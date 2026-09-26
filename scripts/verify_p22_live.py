from __future__ import annotations

import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.benchmark_webgpt_concurrency import FIXTURE, _critical_gate
from vi_dubber.terminology import validate_terminology_segments
from vi_dubber.translate import WebGptTranslator, load_glossary, webgpt_model_catalog, webgpt_route_info
from vi_dubber.types import Segment


def main() -> None:
    project_root = PROJECT_ROOT
    config = yaml.safe_load((project_root / "config.yaml").read_text(encoding="utf-8"))
    translation = dict(config.get("translation") or {})
    translation.update({
        "webgpt_transport": "direct-responses",
        "webgpt_concurrency": 1,
        "global_context_enabled": False,
    })
    model = str(translation.get("webgpt_model") or "chatgpt-web/gpt-5.6-sol")
    effort = str(translation.get("webgpt_effort") or translation.get("reasoning_effort") or "high")

    route = webgpt_route_info({**translation, "webgpt_model": model})
    if not route.get("ready"):
        raise RuntimeError(f"Dedicated Dubber-WebGPT route is not ready: {route.get('reason')}")
    catalog = webgpt_model_catalog(translation)

    work_root = project_root / "work" / "p22-live-acceptance"
    work_root.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = work_root / "runs" / run_stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    glossary = load_glossary(project_root / "glossary.yaml")
    segments = [Segment.from_dict(item.to_dict()) for item in FIXTURE]

    translator = WebGptTranslator(
        translation,
        run_dir,
        retry_budget=1,
        model_override=model,
        effort_override=effort,
    )
    started = time.perf_counter()
    with translator.running() as client:
        client.translate_segments(segments, glossary)
    elapsed = time.perf_counter() - started
    stats = translator.stats()
    if int(stats.get("webgpt_attempts") or 0) < 1:
        raise RuntimeError("Live verifier không phát sinh WebGPT request; không được chấp nhận cache-only pass.")

    terminology = validate_terminology_segments(segments, glossary)
    critical = _critical_gate(segments)
    passed = bool(terminology["passed"] and critical["passed"])
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if passed else "failed",
        "route": route,
        "catalog": catalog,
        "transport": "direct-responses",
        "model": model,
        "effort": effort,
        "concurrency": 1,
        "global_context_enabled": False,
        "wall_seconds": round(elapsed, 4),
        "stats": stats,
        "terminology_gate": {
            "passed": terminology["passed"],
            "terms_checked": terminology["terms_checked"],
            "failed_segments": terminology["failed_segments"],
        },
        "critical_gate": critical,
        "translations": [segment.to_dict() for segment in segments],
    }
    output = work_root / "p22_p04_live_results.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"status={receipt['status']}")
    print(f"route={route.get('base_url')} model={model} effort={effort}")
    print(f"wall_seconds={receipt['wall_seconds']}")
    print(f"terminology={terminology['passed']} critical={critical['passed']}")
    print(f"receipt={output}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
