from pathlib import Path

from vi_dubber.longform import MacroChunk
from vi_dubber.longform_state import (
    CHUNK_STAGE_ORDER,
    chunk_plan_fingerprint,
    chunk_stage_fingerprint,
    chunk_stage_manifest_path,
    commit_chunk_stage,
    downstream_chunk_stages,
    invalidate_chunk_from,
    load_chunk_stage,
)


def _chunk(index: int, start: float, end: float) -> MacroChunk:
    return MacroChunk(
        chunk_id=f"chunk_{index + 1:04d}",
        index=index,
        source_start=start,
        source_end=end,
        context_start=max(0.0, start - 2.0),
        context_end=end + 2.0,
        boundary_reason="safe_cut",
    )


def _artifact(job: Path, chunk: MacroChunk, stage: str, content: str) -> Path:
    path = job / "chunks" / chunk.chunk_id / "artifacts" / f"{stage}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_chunk_plan_fingerprint_tracks_source_context_and_boundaries() -> None:
    chunks = [_chunk(0, 0.0, 100.0), _chunk(1, 100.0, 210.0)]
    baseline = chunk_plan_fingerprint(chunks, source_identity={"sha256": "a" * 64}, context_fingerprint="ctx-a")

    assert chunk_plan_fingerprint(chunks, source_identity={"sha256": "a" * 64}, context_fingerprint="ctx-a") == baseline
    assert chunk_plan_fingerprint(chunks, source_identity={"sha256": "b" * 64}, context_fingerprint="ctx-a") != baseline
    assert chunk_plan_fingerprint(chunks, source_identity={"sha256": "a" * 64}, context_fingerprint="ctx-b") != baseline
    changed = [_chunk(0, 0.0, 90.0), _chunk(1, 90.0, 210.0)]
    assert chunk_plan_fingerprint(changed, source_identity={"sha256": "a" * 64}, context_fingerprint="ctx-a") != baseline


def test_chunk_stage_manifest_is_cache_safe_and_chunk_specific(tmp_path: Path) -> None:
    job = tmp_path / "job"
    first = _chunk(0, 0.0, 100.0)
    second = _chunk(1, 100.0, 210.0)
    first_out = _artifact(job, first, "asr", "first transcript")

    fingerprint = chunk_stage_fingerprint(first, "asr", inputs={"audio": "audio-a"})
    manifest = commit_chunk_stage(
        job,
        first,
        "asr",
        inputs={"audio": "audio-a"},
        artifacts=[first_out],
    )

    assert manifest["fingerprint"] == fingerprint
    assert load_chunk_stage(job, first, "asr", fingerprint) == manifest
    second_fingerprint = chunk_stage_fingerprint(second, "asr", inputs={"audio": "audio-a"})
    assert second_fingerprint != fingerprint
    assert load_chunk_stage(job, second, "asr", second_fingerprint) is None


def test_changed_artifact_fails_closed_on_resume(tmp_path: Path) -> None:
    job = tmp_path / "job"
    chunk = _chunk(0, 0.0, 100.0)
    output = _artifact(job, chunk, "translation", "xin chao")
    fingerprint = chunk_stage_fingerprint(chunk, "translation", inputs={"source": "hello"})
    commit_chunk_stage(job, chunk, "translation", inputs={"source": "hello"}, artifacts=[output])

    assert load_chunk_stage(job, chunk, "translation", fingerprint) is not None
    output.write_text("tampered", encoding="utf-8")
    assert load_chunk_stage(job, chunk, "translation", fingerprint) is None


def test_invalidation_is_dependency_aware_and_scoped_to_one_chunk(tmp_path: Path) -> None:
    job = tmp_path / "job"
    first = _chunk(0, 0.0, 100.0)
    second = _chunk(1, 100.0, 210.0)

    for chunk in (first, second):
        upstream = None
        for stage in CHUNK_STAGE_ORDER:
            output = _artifact(job, chunk, stage, f"{chunk.chunk_id}:{stage}")
            manifest = commit_chunk_stage(
                job,
                chunk,
                stage,
                inputs={"payload": stage},
                artifacts=[output],
                upstream=upstream,
            )
            upstream = manifest["fingerprint"]

    assert invalidate_chunk_from(job, first.chunk_id, "tts") == ["tts", "qa", "preview"]
    assert chunk_stage_manifest_path(job, first.chunk_id, "asr").is_file()
    assert chunk_stage_manifest_path(job, first.chunk_id, "translation").is_file()
    assert not chunk_stage_manifest_path(job, first.chunk_id, "tts").exists()
    assert not chunk_stage_manifest_path(job, first.chunk_id, "qa").exists()
    assert not chunk_stage_manifest_path(job, first.chunk_id, "preview").exists()
    assert all(chunk_stage_manifest_path(job, second.chunk_id, stage).is_file() for stage in CHUNK_STAGE_ORDER)


def test_completed_chunk_survives_failure_scope_of_sibling_chunk(tmp_path: Path) -> None:
    job = tmp_path / "job"
    first = _chunk(0, 0.0, 100.0)
    second = _chunk(1, 100.0, 210.0)
    first_out = _artifact(job, first, "preview", "ready")
    second_out = _artifact(job, second, "asr", "partial")

    first_fp = chunk_stage_fingerprint(first, "preview", inputs={"ready": True})
    commit_chunk_stage(job, first, "preview", inputs={"ready": True}, artifacts=[first_out])
    second_fp = chunk_stage_fingerprint(second, "asr", inputs={"audio": "b"})
    commit_chunk_stage(job, second, "asr", inputs={"audio": "b"}, artifacts=[second_out])

    invalidate_chunk_from(job, second.chunk_id, "asr")

    assert load_chunk_stage(job, first, "preview", first_fp) is not None
    assert load_chunk_stage(job, second, "asr", second_fp) is None


def test_downstream_stage_contract_is_explicit() -> None:
    assert downstream_chunk_stages("translation") == ("translation", "tts", "qa", "preview")
    assert downstream_chunk_stages("translation", include_self=False) == ("tts", "qa", "preview")
