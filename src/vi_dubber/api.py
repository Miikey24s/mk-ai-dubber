from __future__ import annotations

import json
import os
import sqlite3
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .artifacts import (
    atomic_write_json,
    fingerprint_data,
    fingerprint_file,
    load_stage_manifest,
    resolve_artifact_path,
)
from .catalog_store import (
    AVAILABILITY_STATES,
    CATALOG_SCHEMA_VERSION,
    MAX_SEARCH_QUERY_CHARS,
    CatalogError,
    CatalogStore,
)
from .catalog_view import build_catalog_view
from .jobs import (
    JobAlreadyRunning,
    clear_control,
    job_lease_is_live,
    list_job_states,
    load_job_state,
    load_json,
    reconcile_job_state,
    request_control,
    update_job_state,
)
from .pipeline import load_config, run_pipeline
from .longform_state import invalidate_chunk_from
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
from .webgpt_runtime import DUBBER_WEBGPT_PORT, runtime_status, start_runtime
from .youtube import download_youtube, is_youtube_url

_START_TIME = time.time()
_active_threads: dict[str, threading.Thread] = {}
_active_threads_lock = threading.Lock()
_CATALOG_DB_NAME = "catalog.sqlite3"


def _catalog_db_path() -> Path:
    """Return the local catalog location for the currently configured work root."""

    return WORK_DIR / _CATALOG_DB_NAME


def _open_catalog_for_read() -> CatalogStore | None:
    """Open an already-initialized catalog without creating or migrating it.

    API GETs must not turn an absent or partially-created work directory into a
    new SQLite database.  Probe the schema through SQLite's read-only URI first;
    ``CatalogStore.read_view`` is then safe because it sees the supported schema
    version and only performs SELECTs.
    """

    db_path = _catalog_db_path()
    if not db_path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()
        if version != CATALOG_SCHEMA_VERSION:
            return None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None
    return CatalogStore(db_path)


def _read_catalog_view(
    *,
    query: str = "",
    availability: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[dict[str, Any] | None, str]:
    """Read the metadata-only catalog view, reporting availability separately.

    The adapter intentionally does not rebuild from ``work/``.  Projection
    rebuilds are an explicit lifecycle operation; a GET must never hash media,
    overwrite review state, or manufacture a catalog as a side effect.
    """

    store = _open_catalog_for_read()
    if store is None:
        return None, "unavailable"
    try:
        return store.read_view(query, availability=availability, limit=limit, offset=offset), "ready"
    except (CatalogError, OSError, sqlite3.Error, TypeError, ValueError):
        return None, "invalid"


def _empty_catalog_view(
    *,
    query: str = "",
    availability: str | None = None,
    limit: int = 100,
    offset: int = 0,
    status_value: str = "unavailable",
) -> dict[str, Any]:
    """Keep the API shape stable while the durable projection is unavailable."""

    return {
        **build_catalog_view(
            [],
            query=query,
            availability=availability,
            limit=limit,
            offset=offset,
        ),
        "catalog_status": status_value,
    }


def _catalog_row_for_job(job_id: str) -> dict[str, Any] | None:
    """Return one metadata-only row for a job, if a durable projection exists."""

    store = _open_catalog_for_read()
    if store is None:
        return None
    try:
        item = store.get_item(job_id)
        if item is None:
            return None
        state = store.get_user_state(job_id)
        view = build_catalog_view([item], [state] if state is not None else ())
        return view["items"][0] if view.get("items") else None
    except (CatalogError, OSError, sqlite3.Error, TypeError, ValueError):
        return None


def _cors_origins() -> list[str]:
    """Return explicit browser origins; never combine wildcard CORS with credentials."""

    configured = os.getenv("VI_DUBBER_CORS_ORIGINS", "").strip()
    if configured:
        return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return [
        "http://127.0.0.1:7860",
        "http://localhost:7860",
    ]


def _ensure_webgpt_runtime_ready() -> dict[str, Any]:
    """Self-heal the dedicated translation runtime before accepting a job."""
    current = runtime_status(timeout=0.5)
    if bool(current.get("ready")):
        return current
    try:
        started = start_runtime(wait_seconds=15.0)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Dedicated Dubber-WebGPT chưa sẵn sàng: {exc}",
        ) from exc
    if not bool(started.get("ready")):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dedicated Dubber-WebGPT không sẵn sàng sau khi tự khởi động.",
        )
    return started


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


