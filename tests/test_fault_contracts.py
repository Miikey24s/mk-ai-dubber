from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest

from vi_dubber.artifacts import (
    atomic_write_json,
    build_stage_manifest,
    load_stage_manifest,
    stage_fingerprint,
    write_stage_manifest,
)
from vi_dubber.jobs import (
    JobAlreadyRunning,
    claim_job,
    list_job_states,
    load_job_state,
    reconcile_job_state,
    update_job_state,
)
from vi_dubber.preflight import raise_for_preflight, run_preflight
from vi_dubber.profiles import resolve_profile


def _base_config() -> dict:
    return {
        "profile": "balanced_best",
        "asr": {"batch_size": 4, "model": "large-v3"},
        "separation": {"enabled": True},
        "segmentation": {"mode": "smart"},
        "translation": {"provider": "webgpt", "temperature": 0.1},
        "tts": {"backend": "onnx", "device": "cuda", "precision": "fp16"},
        "timing": {
            "max_speedup": 1.25,
            "max_slowdown": 0.92,
            "rewrite_threshold": 1.25,
            "aggressive_rewrite_threshold": 1.4,
        },
        "mix": {"final_lufs": -14.0, "final_true_peak_db": -1.5},
        "qa": {"enabled": True, "semantic": {"max_attempts": 3}},
        "diarization": {},
        "reliability": {"retry_budget": 3, "final_full_qa": True},
    }


def test_corrupt_or_missing_artifact_invalidates_complete_stage_manifest(tmp_path: Path) -> None:
    job = tmp_path / "job-artifact-fault"
    artifact = job / "segments.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('[{"id": 1}]', encoding="utf-8")
    manifest = build_stage_manifest(job, "translation", inputs={"source": "abc"}, artifacts=[artifact])
    manifest_path = job / "manifests" / "translation.json"
    write_stage_manifest(manifest_path, manifest)

    assert load_stage_manifest(manifest_path, job_dir=job) is not None

    artifact.write_text('[{"id": 999}]', encoding="utf-8")
    assert load_stage_manifest(manifest_path, job_dir=job) is None

    artifact.unlink()
    assert load_stage_manifest(manifest_path, job_dir=job) is None


def test_duplicate_lease_fails_closed_and_corrupt_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    job = tmp_path / "job-lease-fault"
    job.mkdir()
    lock_path = job / "run.lock"

    lock_path.write_text(json.dumps({"version": 1, "pid": os.getpid()}), encoding="utf-8")
    with pytest.raises(JobAlreadyRunning, match="không tạo duplicate run"):
        with claim_job(job):
            pass
    assert lock_path.exists()

    lock_path.write_text("partial-json", encoding="utf-8")
    with claim_job(job):
        claimed = json.loads(lock_path.read_text(encoding="utf-8"))
        assert claimed["pid"] == os.getpid()
    assert not lock_path.exists()


@pytest.mark.parametrize(
    ("stage", "previous_stage", "committed_relative", "partial_relative", "progress"),
    [
        ("asr", "separation", "stems/vocals.wav", "segments_raw.partial.json", 0.20),
        ("translation", "asr", "segments_raw.json", "segments_translated.partial.json", 0.34),
        ("tts", "translation", "segments_translated.json", "tts/00001_raw.partial.wav", 0.60),
        ("mix_mux", "timing_assembly", "voice_vi.wav", "dubbed.partial.mp4", 0.84),
    ],
)
def test_terminated_process_preserves_committed_checkpoint_and_reclaims_stale_lease(
    tmp_path: Path,
    stage: str,
    previous_stage: str,
    committed_relative: str,
    partial_relative: str,
    progress: float,
) -> None:
    job = tmp_path / f"job-process-kill-{stage}"
    committed = job / Path(committed_relative)
    committed.parent.mkdir(parents=True)
    committed.write_bytes(b"committed-checkpoint")
    previous_manifest = build_stage_manifest(
        job,
        previous_stage,
        inputs={"source": "fixture"},
        artifacts=[committed],
    )
    previous_manifest_path = job / "manifests" / f"{previous_stage}.json"
    current_manifest_path = job / "manifests" / f"{stage}.json"
    write_stage_manifest(previous_manifest_path, previous_manifest)

    child_code = "\n".join(
        [
            "import sys, time",
            "from pathlib import Path",
            "from vi_dubber.jobs import claim_job, update_job_state",
            "job = Path(sys.argv[1])",
            "stage = sys.argv[2]",
            "partial_relative = sys.argv[3]",
            "progress = float(sys.argv[4])",
            "update_job_state(job, status='running', stage=stage, progress=progress)",
            "with claim_job(job):",
            "    partial = job / Path(partial_relative)",
            "    partial.parent.mkdir(parents=True, exist_ok=True)",
            "    partial.write_bytes(b'uncommitted-partial')",
            "    time.sleep(60)",
        ]
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(job), stage, partial_relative, str(progress)],
        cwd=Path(__file__).resolve().parents[1],
    )
    lock_path = job / "run.lock"
    partial_path = job / Path(partial_relative)
    deadline = time.monotonic() + 5.0
    try:
        while time.monotonic() < deadline and not (lock_path.is_file() and partial_path.is_file()):
            time.sleep(0.05)
        assert lock_path.is_file(), "child process did not acquire the job lease"
        assert partial_path.is_file(), f"child process did not reach the {stage} partial-write boundary"
        assert (
            load_stage_manifest(
                previous_manifest_path,
                job_dir=job,
                expected_stage=previous_stage,
            )
            is not None
        )
        assert load_stage_manifest(current_manifest_path, job_dir=job, expected_stage=stage) is None
    finally:
        process.terminate()
        process.wait(timeout=5)

    recovered = reconcile_job_state(job)
    assert recovered["status"] == "paused"
    assert recovered["stage"] == stage
    assert recovered["progress"] == pytest.approx(progress)
    assert recovered["metadata"]["recovered_from_stale_running"] is True
    assert (
        load_stage_manifest(
            previous_manifest_path,
            job_dir=job,
            expected_stage=previous_stage,
        )
        is not None
    )
    assert load_stage_manifest(current_manifest_path, job_dir=job, expected_stage=stage) is None

    with claim_job(job):
        assert lock_path.is_file()
    assert not lock_path.exists()


