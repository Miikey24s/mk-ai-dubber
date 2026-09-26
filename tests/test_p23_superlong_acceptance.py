import importlib.util
from pathlib import Path
import sys


SPEC = importlib.util.spec_from_file_location(
    "verify_p23_superlong",
    Path(__file__).resolve().parents[1] / "scripts" / "verify_p23_superlong.py",
)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)
build_synthetic_timeline = verify.build_synthetic_timeline
deterministic_assembly_digest = verify.deterministic_assembly_digest
run_bounded_resource_probe = verify.run_bounded_resource_probe
run_manifest_fault_resume_probe = verify.run_manifest_fault_resume_probe
simulate_fault_resume = verify.simulate_fault_resume
validate_order = verify.validate_order


def test_synthetic_harness_covers_more_than_six_hour_ordered_timeline() -> None:
    chunks = build_synthetic_timeline(6.25)

    assert chunks[-1].end / 3600 >= 6
    assert len(chunks) >= 25
    assert validate_order(chunks)


def test_real_bounded_executor_applies_backpressure_and_bounds_resource_working_set(
    tmp_path: Path,
) -> None:
    chunks = build_synthetic_timeline()

    result = run_bounded_resource_probe(
        tmp_path,
        chunks,
        max_workers=1,
        max_pending=2,
        payload_bytes=1024 * 1024,
        downstream_delay_seconds=0.002,
    )

    assert result["completed"] == len(chunks)
    assert result["queue"]["saturated_running"] == 1
    assert result["queue"]["saturated_queued"] == 2
    assert result["queue"]["producer_was_blocked"] is True
    assert result["disk"]["peak_temp_bytes"] <= 1024 * 1024
    assert result["disk"]["temp_files_after"] == 0
    # Process-level RSS guard. The generous ceiling absorbs interpreter/allocator
    # noise while still catching accidental whole-timeline buffering in this fixture.
    assert result["rss"]["delta_bytes"] < 64 * 1024 * 1024


def test_mid_job_crash_resume_uses_real_chunk_manifests_and_preserves_siblings(
    tmp_path: Path,
) -> None:
    chunks = build_synthetic_timeline()
    failed_index = 10

    result = run_manifest_fault_resume_probe(tmp_path, chunks, failed_index)

    assert result["failed_reusable_before_resume"] is False
    assert result["reused_indices"] == list(range(failed_index))
    assert result["recomputed_indices"] == list(range(failed_index, len(chunks)))
    assert result["completed_sibling_hashes_preserved"] is True
    assert result["all_chunks_committed_after_resume"] is True


def test_fault_resume_boundary_summary_matches_manifest_probe_contract() -> None:
    chunks = build_synthetic_timeline()

    result = simulate_fault_resume(chunks, 10)

    assert result["resume_from"] == 10
    assert result["completed_preserved"] == set(range(10))
    assert result["reused_after_fault"] is True


def test_out_of_order_completion_has_identical_deterministic_assembly_digest() -> None:
    chunks = build_synthetic_timeline()
    indices = [chunk.index for chunk in chunks]
    forward = deterministic_assembly_digest(chunks, indices)
    reverse = deterministic_assembly_digest(chunks, list(reversed(indices)))
    interleaved = deterministic_assembly_digest(chunks, indices[::2] + indices[1::2])

    assert forward == reverse == interleaved


def test_harness_documents_that_acceptance_is_synthetic_only(tmp_path: Path) -> None:
    receipt = verify.build_receipt(tmp_path)

    assert receipt["timeline"]["hours"] >= 6
    assert receipt["timeline"]["ordered"] is True
    assert receipt["ordering"]["completion_order_independent"] is True
    assert "real 6h media throughput" in receipt["claims_excluded"]
    assert "VRAM/OOM acceptance" in receipt["claims_excluded"]
