"""PREP_ONLY E3 metadata/catalog benchmark for the M5 local boundary.

The harness uses the deterministic M5 fixture against the existing local
``CatalogStore`` implementation.  It writes only to temporary SQLite files,
never opens media, and never calls a provider or connector.  The receipt is
deliberately a preparation artifact: timing values are observations on the
current machine, not product acceptance or a production performance claim.
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import platform
import sqlite3
import sys
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from typing import Any, Callable, Iterable

from vi_dubber.catalog_store import CatalogItem, CatalogStore


BENCHMARK_ID = "m5-e3-local-metadata-v1"
DEFAULT_REPEATS = 11
MIN_REPEATS = 3


def _load_fixture_module() -> Any:
    fixture_path = Path(__file__).with_name("benchmark_m5_catalog_contract.py")
    spec = importlib.util.spec_from_file_location("m5_catalog_contract_fixture", fixture_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load fixture module: {fixture_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FIXTURE = _load_fixture_module()
CONTRACT_PATH: Path = _FIXTURE.CONTRACT_PATH
canonical_digest = _FIXTURE.canonical_digest
generate_catalog = _FIXTURE.generate_catalog
load_contract = _FIXTURE.load_contract


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot calculate a percentile from an empty sample")
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summary(samples_ms: list[float]) -> dict[str, Any]:
    if not samples_ms:
        raise ValueError("benchmark samples cannot be empty")
    return {
        "samples": len(samples_ms),
        "min_ms": min(samples_ms),
        "p50_ms": _percentile(samples_ms, 50),
        "p95_ms": _percentile(samples_ms, 95),
        "max_ms": max(samples_ms),
    }


def _elapsed_ms(operation: Callable[[], Any]) -> tuple[float, Any]:
    started = perf_counter_ns()
    result = operation()
    elapsed = (perf_counter_ns() - started) / 1_000_000.0
    return elapsed, result


def validate_fixture(contract: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate shape and search expectations before any timed operation."""
    dataset = contract.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("contract.dataset must be an object")
    expected_items = int(dataset["item_count"])
    expected_segments = int(dataset["segment_count"])
    if len(items) != expected_items:
        raise ValueError(f"fixture item count mismatch: {len(items)} != {expected_items}")
    total_segments = sum(int(item["segment_count"]) for item in items)
    if total_segments != expected_segments:
        raise ValueError(f"fixture segment count mismatch: {total_segments} != {expected_segments}")
    ids = [item.get("item_id") for item in items]
    if ids != [f"job-{index:04d}" for index in range(expected_items)]:
        raise ValueError("fixture item IDs are not contiguous and deterministic")
    if any("blob" in json.dumps(item).lower() or "bytes" in json.dumps(item).lower() for item in items):
        raise ValueError("fixture metadata unexpectedly contains blob/byte fields")
    if any(Path(str(item["source_ref"])).is_absolute() or "\\" in str(item["source_ref"]) for item in items):
        raise ValueError("fixture source references must be portable relative paths")
    expected_cases = contract.get("search_cases")
    if not isinstance(expected_cases, list) or not expected_cases:
        raise ValueError("contract.search_cases must be a non-empty list")
    return {
        "item_count": expected_items,
        "segment_count": expected_segments,
        "catalog_digest": canonical_digest({"catalog": items}),
        "search_case_count": len(expected_cases),
    }


def _catalog_items(items: list[dict[str, Any]], seed: str) -> list[CatalogItem]:
    return [
        CatalogItem(
            item_id=item["item_id"],
            title=item["title"],
            source_fingerprint=item["source_fingerprint"],
            revision=item["revision"],
            availability=item["availability"],
            segment_count=item["segment_count"],
            source_ref=item["source_ref"],
            metadata={"fixture_seed": seed, "fixture_kind": "m5-e3-metadata"},
        )
        for item in items
    ]


def _search(store: CatalogStore, case: dict[str, Any]) -> list[CatalogItem]:
    filters = case.get("filter") or {}
    if not isinstance(filters, dict):
        raise ValueError(f"search case filter must be an object: {case.get('id')}")
    availability = filters.get("availability")
    return store.search(str(case.get("query") or ""), availability=availability, limit=5000)


def _assert_search_result(case: dict[str, Any], result: list[CatalogItem]) -> None:
    expected_ids = case.get("expected_item_ids")
    expected_count = case.get("expected_count")
    actual_ids = [item.item_id for item in result]
    if expected_ids is not None and actual_ids != expected_ids:
        raise AssertionError(f"search case {case.get('id')} IDs differ: {actual_ids!r}")
    if expected_count is not None and len(result) != expected_count:
        raise AssertionError(
            f"search case {case.get('id')} count differs: {len(result)} != {expected_count}"
        )


def _benchmark_case(
    db_path: Path,
    store: CatalogStore,
    case: dict[str, Any],
    repeats: int,
) -> dict[str, Any]:
    """Measure a fresh-store (cold-ish) and same-store (warm) query path."""
    # The cold protocol recreates the Python CatalogStore and SQLite connection
    # for every sample.  It intentionally does not flush the OS page cache;
    # doing so would require privileged/platform-specific operations.
    cold_samples: list[float] = []
    for _ in range(repeats):
        cold_store = CatalogStore(db_path)
        elapsed, result = _elapsed_ms(lambda: _search(cold_store, case))
        _assert_search_result(case, result)
        cold_samples.append(elapsed)

    # One discarded warm-up avoids measuring the first Python/SQLite call in
    # the warm distribution while keeping the protocol deterministic.
    _assert_search_result(case, _search(store, case))
    warm_samples: list[float] = []
    for _ in range(repeats):
        elapsed, result = _elapsed_ms(lambda: _search(store, case))
        _assert_search_result(case, result)
        warm_samples.append(elapsed)

    return {
        "id": case.get("id"),
        "query": case.get("query", ""),
        "filter": case.get("filter") or {},
        "expected_count": case.get("expected_count", len(case.get("expected_item_ids") or [])),
        "cold": _summary(cold_samples),
        "warm": _summary(warm_samples),
        "target_p95_ms": 500,
        "target_decision": "NOT_A_PRODUCT_CLAIM",
    }


