from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_m5_catalog_api_consistency.py"
SPEC = importlib.util.spec_from_file_location("verify_m5_catalog_api_consistency", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_m5_catalog_api_consistency_is_prep_only_and_read_only() -> None:
    receipt = audit.run_probe()

    assert receipt["status"] == "PREP_ONLY"
    assert receipt["audit"] == "vi-dubber-catalog-api-audit-v1"
    assert receipt["scope"] == {
        "temporary_sqlite": True,
        "api_routes": ["/api/catalog", "/api/jobs", "/api/jobs/{job_id}"],
        "media_opened": False,
        "provider_called": False,
        "oauth_or_connector_called": False,
        "startup_rebuild": False,
        "job12_touched": False,
    }
    assert receipt["observations"] == {
        "schema_and_format_stable": True,
        "status_ready_for_valid_projection": True,
        "jobs_projection_consistent": True,
        "details_projection_consistent": True,
        "allowlist_and_path_boundary_preserved": True,
        "missing_projection_is_read_only": True,
        "future_schema_not_migrated_by_read": True,
    }
    assert "M5 product/UI acceptance" in receipt["claims_excluded"]
