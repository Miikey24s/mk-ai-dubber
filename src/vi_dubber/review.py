from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import load_stage_manifest, resolve_artifact_path
from .types import Segment


REVIEW_HISTORY_VERSION = 1
REVIEW_STATUSES = frozenset({"unreviewed", "needs_review", "reviewed", "accepted"})

_DOWNSTREAM_STAGES = ("tts", "timing_assembly", "mix_mux", "acoustic_qa")
_REQUIRED_DOWNSTREAM_STAGES = frozenset({"tts", "timing_assembly", "mix_mux"})
_EDITABLE_ARTIFACT = "segments_vi.json"


class ReviewDataError(ValueError):
    """Raised when a completed job cannot be reviewed safely."""


def load_review_overrides(job_dir: Path) -> dict[int, dict[str, str]]:
    """Return the latest durable state for segments with a manual content edit."""
    root = Path(job_dir).resolve()
    if not root.is_dir():
        raise ReviewDataError(f"Job directory does not exist: {job_dir}")
    history = _load_history(root)
    latest: dict[int, dict[str, str]] = {}
    content_edited_ids: set[int] = set()
    for receipt in history["edits"]:
        segment_id = receipt.get("segment_id")
        before = receipt.get("before")
        after = receipt.get("after")
        if isinstance(segment_id, bool) or not isinstance(segment_id, int) or segment_id < 0:
            raise ReviewDataError("Invalid segment id in review history")
        if not isinstance(after, dict):
            raise ReviewDataError("Invalid after state in review history")
        text = after.get("text")
        speaker = after.get("speaker")
        review_status = after.get("review_status")
        if not isinstance(text, str) or not text.strip():
            raise ReviewDataError("Invalid text in review history")
        if not isinstance(speaker, str) or not speaker.strip():
            raise ReviewDataError("Invalid speaker in review history")
        if review_status not in REVIEW_STATUSES:
            raise ReviewDataError("Invalid review status in review history")
        latest[segment_id] = {
            "text": text,
            "speaker": speaker,
            "review_status": review_status,
        }
        inferred_content_edit = (
            isinstance(before, dict)
            and (
                before.get("text") != after.get("text")
                or before.get("speaker") != after.get("speaker")
            )
        )
        if receipt.get("kind") == "content_edit" or inferred_content_edit:
            content_edited_ids.add(segment_id)
    return {
        segment_id: latest[segment_id]
        for segment_id in sorted(content_edited_ids)
        if segment_id in latest
    }


def load_review_rows(job_dir: Path) -> list[dict[str, Any]]:
    """Load one normalized review row per final segment from a completed job."""
    root = _completed_job_dir(job_dir)
    translated = _load_segments(root / "segments_translated.json", "translated segments")
    final = _load_segments(root / _EDITABLE_ARTIFACT, "final segments")
    _require_matching_ids(translated, final)
    source = _load_review_source(root, set(final))

    history = _load_history(root)
    tts_path = root / "tts_stats.json"
    pending_override_ids: set[int] = set()
    if tts_path.is_file():
        tts = _load_tts_stats(tts_path, set(final))
    elif history["edits"] and not (root / "manifests" / "tts.json").exists():
        tts = {}
        pending_override_ids = set(load_review_overrides(root))
    else:
        raise ReviewDataError("Missing tts_stats.json for completed job")

    semantic, thresholds = _load_semantic_qa(root)
    if not set(semantic).issubset(final):
        raise ReviewDataError("Semantic QA contains unknown segment ids")
    rows: list[dict[str, Any]] = []
    for segment_id in sorted(final):
        source_item = source[segment_id]
        translated_item = translated[segment_id]
        final_item = final[segment_id]
        tts_item = tts.get(segment_id)
        semantic_item = semantic.get(segment_id)
        speaker_visibility = Segment.from_dict(final_item).speaker_visibility()

        semantic_flag = False
        if semantic_item is not None:
            probabilities = semantic_item.get("probabilities")
            faithful = probabilities.get("faithful") if isinstance(probabilities, dict) else None
            semantic_flag = (
                semantic_item.get("choice") not in (None, "faithful")
                or _below(semantic_item.get("confidence"), thresholds.get("review_confidence_below"))
                or _below(
                    semantic_item.get("critical_facts_probability"),
                    thresholds.get("review_critical_below"),
                )
                or _below(faithful, thresholds.get("review_faithful_below"))
            )

        rewritten = bool(tts_item and int(tts_item.get("rewrites", 0)) > 0)
        overflow = bool(
            tts_item
            and float(tts_item["final_duration"]) > float(tts_item["target_duration"]) * 1.03
        )
        rows.append(
            {
                "id": segment_id,
                "start": final_item["start"],
                "end": final_item["end"],
                "source_en": source_item["text"],
                "translated_vi": translated_item["vi"],
                "selected_vi": final_item["vi"],
                "speaker": final_item["speaker"],
                "review_status": final_item.get("review_status", "unreviewed"),
                "rewritten": rewritten,
                "overflow": overflow,
                "semantic_flag": semantic_flag,
                "speaker_overlap": bool(speaker_visibility["overlap"]),
                "multi_speaker": bool(speaker_visibility["multi_speaker"]),
                "speaker_visibility": speaker_visibility,
                "flagged": bool(
                    rewritten
                    or overflow
                    or semantic_flag
                    or speaker_visibility["needs_review"]
                ),
                "tts": tts_item,
                "semantic_qa": semantic_item,
                "downstream_invalidated": (
                    tts_item is None and segment_id in pending_override_ids
                ),
            }
        )
    return rows


