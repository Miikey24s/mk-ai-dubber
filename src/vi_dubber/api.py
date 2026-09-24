from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .artifacts import atomic_write_json, fingerprint_file, load_stage_manifest
from .jobs import (
    clear_control,
    list_job_states,
    load_job_state,
    load_json,
    reconcile_job_state,
    request_control,
    update_job_state,
)
from .pipeline import load_config, run_pipeline
from .preflight import raise_for_preflight, run_preflight
from .profiles import DEFAULT_PROFILE, resolve_profile
from .review import (
    ReviewDataError,
    load_review_rows,
    update_segment_review,
)
from .runtime import PROJECT_ROOT, WORK_DIR, configure_runtime
from .translate import (
    DEFAULT_WEBGPT_MODEL,
    normalize_translation_provider,
    translation_model_catalog,
    webgpt_route_info,
)
from .youtube import download_youtube, is_youtube_url

_START_TIME = time.time()
_active_threads: dict[str, threading.Thread] = {}


def _read_json_any(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_job_dir(job_id: str) -> Path:
    clean_id = Path(job_id).name
    candidate = WORK_DIR / clean_id
    if not candidate.is_dir():
        # Check by prefix match if user passed partial hash
        matches = [d for d in WORK_DIR.glob(f"job-*{clean_id}*") if d.is_dir()]
        if len(matches) == 1:
            return matches[0]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )
    return candidate


def _run_job_worker(
    job_dir: Path,
    input_path: Path,
    output_path: Path,
    config_path: Path,
    *,
    voice_ref: Path | None = None,
    hf_token: str | None = None,
    diarize_override: bool | None = None,
    translation_provider: str | None = None,
    translation_model: str | None = None,
    translation_effort: str | None = None,
    profile: str | None = None,
    resume: bool = True,
) -> None:
    job_id = job_dir.name
    try:
        update_job_state(
            job_dir,
            status="running",
            stage="starting",
            progress=0.01,
            message="Đang chuẩn bị chạy pipeline...",
        )
        run_pipeline(
            input_path=input_path,
            output_path=output_path,
            config_path=config_path,
            voice_ref=voice_ref,
            hf_token=hf_token,
            diarize_override=diarize_override,
            translation_provider=translation_provider,
            translation_model=translation_model,
            translation_effort=translation_effort,
            profile=profile,
            resume=resume,
            progress_callback=lambda p, msg: None,
        )
    except Exception as exc:
        update_job_state(
            job_dir,
            status="failed",
            message=str(exc),
            error={"type": type(exc).__name__, "message": str(exc)},
        )
    finally:
        _active_threads.pop(job_id, None)


def _trigger_job_resume(job_dir: Path) -> dict[str, Any]:
    job_id = job_dir.name
    if job_id in _active_threads and _active_threads[job_id].is_alive():
        return {"status": "already_running", "job_id": job_id}

    state = reconcile_job_state(job_dir)
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    job_info = load_json(job_dir / "job.json")

    input_val = str(metadata.get("input_path") or job_info.get("source_path") or "")
    if not input_val or not Path(input_val).is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resume job: source input file not found for {job_id}",
        )

    output_val = str(metadata.get("output") or job_dir / "dubbed_output.mp4")
    config_snapshot = job_dir / "resolved_config.json"
    config_path = config_snapshot if config_snapshot.is_file() else (PROJECT_ROOT / "config.yaml")
    provider = str(metadata.get("translation_provider") or "webgpt")
    profile = str(metadata.get("profile") or DEFAULT_PROFILE)
    model = str(metadata.get("translation_model") or "").strip() or None
    effort = str(metadata.get("translation_effort") or "").strip() or None
    voice_ref_val = metadata.get("voice_ref")
    voice_ref = Path(voice_ref_val) if voice_ref_val and Path(voice_ref_val).is_file() else None
    diarize_val = metadata.get("diarization")
    diarize_override = (
        True
        if diarize_val in {True, "True", "true", "Bật"}
        else (False if diarize_val in {False, "False", "false", "Tắt"} else None)
    )

    clear_control(job_dir)
    thread = threading.Thread(
        target=_run_job_worker,
        args=(job_dir, Path(input_val), Path(output_val), config_path),
        kwargs={
            "voice_ref": voice_ref,
            "diarize_override": diarize_override,
            "translation_provider": provider,
            "translation_model": model,
            "translation_effort": effort,
            "profile": profile,
            "resume": True,
        },
        name=f"vi-dubber-{job_id}",
        daemon=True,
    )
    _active_threads[job_id] = thread
    thread.start()

    return {"status": "started", "job_id": job_id, "action": "resume"}


