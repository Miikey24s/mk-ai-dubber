"""Offline PREP_ONLY probe for the M5 catalog-to-Review read model.

The probe exercises only temporary SQLite metadata and synthetic catalog rows.
It does not start the application, open media, call a provider/connector, or
touch the retained Job12 work directory.  A successful receipt is a contract
check, not M5 product acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vi_dubber.catalog_projection import rebuild_from_work_dir
from vi_dubber.catalog_store import CatalogIntegrityError, CatalogItem, CatalogStore, UserState
from vi_dubber.catalog_view import CATALOG_VIEW_FORMAT, catalog_view_from_backup


def _item(item_id: str, availability: str) -> CatalogItem:
    fingerprint = item_id.removeprefix("job-")[0] * 64
    return CatalogItem(
        item_id=item_id,
        title=f"M5 view fixture {item_id}",
        source_fingerprint=fingerprint,
        revision="lineage-r1",
        availability=availability,
        segment_count=4,
        source_ref=f"sources/{item_id}/source.mp4",
        metadata={
            "source_name": f"{item_id}.mp4",
            "job_status": "completed",
            "job_stage": "complete",
            "progress": 1.0,
            "source_size_bytes": 12,
            "internal_debug": "not part of the view contract",
        },
    )


def run_probe() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="vi-dubber-m5-view-") as raw_root:
        root = Path(raw_root)
        store = CatalogStore(root / "catalog.sqlite3")
        store.rebuild([_item("job-a", "available"), _item("job-b", "missing")])
        store.set_user_state(
            UserState(
                item_id="job-a",
                revision="lineage-r1",
                bookmarks=({"segment_index": 1, "position_seconds": 2.5},),
                review_state="in_review",
                watch_position_seconds=3.0,
            )
        )

        view = store.read_view("M5 view", limit=10)
        assert view["format"] == CATALOG_VIEW_FORMAT
        assert view["metadata_only"] is True
        assert [item["item_id"] for item in view["items"]] == ["job-a", "job-b"]
        assert view["items"][0]["review"]["review_state"] == "in_review"
        encoded = json.dumps(view, ensure_ascii=False, sort_keys=True)
        assert "internal_debug" not in encoded
        assert str(root) not in encoded

        backup_view = catalog_view_from_backup(store.export_metadata())
        assert backup_view == {
            **view,
            "query": "",
            "availability_filter": None,
            "pagination": {"limit": None, "offset": 0, "returned": 2},
        }

        changed = store.export_metadata()
        changed["user_state"][0]["revision"] = "lineage-r2"
        core = {key: changed[key] for key in ("format", "schema_version", "catalog", "user_state")}
        from vi_dubber.catalog_store import _digest_payload

        changed["payload_digest"] = _digest_payload(core)
        try:
            catalog_view_from_backup(changed)
        except CatalogIntegrityError:
            state_boundary_rejected = True
        else:  # pragma: no cover - defensive assertion for the receipt
            state_boundary_rejected = False
        assert state_boundary_rejected

        # A startup/reload source outage must not turn into an empty rebuild
        # that silently deletes durable review state.
        invalid_work = root / "invalid-work"
        invalid_job = invalid_work / "job-invalid"
        invalid_job.mkdir(parents=True)
        (invalid_job / "job.json").write_text("{not-json", encoding="utf-8")
        invalid_report = rebuild_from_work_dir(store, invalid_work, source_root=root)
        assert invalid_report.rebuild_applied is False
        assert invalid_report.preserved_existing is True
        assert invalid_report.reason == "all_jobs_invalid"
        assert store.get_user_state("job-a") is not None

        mixed_work = root / "mixed-work"
        valid_source = root / "mixed-source.mp4"
        valid_source.write_bytes(b"mixed source")
        valid_job = mixed_work / "job-valid"
        valid_job.mkdir(parents=True)
        digest = hashlib.sha256(valid_source.read_bytes()).hexdigest()
        (valid_job / "job.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "source": {"sha256": digest, "size_bytes": valid_source.stat().st_size},
                    "source_path": str(valid_source),
                    "source_name": "mixed-source.mp4",
                    "translation": {"provider": "offline", "model_id": "fixture", "effort": "medium"},
                }
            ),
            encoding="utf-8",
        )
        (valid_job / "state.json").write_text(
            json.dumps({"version": 1, "status": "completed", "stage": "complete", "progress": 1.0}),
            encoding="utf-8",
        )
        mixed_invalid = mixed_work / "job-a"
        mixed_invalid.mkdir()
        (mixed_invalid / "job.json").write_text("{not-json", encoding="utf-8")
        mixed_report = rebuild_from_work_dir(store, mixed_work, source_root=root)
        assert mixed_report.rebuild_applied is True
        assert mixed_report.preserved_existing is True
        assert mixed_report.reason == "rebuilt_with_preserved_invalid"
        assert store.get_item("job-a").availability == "unknown"  # type: ignore[union-attr]
        assert store.get_user_state("job-a") is not None

        empty_report = rebuild_from_work_dir(store, root / "empty-work", source_root=root)
        assert empty_report.rebuild_applied is False
        assert empty_report.preserved_existing is True
        assert empty_report.reason == "work_dir_unavailable"
        assert store.get_user_state("job-a") is not None

        return {
            "status": "PREP_ONLY",
            "contract": CATALOG_VIEW_FORMAT,
            "scope": {
                "items": 2,
                "review_states": 1,
                "availability_states": ["available", "missing"],
                "storage": "temporary SQLite only",
                "media_opened": False,
                "provider_called": False,
                "connector_called": False,
                "job12_touched": False,
            },
            "observations": {
                "stable_schema": True,
                "joined_review_state": True,
                "allowlisted_job_metadata": True,
                "backup_view_round_trip": True,
                "cross_lineage_state_rejected": state_boundary_rejected,
                "invalid_projection_preserved": True,
                "mixed_invalid_projection_preserved": True,
                "unavailable_projection_preserved": True,
            },
            "claims_excluded": [
                "VI M0 baseline acceptance",
                "M5 product/UI acceptance",
                "real licensed media availability or relink",
                "application server/browser wiring",
                "cold/warm p95 or DOM/memory performance",
                "provider, OAuth, Drive, network, and Job12 behavior",
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON receipt path")
    args = parser.parse_args()
    receipt = run_probe()
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
