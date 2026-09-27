import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_m5_catalog_contract.py"
SPEC = importlib.util.spec_from_file_location("benchmark_m5_catalog_contract", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_m5_fixture_is_explicitly_prep_only_and_has_stable_shape() -> None:
    contract = benchmark.load_contract()
    dataset = contract["dataset"]

    assert contract["status"] == "PREP_ONLY"
    assert dataset["item_count"] == 1000
    assert dataset["segment_count"] == 100000
    assert dataset["item_count"] * dataset["segments_per_item"] == dataset["segment_count"]
    assert [item["item_id"] for item in benchmark.generate_catalog(contract)[:2]] == [
        "job-0000",
        "job-0001",
    ]


def test_m5_search_cases_are_deterministic() -> None:
    contract = benchmark.load_contract()
    items = benchmark.generate_catalog(contract)
    assert [item["item_id"] for item in benchmark.search_catalog(items, "FIXTURE VIDEO 0007")] == [
        "job-0007"
    ]
    assert len(benchmark.search_catalog(items, "", availability="missing")) == 200
    assert len(benchmark.search_catalog(items, "fixture video 0")) == 1000


def test_m5_metadata_backup_round_trip_excludes_blobs_and_preserves_user_state() -> None:
    contract = benchmark.load_contract()
    items = benchmark.generate_catalog(contract)[:8]
    user_state = {
        "job-0007": {
            "revision": items[7]["revision"],
            "bookmarks": [{"segment_index": 12, "position_seconds": 34.5}],
        }
    }
    backup = benchmark.backup_metadata(items, user_state)
    restored = json.loads(json.dumps(backup, ensure_ascii=False, sort_keys=True))

    assert benchmark.canonical_digest(backup) == benchmark.canonical_digest(restored)
    assert restored["user_state"] == user_state
    assert all("blob" not in key and "bytes" not in key for key in restored)
    assert all("source_ref" in item and not Path(item["source_ref"]).is_absolute() for item in restored["catalog"])


def test_m5_probe_records_excluded_product_claims() -> None:
    receipt = benchmark.run_probe(benchmark.load_contract())

    assert receipt["status"] == "PREP_ONLY"
    assert receipt["dataset"]["item_count"] == 1000
    assert receipt["dataset"]["segment_count"] == 100000
    assert receipt["backup"]["metadata_only"] is True
    assert receipt["backup"]["digest"] == receipt["backup"]["restored_digest"]
    assert "product catalog implementation" in receipt["claims_excluded"]