def _safe_job_file(job_dir: Path, value: str | Path) -> Path | None:
    """Resolve one file only when it remains inside the job directory.

    Job state and result metadata are persisted data, not a trust boundary. A
    stale or tampered output path must never turn the download endpoint into an
    arbitrary local-file reader. Resolving before the containment check also
    rejects symlinks that point outside the job directory.
    """
    try:
        root = job_dir.resolve(strict=True)
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _list_chunk_previews(job_dir: Path) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    chunks_dir = job_dir / "chunks"
    if not chunks_dir.is_dir():
        return previews
    for chunk_dir in sorted(path for path in chunks_dir.glob("chunk_*") if path.is_dir()):
        manifest_path = chunk_dir / "manifests" / "preview.json"
        manifest = load_stage_manifest(
            manifest_path,
            job_dir=job_dir,
            expected_stage="chunk_preview",
            verify_artifacts=True,
        )
        if manifest is None:
            continue
        meta_path = chunk_dir / "preview.json"
        meta = load_json(meta_path)
        artifact = meta.get("artifact")
        if not isinstance(artifact, str):
            continue
        artifact_paths = {
            str(item.get("path"))
            for item in manifest.get("artifacts", [])
            if isinstance(item, dict)
        }
        if artifact not in artifact_paths:
            continue
        try:
            preview_path = resolve_artifact_path(job_dir, artifact)
        except ValueError:
            continue
        if not preview_path.is_file():
            continue
        chunk_id = chunk_dir.name
        previews.append(
            {
                **meta,
                "chunk_id": chunk_id,
                "label": "PREVIEW",
                "final": False,
                "stale": False,
                "manifest_fingerprint": manifest.get("fingerprint"),
                "play_url": f"/api/jobs/{job_dir.name}/previews/{chunk_id}",
                "download_url": f"/api/jobs/{job_dir.name}/previews/{chunk_id}?download=true",
                "download_available": True,
            }
        )
    return previews


def _invalidate_longform_chunk_for_segment(job_dir: Path, segment_id: int) -> str | None:
    plan = load_json(job_dir / "chunks" / "plan.json")
    raw_chunks = plan.get("chunks")
    raw_segments = _read_json_any(job_dir / "segments_vi.json")
    if not isinstance(raw_chunks, list) or not isinstance(raw_segments, list):
        return None
    segment = next(
        (
            item
            for item in raw_segments
            if isinstance(item, dict) and int(item.get("id", -1)) == int(segment_id)
        ),
        None,
    )
    if segment is None:
        return None
    midpoint = (float(segment["start"]) + float(segment["end"])) / 2.0
    for raw_chunk in raw_chunks:
        if not isinstance(raw_chunk, dict):
            continue
        start = float(raw_chunk.get("source_start", 0.0))
        end = float(raw_chunk.get("source_end", 0.0))
        if start <= midpoint < end:
            chunk_id = str(raw_chunk.get("chunk_id") or "")
            if chunk_id:
                invalidate_chunk_from(job_dir, chunk_id, "tts")
                return chunk_id
    return None


def _mark_chunk_preview_stale(job_dir: Path, chunk_id: str) -> None:
    state = load_job_state(job_dir)
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    longform = metadata.get("longform") if isinstance(metadata.get("longform"), dict) else {}
    ready = [
        value
        for value in longform.get("preview_ready_chunks", [])
        if isinstance(value, str) and value != chunk_id
    ]
    stale = {
        value
        for value in longform.get("preview_stale_chunks", [])
        if isinstance(value, str)
    }
    stale.add(chunk_id)
    update_job_state(
        job_dir,
        metadata={
            "longform": {
                **longform,
                "preview_ready_chunks": ready,
                "preview_stale_chunks": sorted(stale),
            }
        },
    )


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
    except JobAlreadyRunning:
        # Another process owns the durable run lease.  Its state is authoritative;
        # a duplicate API request must never overwrite that run as failed.
        return
    except Exception as exc:
        update_job_state(
            job_dir,
            status="failed",
            message=str(exc),
            error={"type": type(exc).__name__, "message": str(exc)},
        )
    finally:
        with _active_threads_lock:
            if _active_threads.get(job_id) is threading.current_thread():
                _active_threads.pop(job_id, None)


