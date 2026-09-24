from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import vi_dubber.api as api_mod
from vi_dubber.api import create_app
from vi_dubber.artifacts import atomic_write_json
from vi_dubber.jobs import update_job_state


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(api_mod, "WORK_DIR", work)
    app = create_app()
    return TestClient(app)


def test_api_health(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["service"] == "vi-dubber"
    assert "timestamp" in data


def test_api_system(client: TestClient) -> None:
    res = client.get("/api/system")
    assert res.status_code == 200
    data = res.json()
    assert "gpu_name" in data
    assert "disk_space" in data
    assert "webgpt_connected" in data
    assert "model_catalog" in data
    assert "active_jobs_count" in data


def test_api_jobs_empty(client: TestClient) -> None:
    res = client.get("/api/jobs")
    assert res.status_code == 200
    assert res.json() == []


def test_api_jobs_with_persisted_job(client: TestClient, tmp_path: Path) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-12345678"
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Done",
        metadata={"input_name": "sample.mp4", "profile": "balanced_best"},
    )

    res = client.get("/api/jobs")
    assert res.status_code == 200
    jobs = res.json()
    assert len(jobs) == 1
    assert jobs[0]["id"] == "job-12345678"
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["progress"] == 1.0


def test_api_get_job_details(client: TestClient, tmp_path: Path) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-12345678"
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Finished successfully",
        metadata={"input_name": "test.mp4"},
    )
    atomic_write_json(
        job_dir / "segments_vi.json",
        [
            {
                "id": 1,
                "start": 0.0,
                "end": 2.5,
                "text": "Hello world",
                "vi": "Xin chào thế giới",
                "speaker": "SPEAKER_00",
                "review_status": "unreviewed",
            }
        ],
    )

    # 404 on missing
    res_404 = client.get("/api/jobs/missing-job")
    assert res_404.status_code == 404

    # 200 on existing
    res = client.get("/api/jobs/job-12345678")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "job-12345678"
    assert data["status"] == "completed"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["vi"] == "Xin chào thế giới"


def test_api_job_control_and_segments(client: TestClient, tmp_path: Path) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-abcdef12"
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job_state(
        job_dir,
        status="running",
        stage="tts",
        progress=0.5,
        message="Synthesizing audio",
    )

    # Pause action
    res_pause = client.post("/api/jobs/job-abcdef12/control", json={"action": "pause"})
    assert res_pause.status_code == 200
    assert res_pause.json()["action"] == "pause"

    # Cancel action
    res_cancel = client.post("/api/jobs/job-abcdef12/control", json={"action": "cancel"})
    assert res_cancel.status_code == 200
    assert res_cancel.json()["action"] == "cancel"

    # Segments list
    atomic_write_json(
        job_dir / "segments_source.json",
        [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.0,
                "text": "Start",
                "speaker": "SPEAKER_00",
            }
        ],
    )
    res_seg = client.get("/api/jobs/job-abcdef12/segments")
    assert res_seg.status_code == 200
    segs = res_seg.json()
    assert len(segs) == 1
    assert segs[0]["text"] == "Start"


def test_api_update_segment(client: TestClient, tmp_path: Path) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-review01"
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Done",
        metadata={"input_name": "vid.mp4"},
    )
    seg_source = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "Original English", "speaker": "SPEAKER_00"}
    ]
    seg_trans = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "Original English", "vi": "Bản dịch cũ", "speaker": "SPEAKER_00"}
    ]
    seg_vi = [
        {
            "id": 0,
            "start": 0.0,
            "end": 2.0,
            "text": "Original English",
            "vi": "Bản dịch cũ",
            "speaker": "SPEAKER_00",
            "review_status": "unreviewed",
        }
    ]
    atomic_write_json(job_dir / "segments_source.json", seg_source)
    atomic_write_json(job_dir / "segments_translated.json", seg_trans)
    atomic_write_json(job_dir / "segments_vi.json", seg_vi)
    atomic_write_json(
        job_dir / "tts_stats.json",
        [
            {
                "segment_id": 0,
                "speaker": "SPEAKER_00",
                "target_duration": 2.0,
                "generated_duration": 1.9,
                "final_duration": 1.95,
                "tempo": 1.0,
                "rewrites": 0,
                "used_clone": False,
            }
        ],
    )
    manifests_dir = job_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    for stage_name in ("tts", "timing_assembly", "mix_mux"):
        atomic_write_json(
            manifests_dir / f"{stage_name}.json",
            {
                "version": 1,
                "stage": stage_name,
                "fingerprint": f"fp_{stage_name}",
                "artifacts": [],
            },
        )

    # POST to update segment text and review status
    res = client.post(
        "/api/jobs/job-review01/segments/0",
        json={"vi": "Bản dịch mới đã duyệt", "review_status": "reviewed"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["receipt"]["kind"] == "content_edit"

    # Verify updated segment is returned
    res_segs = client.get("/api/jobs/job-review01/segments")
    assert res_segs.status_code == 200
    segs = res_segs.json()
    assert segs[0]["vi"] == "Bản dịch mới đã duyệt"
    assert segs[0]["review_status"] == "reviewed"


def test_api_rerender_and_dub(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-render01"
    job_dir.mkdir(parents=True, exist_ok=True)

    fake_input = tmp_path / "input.mp4"
    fake_input.write_bytes(b"dummy video data")

    update_job_state(
        job_dir,
        status="paused",
        stage="review",
        progress=0.9,
        message="Awaiting rerender",
        metadata={"input_path": str(fake_input)},
    )
    atomic_write_json(
        job_dir / "job.json",
        {"source_path": str(fake_input), "source_name": "input.mp4"},
    )

    # Mock _run_job_worker so it doesn't spin real pipeline during test
    monkeypatch.setattr(api_mod, "_run_job_worker", lambda *args, **kwargs: None)

    # POST rerender
    res_rerender = client.post("/api/jobs/job-render01/rerender")
    assert res_rerender.status_code == 200
    assert res_rerender.json()["status"] == "started"

    # Mock preflight for create dub
    monkeypatch.setattr(api_mod, "run_preflight", lambda *args, **kwargs: [])
    monkeypatch.setattr(api_mod, "raise_for_preflight", lambda *args, **kwargs: None)

    # POST dub job
    res_dub = client.post(
        "/api/dub",
        json={"input_path": str(fake_input), "profile": "fast", "provider": "local"},
    )
    assert res_dub.status_code == 200
    dub_data = res_dub.json()
    assert dub_data["status"] == "ok"
    assert "job_id" in dub_data


def test_api_spa_fallback_or_redirect(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code in {200, 307}
    # Non-existent API path returns 404
    res_api_404 = client.get("/api/unknown_route")
    assert res_api_404.status_code == 404

