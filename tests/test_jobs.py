from __future__ import annotations

import pytest

from vi_dubber.jobs import (
    InvalidJobControl,
    JobAlreadyRunning,
    PipelineCancelled,
    PipelinePaused,
    check_control,
    claim_job,
    clear_control,
    job_lease_is_live,
    list_job_states,
    reconcile_job_state,
    request_control,
    update_job_state,
)


def test_job_state_is_persisted_and_discoverable(tmp_path) -> None:
    job = tmp_path / "job-1234"
    job.mkdir()
    update_job_state(job, status="running", stage="asr", progress=0.25, message="ASR")
    update_job_state(job, status="completed", progress=1.0, result={"output": "done.mp4"})

    states = list_job_states(tmp_path)
    assert len(states) == 1
    assert states[0]["status"] == "completed"
    assert states[0]["stage"] == "asr"
    assert states[0]["result"]["output"] == "done.mp4"


def test_job_control_is_fail_closed_at_checkpoints(tmp_path) -> None:
    job = tmp_path / "job-control"
    job.mkdir()

    request_control(job, "pause")
    with pytest.raises(PipelinePaused):
        check_control(job)

    request_control(job, "cancel")
    with pytest.raises(PipelineCancelled):
        check_control(job)

    clear_control(job)
    check_control(job)


def test_job_claim_blocks_duplicate_and_recovers_stale_lock(tmp_path) -> None:
    job = tmp_path / "job-lease"
    job.mkdir()

    with claim_job(job):
        with pytest.raises(JobAlreadyRunning):
            with claim_job(job):
                pass

    (job / "run.lock").write_text('{"version": 1, "pid": 99999999}\n', encoding="utf-8")
    with claim_job(job):
        assert (job / "run.lock").exists()
    assert not (job / "run.lock").exists()


def test_running_state_reconciliation_blocks_live_lease_and_recovers_missing_or_stale_lease(tmp_path) -> None:
    live_job = tmp_path / "job-live"
    live_job.mkdir()
    update_job_state(live_job, status="running", stage="translation", progress=0.4)
    with claim_job(live_job):
        assert job_lease_is_live(live_job) is True
        assert reconcile_job_state(live_job)["status"] == "running"

    missing_job = tmp_path / "job-missing"
    missing_job.mkdir()
    update_job_state(missing_job, status="running", stage="tts", progress=0.6)
    recovered = reconcile_job_state(missing_job)
    assert recovered["status"] == "paused"
    assert recovered["stage"] == "tts"
    assert recovered["progress"] == pytest.approx(0.6)
    assert recovered["metadata"]["recovery_reason"] == "missing_or_stale_run_lease"

    stale_job = tmp_path / "job-stale"
    stale_job.mkdir()
    update_job_state(stale_job, status="running", stage="mix", progress=0.8)
    (stale_job / "run.lock").write_text('{"version": 1, "pid": 99999999}\n', encoding="utf-8")
    discovered = {state["job_dir"]: state for state in list_job_states(tmp_path)}
    assert discovered[str(stale_job)]["status"] == "paused"
    assert discovered[str(stale_job)]["metadata"]["recovered_from_stale_running"] is True


def test_job_claim_treats_permission_denied_pid_probe_as_alive(tmp_path, monkeypatch) -> None:
    import vi_dubber.jobs as jobs

    job = tmp_path / "job-permission"
    job.mkdir()
    (job / "run.lock").write_text('{"version": 1, "pid": 424242}\n', encoding="utf-8")
    monkeypatch.setattr(jobs.os, "kill", lambda *_args: (_ for _ in ()).throw(PermissionError()))

    with pytest.raises(JobAlreadyRunning, match="không tạo duplicate run"):
        with claim_job(job):
            pass


def test_job_control_rejects_invalid_or_corrupt_requests(tmp_path) -> None:
    job = tmp_path / "job-control-corrupt"
    job.mkdir()

    with pytest.raises(ValueError, match="không hợp lệ"):
        request_control(job, "bogus")  # type: ignore[arg-type]

    (job / "control.json").write_text("{partial", encoding="utf-8")
    with pytest.raises(InvalidJobControl, match="bị hỏng"):
        check_control(job)