def review_summary(job_dir: Path) -> dict[str, int]:
    """Return compact deterministic counts for the review workspace."""
    rows = load_review_rows(job_dir)
    return {
        "total": len(rows),
        "flagged": sum(bool(row["flagged"]) for row in rows),
        "rewritten": sum(bool(row["rewritten"]) for row in rows),
        "overflow": sum(bool(row["overflow"]) for row in rows),
        "semantic_flag": sum(bool(row["semantic_flag"]) for row in rows),
        "speaker_overlap": sum(bool(row["speaker_overlap"]) for row in rows),
        "multi_speaker": sum(bool(row["multi_speaker"]) for row in rows),
        "reviewed": sum(row["review_status"] in {"reviewed", "accepted"} for row in rows),
        "accepted": sum(row["review_status"] == "accepted" for row in rows),
        "downstream_invalidated": sum(bool(row["downstream_invalidated"]) for row in rows),
    }


def update_segment_review(
    job_dir: Path,
    segment_id: int,
    *,
    text: str | None = None,
    speaker: str | None = None,
    review_status: str | None = None,
) -> dict[str, Any]:
    """Atomically edit one final segment and invalidate only downstream render stages."""
    root = _completed_job_dir(job_dir)
    if isinstance(segment_id, bool) or not isinstance(segment_id, int):
        raise ReviewDataError("segment_id must be an integer")
    if text is None and speaker is None and review_status is None:
        raise ReviewDataError("At least one segment field must be updated")

    normalized_text = _optional_nonempty(text, "text")
    normalized_speaker = _optional_nonempty(speaker, "speaker")
    if review_status is not None and review_status not in REVIEW_STATUSES:
        raise ReviewDataError(f"Unsupported review status: {review_status!r}")

    translated = _load_segments(root / "segments_translated.json", "translated segments")
    final_path = root / _EDITABLE_ARTIFACT
    final = _load_segments(final_path, "final segments")
    _require_matching_ids(translated, final)
    source = _load_review_source(root, set(final))
    if segment_id not in final:
        raise ReviewDataError(f"Unknown segment id: {segment_id}")

    history = _load_history(root)

    updated = {key: dict(value) for key, value in final.items()}
    target = updated[segment_id]
    previous = {
        "text": target["vi"],
        "speaker": target["speaker"],
        "review_status": target.get("review_status", "unreviewed"),
    }
    if normalized_text is not None:
        target["vi"] = normalized_text
    if normalized_speaker is not None:
        target["speaker"] = normalized_speaker
    if review_status is not None:
        target["review_status"] = review_status

    next_state = {
        "text": target["vi"],
        "speaker": target["speaker"],
        "review_status": target.get("review_status", "unreviewed"),
    }
    if next_state == previous:
        raise ReviewDataError("Review update does not change the selected segment")

    content_changed = (
        next_state["text"] != previous["text"]
        or next_state["speaker"] != previous["speaker"]
    )
    invalidation = (
        _load_invalidation_plan(root, allow_missing=bool(history["edits"]))
        if content_changed
        else []
    )

    timestamp = datetime.now(UTC).isoformat()
    receipt = {
        "version": REVIEW_HISTORY_VERSION,
        "kind": "content_edit" if content_changed else "status_update",
        "edit_id": uuid.uuid4().hex,
        "edited_at": timestamp,
        "segment_id": segment_id,
        "before": previous,
        "after": next_state,
        "invalidated_stages": [item["stage"] for item in invalidation],
        "invalidated_artifacts": sorted(
            {
                relative
                for item in invalidation
                for relative in item["artifact_paths"]
                if relative != _EDITABLE_ARTIFACT
            }
        ),
    }

    ordered_segments = [updated[key] for key in final]
    _atomic_write_json(final_path, ordered_segments)

    receipt_path = root / "review_receipts" / f"{receipt['edit_id']}.json"
    _atomic_write_json(receipt_path, receipt)
    history["edits"].append(receipt)
    _atomic_write_json(root / "review_history.json", history)

    if content_changed:
        _invalidate_downstream(root, invalidation)
    return receipt


