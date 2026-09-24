import json
import shutil
from pathlib import Path

import pytest

from vi_dubber.artifacts import (
    artifact_record,
    atomic_write_json,
    build_stage_manifest,
    fingerprint_data,
    fingerprint_file,
    load_stage_manifest,
    relative_artifact_path,
    stage_fingerprint,
    write_stage_manifest,
)


def test_canonical_hash_is_stable_across_mapping_order() -> None:
    first = {"b": [2, 1], "a": {"x": "Xin chào", "n": 3}}
    second = {"a": {"n": 3, "x": "Xin chào"}, "b": [2, 1]}
    assert fingerprint_data(first) == fingerprint_data(second)


def test_file_fingerprint_is_content_based_not_path_based(tmp_path: Path) -> None:
    first = tmp_path / "one" / "video.mp4"
    second = tmp_path / "two" / "renamed.mp4"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"same video bytes")
    second.write_bytes(b"same video bytes")
    assert fingerprint_file(first) == fingerprint_file(second)

    second.write_bytes(b"changed video bytes")
    assert fingerprint_file(first) != fingerprint_file(second)


def test_stage_fingerprint_changes_only_for_data_the_caller_supplies() -> None:
    base = {
        "stage": "tts",
        "inputs": {
            "source": {"sha256": "a" * 64},
            "text": "Xin chào",
            "speaker": "SPEAKER_00",
            "ref": "ref-hash",
        },
        "upstream": {"translation": "upstream-hash"},
        "config": {"precision": "fp16", "batch_size": 4},
        "model": {"name": "VieNeu", "version": "3.7.1"},
        "prompt": {"policy": "tts-text-v1"},
        "versions": {"vi_dubber": "0.1.0"},
    }

    def make(**overrides):
        data = {**base, **overrides}
        stage = data.pop("stage")
        return stage_fingerprint(stage, **data)

    baseline = make()
    assert make() == baseline
    assert make(inputs={**base["inputs"], "source": {"sha256": "b" * 64}}) != baseline
    assert make(inputs={**base["inputs"], "text": "Xin chào bạn"}) != baseline
    assert make(inputs={**base["inputs"], "ref": "other-ref-hash"}) != baseline
    assert make(model={"name": "VieNeu", "version": "3.8.0"}) != baseline
    assert make(config={"precision": "fp32", "batch_size": 4}) != baseline
    assert make(prompt={"policy": "tts-text-v2"}) != baseline
    assert make(upstream={"translation": "different-upstream"}) != baseline
    assert make(versions={"vi_dubber": "0.2.0"}) != baseline

    full_runtime_config_a = {"tts": base["config"], "ui_theme": "dark"}
    full_runtime_config_b = {"tts": base["config"], "ui_theme": "light"}
    assert full_runtime_config_a != full_runtime_config_b
    assert make(config=full_runtime_config_a["tts"]) == make(config=full_runtime_config_b["tts"])


def test_artifact_records_are_relative_and_reject_outside_job(tmp_path: Path) -> None:
    job = tmp_path / "job"
    artifact = job / "tts" / "00001.wav"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"wave")
    record = artifact_record(job, artifact)
    assert record["path"] == "tts/00001.wav"
    assert relative_artifact_path(job, artifact) == "tts/00001.wav"

    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"wave")
    with pytest.raises(ValueError):
        artifact_record(job, outside)


def test_manifest_round_trip_survives_job_directory_move(tmp_path: Path) -> None:
    original = tmp_path / "original-job"
    output = original / "tts" / "00001.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"rendered audio")
    manifest = build_stage_manifest(
        original,
        "tts",
        inputs={"text": "Xin chào", "source": {"sha256": "a" * 64}},
        config={"precision": "fp16"},
        model={"name": "VieNeu", "version": "3.7.1"},
        artifacts=[output],
        created_at="2026-09-22T00:00:00+00:00",
    )
    manifest_path = original / "manifests" / "tts.json"
    write_stage_manifest(manifest_path, manifest)
    assert load_stage_manifest(manifest_path, job_dir=original) == manifest

    moved = tmp_path / "moved-job"
    shutil.copytree(original, moved)
    moved_manifest = moved / "manifests" / "tts.json"
    loaded = load_stage_manifest(moved_manifest, job_dir=moved)
    assert loaded is not None
    assert loaded["artifacts"][0]["path"] == "tts/00001.wav"
    assert loaded["fingerprint"] == manifest["fingerprint"]