def _trigger_job_resume(job_dir: Path) -> dict[str, Any]:
    job_id = job_dir.name
    with _active_threads_lock:
        existing = _active_threads.get(job_id)
        if existing is not None:
            if existing.is_alive():
                return {"status": "already_running", "job_id": job_id}
            _active_threads.pop(job_id, None)

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
    with _active_threads_lock:
        existing = _active_threads.get(job_id)
        if existing is not None and existing.is_alive():
            return {"status": "already_running", "job_id": job_id}
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
    profile: str = "balanced_fast"
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
        allow_origins=_cors_origins(),
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
        webgpt_port = DUBBER_WEBGPT_PORT
        webgpt_status_dict: dict[str, Any] = {}
        try:
            webgpt_status_dict = webgpt_route_info()
            webgpt_connected = bool(webgpt_status_dict.get("ready"))
            webgpt_model = str(webgpt_status_dict.get("model") or DEFAULT_WEBGPT_MODEL)
            webgpt_port = int(webgpt_status_dict.get("port") or DUBBER_WEBGPT_PORT)
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
        # Read the projection once for the whole page.  The catalog is
        # optional during migration, so job-state listing remains available
        # when the SQLite projection has not been created yet.
        catalog_view, _catalog_status = _read_catalog_view(
            limit=min(max(100, len(states)), 5_000),
            offset=0,
        )
        catalog_by_id = {
            str(item.get("item_id")): item
            for item in (catalog_view or {}).get("items", [])
            if isinstance(item, dict) and isinstance(item.get("item_id"), str)
        }
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
                    # Additive field: the existing state payload stays the
                    # source of truth for runtime control, while this
                    # metadata-only row supplies revision/availability/review
                    # state when M5 has a durable projection.
                    "catalog": catalog_by_id.get(job_id),
                }
            )
        return results

    @app.get("/api/catalog")
    def get_catalog(
        request: Request,
        query: str = "",
        availability: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return the versioned metadata-only catalog read model.

        This endpoint never rebuilds the projection and never returns media
        URLs or bytes.  An absent/unsupported catalog is represented by the
        same stable empty view with ``catalog_status`` so the frontend can
        render an empty/unavailable state without guessing.
        """

        if not isinstance(query, str):
            raise HTTPException(status_code=400, detail="catalog query must be a string")
        if len(query) > MAX_SEARCH_QUERY_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"catalog query must be at most {MAX_SEARCH_QUERY_CHARS} characters",
            )
        if "\x00" in query:
            raise HTTPException(status_code=400, detail="catalog query must not contain NUL")
        if availability is not None and availability not in AVAILABILITY_STATES:
            raise HTTPException(status_code=400, detail=f"unsupported catalog availability: {availability}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= 5_000:
            raise HTTPException(status_code=400, detail="catalog limit must be between 1 and 5000")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise HTTPException(status_code=400, detail="catalog offset must be non-negative")

        view, read_status = _read_catalog_view(
            query=query,
            availability=availability,
            limit=limit,
            offset=offset,
        )
        if view is None:
            payload = _empty_catalog_view(
                query=query,
                availability=availability,
                limit=limit,
                offset=offset,
                status_value=read_status,
            )
        else:
            payload = {**view, "catalog_status": read_status}

        # The projection and query are deterministic, so clients can
        # revalidate without downloading an unchanged page. Keep it private:
        # local job metadata must not become shared-proxy cache content.
        etag = f'"{fingerprint_data(payload)}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(content=payload, headers=headers)

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
            "previews": _list_chunk_previews(job_dir),
            "segments": segments,
            "job_dir": str(job_dir),
            "catalog": _catalog_row_for_job(job_dir.name),
        }

    @app.get("/api/jobs/{job_id}/previews")
    def list_job_previews(job_id: str) -> list[dict[str, Any]]:
        job_dir = _resolve_job_dir(job_id)
        return _list_chunk_previews(job_dir)

    @app.get("/api/jobs/{job_id}/previews/{chunk_id}")
    def get_job_preview(job_id: str, chunk_id: str, download: bool = False) -> FileResponse:
        job_dir = _resolve_job_dir(job_id)
        clean_chunk_id = Path(chunk_id).name
        preview = next(
            (
                item
                for item in _list_chunk_previews(job_dir)
                if item.get("chunk_id") == clean_chunk_id
            ),
            None,
        )
        if preview is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Preview not found or stale: {chunk_id}",
            )
        preview_path = resolve_artifact_path(job_dir, str(preview["artifact"]))
        headers = {
            "Cache-Control": "no-store",
            "X-VI-Dubber-Artifact": "PREVIEW",
        }
        if download:
            filename = f"PREVIEW_{clean_chunk_id}.mp4"
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
            return FileResponse(
                preview_path,
                media_type="video/mp4",
                filename=filename,
                headers=headers,
            )
        return FileResponse(preview_path, media_type="video/mp4", headers=headers)

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
                stale_chunk_id = _invalidate_longform_chunk_for_segment(job_dir, segment_id)
                if stale_chunk_id is not None:
                    _mark_chunk_preview_stale(job_dir, stale_chunk_id)
                    receipt = {**receipt, "longform_chunk_id": stale_chunk_id}
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
                            content_changed = text_update is not None or payload.speaker is not None
                            stale_chunk_id = (
                                _invalidate_longform_chunk_for_segment(job_dir, segment_id)
                                if content_changed
                                else None
                            )
                            if stale_chunk_id is not None:
                                _mark_chunk_preview_stale(job_dir, stale_chunk_id)
                            update_job_state(
                                job_dir,
                                status="paused",
                                stage="review",
                                message=f"Segment {segment_id} đã sửa; cần render lại downstream.",
                            )
                            return {
                                "status": "ok",
                                "receipt": {
                                    "kind": "content_edit" if content_changed else "status_update",
                                    "segment_id": segment_id,
                                    "after": item,
                                    "longform_chunk_id": stale_chunk_id,
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

    @app.get("/api/jobs/{job_id}/download")
    def download_job_output(job_id: str, type: str = "video") -> FileResponse:
        job_dir = _resolve_job_dir(job_id)
        state = reconcile_job_state(job_dir)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        result = state.get("result") if isinstance(state.get("result"), dict) else {}
        input_name = metadata.get("input_name", job_id)
        stem = Path(input_name).stem if input_name else job_id

        if type in {"subtitles", "srt"}:
            for s_name in ("source_turns.srt", "source_en.srt", "subtitles.srt"):
                candidate = _safe_job_file(job_dir, s_name)
                if candidate is not None:
                    return FileResponse(
                        candidate,
                        media_type="application/x-subrip",
                        filename=f"{stem}.vi.srt",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.vi.srt"'},
                    )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subtitle file not found")

        # Video format download
        candidates: list[Path] = []
        for candidate_key in [result.get("output"), metadata.get("output")]:
            if candidate_key:
                p = _safe_job_file(job_dir, str(candidate_key))
                if p is not None:
                    candidates.append(p)

        for name in ("dubbed.mp4", f"{stem}.vi.mp4", "output.mp4", "final.mp4"):
            p = _safe_job_file(job_dir, name)
            if p is not None and p not in candidates:
                candidates.append(p)

        for candidate in sorted(job_dir.glob("*.mp4")):
            p = _safe_job_file(job_dir, candidate)
            if p is not None and p not in candidates:
                candidates.append(p)

        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No output video found for job {job_id}.",
            )

        target_file = candidates[0]
        download_name = f"{stem}.vi.mp4"
        return FileResponse(
            target_file,
            media_type="video/mp4",
            filename=download_name,
            headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
        )

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

        if youtube_url and not is_youtube_url(youtube_url):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid YouTube URL.",
            )

        input_path: Path | None = None
        if input_path_str and not youtube_url:
            input_path = Path(input_path_str).expanduser().resolve()
            if not input_path.is_file():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Input file not found: {input_path_str}",
                )

        profile = payload.profile or DEFAULT_PROFILE
        config_path = PROJECT_ROOT / "config.yaml"
        try:
            resolved_config = load_config(config_path, profile)
            provider = normalize_translation_provider(payload.provider or "webgpt")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # The dedicated WebGPT listener is an owned dependency of the product.
        # Recover it on demand instead of surfacing a generic 500 to the UI.
        if provider == "webgpt":
            _ensure_webgpt_runtime_ready()

        if youtube_url:
            yt_dir = WORK_DIR / "youtube"
            try:
                media_path, _meta = download_youtube(youtube_url, yt_dir)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Không tải được video YouTube: {exc}",
                ) from exc
            input_path = media_path

        assert input_path is not None
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
        try:
            raise_for_preflight(checks)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        source_id = fingerprint_file(input_path)
        job_dir = WORK_DIR / f"job-{source_id['sha256'][:16]}"
        job_dir.mkdir(parents=True, exist_ok=True)

        output_path = (
            Path(payload.output_path).resolve()
            if payload.output_path
            else (job_dir / f"{input_path.stem}.vi.mp4")
        )

        with _active_threads_lock:
            existing = _active_threads.get(job_dir.name)
            if existing is not None:
                if existing.is_alive():
                    return {"status": "already_running", "job_id": job_dir.name}
                _active_threads.pop(job_dir.name, None)
            if job_lease_is_live(job_dir):
                return {"status": "already_running", "job_id": job_dir.name}

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

    # Mount work directory for video/audio playback and artifacts
    if WORK_DIR.is_dir():
        app.mount("/work", StaticFiles(directory=str(WORK_DIR)), name="work_files")

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
            if full_path.startswith("work/") or full_path == "work":
                raise HTTPException(status_code=404, detail="Work asset not found")
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
