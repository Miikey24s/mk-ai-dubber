import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vi_dubber.cli as cli
import vi_dubber.metrics as metrics_module
import vi_dubber.pipeline as pipeline
from vi_dubber.artifacts import fingerprint_file
from vi_dubber.jobs import PipelineCancelled, load_job_state
from vi_dubber.types import Segment


def test_job_dir_is_content_addressed_not_filename_or_size(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "WORK_DIR", tmp_path / "work")
    first = tmp_path / "a" / "sample.mp4"
    second = tmp_path / "b" / "sample.mp4"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"AAAA")
    second.write_bytes(b"BBBB")

    first_dir = pipeline._job_dir(first, fingerprint_file(first))
    second_dir = pipeline._job_dir(second, fingerprint_file(second))

    assert first_dir.name.startswith("job-")
    assert second_dir.name.startswith("job-")
    assert first_dir != second_dir


def test_same_content_maps_to_same_job_even_after_move_or_rename(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "WORK_DIR", tmp_path / "work")
    first = tmp_path / "one" / "clip.mp4"
    moved = tmp_path / "two" / "renamed.mp4"
    first.parent.mkdir()
    moved.parent.mkdir()
    first.write_bytes(b"same-content")
    moved.write_bytes(b"same-content")

    assert pipeline._job_dir(first, fingerprint_file(first)) == pipeline._job_dir(
        moved, fingerprint_file(moved)
    )


def test_stems_v2_are_relative_and_survive_job_move(tmp_path: Path) -> None:
    original = tmp_path / "job-a"
    stems = original / "stems"
    stems.mkdir(parents=True)
    (stems / "vocals.wav").write_bytes(b"voice")
    (stems / "background.wav").write_bytes(b"music")
    meta = original / "stems.json"
    meta.write_text(
        '{"version":2,"vocals":"stems/vocals.wav","background":"stems/background.wav"}',
        encoding="utf-8",
    )

    vocals, background = pipeline._load_stems(original, meta)
    assert vocals == (stems / "vocals.wav").resolve()
    assert background == (stems / "background.wav").resolve()

    moved = tmp_path / "job-b"
    original.rename(moved)
    vocals, background = pipeline._load_stems(moved, moved / "stems.json")
    assert vocals == (moved / "stems" / "vocals.wav").resolve()
    assert background == (moved / "stems" / "background.wav").resolve()