def _completed_job_dir(job_dir: Path) -> Path:
    root = Path(job_dir).resolve()
    if not root.is_dir():
        raise ReviewDataError(f"Job directory does not exist: {job_dir}")

    job = _read_json(root / "job.json", "job metadata")
    if not isinstance(job, dict) or job.get("version") != 1:
        raise ReviewDataError("Invalid job.json schema")
    source = job.get("source")
    if not isinstance(source, dict):
        raise ReviewDataError("Invalid job source identity")
    sha256 = source.get("sha256")
    size_bytes = source.get("size_bytes")
    if not isinstance(sha256, str) or len(sha256) != 64:
        raise ReviewDataError("Invalid job source hash")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise ReviewDataError("Invalid job source size")

    metrics = _read_json(root / "metrics.json", "job metrics")
    if not isinstance(metrics, dict) or metrics.get("status") != "complete":
        raise ReviewDataError("Review is only available for completed jobs")
    return root


def _load_segments(path: Path, label: str) -> dict[int, dict[str, Any]]:
    raw = _read_json(path, label)
    if not isinstance(raw, list):
        raise ReviewDataError(f"Invalid {label} schema: expected a JSON array")

    by_id: dict[int, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ReviewDataError(f"Invalid {label} schema: segment must be an object")
        segment_id = item.get("id")
        if isinstance(segment_id, bool) or not isinstance(segment_id, int) or segment_id < 0:
            raise ReviewDataError(f"Invalid {label} segment id")
        if segment_id in by_id:
            raise ReviewDataError(f"Duplicate {label} segment id: {segment_id}")
        start = item.get("start")
        end = item.get("end")
        if not _number(start) or not _number(end) or float(end) < float(start):
            raise ReviewDataError(f"Invalid {label} timing for segment {segment_id}")
        if not isinstance(item.get("text"), str):
            raise ReviewDataError(f"Invalid {label} source text for segment {segment_id}")
        if not isinstance(item.get("vi"), str):
            raise ReviewDataError(f"Invalid {label} Vietnamese text for segment {segment_id}")
        if not isinstance(item.get("speaker"), str) or not item["speaker"].strip():
            raise ReviewDataError(f"Invalid {label} speaker for segment {segment_id}")
        review_status = item.get("review_status")
        if review_status is not None and review_status not in REVIEW_STATUSES:
            raise ReviewDataError(f"Invalid review status for segment {segment_id}")
        by_id[segment_id] = dict(item)
    return by_id


def _load_review_source(job_dir: Path, expected_ids: set[int]) -> dict[int, dict[str, Any]]:
    turns_path = job_dir / "segments_turns.json"
    if turns_path.is_file():
        turns = _load_segments(turns_path, "speech turn segments")
        if set(turns) == expected_ids:
            return turns

    source = _load_segments(job_dir / "segments_source.json", "source segments")
    if set(source) == expected_ids:
        return source

    turn_count = len(turns) if turns_path.is_file() else 0
    raise ReviewDataError(
        "Review source segment ids do not align with translated/final segments "
        f"(expected={len(expected_ids)}, turns={turn_count}, source={len(source)})"
    )


def _require_matching_ids(*collections: dict[int, dict[str, Any]]) -> None:
    if not collections:
        return
    expected = set(collections[0])
    if any(set(collection) != expected for collection in collections[1:]):
        raise ReviewDataError("Segment artifacts do not contain the same segment ids")


def _load_tts_stats(path: Path, expected_ids: set[int]) -> dict[int, dict[str, Any]]:
    raw = _read_json(path, "TTS stats")
    if not isinstance(raw, list):
        raise ReviewDataError("Invalid tts_stats.json schema: expected a JSON array")
    stats: dict[int, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ReviewDataError("Invalid TTS stat row")
        segment_id = item.get("segment_id")
        if isinstance(segment_id, bool) or not isinstance(segment_id, int) or segment_id in stats:
            raise ReviewDataError("Invalid or duplicate TTS segment id")
        for field in ("target_duration", "generated_duration", "final_duration", "tempo"):
            if not _number(item.get(field)):
                raise ReviewDataError(f"Invalid TTS {field} for segment {segment_id}")
        rewrites = item.get("rewrites")
        if isinstance(rewrites, bool) or not isinstance(rewrites, int) or rewrites < 0:
            raise ReviewDataError(f"Invalid TTS rewrites for segment {segment_id}")
        stats[segment_id] = dict(item)
    if set(stats) != expected_ids:
        raise ReviewDataError("TTS stats do not match final segment ids")
    return stats


def _load_semantic_qa(job_dir: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    index = _read_json(job_dir / "semantic_qa.json", "semantic QA index")
    if (
        not isinstance(index, dict)
        or index.get("version") not in {2, 3}
        or index.get("kind") != "semantic_qa_index"
    ):
        raise ReviewDataError("Invalid semantic_qa.json schema")
    stages = index.get("stages")
    translated = stages.get("translated") if isinstance(stages, dict) else None
    if not isinstance(translated, dict):
        raise ReviewDataError("Missing translated semantic QA stage")
    thresholds = translated.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ReviewDataError("Invalid semantic QA thresholds")
    if translated.get("status") == "not_applicable":
        return {}, dict(thresholds)
    if translated.get("status") != "ok":
        raise ReviewDataError("Translated semantic QA is not complete")

    stored_path = translated.get("artifact")
    if not isinstance(stored_path, str):
        raise ReviewDataError("Semantic QA artifact path is missing")
    try:
        artifact_path = resolve_artifact_path(job_dir, stored_path)
    except ValueError as exc:
        raise ReviewDataError("Semantic QA artifact path is unsafe") from exc
    stage = _read_json(artifact_path, "translated semantic QA")
    if not isinstance(stage, dict) or stage.get("kind") != "semantic_qa_stage":
        raise ReviewDataError("Invalid translated semantic QA schema")
    raw_result = stage.get("raw_result")
    items = raw_result.get("items") if isinstance(raw_result, dict) else None
    if not isinstance(items, list):
        raise ReviewDataError("Invalid translated semantic QA items")

    by_id: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ReviewDataError("Invalid semantic QA item")
        segment_id = item.get("id")
        if isinstance(segment_id, bool) or not isinstance(segment_id, int) or segment_id in by_id:
            raise ReviewDataError("Invalid or duplicate semantic QA segment id")
        by_id[segment_id] = dict(item)
    return by_id, dict(thresholds)


def _load_history(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "review_history.json"
    if not path.exists():
        return {"version": REVIEW_HISTORY_VERSION, "edits": []}
    raw = _read_json(path, "review history")
    if not isinstance(raw, dict) or raw.get("version") != REVIEW_HISTORY_VERSION:
        raise ReviewDataError("Invalid review history schema")
    edits = raw.get("edits")
    if not isinstance(edits, list) or any(not isinstance(item, dict) for item in edits):
        raise ReviewDataError("Invalid review history edits")
    return {"version": REVIEW_HISTORY_VERSION, "edits": list(edits)}


def _load_invalidation_plan(job_dir: Path, *, allow_missing: bool) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for stage in _DOWNSTREAM_STAGES:
        manifest_path = job_dir / "manifests" / f"{stage}.json"
        if not manifest_path.exists():
            if not allow_missing and stage in _REQUIRED_DOWNSTREAM_STAGES:
                raise ReviewDataError(f"Missing downstream manifest: {stage}")
            continue
        manifest = load_stage_manifest(
            manifest_path,
            job_dir=job_dir,
            expected_stage=stage,
            verify_artifacts=True,
        )
        if manifest is None:
            raise ReviewDataError(f"Invalid downstream manifest or artifact: {stage}")
        artifact_paths: list[str] = []
        for record in manifest["artifacts"]:
            stored_path = record.get("path")
            if not isinstance(stored_path, str):
                raise ReviewDataError(f"Invalid artifact path in {stage} manifest")
            try:
                resolve_artifact_path(job_dir, stored_path)
            except ValueError as exc:
                raise ReviewDataError(f"Unsafe artifact path in {stage} manifest") from exc
            artifact_paths.append(stored_path)
        plan.append({"stage": stage, "manifest": manifest_path, "artifact_paths": artifact_paths})
    return plan


def _invalidate_downstream(job_dir: Path, plan: list[dict[str, Any]]) -> None:
    for item in plan:
        Path(item["manifest"]).unlink(missing_ok=True)

    for item in plan:
        for stored_path in item["artifact_paths"]:
            if stored_path == _EDITABLE_ARTIFACT:
                continue
            artifact = resolve_artifact_path(job_dir, stored_path)
            if artifact.is_dir():
                shutil.rmtree(artifact)
            else:
                artifact.unlink(missing_ok=True)


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReviewDataError(f"Missing {label}: {path.name}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewDataError(f"Corrupt {label}: {path.name}") from exc


def _atomic_write_json(path: Path, data: Any) -> None:
    try:
        text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ReviewDataError(f"Cannot serialize review data for {path.name}") from exc
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


def _optional_nonempty(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReviewDataError(f"{label} must be a non-empty string")
    return value.strip()


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _below(value: Any, threshold: Any) -> bool:
    return _number(value) and _number(threshold) and float(value) < float(threshold)
