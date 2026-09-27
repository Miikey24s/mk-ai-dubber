from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_m5_process_startup.py"
SPEC = importlib.util.spec_from_file_location("verify_m5_process_startup", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_m5_process_startup_restart_smoke_is_prep_only() -> None:
    receipt = smoke.run_smoke()

    assert receipt["status"] == "PREP_ONLY"
    assert receipt["smoke"] == "m5-process-startup-restart-v1"
    assert receipt["scope"]["fresh_python_processes"] == 3
    assert receipt["scope"]["provider"] is False
    assert receipt["scope"]["real_media"] is False
    assert receipt["scope"]["real_work_dir"] is False
    assert receipt["scope"]["job12_touched"] is False
    observations = receipt["observations"]
    assert observations["boot_indexed"] == 2
    assert observations["boot_skipped"] == ["job-invalid"]
    assert observations["restart_indexed"] == 2
    assert observations["restart_app_entrypoint_imported"] is True
    assert observations["restart_health_status"] == 200
    assert observations["restart_state_retained_same_revision"] is True
    assert observations["lineage_change_invalidated_state"] is True
    assert "M5 product/UI acceptance" in receipt["claims_excluded"]
