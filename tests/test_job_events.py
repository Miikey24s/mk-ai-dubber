from __future__ import annotations

import json

from vi_dubber.jobs import EVENTS_FILE, append_job_event, load_job_events, update_job_state


def test_job_events_are_monotonic_and_state_updates_are_observable(tmp_path) -> None:
    job = tmp_path / "job-events"

    update_job_state(job, status="running", stage="asr", progress=0.25, metadata={"attempt": 2})
    record = append_job_event(
        job,
        "checkpoint.saved",
        stage="asr",
        progress=0.5,
        attempt=2,
        payload={"output": r"C:\private\secret.wav", "api_key": "do-not-store"},
    )

    events = load_job_events(job)
    assert [item["seq"] for item in events] == [1, 2]
    assert events[-1] == record
    assert events[0]["event"] == "state.updated"
    assert events[-1]["payload"] == {
        "api_key": "[redacted]",
        "output": "[redacted-path]",
    }
    assert json.loads((job / "state.json").read_text(encoding="utf-8"))["status"] == "running"


def test_partial_last_event_is_discarded_before_resume_append(tmp_path) -> None:
    job = tmp_path / "job-recovery"
    job.mkdir()
    path = job / EVENTS_FILE
    first = append_job_event(job, "started", stage="extract", progress=0.0)
    with path.open("ab") as handle:
        handle.write(b'{"version": 1, "seq": 999, "event": "partial"')

    resumed = append_job_event(job, "resumed", stage="extract", progress=0.1)
    events = load_job_events(job)
    assert [item["seq"] for item in events] == [first["seq"], resumed["seq"]]
    assert resumed["seq"] == 2
    assert path.read_bytes().endswith(b"\n")


def test_event_loader_skips_corrupt_complete_lines_and_limit_is_recent(tmp_path) -> None:
    job = tmp_path / "job-corrupt"
    job.mkdir()
    path = job / EVENTS_FILE
    first = append_job_event(job, "one")
    with path.open("ab") as handle:
        handle.write(b"not-json\n")
    second = append_job_event(job, "two")

    assert [item["event"] for item in load_job_events(job)] == ["one", "two"]
    assert load_job_events(job, limit=1) == [second]
    assert first["seq"] == 1

