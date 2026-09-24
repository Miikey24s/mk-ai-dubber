from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


MANIFEST_VERSION = 1
_COMPLETE_STATUS = "complete"


def canonical_json(value: Any) -> str:
    """Serialize JSON data deterministically for cache identity."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint_data(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def fingerprint_file(path: Path, *, chunk_size: int = 1024 * 1024) -> dict[str, Any]:
    """Return a content identity that is independent of filename/location."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            size_bytes += len(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": size_bytes}


def stage_identity(
    stage: str,
    *,
    inputs: Any,
    upstream: Any = None,
    config: Any = None,
    model: Any = None,
    prompt: Any = None,
    versions: Any = None,
) -> dict[str, Any]:
    """Build the exact caller-selected data that controls a stage cache key."""
    if not stage:
        raise ValueError("stage must be non-empty")
    return {
        "manifest_version": MANIFEST_VERSION,
        "stage": stage,
        "inputs": inputs,
        "upstream": {} if upstream is None else upstream,
        "config": {} if config is None else config,
        "model": {} if model is None else model,
        "prompt": {} if prompt is None else prompt,
        "versions": {} if versions is None else versions,
    }


def stage_fingerprint(
    stage: str,
    *,
    inputs: Any,
    upstream: Any = None,
    config: Any = None,
    model: Any = None,
    prompt: Any = None,
    versions: Any = None,
) -> str:
    return fingerprint_data(
        stage_identity(
            stage,
            inputs=inputs,
            upstream=upstream,
            config=config,
            model=model,
            prompt=prompt,
            versions=versions,
        )
    )


def relative_artifact_path(job_dir: Path, artifact_path: Path) -> str:
    root = job_dir.resolve()
    artifact = artifact_path.resolve()
    try:
        relative = artifact.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact must be inside job_dir: {artifact_path}") from exc
    return relative.as_posix()


def resolve_artifact_path(job_dir: Path, stored_path: str) -> Path:
    if not stored_path or "\\" in stored_path:
        raise ValueError("artifact path must be a portable relative POSIX path")
    relative = PurePosixPath(stored_path)
    if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts):
        raise ValueError("artifact path must stay inside job_dir")

    root = job_dir.resolve()
    candidate = (root / Path(*relative.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact path resolves outside job_dir") from exc
    return candidate


def artifact_record(job_dir: Path, artifact_path: Path) -> dict[str, Any]:
    relative_path = relative_artifact_path(job_dir, artifact_path)
    identity = fingerprint_file(artifact_path)
    return {
        "path": relative_path,
        **identity,
    }


def build_stage_manifest(
    job_dir: Path,
    stage: str,
    *,
    inputs: Any,
    artifacts: Iterable[Path],
    upstream: Any = None,
    config: Any = None,
    model: Any = None,
    prompt: Any = None,
    versions: Any = None,
    status: str = _COMPLETE_STATUS,
    created_at: str | None = None,
) -> dict[str, Any]:
    identity = stage_identity(
        stage,
        inputs=inputs,
        upstream=upstream,
        config=config,
        model=model,
        prompt=prompt,
        versions=versions,
    )
    return {
        "version": MANIFEST_VERSION,
        "stage": stage,
        "status": status,
        "fingerprint": fingerprint_data(identity),
        "identity": identity,
        "artifacts": [artifact_record(job_dir, path) for path in artifacts],
        "created_at": created_at or datetime.now(UTC).isoformat(),
    }


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Atomically replace a JSON file after the full payload is serialized and flushed."""
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def write_stage_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    atomic_write_json(path, manifest)


def load_stage_manifest(
    path: Path,
    *,
    job_dir: Path | None = None,
    expected_stage: str | None = None,
    expected_fingerprint: str | None = None,
    verify_artifacts: bool = True,
) -> dict[str, Any] | None:
    """Return a cache-safe complete manifest, otherwise None."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("version") != MANIFEST_VERSION or raw.get("status") != _COMPLETE_STATUS:
        return None

    stage = raw.get("stage")
    identity = raw.get("identity")
    fingerprint = raw.get("fingerprint")
    artifacts = raw.get("artifacts")
    if not isinstance(stage, str) or not stage:
        return None
    if expected_stage is not None and stage != expected_stage:
        return None
    if not isinstance(identity, dict) or identity.get("stage") != stage:
        return None
    if identity.get("manifest_version") != MANIFEST_VERSION:
        return None
    try:
        calculated = fingerprint_data(identity)
    except (TypeError, ValueError):
        return None
    if not isinstance(fingerprint, str) or calculated != fingerprint:
        return None
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        return None
    if not isinstance(artifacts, list):
        return None

    if verify_artifacts:
        if job_dir is None:
            job_dir = path.parent
        for record in artifacts:
            if not _valid_artifact_record(job_dir, record):
                return None
    return raw


def _valid_artifact_record(job_dir: Path, record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    stored_path = record.get("path")
    expected_sha256 = record.get("sha256")
    expected_size = record.get("size_bytes")
    if not isinstance(stored_path, str):
        return False
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        return False
    if not isinstance(expected_size, int) or expected_size < 0:
        return False
    try:
        artifact = resolve_artifact_path(job_dir, stored_path)
        if not artifact.is_file() or artifact.stat().st_size != expected_size:
            return False
        return fingerprint_file(artifact)["sha256"] == expected_sha256
    except (OSError, ValueError):
        return False