def test_legacy_stems_outside_job_are_rejected(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    outside = tmp_path / "legacy"
    outside.mkdir()
    vocals = outside / "vocals.wav"
    background = outside / "background.wav"
    vocals.write_bytes(b"voice")
    background.write_bytes(b"music")
    meta = job / "stems.json"
    meta.write_text(
        json.dumps({"vocals": str(vocals), "background": str(background)}),
        encoding="utf-8",
    )

    assert pipeline._load_stems(job, meta) == (None, None)


def test_legacy_resolved_config_recovers_original_work_config_base(tmp_path: Path) -> None:
    project = tmp_path / "project"
    work = project / "work"
    job = work / "job-abc"
    job.mkdir(parents=True)
    glossary = project / "glossary.yaml"
    glossary.write_text("FVG: ep vi gi\n", encoding="utf-8")
    snapshot = job / "resolved_config.json"
    config = {"translation": {"glossary": "../glossary.yaml"}}

    resolved = pipeline._resolve_config_path(config, snapshot, "../glossary.yaml")

    assert pipeline._config_origin_base(config, snapshot) == work.resolve()
    assert resolved == glossary.resolve()


def test_resolved_config_prefers_persisted_config_origin(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config_dir = project / "configs"
    job = project / "work" / "job-abc"
    config_dir.mkdir(parents=True)
    job.mkdir(parents=True)
    glossary = config_dir / "glossary.yaml"
    glossary.write_text("FVG: ep vi gi\n", encoding="utf-8")
    snapshot = job / "resolved_config.json"
    config = {
        "_config_origin": {"base_dir": str(config_dir)},
        "translation": {"glossary": "glossary.yaml"},
    }

    resolved = pipeline._resolve_config_path(config, snapshot, "glossary.yaml")

    assert pipeline._config_origin_base(config, snapshot) == config_dir.resolve()
    assert resolved == glossary.resolve()


def test_pipeline_failure_persists_stage_metrics_and_cold_resume_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(pipeline, "configure_runtime", lambda: None)
    monkeypatch.setattr(
        metrics_module,
        "cuda_memory_snapshot",
        lambda *, enabled=True: {"available": False, "reason": "test"},
    )

    def fail_duration(_path: Path) -> float:
        raise RuntimeError("fixture duration failure")

    monkeypatch.setattr(pipeline, "media_duration", fail_duration)
    input_path = tmp_path / "input.mp4"
    config_path = tmp_path / "config.yaml"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"fixture-video")
    config_path.write_text(_MINIMAL_VALID_CONFIG, encoding="utf-8")

    source_identity = fingerprint_file(input_path)
    job_dir = pipeline._job_dir(input_path, source_identity)

    for expected_kind in ("cold", "resume"):
        with pytest.raises(RuntimeError, match="fixture duration failure"):
            pipeline.run_pipeline(input_path, output_path, config_path, resume=True)

        metrics = json.loads((job_dir / "metrics.json").read_text(encoding="utf-8"))
        stage = metrics["stages"]["input_extract"]
        assert metrics["status"] == "failed"
        assert metrics["run"]["kind"] == expected_kind
        assert stage["calls"] == 1
        assert stage["failed_calls"] == 1
        assert stage["wall_seconds"] >= 0.0


def test_aurora_selection_is_snapshotted_and_resume_rejects_model_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(pipeline, "configure_runtime", lambda: None)
    monkeypatch.setattr(
        metrics_module,
        "cuda_memory_snapshot",
        lambda *, enabled=True: {"available": False, "reason": "test"},
    )
    monkeypatch.setattr(
        pipeline,
        "media_duration",
        lambda _path: (_ for _ in ()).throw(RuntimeError("stop after snapshot")),
    )
    input_path = tmp_path / "input.mp4"
    config_path = tmp_path / "config.yaml"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"fixture-video")
    config_path.write_text(_MINIMAL_VALID_CONFIG, encoding="utf-8")

    with pytest.raises(RuntimeError, match="stop after snapshot"):
        pipeline.run_pipeline(
            input_path,
            output_path,
            config_path,
            translation_provider="aurora",
            translation_model="gpt-fixture-sol",
            translation_effort="high",
            translation_display_name="GPT Fixture Sol",
            translation_catalog_revision="catalog-r1",
            translation_catalog_timestamp="2026-09-23T09:00:00Z",
            resume=True,
        )

    job_dir = pipeline._job_dir(input_path, fingerprint_file(input_path))
    state = load_job_state(job_dir)
    assert state["metadata"]["translation_provider"] == "aurora"
    assert state["metadata"]["translation_model"] == "gpt-fixture-sol"
    assert state["metadata"]["translation_model_display_name"] == "GPT Fixture Sol"
    assert state["metadata"]["translation_effort"] == "high"
    assert state["metadata"]["translation_catalog_revision"] == "catalog-r1"
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["translation"] == {
        "provider": "aurora",
        "model_id": "gpt-fixture-sol",
        "display_name": "GPT Fixture Sol",
        "effort": "high",
        "catalog_revision": "catalog-r1",
        "catalog_timestamp": "2026-09-23T09:00:00Z",
    }
    resolved = json.loads((job_dir / "resolved_config.json").read_text(encoding="utf-8"))
    assert resolved["_translation_selection"] == job["translation"]

    with pytest.raises(ValueError, match="--fresh"):
        pipeline.run_pipeline(
            input_path,
            output_path,
            config_path,
            translation_provider="aurora",
            translation_model="different-model",
            translation_effort="high",
            resume=True,
        )

    with pytest.raises(RuntimeError, match="stop after snapshot"):
        pipeline.run_pipeline(
            input_path,
            output_path,
            config_path,
            translation_provider="aurora",
            resume=True,
        )
    assert load_job_state(job_dir)["metadata"]["translation_model"] == "gpt-fixture-sol"