# Models for API requests
class JobControlRequest(BaseModel):
    action: Literal["pause", "cancel", "run"]


class SegmentUpdateRequest(BaseModel):
    text: str | None = None
    vi: str | None = None
    speaker: str | None = None
    review_status: str | None = None


class DubRequest(BaseModel):
    input_path: str | None = None
    youtube_url: str | None = None
    profile: str = "balanced_best"
    provider: str = "webgpt"
    translation_model: str | None = None
    translation_effort: str | None = None
    voice_ref: str | None = None
    diarize: bool | str | None = "auto"
    output_path: str | None = None


def create_app() -> FastAPI:
    configure_runtime()
    app = FastAPI(
        title="VI Dubber API",
        version="1.0.0",
        description="REST API and UI Server for VI Dubber Video Dubbing System",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health_check() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "vi-dubber",
            "version": "1.0.0",
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/system")
    def system_status() -> dict[str, Any]:
        gpu_name = "None"
        gpu_vram_free = 0
        gpu_vram_total = 0
        gpu_vram_used = 0
        try:
            import torch

            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                free_b, total_b = torch.cuda.mem_get_info(0)
                gpu_vram_free = int(free_b)
                gpu_vram_total = int(total_b)
                gpu_vram_used = max(0, gpu_vram_total - gpu_vram_free)
        except Exception:
            pass

        disk_free = 0
        disk_total = 0
        try:
            usage = shutil.disk_usage(WORK_DIR if WORK_DIR.exists() else WORK_DIR.parent)
            disk_free = usage.free
            disk_total = usage.total
        except Exception:
            pass

        webgpt_connected = False
        webgpt_model = DEFAULT_WEBGPT_MODEL
        webgpt_port = 17842
        webgpt_status_dict: dict[str, Any] = {}
        try:
            webgpt_status_dict = webgpt_route_info()
            webgpt_connected = bool(webgpt_status_dict.get("ready"))
            webgpt_model = str(webgpt_status_dict.get("model") or DEFAULT_WEBGPT_MODEL)
            webgpt_port = int(webgpt_status_dict.get("port") or 17842)
        except Exception:
            pass

        catalog: dict[str, Any] = {"status": "unavailable", "models": []}
        try:
            catalog = translation_model_catalog()
        except Exception:
            pass

        all_states = list_job_states(WORK_DIR)
        active_count = sum(1 for s in all_states if s.get("status") in {"running", "queued"})

        cpu_util = 0.0
        try:
            import psutil

            cpu_util = float(psutil.cpu_percent(interval=None))
        except Exception:
            pass

        uptime_seconds = float(time.time() - _START_TIME)

        return {
            "gpu_name": gpu_name,
            "gpu_device_name": gpu_name,
            "gpu_vram_used_bytes": gpu_vram_used,
            "gpu_vram_total_bytes": gpu_vram_total,
            "gpu_vram_free_bytes": gpu_vram_free,
            "vram_free_gb": round(gpu_vram_free / (1024**3), 2),
            "vram_total_gb": round(gpu_vram_total / (1024**3), 2),
            "gpu_utilization_pct": 0.0,
            "cpu_utilization_pct": cpu_util,
            "active_jobs_count": active_count,
            "webgpt_connected": webgpt_connected,
            "webgpt_model": webgpt_model,
            "webgpt_port": webgpt_port,
            "webgpt_status": webgpt_status_dict,
            "model_catalog": catalog,
            "disk_space": {
                "free_bytes": disk_free,
                "total_bytes": disk_total,
                "free_gb": round(disk_free / (1024**3), 2),
                "total_gb": round(disk_total / (1024**3), 2),
                "percent_used": round(
                    ((disk_total - disk_free) / max(1, disk_total)) * 100.0, 1
                ),
            },
            "server_uptime_seconds": uptime_seconds,
            "websocket_connected": True,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        states = list_job_states(WORK_DIR)
        results: list[dict[str, Any]] = []
        for state in states:
            job_dir_path = Path(str(state.get("job_dir") or ""))
            job_id = job_dir_path.name
            results.append(
                {
                    "id": job_id,
                    "job_id": job_id,
                    "status": state.get("status", "unknown"),
                    "stage": state.get("stage", ""),
                    "progress": float(state.get("progress") or 0.0),
                    "message": state.get("message", ""),
                    "created_at": state.get("created_at", ""),
                    "updated_at": state.get("updated_at", ""),
                    "metadata": state.get("metadata") or {},
                    "result": state.get("result"),
                    "error": state.get("error"),
                    "metrics": state.get("metrics"),
                    "job_dir": str(job_dir_path),
                }
            )
        return results

    @app.get("/api/jobs/{job_id}")
    def get_job_details(job_id: str) -> dict[str, Any]:
        job_dir = _resolve_job_dir(job_id)
        state = reconcile_job_state(job_dir)
        manifests: dict[str, Any] = {}
        manifests_dir = job_dir / "manifests"
        if manifests_dir.is_dir():
            for m_path in sorted(manifests_dir.glob("*.json")):
                m_data = load_json(m_path)
                manifests[m_path.stem] = m_data

        segments: list[dict[str, Any]] = []
        try:
            segments = load_review_rows(job_dir)
        except Exception:
            for s_name in ("segments_vi.json", "segments_translated.json", "segments_source.json"):
                s_path = job_dir / s_name
                if s_path.is_file():
                    raw = _read_json_any(s_path)
                    if isinstance(raw, list):
                        segments = raw
                        break

        job_info = load_json(job_dir / "job.json")
        result = load_json(job_dir / "result.json")
        metrics_data = load_json(job_dir / "metrics.json")
        qa_data = load_json(job_dir / "qa.json")

        return {
            "id": job_dir.name,
            "job_id": job_dir.name,
            "status": state.get("status", "unknown"),
            "stage": state.get("stage", ""),
            "progress": float(state.get("progress") or 0.0),
            "message": state.get("message", ""),
            "created_at": state.get("created_at", ""),
            "updated_at": state.get("updated_at", ""),
            "metadata": state.get("metadata") or {},
            "manifests": manifests,
            "job": job_info,
            "result": result or state.get("result"),
            "metrics": metrics_data or state.get("metrics"),
            "qa": qa_data,
            "segments": segments,
            "job_dir": str(job_dir),
        }

    @app.post("/api/jobs/{job_id}/control")
    def job_control(job_id: str, payload: JobControlRequest) -> dict[str, Any]:
        job_dir = _resolve_job_dir(job_id)
        action = payload.action
        if action in {"pause", "cancel"}:
            request_control(job_dir, action)
            reconcile_job_state(job_dir)
            return {"status": "ok", "job_id": job_dir.name, "action": action}
        elif action == "run":
            clear_control(job_dir)
            return _trigger_job_resume(job_dir)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid action: {action}",
            )

    @app.get("/api/jobs/{job_id}/segments")
    def get_job_segments(job_id: str) -> list[dict[str, Any]]:
        job_dir = _resolve_job_dir(job_id)
        try:
            return load_review_rows(job_dir)
        except Exception:
            pass

        for s_name in ("segments_vi.json", "segments_translated.json", "segments_source.json"):
            s_path = job_dir / s_name
            if s_path.is_file():
                raw = _read_json_any(s_path)
                if isinstance(raw, list):
                    return raw
        return []

    @app.post("/api/jobs/{job_id}/segments/{segment_id}")
    @app.patch("/api/jobs/{job_id}/segments/{segment_id}")
    def update_job_segment(
        job_id: str,
        segment_id: int,
        payload: SegmentUpdateRequest,
    ) -> dict[str, Any]:
        job_dir = _resolve_job_dir(job_id)
        text_update = payload.vi or payload.text
        try:
            receipt = update_segment_review(
                job_dir,
                segment_id,
                text=text_update,
                speaker=payload.speaker,
                review_status=payload.review_status,
            )
            if receipt.get("kind") == "content_edit":
                update_job_state(
                    job_dir,
                    status="paused",
                    stage="review",
                    message=f"Segment {segment_id} đã sửa; cần render lại downstream.",
                )
            return {"status": "ok", "receipt": receipt}
        except ReviewDataError as exc:
            vi_path = job_dir / "segments_vi.json"
            if vi_path.is_file():
                segments = _read_json_any(vi_path)
                if isinstance(segments, list):
                    for item in segments:
                        if isinstance(item, dict) and item.get("id") == segment_id:
                            if text_update is not None:
                                item["vi"] = text_update
                            if payload.speaker is not None:
                                item["speaker"] = payload.speaker
                            if payload.review_status is not None:
                                item["review_status"] = payload.review_status
                            atomic_write_json(vi_path, segments)
                            update_job_state(
                                job_dir,
                                status="paused",
                                stage="review",
                                message=f"Segment {segment_id} đã sửa; cần render lại downstream.",
                            )
                            return {
                                "status": "ok",
                                "receipt": {
                                    "kind": "content_edit" if text_update else "status_update",
                                    "segment_id": segment_id,
                                    "after": item,
                                },
                            }
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.post("/api/jobs/{job_id}/rerender")
    def rerender_job(job_id: str) -> dict[str, Any]:
        job_dir = _resolve_job_dir(job_id)
        return _trigger_job_resume(job_dir)

    @app.post("/api/upload")
    async def upload_media_file(file: UploadFile = File(...)) -> dict[str, Any]:
        upload_dir = WORK_DIR / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename or "uploaded_video.mp4").name
        dest_path = upload_dir / safe_name
        try:
            with dest_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        finally:
            await file.close()

        return {
            "status": "ok",
            "filename": safe_name,
            "file_path": str(dest_path),
            "size_bytes": dest_path.stat().st_size,
        }

    @app.post("/api/dub")
    @app.post("/api/jobs")
    def create_dub_job(payload: DubRequest) -> dict[str, Any]:
        input_path_str = payload.input_path
        youtube_url = payload.youtube_url

        if not input_path_str and not youtube_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either input_path or youtube_url must be provided.",
            )

        if youtube_url:
            if not is_youtube_url(youtube_url):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid YouTube URL.",
                )
            yt_dir = WORK_DIR / "youtube"
            media_path, _meta = download_youtube(youtube_url, yt_dir)
            input_path = media_path
        else:
            assert input_path_str is not None
            input_path = Path(input_path_str).expanduser().resolve()
            if not input_path.is_file():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Input file not found: {input_path_str}",
                )

        profile = payload.profile or DEFAULT_PROFILE
        config_path = PROJECT_ROOT / "config.yaml"
        resolved_config = load_config(config_path, profile)

        provider = normalize_translation_provider(payload.provider or "webgpt")
        voice_ref = Path(payload.voice_ref).resolve() if payload.voice_ref else None
        diarize_bool = (
            True
            if payload.diarize in {True, "True", "true", "on", "Bật"}
            else (False if payload.diarize in {False, "False", "false", "off", "Tắt"} else None)
        )

        checks = run_preflight(
            resolved_config,
            translation_provider=provider,
            input_path=input_path,
            voice_ref=voice_ref,
            diarize=(diarize_bool is True),
        )
        raise_for_preflight(checks)

        source_id = fingerprint_file(input_path)
        job_dir = WORK_DIR / f"job-{source_id['sha256'][:16]}"
        job_dir.mkdir(parents=True, exist_ok=True)

        output_path = (
            Path(payload.output_path).resolve()
            if payload.output_path
            else (job_dir / f"{input_path.stem}.vi.mp4")
        )

        update_job_state(
            job_dir,
            status="queued",
            stage="prepare",
            progress=0.0,
            message="Đã khởi tạo tác vụ",
            metadata={
                "input_path": str(input_path),
                "input_name": input_path.name,
                "source_mode": "YouTube" if youtube_url else "Local",
                "youtube_url": youtube_url or "",
                "profile": profile,
                "translation_provider": provider,
                "translation_model": payload.translation_model or "",
                "translation_effort": payload.translation_effort or "",
                "voice_ref": str(voice_ref) if voice_ref else "",
                "diarization": payload.diarize,
                "output": str(output_path),
            },
        )

        clear_control(job_dir)
        thread = threading.Thread(
            target=_run_job_worker,
            args=(job_dir, input_path, output_path, config_path),
            kwargs={
                "voice_ref": voice_ref,
                "diarize_override": diarize_bool,
                "translation_provider": provider,
                "translation_model": payload.translation_model,
                "translation_effort": payload.translation_effort,
                "profile": profile,
                "resume": True,
            },
            name=f"vi-dubber-{job_dir.name}",
            daemon=True,
        )
        _active_threads[job_dir.name] = thread
        thread.start()

        return {
            "status": "ok",
            "job_id": job_dir.name,
            "job_dir": str(job_dir),
            "message": "Dubbing job started",
        }

    # Mount static files from frontend/dist if available
    dist_dir = PROJECT_ROOT / "frontend" / "dist"
    if dist_dir.is_dir() and (dist_dir / "index.html").is_file():
        assets_dir = dist_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend_assets")

        @app.get("/{full_path:path}")
        async def serve_spa_frontend(full_path: str) -> FileResponse:
            if full_path.startswith("api/") or full_path == "api":
                raise HTTPException(status_code=404, detail="API endpoint not found")
            if full_path.startswith("gradio"):
                raise HTTPException(status_code=404, detail="Gradio route")
            candidate = dist_dir / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist_dir / "index.html")
    else:
        @app.get("/")
        async def root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/gradio")

    return app


api_app = create_app()
