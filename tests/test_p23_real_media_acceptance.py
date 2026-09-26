import importlib.util
from pathlib import Path
import sys


SPEC = importlib.util.spec_from_file_location(
    "verify_p23_real_media",
    Path(__file__).resolve().parents[1] / "scripts" / "verify_p23_real_media.py",
)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)


def test_real_media_fixture_and_production_mux_path_are_valid(tmp_path: Path) -> None:
    receipt = verify.build_receipt(
        tmp_path,
        duration_seconds=4.0,
        max_rss_mib=2048.0,
        max_workspace_growth_mib=512.0,
        max_process_gpu_mib=2048.0,
    )

    assert receipt["representative_superlong"] is False
    assert receipt["gates"]["final_media_valid"] is True
    assert receipt["gates"]["bounded_process_tree_rss"] is True
    assert receipt["gates"]["bounded_workspace_growth"] is True
    assert receipt["mux"]["process_gpu_attribution"]["source"] == "windows-pdh-dedicated-usage"
    assert receipt["mux"]["process_gpu_attribution"]["available"] is True
    assert receipt["gates"]["final_media_process_gpu_pressure"] is True
    assert receipt["mux"]["stale_output_was_overwritten"] is True
    assert receipt["output"]["stream_codecs"]["video"] == ["h264"]
    assert receipt["output"]["stream_codecs"]["audio"] == ["aac"]
    assert receipt["output"]["duration_delta_seconds"] <= 2.0
    assert "whole-pipeline CUDA VRAM/OOM acceptance" in receipt["claims_excluded"]


def test_short_probe_never_promotes_superlong_gate(tmp_path: Path) -> None:
    receipt = verify.build_receipt(
        tmp_path,
        duration_seconds=2.0,
        max_rss_mib=2048.0,
        max_workspace_growth_mib=512.0,
        max_process_gpu_mib=2048.0,
    )

    assert receipt["representative_superlong"] is False
    assert receipt["gates"]["p23_real_media_final_assembly"] is False
    assert receipt["status"] == "failed"