def test_webgpt_cli_path_snapshots_live_catalog_when_not_supplied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(pipeline, "configure_runtime", lambda: None)
    monkeypatch.setattr(
        metrics_module,
        "cuda_memory_snapshot",
        lambda *, enabled=True: {"available": False, "reason": "test"},
    )
    monkeypatch.setattr(
        pipeline,
        "webgpt_model_catalog",
        lambda _config=None: {
            "models": [
                {
                    "id": "chatgpt-web/gpt-5.6-sol",
                    "display_name": "GPT-5.6 Sol (Web)",
                    "supported_efforts": ["medium", "high"],
                    "default_effort": "high",
                }
            ],
            "default_effort": "high",
            "revision": "sha256:fixture-catalog",
            "updated_at": "",
        },
    )
    monkeypatch.setattr(
        pipeline,
        "media_duration",
        lambda _path: (_ for _ in ()).throw(RuntimeError("stop after snapshot")),
    )
    input_path = tmp_path / "input.mp4"
    config_path = tmp_path / "config.yaml"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"fixture-video")
    config_path.write_text(_MINIMAL_VALID_CONFIG, encoding="utf-8")

    with pytest.raises(RuntimeError, match="stop after snapshot"):
        pipeline.run_pipeline(
            input_path,
            output_path,
            config_path,
            translation_provider="webgpt",
            translation_model="chatgpt-web/gpt-5.6-sol",
            resume=False,
        )

    job_dir = pipeline._job_dir(input_path, fingerprint_file(input_path))
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert job["translation"] == {
        "provider": "webgpt",
        "model_id": "chatgpt-web/gpt-5.6-sol",
        "display_name": "GPT-5.6 Sol (Web)",
        "effort": "high",
        "catalog_revision": "sha256:fixture-catalog",
        "catalog_timestamp": "",
    }


def test_fresh_existing_job_is_recorded_as_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(pipeline, "configure_runtime", lambda: None)
    monkeypatch.setattr(
        metrics_module,
        "cuda_memory_snapshot",
        lambda *, enabled=True: {"available": False, "reason": "test"},
    )
    monkeypatch.setattr(
        pipeline,
        "media_duration",
        lambda _path: (_ for _ in ()).throw(RuntimeError("fixture duration failure")),
    )
    input_path = tmp_path / "input.mp4"
    config_path = tmp_path / "config.yaml"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"fixture-video")
    config_path.write_text(_MINIMAL_VALID_CONFIG, encoding="utf-8")
    source_identity = fingerprint_file(input_path)
    job_dir = pipeline._job_dir(input_path, source_identity)

    with pytest.raises(RuntimeError, match="fixture duration failure"):
        pipeline.run_pipeline(input_path, output_path, config_path, resume=True)
    with pytest.raises(RuntimeError, match="fixture duration failure"):
        pipeline.run_pipeline(input_path, output_path, config_path, resume=False)

    metrics = json.loads((job_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "failed"
    assert metrics["run"]["kind"] == "warm"


def test_outer_failure_overwrites_stale_complete_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "WORK_DIR", tmp_path / "work")
    input_path = tmp_path / "input.mp4"
    config_path = tmp_path / "config.yaml"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"fixture-video")
    config_path.write_text("{}\n", encoding="utf-8")
    source_identity = fingerprint_file(input_path)
    job_dir = pipeline._job_dir(input_path, source_identity)
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text("{}", encoding="utf-8")
    metrics_path = job_dir / "metrics.json"
    metrics_path.write_text('{"schema_version":1,"status":"complete"}', encoding="utf-8")

    def fail_before_metrics(*args, **kwargs):
        raise RuntimeError("outside stage failure")

    monkeypatch.setattr(pipeline, "_run_pipeline_impl", fail_before_metrics)

    with pytest.raises(RuntimeError, match="outside stage failure"):
        pipeline.run_pipeline(input_path, output_path, config_path, resume=True)

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["status"] == "failed"
    assert metrics["run"]["kind"] == "resume"
    assert metrics["error"]["type"] == "RuntimeError"


def test_cancelled_pipeline_persists_cancelled_state_without_committing_partial_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "WORK_DIR", tmp_path / "work")
    input_path = tmp_path / "input.mp4"
    config_path = tmp_path / "config.yaml"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"fixture-video")
    config_path.write_text("{}\n", encoding="utf-8")
    identity = fingerprint_file(input_path)
    job_dir = pipeline._job_dir(input_path, identity)

    def cancel_mid_stage(*args, **kwargs):
        del args, kwargs
        partial = job_dir / "tts" / "00001_raw.partial.wav"
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(b"partial-audio")
        raise PipelineCancelled("Tác vụ đã bị hủy theo yêu cầu.")

    monkeypatch.setattr(pipeline, "_run_pipeline_impl", cancel_mid_stage)

    with pytest.raises(PipelineCancelled, match="đã bị hủy"):
        pipeline.run_pipeline(input_path, output_path, config_path, resume=True)

    state = load_job_state(job_dir)
    assert state["status"] == "cancelled"
    assert state["error"]["type"] == "PipelineCancelled"
    assert not (job_dir / "result.json").exists()
    assert not (job_dir / "manifests" / "tts.json").exists()
    assert not (job_dir / "run.lock").exists()


