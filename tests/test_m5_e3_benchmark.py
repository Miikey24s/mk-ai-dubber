from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_script(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark = _load_script("benchmark_m5_e3_metadata")
fixture = _load_script("benchmark_m5_catalog_contract")


def test_e3_fixture_integrity_is_deterministic() -> None:
    contract = fixture.load_contract()
    items = fixture.generate_catalog(contract)
    summary = benchmark.validate_fixture(contract, items)
    assert summary["item_count"] == 1000
    assert summary["segment_count"] == 100000
    assert summary["search_case_count"] == 3


def test_e3_benchmark_is_explicitly_prep_only_and_no_claim() -> None:
    receipt = benchmark.run_benchmark(fixture.load_contract(), repeats=3)
    assert receipt["status"] == "PREP_ONLY"
    assert receipt["benchmark"] == "m5-e3-local-metadata-v1"
    assert receipt["dataset"]["item_count"] == 1000
    assert receipt["dataset"]["segment_count"] == 100000
    assert receipt["integrity"]["fixture_shape"] is True
    assert receipt["integrity"]["search_expectations"] is True
    assert receipt["integrity"]["metadata_backup_round_trip"] is True
    assert receipt["integrity"]["portable_metadata_paths"] is True
    assert receipt["promotion"]["decision"] == "NO_CLAIM"
    assert len(receipt["measurements"]["search_cases"]) == 3
    for case in receipt["measurements"]["search_cases"]:
        assert case["cold"]["samples"] == 3
        assert case["warm"]["samples"] == 3
        assert case["target_decision"] == "NOT_A_PRODUCT_CLAIM"
    metadata = receipt["measurements"]["initial_metadata_view"]
    assert metadata["cold"]["samples"] == 3
    assert metadata["warm"]["samples"] == 3
    assert metadata["target_decision"] == "NOT_A_PRODUCT_CLAIM"
