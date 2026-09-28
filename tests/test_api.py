from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import vi_dubber.api as api_mod
from vi_dubber.api import create_app
from vi_dubber.artifacts import atomic_write_json
from vi_dubber.catalog_store import CatalogItem, CatalogStore, UserState
from vi_dubber.jobs import JobAlreadyRunning, update_job_state
from vi_dubber.longform import MacroChunk
from vi_dubber.longform_state import commit_chunk_stage


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


def test_api_catalog_unavailable_is_stable_and_read_only(client: TestClient, tmp_path: Path) -> None:
    res = client.get("/api/catalog")
    assert res.status_code == 200
    data = res.json()
    assert data["catalog_status"] == "unavailable"
    assert data["format"] == "vi-dubber-catalog-view-v1"
    assert data["metadata_only"] is True
    assert data["items"] == []
    assert not (tmp_path / "work" / "catalog.sqlite3").exists()


def test_api_catalog_etag_revalidation_and_query_budget_are_fail_closed(
    client: TestClient,
) -> None:
    first = client.get("/api/catalog")
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    assert first.headers["cache-control"] == "private, no-cache"

    cached = client.get("/api/catalog", headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == etag

    too_long = client.get("/api/catalog", params={"query": "x" * 257})
    assert too_long.status_code == 400
    assert "at most 256" in too_long.json()["detail"]

    nul = client.get("/api/catalog", params={"query": "fixture\x00video"})
    assert nul.status_code == 400
    assert "NUL" in nul.json()["detail"]


def test_api_jobs_and_details_attach_metadata_only_catalog_projection(
    client: TestClient,
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-catalog01"
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Done",
        metadata={"input_name": "sample.mp4"},
    )
    store = CatalogStore(work / "catalog.sqlite3")
    store.rebuild(
        [
            CatalogItem(
                item_id="job-catalog01",
                title="sample.mp4",
                source_fingerprint="a" * 64,
                revision="revision-1",
                availability="available",
                segment_count=3,
                source_ref="jobs/job-catalog01/source/sample.mp4",
                metadata={
                    "source_name": "sample.mp4",
                    "job_status": "completed",
                    "job_stage": "complete",
                    "progress": 1.0,
                    "provider_credentials": "must-not-leak",
                },
            )
        ]
    )
    store.set_user_state(UserState("job-catalog01", "revision-1", (), "reviewed", 12.5))

    catalog = client.get("/api/catalog?query=sample&limit=10")
    assert catalog.status_code == 200
    catalog_data = catalog.json()
    assert catalog_data["catalog_status"] == "ready"
    assert catalog_data["items"][0]["review"]["review_state"] == "reviewed"
    assert "provider_credentials" not in json.dumps(catalog_data)

    listed = client.get("/api/jobs")
    assert listed.status_code == 200
    row = listed.json()[0]
    assert row["catalog"]["item_id"] == "job-catalog01"
    assert row["catalog"]["review"]["watch_position_seconds"] == 12.5

    details = client.get("/api/jobs/job-catalog01")
    assert details.status_code == 200
    assert details.json()["catalog"]["lineage"]["revision"] == "revision-1"


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


def test_api_lists_and_serves_only_verified_chunk_previews(
    client: TestClient,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "work" / "job-preview01"
    preview_dir = job_dir / "chunks" / "chunk_0001" / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / "PREVIEW_chunk_0001.mp4"
    preview_path.write_bytes(b"verified-preview")
    meta_path = job_dir / "chunks" / "chunk_0001" / "preview.json"
    atomic_write_json(
        meta_path,
        {
            "version": 1,
            "kind": "preview",
            "label": "PREVIEW",
            "final": False,
            "stale": False,
            "chunk_id": "chunk_0001",
            "index": 0,
            "start": 0.0,
            "end": 30.0,
            "duration": 30.0,
            "qa_status": "passed",
            "artifact": "chunks/chunk_0001/preview/PREVIEW_chunk_0001.mp4",
        },
    )
    chunk = MacroChunk(
        chunk_id="chunk_0001",
        index=0,
        source_start=0.0,
        source_end=30.0,
        context_start=0.0,
        context_end=30.0,
        boundary_reason="end",
    )
    commit_chunk_stage(
        job_dir,
        chunk,
        "preview",
        inputs={"fixture": True},
        artifacts=[preview_path, meta_path],
        versions={"policy": 1},
    )

    listed = client.get("/api/jobs/job-preview01/previews")
    assert listed.status_code == 200
    previews = listed.json()
    assert len(previews) == 1
    assert previews[0]["label"] == "PREVIEW"
    assert previews[0]["final"] is False
    assert previews[0]["download_available"] is True
    assert previews[0]["play_url"].endswith("/previews/chunk_0001")

    played = client.get("/api/jobs/job-preview01/previews/chunk_0001")
    assert played.status_code == 200
    assert played.content == b"verified-preview"
    assert played.headers["x-vi-dubber-artifact"] == "PREVIEW"

    preview_path.write_bytes(b"tampered")
    assert client.get("/api/jobs/job-preview01/previews").json() == []
    assert client.get("/api/jobs/job-preview01/previews/chunk_0001").status_code == 404


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


def test_segment_edit_marks_only_own_longform_preview_stale(
    client: TestClient,
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "work" / "job-longedit01"
    job_dir.mkdir(parents=True, exist_ok=True)
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Done",
        metadata={
            "longform": {
                "enabled": True,
                "preview_ready_chunks": ["chunk_0001", "chunk_0002"],
                "preview_stale_chunks": [],
            }
        },
    )
    atomic_write_json(
        job_dir / "segments_vi.json",
        [
            {
                "id": 1,
                "start": 10.0,
                "end": 12.0,
                "text": "source",
                "vi": "bản cũ",
                "speaker": "SPEAKER_00",
                "review_status": "unreviewed",
            }
        ],
    )
    atomic_write_json(
        job_dir / "chunks" / "plan.json",
        {
            "version": 1,
            "chunks": [
                {
                    "chunk_id": "chunk_0001",
                    "source_start": 0.0,
                    "source_end": 30.0,
                },
                {
                    "chunk_id": "chunk_0002",
                    "source_start": 30.0,
                    "source_end": 60.0,
                },
            ],
        },
    )
    preview_manifest = job_dir / "chunks" / "chunk_0001" / "manifests" / "preview.json"
    preview_manifest.parent.mkdir(parents=True, exist_ok=True)
    preview_manifest.write_text("{}\n", encoding="utf-8")

    response = client.patch(
        "/api/jobs/job-longedit01/segments/1",
        json={"vi": "bản mới"},
    )

    assert response.status_code == 200
    assert response.json()["receipt"]["longform_chunk_id"] == "chunk_0001"
    assert not preview_manifest.exists()
    state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    longform = state["metadata"]["longform"]
    assert longform["preview_ready_chunks"] == ["chunk_0002"]
    assert longform["preview_stale_chunks"] == ["chunk_0001"]


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


def test_api_duplicate_dub_request_is_idempotent_while_active(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_input = tmp_path / "duplicate-input.mp4"
    fake_input.write_bytes(b"duplicate-video")
    started = threading.Event()
    release = threading.Event()

    def fake_pipeline(**_kwargs: object) -> dict[str, object]:
        started.set()
        assert release.wait(5), "fake pipeline did not receive release"
        return {}

    monkeypatch.setattr(api_mod, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(api_mod, "run_preflight", lambda *args, **kwargs: [])
    monkeypatch.setattr(api_mod, "raise_for_preflight", lambda *args, **kwargs: None)

    first = client.post(
        "/api/dub",
        json={"input_path": str(fake_input), "profile": "fast", "provider": "local"},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "ok"
    assert started.wait(5), "worker did not start"

    duplicate = client.post(
        "/api/dub",
        json={"input_path": str(fake_input), "profile": "fast", "provider": "local"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "status": "already_running",
        "job_id": first.json()["job_id"],
    }

    release.set()
    deadline = time.monotonic() + 5
    job_id = first.json()["job_id"]
    while time.monotonic() < deadline and job_id in api_mod._active_threads:
        time.sleep(0.01)
    assert job_id not in api_mod._active_threads


def test_api_worker_does_not_overwrite_state_on_duplicate_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "job-duplicate-lease"
    job_dir.mkdir()
    update_job_state(
        job_dir,
        status="running",
        stage="translation",
        progress=0.5,
        message="Owner still running",
    )

    def duplicate_lease(**_kwargs: object) -> dict[str, object]:
        raise JobAlreadyRunning("owned by another process")

    monkeypatch.setattr(api_mod, "run_pipeline", duplicate_lease)
    api_mod._run_job_worker(
        job_dir,
        tmp_path / "input.mp4",
        tmp_path / "output.mp4",
        tmp_path / "config.yaml",
        translation_provider="local",
    )

    state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "running"
    assert "error" not in state


def test_api_upload(client: TestClient) -> None:
    res = client.post(
        "/api/upload",
        files={"file": ("video_sample.mp4", b"synthetic mp4 test content")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["filename"] == "video_sample.mp4"
    assert data["size_bytes"] == len(b"synthetic mp4 test content")
    assert Path(data["file_path"]).is_file()


def test_api_download_rejects_output_path_outside_job_directory(
    client: TestClient,
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-pathsafe01"
    job_dir.mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "private.mp4"
    secret.write_bytes(b"must-not-be-served")
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Done",
        metadata={"input_name": "sample.mp4", "output": str(secret)},
    )

    response = client.get("/api/jobs/job-pathsafe01/download")

    assert response.status_code == 404
    assert response.content != secret.read_bytes()


def test_api_download_serves_output_inside_job_directory(
    client: TestClient,
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-pathsafe02"
    job_dir.mkdir(parents=True, exist_ok=True)
    output = job_dir / "dubbed.mp4"
    output.write_bytes(b"verified-output")
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Done",
        metadata={"input_name": "sample.mp4", "output": str(output)},
    )

    response = client.get("/api/jobs/job-pathsafe02/download")

    assert response.status_code == 200
    assert response.content == b"verified-output"


def test_api_download_rejects_symlinked_output_outside_job_directory(
    client: TestClient,
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    job_dir = work / "job-pathsafe03"
    job_dir.mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "private-symlink.mp4"
    secret.write_bytes(b"must-not-be-served-through-symlink")
    output_link = job_dir / "output.mp4"
    try:
        output_link.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable in this environment: {exc}")
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Done",
        metadata={"input_name": "sample.mp4", "output": str(output_link)},
    )

    response = client.get("/api/jobs/job-pathsafe03/download")

    assert response.status_code == 404


def test_api_spa_fallback_or_redirect(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code in {200, 307}
    # Non-existent API path returns 404
    res_api_404 = client.get("/api/unknown_route")
    assert res_api_404.status_code == 404