def test_manifest_rejects_missing_changed_partial_and_corrupt_artifacts(tmp_path: Path) -> None:
    job = tmp_path / "job"
    output = job / "segments.json"
    output.parent.mkdir()
    output.write_text('[{"id": 1}]', encoding="utf-8")
    manifest = build_stage_manifest(job, "translation", inputs={"source": "hello"}, artifacts=[output])
    manifest_path = job / "translation.manifest.json"
    write_stage_manifest(manifest_path, manifest)
    assert load_stage_manifest(manifest_path, job_dir=job) is not None

    output.write_text('[{"id": 2}]', encoding="utf-8")
    assert load_stage_manifest(manifest_path, job_dir=job) is None

    output.unlink()
    assert load_stage_manifest(manifest_path, job_dir=job) is None

    manifest_path.write_text('{"version": 1,', encoding="utf-8")
    assert load_stage_manifest(manifest_path, job_dir=job) is None

    manifest_path.write_text("not json", encoding="utf-8")
    assert load_stage_manifest(manifest_path, job_dir=job) is None


def test_manifest_rejects_incomplete_or_tampered_identity(tmp_path: Path) -> None:
    job = tmp_path / "job"
    output = job / "out.json"
    output.parent.mkdir()
    output.write_text("{}", encoding="utf-8")

    incomplete = build_stage_manifest(job, "asr", inputs={"source": "abc"}, artifacts=[output], status="running")
    path = job / "asr.manifest.json"
    write_stage_manifest(path, incomplete)
    assert load_stage_manifest(path, job_dir=job) is None

    complete = build_stage_manifest(job, "asr", inputs={"source": "abc"}, artifacts=[output])
    complete["identity"]["inputs"] = {"source": "tampered"}
    write_stage_manifest(path, complete)
    assert load_stage_manifest(path, job_dir=job) is None


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    job = tmp_path / "job"
    output = job / "out.json"
    output.parent.mkdir()
    output.write_text("{}", encoding="utf-8")
    manifest = build_stage_manifest(job, "asr", inputs={"source": "abc"}, artifacts=[output])
    manifest["artifacts"][0]["path"] = "../outside.json"
    path = job / "asr.manifest.json"
    write_stage_manifest(path, manifest)
    assert load_stage_manifest(path, job_dir=job) is None


def test_atomic_json_write_preserves_previous_file_on_serialization_failure(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    atomic_write_json(path, {"status": "complete", "value": 1})
    before = path.read_text(encoding="utf-8")
    assert json.loads(before)["value"] == 1

    with pytest.raises(ValueError):
        atomic_write_json(path, {"status": "complete", "value": float("nan")})
    assert path.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


def test_expected_stage_and_fingerprint_guard_cache_reuse(tmp_path: Path) -> None:
    job = tmp_path / "job"
    output = job / "out.json"
    output.parent.mkdir()
    output.write_text("{}", encoding="utf-8")
    manifest = build_stage_manifest(job, "asr", inputs={"source": "abc"}, artifacts=[output])
    path = job / "asr.manifest.json"
    write_stage_manifest(path, manifest)

    assert (
        load_stage_manifest(
            path,
            job_dir=job,
            expected_stage="asr",
            expected_fingerprint=manifest["fingerprint"],
        )
        is not None
    )
    assert load_stage_manifest(path, job_dir=job, expected_stage="translation") is None
    assert load_stage_manifest(path, job_dir=job, expected_fingerprint="0" * 64) is None


def test_phase_cache_fingerprints_invalidate_on_relevant_translation_and_tts_inputs() -> None:
    translation = {
        "inputs": {"turns": "turns-v1", "glossary": "glossary-v1"},
        "config": {"provider": "webgpt", "duration_policy": "fit-v1"},
        "model": {"name": "chatgpt-web/high"},
        "prompt": {"prompt_policy": "translate-v2", "context_policy": "context-v1"},
    }
    translation_base = stage_fingerprint("translation", **translation)
    assert stage_fingerprint(
        "translation",
        **{**translation, "inputs": {**translation["inputs"], "glossary": "glossary-v2"}},
    ) != translation_base

    tts = {
        "inputs": {"text": "Xin chào", "speaker": "A", "ref_audio": "ref-v1"},
        "config": {"device": "cuda", "precision": "fp16"},
        "model": {"name": "VieNeu", "version": "3.7.1"},
    }
    tts_base = stage_fingerprint("tts", **tts)
    assert stage_fingerprint(
        "tts", **{**tts, "inputs": {**tts["inputs"], "text": "Xin chào bạn"}}
    ) != tts_base
    assert stage_fingerprint(
        "tts", **{**tts, "inputs": {**tts["inputs"], "ref_audio": "ref-v2"}}
    ) != tts_base
