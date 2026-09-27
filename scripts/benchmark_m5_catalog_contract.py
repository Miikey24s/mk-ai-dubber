"""Offline PREP_ONLY benchmark contract for the future M5 local catalog.

This module deliberately does not import vi_dubber runtime code, touch a database,
open media, call a provider, or implement a product catalog. It supplies a stable
fixture generator and a shape/performance probe so the later M5 implementation can
be compared against the same inputs and acceptance targets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "m5_catalog_contract.json"


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "PREP_ONLY":
        raise ValueError("M5 catalog contract must remain PREP_ONLY")
    return payload


def _digest_text(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()


def generate_catalog(contract: dict[str, Any]) -> list[dict[str, Any]]:
    dataset = contract["dataset"]
    seed = str(dataset["seed"])
    item_count = int(dataset["item_count"])
    segments_per_item = int(dataset["segments_per_item"])
    availability_cycle = tuple(str(value) for value in dataset["availability_cycle"])
    return [
        {
            "item_id": f"job-{index:04d}",
            "title": f"Fixture video {index:04d}",
            "source_fingerprint": _digest_text(seed, f"source:{index}"),
            "revision": f"r{index % 3 + 1}",
            "availability": availability_cycle[index % len(availability_cycle)],
            "segment_count": segments_per_item,
            "source_ref": f"sources/job-{index:04d}/source.mp4",
        }
        for index in range(item_count)
    ]


def iter_segments(contract: dict[str, Any]) -> Iterable[tuple[str, int, str]]:
    dataset = contract["dataset"]
    seed = str(dataset["seed"])
    for item_index in range(int(dataset["item_count"])):
        item_id = f"job-{item_index:04d}"
        for segment_index in range(int(dataset["segments_per_item"])):
            yield (
                item_id,
                segment_index,
                _digest_text(seed, f"segment:{item_index}:{segment_index}")[:16],
            )


def search_catalog(
    items: list[dict[str, Any]],
    query: str,
    availability: str | None = None,
) -> list[dict[str, Any]]:
    normalized = query.casefold().strip()
    return [
        item
        for item in items
        if (not normalized or normalized in str(item["title"]).casefold())
        and (availability is None or item["availability"] == availability)
    ]


def backup_metadata(items: list[dict[str, Any]], user_state: dict[str, Any]) -> dict[str, Any]:
    """Create metadata-only backup payload; media bytes and machine paths are absent."""
    return {
        "schema_version": 1,
        "catalog": [
            {
                key: item[key]
                for key in (
                    "item_id",
                    "title",
                    "source_fingerprint",
                    "revision",
                    "availability",
                    "segment_count",
                    "source_ref",
                )
            }
            for item in items
        ],
        "user_state": user_state,
    }


def canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def run_probe(contract: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    items = generate_catalog(contract)
    generated_seconds = time.perf_counter() - started

    search_started = time.perf_counter()
    search_counts = {
        "title_exact_casefold": len(search_catalog(items, "FIXTURE VIDEO 0007")),
        "availability_missing": len(search_catalog(items, "", availability="missing")),
        "title_prefix_all": len(search_catalog(items, "fixture video 0")),
    }
    search_seconds = time.perf_counter() - search_started

    segment_started = time.perf_counter()
    segment_count = sum(1 for _ in iter_segments(contract))
    segment_seconds = time.perf_counter() - segment_started

    user_state = {
        "job-0007": {
            "revision": items[7]["revision"],
            "bookmarks": [{"segment_index": 12, "position_seconds": 34.5}],
            "review_state": "in_review",
        }
    }
    backup = backup_metadata(items[:8], user_state)
    return {
        "status": "PREP_ONLY",
        "contract": str(CONTRACT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "dataset": {
            "item_count": len(items),
            "segment_count": segment_count,
            "catalog_digest": canonical_digest({"catalog": items}),
        },
        "search_counts": search_counts,
        "backup": {
            "metadata_only": "source_bytes" not in json.dumps(backup),
            "digest": canonical_digest(backup),
            "restored_digest": canonical_digest(json.loads(json.dumps(backup))),
        },
        "timing_seconds": {
            "catalog_generation": generated_seconds,
            "search_cases": search_seconds,
            "segment_iteration": segment_seconds,
        },
        "claims_excluded": [
            "product catalog implementation",
            "SQLite migration/transaction acceptance",
            "real media availability or relink behavior",
            "UI rendering or p95 acceptance",
            "provider, OAuth, cloud export, and blob backup",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON receipt path")
    args = parser.parse_args()
    receipt = run_probe(load_contract())
    serialized = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
