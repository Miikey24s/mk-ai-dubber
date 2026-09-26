import importlib.util
from pathlib import Path
import sys

import yaml


SPEC = importlib.util.spec_from_file_location(
    "benchmark_p23_short_overhead",
    Path(__file__).resolve().parents[1] / "scripts" / "benchmark_p23_short_overhead.py",
)
assert SPEC is not None and SPEC.loader is not None
bench = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bench
SPEC.loader.exec_module(bench)


def _run(label: str, *, wall: float, enabled: bool) -> dict:
    return {
        "label": label,
        "status": "passed",
        "wall_seconds": wall,
        "output_exists": True,
        "qa_passed": True,
        "resolved_axes": {
            "profile": "balanced_fast",
            "asr": {"model": "large-v3", "device": "cuda", "compute_type": "float16", "batch_size": 4},
            "tts": {"backend": "torch", "device": "cuda", "precision": "fp16", "batch_size": 4},
            "translation": {
                "webgpt_base_url": "http://127.0.0.1:17850/v1",
                "webgpt_transport": "direct-responses",
                "webgpt_model": "chatgpt-web/gpt-5.6-sol",
                "webgpt_concurrency": 2,
                "codex_segments_per_batch": 32,
            },
            "longform": {
                "enabled": enabled,
                "single_chunk_threshold_seconds": 900,
                "target_seconds": 1500,
            },
        },
        "chunk_plan_exists": False,
        "macro_chunks": 0,
        "source_script": "same source script",
        "translated_script": "cung ban dich",
    }


def test_benchmark_configs_only_toggle_longform_and_keep_glossary_absolute(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "config.yaml"
    baseline = tmp_path / "baseline.yaml"
    candidate = tmp_path / "candidate.yaml"

    bench._write_comparable_config(
        source,
        baseline,
        "balanced_fast",
        "direct-responses",
        longform_enabled=False,
    )
    bench._write_comparable_config(
        source,
        candidate,
        "balanced_fast",
        "direct-responses",
        longform_enabled=True,
    )

    baseline_raw = yaml.safe_load(baseline.read_text(encoding="utf-8"))
    candidate_raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    assert baseline_raw["longform"]["enabled"] is False
    assert candidate_raw["longform"]["enabled"] is True
    baseline_raw["longform"]["enabled"] = True
    assert baseline_raw == candidate_raw
    assert Path(candidate_raw["translation"]["glossary"]).is_absolute()
    assert Path(candidate_raw["translation"]["glossary"]).resolve() == (project_root / "glossary.yaml").resolve()


def test_summary_passes_when_short_path_is_equivalent_and_under_overhead_gate() -> None:
    runs = [
        _run("baseline", wall=100.0, enabled=False),
        _run("candidate", wall=102.0, enabled=True),
        _run("candidate", wall=101.0, enabled=True),
        _run("baseline", wall=100.5, enabled=False),
        _run("baseline", wall=99.5, enabled=False),
        _run("candidate", wall=103.0, enabled=True),
    ]

    result = bench._summarize(runs, 1.05)

    assert result["gate"] == "PASS"
    assert result["correctness_passed"] is True
    assert result["overhead_passed"] is True
    assert result["baseline_longform_enabled"] is False
    assert result["candidate_longform_enabled"] is True
    assert result["candidate_short_path_bypassed_chunking"] is True


def test_summary_fails_if_enabled_short_path_creates_macro_chunks() -> None:
    baseline = _run("baseline", wall=100.0, enabled=False)
    candidate = _run("candidate", wall=100.0, enabled=True)
    candidate["chunk_plan_exists"] = True
    candidate["macro_chunks"] = 1

    result = bench._summarize([baseline, candidate], 1.05)

    assert result["gate"] == "FAIL"
    assert result["correctness_passed"] is False