def test_terminated_process_during_atomic_write_preserves_previous_committed_json(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    ready = tmp_path / "fsync-ready.txt"
    atomic_write_json(target, {"version": 1, "value": "old"})

    child_code = "\n".join(
        [
            "import sys, time",
            "from pathlib import Path",
            "from vi_dubber import artifacts",
            "target = Path(sys.argv[1])",
            "ready = Path(sys.argv[2])",
            "real_fsync = artifacts.os.fsync",
            "def blocked_fsync(fd):",
            "    real_fsync(fd)",
            "    ready.write_text('ready', encoding='utf-8')",
            "    time.sleep(60)",
            "artifacts.os.fsync = blocked_fsync",
            "artifacts.atomic_write_json(target, {'version': 1, 'value': 'new'})",
        ]
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(target), str(ready)],
        cwd=Path(__file__).resolve().parents[1],
    )
    deadline = time.monotonic() + 5.0
    try:
        while time.monotonic() < deadline and not ready.is_file():
            time.sleep(0.02)
        assert ready.is_file(), "child did not reach the atomic-write fsync boundary"
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1, "value": "old"}
    for orphan in tmp_path.glob(".manifest.json.*.tmp"):
        cleanup_deadline = time.monotonic() + 2.0
        while True:
            try:
                orphan.unlink()
                break
            except PermissionError:
                if time.monotonic() >= cleanup_deadline:
                    raise
                time.sleep(0.02)


def test_live_disk_preflight_fails_clean_without_filling_the_drive(tmp_path: Path) -> None:
    config = _base_config()
    config["asr"]["device"] = "cpu"
    config["tts"]["device"] = "cpu"
    config["reliability"]["min_free_disk_gb"] = 1_000_000_000.0
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"fixture")

    checks = run_preflight(config, translation_provider="local", input_path=input_path)
    disk = {check.name: check for check in checks}["disk"]

    assert disk.status == "error"
    assert "yêu cầu tối thiểu" in disk.detail
    with pytest.raises(RuntimeError, match="disk"):
        raise_for_preflight(checks)


def test_profile_snapshot_is_deterministic_and_only_relevant_stage_inputs_change_cache_key() -> None:
    source = _base_config()
    source_before = deepcopy(source)

    balanced_first = resolve_profile(source, "balanced_best")
    balanced_second = resolve_profile(source, "balanced_best")
    fast = resolve_profile(source, "fast")
    maximum = resolve_profile(source, "max_quality")

    assert source == source_before
    assert balanced_first == balanced_second
    assert balanced_first is not balanced_second

    translation_inputs = {"source_segments": "segments-v1", "glossary": "glossary-v1"}
    balanced_translation = stage_fingerprint(
        "translation",
        inputs=translation_inputs,
        config=balanced_first["translation"],
        model={"name": "chatgpt-web/high"},
    )
    assert stage_fingerprint(
        "translation",
        inputs=translation_inputs,
        config=fast["translation"],
        model={"name": "chatgpt-web/high"},
    ) == balanced_translation
    assert stage_fingerprint(
        "translation",
        inputs=translation_inputs,
        config=maximum["translation"],
        model={"name": "chatgpt-web/high"},
    ) == balanced_translation

    balanced_tts = stage_fingerprint(
        "tts_request",
        inputs={"segments": "translated-v1"},
        config={"tts": balanced_first["tts"], "timing": balanced_first["timing"]},
    )
    fast_tts = stage_fingerprint(
        "tts_request",
        inputs={"segments": "translated-v1"},
        config={"tts": fast["tts"], "timing": fast["timing"]},
    )
    max_tts = stage_fingerprint(
        "tts_request",
        inputs={"segments": "translated-v1"},
        config={"tts": maximum["tts"], "timing": maximum["timing"]},
    )
    assert fast_tts != balanced_tts
    assert max_tts != balanced_tts


def test_persisted_job_state_survives_multiple_updates_and_unrelated_corrupt_state(tmp_path: Path) -> None:
    job = tmp_path / "job-persisted"
    job.mkdir()
    first = update_job_state(
        job,
        status="running",
        stage="translation",
        progress=0.4,
        metadata={"profile": "balanced_best", "source_sha256": "a" * 64},
    )
    completed = update_job_state(
        job,
        status="completed",
        stage="complete",
        progress=2.0,
        result={"output": "done.mp4"},
        metadata={"translator": "webgpt"},
    )

    corrupt = tmp_path / "job-corrupt"
    corrupt.mkdir()
    (corrupt / "state.json").write_text("{partial", encoding="utf-8")

    reloaded = load_job_state(job)
    discovered = list_job_states(tmp_path)

    assert reloaded["created_at"] == first["created_at"]
    assert reloaded["status"] == "completed"
    assert reloaded["progress"] == pytest.approx(1.0)
    assert reloaded["result"] == {"output": "done.mp4"}
    assert reloaded["metadata"] == {
        "profile": "balanced_best",
        "source_sha256": "a" * 64,
        "translator": "webgpt",
    }
    assert completed == reloaded
    assert [state["job_dir"] for state in discovered] == [str(job)]
