"""Build the M5 catalog projection from existing VI job/manifests.

This adapter is intentionally read-only with respect to job directories.  It
normalizes the existing ``job.json``, ``state.json`` and stage manifests into
the local :mod:`catalog_store` boundary without copying blobs or persisting
absolute source paths.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .artifacts import fingerprint_data, fingerprint_file
from .catalog_store import CatalogError, CatalogItem, CatalogStore
from .jobs import load_json


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ProjectionIssue:
    job_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    indexed: int
    skipped: tuple[ProjectionIssue, ...]
    rebuild_applied: bool = True
    preserved_existing: bool = False
    reason: str = "rebuilt"


def _safe_source_alias(job_id: str, source_name: str) -> str:
    name = Path(source_name).name.strip() or "source.bin"
    # Keep the alias portable while retaining enough of the original name for
    # a human relink mapping.  The content hash remains the source identity.
    name = "".join(char if char.isalnum() or char in {".", "-", "_"} else "_" for char in name)
    return PurePosixPath("jobs", job_id, "source", name).as_posix()


def _relative_source_ref(candidate: Path | None, source_root: Path | None, job_id: str, source_name: str) -> str:
    if candidate is not None and source_root is not None:
        try:
            return candidate.resolve().relative_to(source_root.resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return _safe_source_alias(job_id, source_name)


def _source_candidate(job_info: dict[str, Any], state: dict[str, Any], job_dir: Path) -> Path | None:
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    raw = job_info.get("source_path") or metadata.get("input_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = job_dir / candidate
    return candidate.resolve(strict=False)


def _source_availability(candidate: Path | None, source_sha: str, source_size: int | None) -> str:
    if candidate is None:
        return "unknown"
    try:
        if not candidate.is_file():
            return "missing"
        identity = fingerprint_file(candidate)
    except OSError:
        return "failed"
    if identity.get("sha256") != source_sha:
        return "stale"
    if source_size is not None and identity.get("size_bytes") != source_size:
        return "stale"
    return "available"


def _manifest_projection(job_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    manifests_dir = job_dir / "manifests"
    if not manifests_dir.is_dir():
        return records
    for path in sorted(manifests_dir.glob("*.json")):
        payload = load_json(path)
        stage = payload.get("stage")
        fingerprint = payload.get("fingerprint")
        status = payload.get("status")
        if not isinstance(stage, str) or not stage.strip() or not isinstance(fingerprint, str) or not fingerprint.strip():
            continue
        records.append(
            {
                "stage": stage.strip(),
                "fingerprint": fingerprint.strip(),
                "status": str(status or "unknown"),
                "artifact_count": len(payload.get("artifacts") or []) if isinstance(payload.get("artifacts"), list) else 0,
            }
        )
    return records


def _segment_count(job_dir: Path) -> int:
    """Count persisted segments without loading transcript text into catalog metadata."""
    candidates = [job_dir / "segments_source.json"]
    candidates.extend(sorted(job_dir.glob("chunks/*/segments_source.json")))
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(payload, list):
            return len(payload)
    return 0


def catalog_item_from_job(job_dir: Path, *, source_root: Path | None = None) -> CatalogItem | None:
    """Project one content-addressed job directory into a catalog item.

    Invalid/incomplete jobs return ``None`` so a corrupt directory cannot poison
    the catalog projection.  The caller receives a reason via
    :func:`rebuild_from_work_dir`.
    """
    job_dir = Path(job_dir)
    job_id = job_dir.name
    if not job_id.startswith("job-"):
        return None
    job_info = load_json(job_dir / "job.json")
    state = load_json(job_dir / "state.json")
    source = job_info.get("source") if isinstance(job_info.get("source"), dict) else {}
    source_sha = str(source.get("sha256") or "").lower()
    source_size = source.get("size_bytes")
    if not _SHA256_RE.fullmatch(source_sha):
        return None
    if isinstance(source_size, bool) or not isinstance(source_size, int) or source_size < 0:
        source_size = None
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    source_name = str(job_info.get("source_name") or metadata.get("input_name") or job_id).strip() or job_id
    candidate = _source_candidate(job_info, state, job_dir)
    stage_records = _manifest_projection(job_dir)
    translation = job_info.get("translation") if isinstance(job_info.get("translation"), dict) else {}
    safe_state = {
        "status": str(state.get("status") or "unknown"),
        "stage": str(state.get("stage") or "unknown"),
        "progress": float(state.get("progress") or 0.0),
    }
    lineage = {
        "job_version": job_info.get("version"),
        "source": {"sha256": source_sha, "size_bytes": source_size},
        "state": safe_state,
        "translation": {
            "provider": str(translation.get("provider") or ""),
            "model_id": str(translation.get("model_id") or ""),
            "effort": str(translation.get("effort") or ""),
            "catalog_revision": str(translation.get("catalog_revision") or ""),
        },
        "stages": stage_records,
    }
    revision = fingerprint_data(lineage)
    availability = _source_availability(candidate, source_sha, source_size)
    source_ref = _relative_source_ref(candidate, source_root, job_id, source_name)
    item_metadata = {
        "source_size_bytes": source_size,
        "source_name": source_name,
        "job_status": safe_state["status"],
        "job_stage": safe_state["stage"],
        "progress": safe_state["progress"],
        "stages": stage_records,
        "lineage_fingerprint": revision,
    }
    return CatalogItem(
        item_id=job_id,
        title=source_name,
        source_fingerprint=source_sha,
        revision=revision,
        availability=availability,
        segment_count=_segment_count(job_dir),
        source_ref=source_ref,
        metadata=item_metadata,
    )


def rebuild_from_work_dir(
    store: CatalogStore,
    work_dir: Path,
    *,
    source_root: Path | None = None,
) -> ProjectionReport:
    """Atomically rebuild a catalog projection from local job directories.

    A missing/unreadable work root or an all-invalid job set is treated as an
    unavailable projection source.  In those cases the existing catalog and
    its review state are preserved instead of interpreting a transient mount
    or corruption as an intentional empty catalog.
    """
    root = Path(work_dir)
    if not root.is_dir():
        return ProjectionReport(
            indexed=0,
            skipped=(ProjectionIssue(root.name or ".", "work_dir_unavailable"),),
            rebuild_applied=False,
            preserved_existing=True,
            reason="work_dir_unavailable",
        )
    items: list[CatalogItem] = []
    skipped: list[ProjectionIssue] = []
    try:
        job_dirs = sorted(root.glob("job-*"), key=lambda path: path.name)
    except OSError:
        return ProjectionReport(
            indexed=0,
            skipped=(ProjectionIssue(root.name or ".", "work_dir_unavailable"),),
            rebuild_applied=False,
            preserved_existing=True,
            reason="work_dir_unavailable",
        )
    for job_dir in job_dirs:
        try:
            item = catalog_item_from_job(job_dir, source_root=source_root)
        except (CatalogError, OSError, TypeError, ValueError) as exc:
            skipped.append(ProjectionIssue(job_dir.name, f"projection validation failed: {type(exc).__name__}"))
            continue
        if item is None:
            skipped.append(ProjectionIssue(job_dir.name, "missing or invalid job source identity"))
            continue
        items.append(item)
    if not job_dirs:
        return ProjectionReport(
            indexed=0,
            skipped=(),
            rebuild_applied=False,
            preserved_existing=True,
            reason="empty_work_dir",
        )
    if not items:
        return ProjectionReport(
            indexed=0,
            skipped=tuple(skipped),
            rebuild_applied=False,
            preserved_existing=True,
            reason="all_jobs_invalid",
        )
    preserved_invalid_ids: set[str] = set()
    if skipped:
        # Keep an already-known item's review/bookmark row when one job is
        # temporarily unreadable while its siblings are still valid.  The
        # item is marked unknown so the UI cannot present the old source as
        # verified; its unchanged lineage revision lets the user annotations
        # survive the recovery rebuild.
        try:
            existing = {issue.job_id: store.get_item(issue.job_id) for issue in skipped}
        except (CatalogError, OSError, TypeError, ValueError):
            return ProjectionReport(
                indexed=0,
                skipped=tuple(skipped),
                rebuild_applied=False,
                preserved_existing=True,
                reason="existing_projection_unavailable",
            )
        current_ids = {item.item_id for item in items}
        for issue in skipped:
            old = existing.get(issue.job_id)
            if old is None or old.item_id in current_ids:
                continue
            items.append(
                CatalogItem(
                    item_id=old.item_id,
                    title=old.title,
                    source_fingerprint=old.source_fingerprint,
                    revision=old.revision,
                    availability="unknown",
                    segment_count=old.segment_count,
                    source_ref=old.source_ref,
                    metadata={**dict(old.metadata), "projection_warning": issue.reason},
                )
            )
            preserved_invalid_ids.add(old.item_id)
        items.sort(key=lambda item: item.item_id)
    store.rebuild(items)
    return ProjectionReport(
        indexed=len(items),
        skipped=tuple(skipped),
        preserved_existing=bool(preserved_invalid_ids),
        reason="rebuilt_with_preserved_invalid" if preserved_invalid_ids else "rebuilt",
    )