def test_pipeline_stage_cache_requires_valid_manifest_and_resume(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    artifact = job_dir / "segments_translated.json"
    artifact.write_text('[{"id":1,"vi":"xin chao"}]', encoding="utf-8")
    inputs = {"source_segments": "source-v1", "glossary": "glossary-v1"}
    config = {"provider": "webgpt"}
    model = {"webgpt": "chatgpt-web/high"}
    prompt = {"policy": "translation-v1"}
    versions = {"policy": 1}
    fingerprint = pipeline.stage_fingerprint(
        "translation",
        inputs=inputs,
        config=config,
        model=model,
        prompt=prompt,
        versions=versions,
    )
    pipeline._commit_stage(
        job_dir,
        "translation",
        inputs=inputs,
        artifacts=[artifact],
        config=config,
        model=model,
        prompt=prompt,
        versions=versions,
    )

    assert pipeline._stage_cache_hit(job_dir, "translation", fingerprint, resume=True) is not None
    assert pipeline._stage_cache_hit(job_dir, "translation", fingerprint, resume=False) is None

    artifact.write_text('[{"id":1,"vi":"da thay doi"}]', encoding="utf-8")
    assert pipeline._stage_cache_hit(job_dir, "translation", fingerprint, resume=True) is None


def test_segment_qa_policy_version_round_trips_through_manifest_cache(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    artifact = job_dir / "segment_qa.json"
    artifact.write_text('{"summary":{"passed":true}}', encoding="utf-8")
    inputs = {"segments": "segments-v1", "audio": ["audio-v1"]}
    config = {"min_similarity": 0.78}
    model = {"language": "vi", "name": "large-v3"}
    versions = {
        "policy": pipeline.SEGMENT_QA_POLICY_VERSION,
        "pronunciation_policy": 1,
    }
    fingerprint = pipeline.stage_fingerprint(
        "segment_qa",
        inputs=inputs,
        config=config,
        model=model,
        versions=versions,
    )

    pipeline._commit_stage(
        job_dir,
        "segment_qa",
        inputs=inputs,
        artifacts=[artifact],
        config=config,
        model=model,
        versions=versions,
    )

    assert pipeline._stage_cache_hit(
        job_dir,
        "segment_qa",
        fingerprint,
        resume=True,
    ) is not None


def test_tts_manifest_rejects_final_text_tamper(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    tts_dir = job_dir / "tts"
    tts_dir.mkdir(parents=True)
    final_text = job_dir / "segments_vi.json"
    stats = job_dir / "tts_stats.json"
    audio = tts_dir / "00001.wav"
    final_text.write_text('[{"id":1,"vi":"xin chao"}]', encoding="utf-8")
    stats.write_text("[]", encoding="utf-8")
    audio.write_bytes(b"audio")
    request_fingerprint = "request-v1"
    pipeline._commit_stage(
        job_dir,
        "tts",
        inputs={
            "request_fingerprint": request_fingerprint,
            "final_text": fingerprint_file(final_text),
        },
        artifacts=[final_text, stats, audio],
        config={"tts": {"device": "cpu"}},
        versions={"policy": 2},
    )

    assert pipeline._load_tts_manifest(
        job_dir,
        request_fingerprint,
        final_text,
        resume=True,
    ) is not None

    final_text.write_text('[{"id":1,"vi":"da bi sua"}]', encoding="utf-8")
    assert pipeline._load_tts_manifest(
        job_dir,
        request_fingerprint,
        final_text,
        resume=True,
    ) is None


def test_tts_partial_cache_preserves_only_matching_request(tmp_path: Path) -> None:
    tts_dir = tmp_path / "tts"
    receipt, reused = pipeline._prepare_tts_partial_cache(tts_dir, "request-a", resume=True)
    assert reused is False
    partial = tts_dir / "00001_raw.wav"
    partial.write_bytes(b"x" * 2048)

    same_receipt, reused = pipeline._prepare_tts_partial_cache(tts_dir, "request-a", resume=True)
    assert same_receipt == receipt
    assert reused is True
    assert partial.exists()

    _receipt, reused = pipeline._prepare_tts_partial_cache(tts_dir, "request-b", resume=True)
    assert reused is False
    assert not partial.exists()

    partial.write_bytes(b"x" * 2048)
    _receipt, reused = pipeline._prepare_tts_partial_cache(tts_dir, "request-b", resume=False)
    assert reused is False
    assert not partial.exists()


def test_tts_partial_cache_can_preserve_identity_guarded_raw_audio_for_review_rerender(
    tmp_path: Path,
) -> None:
    tts_dir = tmp_path / "tts"
    pipeline._prepare_tts_partial_cache(tts_dir, "request-a", resume=True)
    raw = tts_dir / "00001_raw.wav"
    raw_meta = tts_dir / "00001_raw.meta.json"
    fitted = tts_dir / "00001.wav"
    raw.write_bytes(b"x" * 2048)
    raw_meta.write_text('{"version":2,"fingerprint":"fixture"}', encoding="utf-8")
    fitted.write_bytes(b"rendered")

    _receipt, reused = pipeline._prepare_tts_partial_cache(
        tts_dir,
        "request-b",
        resume=True,
        preserve_raw_on_mismatch=True,
    )

    assert reused is False
    assert raw.is_file()
    assert raw_meta.is_file()
    assert not fitted.exists()


def test_apply_review_overrides_changes_only_reviewed_segments() -> None:
    segments = [
        Segment(id=1, start=0.0, end=1.0, text="a", vi="mot", speaker="A"),
        Segment(id=2, start=1.0, end=2.0, text="b", vi="hai", speaker="B"),
    ]

    statuses = pipeline._apply_review_overrides(
        segments,
        {1: {"text": "ban sua", "speaker": "B", "review_status": "reviewed"}},
    )

    assert segments[0].vi == "ban sua"
    assert segments[0].speaker == "B"
    assert segments[1].vi == "hai"
    assert statuses == {1: "reviewed"}


def test_apply_review_overrides_rejects_stale_segment_ids() -> None:
    segments = [Segment(id=1, start=0.0, end=1.0, text="a", vi="mot")]

    with pytest.raises(ValueError, match="unknown segment ids"):
        pipeline._apply_review_overrides(
            segments,
            {9: {"text": "stale", "speaker": "A", "review_status": "reviewed"}},
        )


def test_segment_qa_uses_spoken_pronunciation_contract_without_mutating_subtitle() -> None:
    segment = Segment(
        id=7,
        start=0.0,
        end=1.0,
        text="source",
        vi="FVG 25",
    )

    expected, critical_terms = pipeline._segment_qa_spoken_contract(
        segment,
        {"FVG": "ép vi gi"},
    )

    assert expected == "ép vi gi hai mươi lăm"
    assert critical_terms == ("hai mươi lăm", "ép vi gi")
    assert segment.vi == "FVG 25"


def test_cli_fresh_disables_pipeline_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path = tmp_path / "input.mp4"
    config_path = tmp_path / "config.yaml"
    input_path.write_bytes(b"fixture-video")
    config_path.write_text("{}\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_pipeline(*args, **kwargs):
        captured["resume"] = kwargs["resume"]
        return {
            "output": str(tmp_path / "output.mp4"),
            "subtitle": str(tmp_path / "output.vi.srt"),
            "work_dir": str(tmp_path / "work"),
        }

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda *_args, **_kwargs: {
            "translation": {"provider": "webgpt"},
            "diarization": {"enabled": False},
        },
    )
    monkeypatch.setattr(cli, "run_preflight", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "raise_for_preflight", lambda _checks: None)
    result = CliRunner().invoke(
        cli.app,
        ["dub", str(input_path), "--config", str(config_path), "--fresh"],
    )

    assert result.exit_code == 0, result.output
    assert captured["resume"] is False

_MINIMAL_VALID_CONFIG = """
profile: balanced_best
asr: {batch_size: 1}
separation: {enabled: true}
segmentation: {mode: legacy}
translation: {}
tts: {}
timing:
  max_speedup: 1.25
  max_slowdown: 0.92
  rewrite_threshold: 1.25
  aggressive_rewrite_threshold: 1.40
mix:
  final_lufs: -14.0
  final_true_peak_db: -1.5
qa:
  enabled: true
  semantic: {max_attempts: 3}
diarization: {}
"""
