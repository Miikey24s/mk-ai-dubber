"""Offline PREP_ONLY consistency audit for the M5 catalog API projection.

The audit uses a temporary SQLite catalog and synthetic job state, then calls
the local FastAPI read routes.  It does not start a provider/runtime, open
media, rebuild from a work directory, use OAuth/connectors, or touch the
retained Job12 directory.  A passing receipt validates the API boundary only;
it is not M5 product/UI acceptance.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vi_dubber.artifacts import atomic_write_json
from vi_dubber.catalog_store import CatalogItem, CatalogStore, UserState
from vi_dubber.jobs import update_job_state


AUDIT_FORMAT = "vi-dubber-catalog-api-audit-v1"


def _item() -> CatalogItem:
    return CatalogItem(
        item_id="job-api-audit-0001",
        title="API audit fixture.mp4",
        source_fingerprint="a" * 64,
        revision="lineage-api-r1",
        availability="available",
        segment_count=2,
        source_ref="jobs/job-api-audit-0001/source/fixture.mp4",
        metadata={
            "source_name": "API audit fixture.mp4",
            "job_status": "completed",
            "job_stage": "complete",
            "progress": 1.0,
            "provider_secret": "must-not-leak",
        },
    )


def run_probe() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="vi-dubber-m5-api-audit-") as raw_root:
        root = Path(raw_root)
        work = root / "work"
        work.mkdir()
        job_dir = work / "job-api-audit-0001"
        job_dir.mkdir()
        # The API list route reads persisted state only; no source/media file is
        # needed for this metadata contract probe.
        update_job_state(
            job_dir,
            status="completed",
            stage="complete",
            progress=1.0,
            message="fixture complete",
            metadata={"input_name": "API audit fixture.mp4"},
        )
        store = CatalogStore(work / "catalog.sqlite3")
        store.rebuild([_item()])
        store.set_user_state(
            UserState(
                item_id="job-api-audit-0001",
                revision="lineage-api-r1",
                bookmarks=({"segment_index": 1, "position_seconds": 2.0},),
                review_state="reviewed",
                watch_position_seconds=4.5,
            )
        )

        import vi_dubber.api as api
        from fastapi.testclient import TestClient

        # The API module may already be imported by the test process.  Patch
        # only its path/setup hooks and restore both modules in finally.
        api_state = (api.WORK_DIR, api.configure_runtime)
        from vi_dubber import runtime

        runtime_state = (runtime, runtime.WORK_DIR, runtime.configure_runtime)
        api.WORK_DIR = work
        api.configure_runtime = lambda: None
        runtime.WORK_DIR = work
        runtime.configure_runtime = lambda: None
        try:
            with TestClient(api.create_app()) as client:
                catalog_response = client.get("/api/catalog", params={"query": "audit", "limit": 10})
                jobs_response = client.get("/api/jobs")
                detail_response = client.get("/api/jobs/job-api-audit-0001")
            assert catalog_response.status_code == 200
            assert jobs_response.status_code == 200
            assert detail_response.status_code == 200
            catalog = catalog_response.json()
            jobs = jobs_response.json()
            details = detail_response.json()
            assert catalog["catalog_status"] == "ready"
            assert catalog["format"] == "vi-dubber-catalog-view-v1"
            assert catalog["schema_version"] == 1
            assert catalog["metadata_only"] is True
            assert catalog["pagination"] == {"limit": 10, "offset": 0, "returned": 1}
            assert catalog["items"][0]["item_id"] == "job-api-audit-0001"
            assert catalog["items"][0]["lineage"]["revision"] == "lineage-api-r1"
            assert catalog["items"][0]["review"]["review_state"] == "reviewed"
            encoded = json.dumps({"catalog": catalog, "jobs": jobs, "details": details}, ensure_ascii=False)
            assert "provider_secret" not in encoded
            assert str(root) not in encoded
            assert jobs[0]["catalog"] == catalog["items"][0]
            assert details["catalog"] == catalog["items"][0]

            # Missing durable projection is an explicit empty/unavailable UI
            # state and must not create a database as a GET side effect.
            empty_work = root / "empty-work"
            empty_work.mkdir()
            api.WORK_DIR = empty_work
            with TestClient(api.create_app()) as empty_client:
                unavailable = empty_client.get("/api/catalog").json()
            assert unavailable["catalog_status"] == "unavailable"
            assert unavailable["items"] == []
            assert not (empty_work / "catalog.sqlite3").exists()

            # A future schema is unavailable, not silently migrated by a read.
            future_work = root / "future-work"
            future_work.mkdir()
            future_db = future_work / "catalog.sqlite3"
            connection = sqlite3.connect(future_db)
            try:
                connection.execute("PRAGMA user_version = 999")
                connection.commit()
            finally:
                connection.close()
            api.WORK_DIR = future_work
            with TestClient(api.create_app()) as future_client:
                future = future_client.get("/api/catalog").json()
            assert future["catalog_status"] == "unavailable"
            connection = sqlite3.connect(future_db)
            try:
                assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 999
            finally:
                connection.close()
        finally:
            api.WORK_DIR, api.configure_runtime = api_state
            runtime.WORK_DIR, runtime.configure_runtime = runtime_state[1], runtime_state[2]

        return {
            "status": "PREP_ONLY",
            "audit": AUDIT_FORMAT,
            "scope": {
                "temporary_sqlite": True,
                "api_routes": ["/api/catalog", "/api/jobs", "/api/jobs/{job_id}"],
                "media_opened": False,
                "provider_called": False,
                "oauth_or_connector_called": False,
                "startup_rebuild": False,
                "job12_touched": False,
            },
            "observations": {
                "schema_and_format_stable": True,
                "status_ready_for_valid_projection": True,
                "jobs_projection_consistent": True,
                "details_projection_consistent": True,
                "allowlist_and_path_boundary_preserved": True,
                "missing_projection_is_read_only": True,
                "future_schema_not_migrated_by_read": True,
            },
            "claims_excluded": [
                "M5 product/UI acceptance",
                "real-media startup/rebuild/relink workflow",
                "provider, OAuth, connector, network, or Job12 behavior",
                "browser DOM, performance, and memory SLOs",
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
        atomic_write_json(args.output, receipt)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
