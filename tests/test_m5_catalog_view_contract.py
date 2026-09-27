from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_m5_catalog_view_contract.py"
SPEC = importlib.util.spec_from_file_location("verify_m5_catalog_view_contract", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_m5_catalog_view_probe_is_prep_only_and_fail_closed() -> None:
    receipt = probe.run_probe()

    assert receipt["status"] == "PREP_ONLY"
    assert receipt["contract"] == "vi-dubber-catalog-view-v1"
    assert receipt["observations"] == {
        "stable_schema": True,
        "joined_review_state": True,
        "allowlisted_job_metadata": True,
        "backup_view_round_trip": True,
        "cross_lineage_state_rejected": True,
        "invalid_projection_preserved": True,
        "mixed_invalid_projection_preserved": True,
        "unavailable_projection_preserved": True,
    }
    assert receipt["scope"]["job12_touched"] is False
    assert "M5 product/UI acceptance" in receipt["claims_excluded"]

