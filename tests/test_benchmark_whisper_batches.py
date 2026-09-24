from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "benchmark_whisper_batches",
    REPO_ROOT / "tools" / "benchmark_whisper_batches.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_signature_match_accepts_small_timestamp_jitter() -> None:
    baseline = [{"start": 1.0, "end": 1.5, "text": "hello"}]
    candidate = [{"start": 1.01, "end": 1.52, "text": "hello"}]

    assert MODULE._signatures_match(baseline, candidate, tolerance_seconds=0.03)
    assert not MODULE._signatures_match(baseline, candidate, tolerance_seconds=0.01)


def test_select_fixture_inputs_requires_full_category_coverage(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"fixture")
    categories = sorted(MODULE.REQUIRED_FIXTURE_CATEGORIES)
    manifest = {
        "fixtures": [
            {
                "id": "fixture",
                "status": "available",
                "categories": categories[:-1],
                "source": {"status": "available", "path": source.name},
            }
        ]
    }

    selected, missing = MODULE.select_fixture_inputs(manifest, root=tmp_path)

    assert len(selected) == 1
    assert missing == [categories[-1]]


def test_select_fixture_inputs_accepts_explicit_unresolved_category_override(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"fixture")
    categories = sorted(MODULE.REQUIRED_FIXTURE_CATEGORIES)
    unresolved = categories[-1]
    manifest = {
        "fixtures": [
            {
                "id": "fixture",
                "status": "available",
                "categories": categories[:-1],
                "source": {"status": "available", "path": source.name},
            }
        ]
    }

    selected, missing = MODULE.select_fixture_inputs(
        manifest,
        root=tmp_path,
        category_overrides={unresolved: source},
    )

    assert not missing
    assert set(selected[0].categories) == set(categories)
