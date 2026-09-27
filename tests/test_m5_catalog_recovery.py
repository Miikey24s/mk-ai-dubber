from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_m5_catalog_recovery.py"
SPEC = importlib.util.spec_from_file_location("verify_m5_catalog_recovery", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)


def test_m5_startup_restart_restore_rehearsal_is_prep_only() -> None:
    receipt = rehearsal.run_rehearsal()

    assert receipt["status"] == "PREP_ONLY"
    assert receipt["rehearsal"] == "m5-catalog-startup-restart-restore-v1"
    observations = receipt["observations"]
    assert observations["initial_indexed"] == 2
    assert observations["initial_skipped"] == ["job-invalid"]
    assert observations["restart_indexed"] == 2
    assert observations["restart_state_retained_same_revision"] is True
    assert observations["backup_restore_count"] == 2
    assert observations["backup_round_trip_equal"] is True
    assert observations["changed_lineage_invalidated_state"] is True
    assert observations["changed_lineage_rebuild_indexed"] == 2
    assert observations["portable_source_ref"] == "job-m5-recovery-0001/source.mp4"
    assert receipt["scope"]["provider"] is False
    assert receipt["scope"]["app_server"] is False
    assert receipt["scope"]["job12_touched"] is False
    assert "M5 product/UI acceptance" in receipt["claims_excluded"]