def _benchmark_metadata_view(db_path: Path, store: CatalogStore, repeats: int) -> dict[str, Any]:
    item_id = "job-0007"
    cold_samples: list[float] = []
    for _ in range(repeats):
        cold_store = CatalogStore(db_path)
        elapsed, result = _elapsed_ms(lambda: cold_store.get_item(item_id))
        if result is None or result.item_id != item_id:
            raise AssertionError("metadata view did not return the expected item")
        cold_samples.append(elapsed)

    if store.get_item(item_id) is None:
        raise AssertionError("warm metadata view did not return the expected item")
    warm_samples: list[float] = []
    for _ in range(repeats):
        elapsed, result = _elapsed_ms(lambda: store.get_item(item_id))
        if result is None or result.item_id != item_id:
            raise AssertionError("metadata view did not return the expected item")
        warm_samples.append(elapsed)
    return {
        "item_id": item_id,
        "cold": _summary(cold_samples),
        "warm": _summary(warm_samples),
        "target_p95_ms": 2000,
        "target_decision": "NOT_A_PRODUCT_CLAIM",
    }


def run_benchmark(contract: dict[str, Any], *, repeats: int = DEFAULT_REPEATS) -> dict[str, Any]:
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < MIN_REPEATS:
        raise ValueError(f"repeats must be an integer >= {MIN_REPEATS}")
    dataset = contract["dataset"]
    items = generate_catalog(contract)
    fixture = validate_fixture(contract, items)
    seed = str(dataset["seed"])
    model_items = _catalog_items(items, seed)
    cases = contract["search_cases"]

    with TemporaryDirectory(prefix="vi-dubber-m5-e3-") as temporary_root:
        root = Path(temporary_root)
        db_path = root / "catalog.sqlite3"
        store = CatalogStore(db_path)
        indexed = store.rebuild(model_items)
        if indexed != fixture["item_count"]:
            raise AssertionError("CatalogStore indexed count differs from fixture")

        case_receipts = [_benchmark_case(db_path, store, case, repeats) for case in cases]
        metadata_receipt = _benchmark_metadata_view(db_path, store, repeats)

        exported = store.export_metadata()
        serialized = json.dumps(exported, ensure_ascii=False, sort_keys=True)
        if any(token in serialized.lower() for token in ("source_bytes", "preview_bytes", "provider_credentials")):
            raise AssertionError("catalog backup contains a forbidden blob/credential field")
        if str(root.resolve()) in serialized:
            raise AssertionError("catalog backup contains a machine-specific temporary path")
        restored_db = root / "restored.sqlite3"
        restored = CatalogStore(restored_db)
        restored.restore_metadata(json.loads(json.dumps(exported, ensure_ascii=False)))
        backup_round_trip = restored.export_metadata() == exported
        if not backup_round_trip:
            raise AssertionError("catalog metadata backup did not round-trip exactly")
        # CatalogStore's current connection wrapper is intentionally exercised
        # as-is.  Drop any sqlite connection objects before Windows removes
        # this temporary directory (sqlite finalization is ref-count based on
        # this platform).
        del restored, store
        gc.collect()

    return {
        "status": "PREP_ONLY",
        "benchmark": BENCHMARK_ID,
        "contract": str(CONTRACT_PATH.relative_to(CONTRACT_PATH.parents[2])).replace("\\", "/"),
        "protocol": {
            "repeats": repeats,
            "cold": "new CatalogStore and SQLite connection per sample; OS page cache is not flushed",
            "warm": "same CatalogStore with one discarded warm-up, then repeated reads",
            "storage": "temporary SQLite files deleted after the run",
            "media": "no media files opened or hashed",
            "external_io": "provider/OAuth/Drive/network/job12 not invoked",
        },
        "dataset": fixture,
        "integrity": {
            "fixture_shape": True,
            "search_expectations": True,
            "catalog_rebuild_count": fixture["item_count"],
            "metadata_backup_round_trip": backup_round_trip,
            "metadata_only_backup": True,
            "portable_metadata_paths": True,
            "temporary_storage_removed": True,
        },
        "measurements": {
            "search_cases": case_receipts,
            "initial_metadata_view": metadata_receipt,
        },
        "promotion": {
            "decision": "NO_CLAIM",
            "reason": "PREP_ONLY observations; no production performance or M5 acceptance is inferred",
            "targets_ms": {"list_search_p95": 500, "initial_metadata_view_p95": 2000},
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
        },
        "claims_excluded": [
            "production M5 acceptance or target attainment",
            "OS page-cache-cold behavior or multi-process contention",
            "UI/DOM rendering, thumbnail concurrency, or browser memory behavior",
            "real licensed media availability, relink, or playback",
            "provider, OAuth, Drive/cloud export, network, and job12 execution",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--output", type=Path, help="Optional JSON receipt path")
    args = parser.parse_args()
    receipt = run_benchmark(load_contract(), repeats=args.repeats)
    serialized = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
