from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .artifacts import (
    build_stage_manifest,
    fingerprint_data,
    load_stage_manifest,
    stage_fingerprint,
    write_stage_manifest,
)
from .longform import MacroChunk


CHUNK_STATE_VERSION = 1
CHUNK_STAGE_ORDER = ("asr", "translation", "tts", "qa", "preview")


def chunk_plan_fingerprint(
    chunks: Iterable[MacroChunk],
    *,
    source_identity: Any,
    context_fingerprint: Any = None,
    policy_version: int = CHUNK_STATE_VERSION,
) -> str:
    ordered = [chunk.to_dict() for chunk in chunks]
    return fingerprint_data(
        {
            "version": int(policy_version),
            "source": source_identity,
            "context": {} if context_fingerprint is None else context_fingerprint,
            "chunks": ordered,
        }
    )


def chunk_stage_manifest_path(job_dir: Path, chunk_id: str, stage: str) -> Path:
    _validate_chunk_id(chunk_id)
    _validate_stage(stage)
    return Path(job_dir) / "chunks" / chunk_id / "manifests" / f"{stage}.json"


def chunk_stage_fingerprint(
    chunk: MacroChunk,
    stage: str,
    *,
    inputs: Any,
    upstream: Any = None,
    config: Any = None,
    model: Any = None,
    prompt: Any = None,
    versions: Any = None,
) -> str:
    _validate_stage(stage)
    return stage_fingerprint(
        f"chunk_{stage}",
        inputs={"chunk": chunk.to_dict(), "payload": inputs},
        upstream=upstream,
        config=config,
        model=model,
        prompt=prompt,
        versions={"chunk_state": CHUNK_STATE_VERSION, **dict(versions or {})},
    )


def commit_chunk_stage(
    job_dir: Path,
    chunk: MacroChunk,
    stage: str,
    *,
    inputs: Any,
    artifacts: Iterable[Path],
    upstream: Any = None,
    config: Any = None,
    model: Any = None,
    prompt: Any = None,
    versions: Any = None,
) -> dict[str, Any]:
    _validate_stage(stage)
    stage_name = f"chunk_{stage}"
    manifest = build_stage_manifest(
        Path(job_dir),
        stage_name,
        inputs={"chunk": chunk.to_dict(), "payload": inputs},
        artifacts=artifacts,
        upstream=upstream,
        config=config,
        model=model,
        prompt=prompt,
        versions={"chunk_state": CHUNK_STATE_VERSION, **dict(versions or {})},
    )
    write_stage_manifest(chunk_stage_manifest_path(job_dir, chunk.chunk_id, stage), manifest)
    return manifest


def load_chunk_stage(
    job_dir: Path,
    chunk: MacroChunk,
    stage: str,
    expected_fingerprint: str,
    *,
    verify_artifacts: bool = True,
) -> dict[str, Any] | None:
    _validate_stage(stage)
    return load_stage_manifest(
        chunk_stage_manifest_path(job_dir, chunk.chunk_id, stage),
        job_dir=Path(job_dir),
        expected_stage=f"chunk_{stage}",
        expected_fingerprint=expected_fingerprint,
        verify_artifacts=verify_artifacts,
    )


def downstream_chunk_stages(stage: str, *, include_self: bool = True) -> tuple[str, ...]:
    _validate_stage(stage)
    index = CHUNK_STAGE_ORDER.index(stage)
    if not include_self:
        index += 1
    return CHUNK_STAGE_ORDER[index:]


def invalidate_chunk_from(job_dir: Path, chunk_id: str, stage: str) -> list[str]:
    """Invalidate one chunk without touching artifacts or state from sibling chunks.

    Stale artifacts are intentionally left on disk. Cache reuse requires a verified
    manifest, so removing manifests is enough for correctness and avoids deleting a
    user-inspectable artifact before a replacement is committed.
    """
    invalidated: list[str] = []
    for downstream in downstream_chunk_stages(stage):
        path = chunk_stage_manifest_path(job_dir, chunk_id, downstream)
        if path.exists():
            path.unlink()
            invalidated.append(downstream)
    return invalidated


def _validate_stage(stage: str) -> None:
    if stage not in CHUNK_STAGE_ORDER:
        raise ValueError(f"unsupported chunk stage: {stage!r}")


def _validate_chunk_id(chunk_id: str) -> None:
    if not chunk_id.startswith("chunk_") or len(chunk_id) != len("chunk_0000"):
        raise ValueError(f"invalid chunk id: {chunk_id!r}")
    suffix = chunk_id.removeprefix("chunk_")
    if not suffix.isdigit() or int(suffix) <= 0:
        raise ValueError(f"invalid chunk id: {chunk_id!r}")
