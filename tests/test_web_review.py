from pathlib import Path

import gradio as gr
import pytest

from vi_dubber import web
from vi_dubber.artifacts import fingerprint_file
from vi_dubber.jobs import claim_job, load_job_state, update_job_state


def _row(
    segment_id: int,
    *,
    speaker: str = "SPEAKER_00",
    status: str = "unreviewed",
    flagged: bool = False,
    rewritten: bool = False,
    overflow: bool = False,
    semantic: bool = False,
    speaker_overlap: bool = False,
    multi_speaker: bool = False,
) -> dict:
    return {
        "id": segment_id,
        "start": float(segment_id),
        "end": float(segment_id) + 0.8,
        "source_en": f"source {segment_id}",
        "translated_vi": f"baseline {segment_id}",
        "selected_vi": f"selected {segment_id}",
        "speaker": speaker,
        "review_status": status,
        "rewritten": rewritten,
        "overflow": overflow,
        "semantic_flag": semantic,
        "speaker_overlap": speaker_overlap,
        "multi_speaker": multi_speaker,
        "flagged": flagged,
        "tts": None,
        "semantic_qa": None,
        "downstream_invalidated": False,
    }


def _summary(total: int) -> dict[str, int]:
    return {
        "total": total,
        "flagged": 1,
        "rewritten": 1,
        "overflow": 1,
        "semantic_flag": 1,
        "speaker_overlap": 0,
        "multi_speaker": 0,
        "reviewed": 1,
        "accepted": 0,
        "downstream_invalidated": 0,
    }


def test_review_view_filters_flagged_and_speaker(monkeypatch, tmp_path: Path) -> None:
    rows = [
        _row(0),
        _row(1, speaker="SPEAKER_01", flagged=True, rewritten=True, overflow=True, semantic=True),
        _row(2, speaker="SPEAKER_01", status="reviewed"),
    ]
    monkeypatch.setattr(web, "load_review_rows", lambda _job: rows)
    monkeypatch.setattr(web, "review_summary", lambda _job: _summary(len(rows)))

    view = web._review_view(tmp_path, "Cần xem lại", "SPEAKER_01")

    assert [item[0] for item in view["table"]] == [1]
    assert "semantic" in view["table"][0][2]
    assert "overflow" in view["table"][0][2]
    assert view["speaker_choices"] == ["Tất cả", "SPEAKER_00", "SPEAKER_01"]
    assert view["segment_choices"][0][1] == "1"


def test_review_view_exposes_overlap_and_multi_speaker_flags(monkeypatch, tmp_path: Path) -> None:
    rows = [
        _row(3, flagged=True, speaker_overlap=True, multi_speaker=True),
    ]
    monkeypatch.setattr(web, "load_review_rows", lambda _job: rows)
    monkeypatch.setattr(web, "review_summary", lambda _job: _summary(len(rows)))

    view = web._review_view(tmp_path, "Cần xem lại", "Tất cả")

    assert "overlap" in view["table"][0][2]
    assert "multi-speaker" in view["table"][0][2]


def test_review_editor_exposes_cached_source_and_dubbed_audio(monkeypatch, tmp_path: Path) -> None:
    job = tmp_path / "job-review-audio"
    job.mkdir()
    (job / "original.wav").write_bytes(b"source-audio")
    tts_dir = job / "tts"
    tts_dir.mkdir()
    dubbed = tts_dir / "00003.wav"
    dubbed.write_bytes(b"dubbed-audio")
    rows = [_row(3)]
    monkeypatch.setattr(web, "load_review_rows", lambda _job: rows)
    calls: list[tuple[Path, Path, float, float]] = []

    def fake_clip(source: Path, output: Path, start: float, duration: float) -> Path:
        calls.append((source, output, start, duration))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"source-slice")
        return output

    monkeypatch.setattr(web, "clip_audio", fake_clip)

    first = web._review_editor_fields(str(job), "3")
    second = web._review_editor_fields(str(job), "3")

    source_slice = job / "review_source" / "00003.wav"
    assert first[6] == str(source_slice)
    assert first[7] == str(dubbed)
    assert second[6] == str(source_slice)
    assert len(calls) == 1
    assert calls[0][:3] == (job / "original.wav", source_slice, 3.0)
    assert calls[0][3] == pytest.approx(0.8)


def test_review_editor_gracefully_handles_missing_source_audio(monkeypatch, tmp_path: Path) -> None:
    job = tmp_path / "job-review-no-source"
    job.mkdir()
    monkeypatch.setattr(web, "load_review_rows", lambda _job: [_row(1)])

    fields = web._review_editor_fields(str(job), "1")

    assert fields[6] is None
    assert fields[7] is None


def test_refresh_review_workspace_populates_default_editor(monkeypatch, tmp_path: Path) -> None:
    rows = [_row(0), _row(1, speaker="SPEAKER_01", flagged=True)]
    monkeypatch.setattr(web, "load_review_rows", lambda _job: rows)
    monkeypatch.setattr(web, "review_summary", lambda _job: _summary(len(rows)))

    workspace = web._refresh_review_workspace(str(tmp_path), "Tất cả", "Tất cả")

    assert workspace[1]["value"] == "0"
    assert workspace[4] == "source 0"
    assert workspace[5] == "baseline 0"
    assert workspace[6] == "selected 0"
    assert workspace[7] == "SPEAKER_00"
    assert workspace[8] == "unreviewed"


def test_select_persisted_completed_job_restores_result(monkeypatch, tmp_path: Path) -> None:
    job = tmp_path / "job-a"
    job.mkdir()
    output = tmp_path / "done.mp4"
    subtitle = tmp_path / "done.srt"
    output.write_bytes(b"video")
    subtitle.write_text("subtitle", encoding="utf-8")
    update_job_state(
        job,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Hoàn tất",
        result={"output": str(output), "subtitle": str(subtitle)},
        metadata={"input_name": "input.mp4"},
    )
    monkeypatch.setattr(web, "review_summary", lambda _job: _summary(2))

    restored = web._select_persisted_job(str(job))

    assert restored[0] == str(job)
    assert "Job đã hoàn tất" in restored[1]
    assert restored[3] == str(output)
    assert restored[4] == str(output)
    assert restored[5] == str(subtitle)


def test_job_status_html_surfaces_failed_and_cancelled_states() -> None:
    failed = web._job_status_html(
        {
            "status": "failed",
            "message": "translation failed",
            "error": {"message": "WebGPT route unavailable"},
        }
    )
    cancelled = web._job_status_html(
        {"status": "cancelled", "message": "Tác vụ đã bị hủy theo yêu cầu."}
    )

    assert "Job dừng do lỗi" in failed
    assert "WebGPT route unavailable" in failed
    assert "LỖI" in failed
    assert "Thử lại từ checkpoint" in failed
    assert "Job đã hủy" in cancelled
    assert "ĐÃ HỦY" in cancelled
    assert "tắt Resume để chạy fresh" in cancelled


def test_job_action_policy_exposes_state_specific_ctas_and_disabled_states() -> None:
    running = web._job_action_policy({"status": "running"})
    paused = web._job_action_policy({"status": "paused"})
    failed = web._job_action_policy({"status": "failed"})
    cancelled = web._job_action_policy({"status": "cancelled"})
    completed = web._job_action_policy({"status": "completed"}, stale_render=True)

    assert running == {
        "pause": True,
        "cancel": True,
        "resume": False,
        "resume_label": "Tiếp tục",
        "rerender": False,
    }
    assert paused["resume"] is True
    assert paused["resume_label"] == "Tiếp tục từ checkpoint"
    assert failed["resume"] is True
    assert failed["resume_label"] == "Thử lại từ checkpoint"
    assert cancelled["pause"] is False
    assert cancelled["cancel"] is False
    assert cancelled["resume"] is False
    assert completed["rerender"] is True


def test_job_action_updates_enable_rerender_only_for_stale_completed_job(monkeypatch, tmp_path: Path) -> None:
    job = tmp_path / "job-stale-render"
    job.mkdir()
    update_job_state(job, status="completed", stage="complete", progress=1.0)
    stale = _summary(1)
    stale["downstream_invalidated"] = 1
    monkeypatch.setattr(web, "review_summary", lambda _job: stale)

    pause, cancel, resume, rerender = web._job_action_updates(str(job))

    assert pause["interactive"] is False
    assert cancel["interactive"] is False
    assert resume["interactive"] is False
    assert rerender["interactive"] is True


def test_pending_job_controls_are_disabled_while_loading_or_control_request_is_pending() -> None:
    pause, cancel, resume, rerender = web._pending_job_action_updates()

    assert pause["interactive"] is False
    assert cancel["interactive"] is False
    assert resume["interactive"] is False
    assert rerender["interactive"] is False


def test_select_persisted_job_has_explicit_empty_state() -> None:
    selected = web._select_persisted_job(None)

    assert selected[0] == ""
    assert "Chưa chọn job" in selected[1]
    assert "CHƯA CHỌN" in selected[1]
    assert "Chưa chọn job" in selected[2]


def test_translation_catalog_refresh_normalizes_models_and_efforts(monkeypatch) -> None:
    monkeypatch.setattr(
        web.translation_runtime,
        "translation_model_catalog",
        lambda: {
            "data": [
                {
                    "id": "gpt-5.6-sol",
                    "display_name": "GPT-5.6 Sol",
                    "reasoning_efforts": ["medium", "high"],
                    "default_effort": "high",
                },
                {"id": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna"},
            ],
            "default_model": "gpt-5.6-sol",
            "revision": "fixture-r1",
        },
        raising=False,
    )

    catalog, model_update, effort_update, status_html, run_update = web._refresh_translation_catalog_ui(
        "Codex WebGPT"
    )

    assert catalog["status"] == "ready"
    assert catalog["default_model"] == "gpt-5.6-sol"
    assert model_update["value"] == "gpt-5.6-sol"
    assert model_update["interactive"] is True
    assert effort_update["choices"] == ["medium", "high"]
    assert effort_update["value"] == "high"
    assert "fixture-r1" in status_html
    assert run_update["interactive"] is True


def test_translation_catalog_error_disables_remote_start_but_not_local(monkeypatch) -> None:
    def fail_catalog():
        raise RuntimeError("catalog offline")

    monkeypatch.setattr(web.translation_runtime, "translation_model_catalog", fail_catalog, raising=False)

    catalog, model_update, effort_update, status_html, run_update = web._refresh_translation_catalog_ui(
        "Codex WebGPT"
    )
    assert catalog["status"] == "error"
    assert model_update["interactive"] is False
    assert effort_update["interactive"] is False
    assert "catalog offline" in status_html
    assert run_update["interactive"] is False

    _catalog, _model, _effort, local_status, local_run = web._refresh_translation_catalog_ui("LLM local only")
    assert "không dùng ChatGPT Web model catalog" in local_status
    assert local_run["interactive"] is True


def _install_web_happy_path_fakes(monkeypatch, tmp_path: Path) -> Path:
    project = tmp_path / "project"
    work = project / "work"
    work.mkdir(parents=True)
    (project / "config.yaml").write_text("profile: fast\n", encoding="utf-8")
    monkeypatch.setattr(web, "PROJECT_ROOT", project)
    monkeypatch.setattr(web, "WORK_DIR", work)
    monkeypatch.setattr(web, "configure_runtime", lambda: None)
    monkeypatch.setattr(web, "load_config", lambda *_args, **_kwargs: {"profile": "fast"})
    preflight_calls: list[Path] = []
    pipeline_calls: list[dict] = []

    def fake_preflight(_config, **kwargs):
        preflight_calls.append(Path(kwargs["input_path"]))
        return []

    monkeypatch.setattr(web, "run_preflight", fake_preflight)
    monkeypatch.setattr(web, "raise_for_preflight", lambda _checks: None)

    def fake_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        input_path = Path(kwargs["input_path"])
        output_path = Path(kwargs["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"final-video")
        subtitle = output_path.with_suffix(".vi.srt")
        subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chao\n", encoding="utf-8")
        kwargs["progress_callback"](0.5, "TTS segment 1/1")
        job_dir = web._job_dir_for_input(input_path)
        result = {
            "output": str(output_path),
            "subtitle": str(subtitle),
            "work_dir": str(job_dir),
            "duration_seconds": 1.0,
            "elapsed_seconds": 0.2,
            "real_time_factor": 0.2,
            "segments": 1,
            "speaker_count": 1,
            "rewritten_segments": 0,
            "overflow_segments": 0,
            "cloned_segments": 1,
            "average_tempo": 1.0,
            "max_tempo": 1.0,
            "translation": {
                "requested": kwargs["translation_provider"],
                "used": kwargs["translation_provider"],
                "model": "fixture",
            },
            "qa": {"similarity": 1.0, "passed": True},
        }
        update_job_state(
            job_dir,
            status="completed",
            stage="complete",
            progress=1.0,
            message="Hoàn tất",
            result=result,
        )
        return result

    monkeypatch.setattr(web, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(web, "_p16_preflight_calls", preflight_calls, raising=False)
    monkeypatch.setattr(web, "_p21_pipeline_calls", pipeline_calls, raising=False)
    return work


def test_run_web_job_webgpt_happy_path_controller_reaches_final_output(monkeypatch, tmp_path: Path) -> None:
    _install_web_happy_path_fakes(monkeypatch, tmp_path)
    input_path = tmp_path / "local-input.mp4"
    input_path.write_bytes(b"local-video")

    updates = list(
        web.run_web_job(
            "Tệp trên máy",
            str(input_path),
            None,
            "Codex WebGPT",
            "Tắt",
            None,
            None,
            True,
            "Fast",
            "chatgpt-web/gpt-5.6-sol",
            None,
            {
                "status": "ready",
                "models": [{"id": "chatgpt-web/gpt-5.6-sol", "display_name": "GPT-5.6 Sol", "efforts": []}],
                "default_model": "chatgpt-web/gpt-5.6-sol",
                "revision": "fixture-r1",
            },
            progress=lambda *_args, **_kwargs: None,
        )
    )

    final = updates[-1]
    job = web._job_dir_for_input(input_path)
    assert Path(final[0]).is_file()
    assert Path(final[2]).is_file()
    assert "XONG" in final[5]
    assert load_job_state(job)["status"] == "completed"
    assert getattr(web, "_p16_preflight_calls") == [input_path]


def test_run_web_job_snapshots_selected_model_and_effort(monkeypatch, tmp_path: Path) -> None:
    _install_web_happy_path_fakes(monkeypatch, tmp_path)
    input_path = tmp_path / "remote-input.mp4"
    input_path.write_bytes(b"remote-video")

    updates = list(
        web.run_web_job(
            "Tệp trên máy",
            str(input_path),
            None,
            "Codex WebGPT",
            "Tắt",
            None,
            None,
            True,
            "Balanced Best",
            "chatgpt-web/gpt-5.6-sol",
            "high",
            {
                "status": "ready",
                "models": [{"id": "chatgpt-web/gpt-5.6-sol", "display_name": "GPT-5.6 Sol", "efforts": ["high"]}],
                "revision": "fixture-r1",
                "fetched_at": "2026-09-23T09:00:00Z",
            },
            progress=lambda *_args, **_kwargs: None,
        )
    )

    assert "XONG" in updates[-1][5]
    job = web._job_dir_for_input(input_path)
    metadata = load_job_state(job)["metadata"]
    assert metadata["translation_model"] == "chatgpt-web/gpt-5.6-sol"
    assert metadata["translation_model_display_name"] == "GPT-5.6 Sol"
    assert metadata["translation_effort"] == "high"
    assert metadata["translation_catalog_revision"] == "fixture-r1"
    pipeline_call = getattr(web, "_p21_pipeline_calls")[-1]
    assert pipeline_call["translation_model"] == "chatgpt-web/gpt-5.6-sol"
    assert pipeline_call["translation_effort"] == "high"
    assert pipeline_call["translation_display_name"] == "GPT-5.6 Sol"
    assert pipeline_call["translation_catalog_revision"] == "fixture-r1"


def test_run_web_job_requires_model_for_remote_provider(monkeypatch, tmp_path: Path) -> None:
    _install_web_happy_path_fakes(monkeypatch, tmp_path)
    input_path = tmp_path / "missing-model.mp4"
    input_path.write_bytes(b"remote-video")

    with pytest.raises(gr.Error, match="chọn model dịch"):
        list(
            web.run_web_job(
                "Tệp trên máy",
                str(input_path),
                None,
                "Codex WebGPT",
                "Tắt",
                None,
                None,
                True,
                "Balanced Best",
                progress=lambda *_args, **_kwargs: None,
            )
        )


def test_run_web_job_youtube_happy_path_controller_uses_downloader(monkeypatch, tmp_path: Path) -> None:
    work = _install_web_happy_path_fakes(monkeypatch, tmp_path)
    downloaded = work / "youtube" / "fixture.mp4"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"youtube-video")
    requested: list[str] = []

    def fake_download(url: str, _destination: Path, progress_callback):
        requested.append(url)
        progress_callback(1.0, "YouTube fixture ready")
        return downloaded, {"title": "YouTube fixture"}

    monkeypatch.setattr(web, "download_youtube", fake_download)

    updates = list(
        web.run_web_job(
            "YouTube",
            None,
            "https://www.youtube.com/watch?v=fixture-safe",
            "Codex WebGPT",
            "Tắt",
            None,
            None,
            True,
            "Fast",
            "chatgpt-web/gpt-5.6-sol",
            None,
            {
                "status": "ready",
                "models": [{"id": "chatgpt-web/gpt-5.6-sol", "display_name": "GPT-5.6 Sol", "efforts": []}],
                "default_model": "chatgpt-web/gpt-5.6-sol",
                "revision": "fixture-r1",
            },
            progress=lambda *_args, **_kwargs: None,
        )
    )

    assert requested == ["https://www.youtube.com/watch?v=fixture-safe"]
    assert Path(updates[-1][0]).is_file()
    assert "YouTube fixture" in updates[-1][5]


def test_save_content_edit_marks_job_for_downstream_render(monkeypatch, tmp_path: Path) -> None:
    job = tmp_path / "job-b"
    job.mkdir()
    update_job_state(job, status="completed", stage="complete", progress=1.0, message="Hoàn tất")
    monkeypatch.setattr(
        web,
        "update_segment_review",
        lambda *_args, **_kwargs: {"kind": "content_edit", "invalidated_stages": ["tts", "mix_mux"]},
    )

    status_html, *_rest = web._save_review_segment(
        str(job),
        "4",
        "Bản sửa",
        "SPEAKER_01",
        "reviewed",
        "Tất cả",
        "Tất cả",
    )

    state = load_job_state(job)
    assert state["status"] == "paused"
    assert state["stage"] == "review"
    assert "Segment 4" in str(state["message"])
    assert "CẦN RENDER" in status_html


def test_accept_status_only_does_not_change_completed_lifecycle(monkeypatch, tmp_path: Path) -> None:
    job = tmp_path / "job-c"
    job.mkdir()
    update_job_state(job, status="completed", stage="complete", progress=1.0, message="Hoàn tất")
    monkeypatch.setattr(
        web,
        "update_segment_review",
        lambda *_args, **_kwargs: {"kind": "status_update", "invalidated_stages": []},
    )

    status_html, *_rest = web._accept_review_segment(str(job), "2", "Tất cả", "Tất cả")

    assert load_job_state(job)["status"] == "completed"
    assert "ACCEPTED" in status_html


def test_run_persisted_job_reuses_stored_nonsecret_options(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"video")
    job = tmp_path / "job-d"
    job.mkdir()
    update_job_state(
        job,
        status="paused",
        metadata={
            "input_path": str(input_path),
            "translation_provider": "webgpt",
            "translation_model": "chatgpt-web/gpt-5.6-sol",
            "translation_effort": "medium",
            "profile": "balanced_best",
            "diarization": "Bật",
            "voice_ref": "",
        },
    )
    called: dict[str, tuple] = {}

    def fake_run_web_job(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        yield (None, None, None, "stats", [], "status", str(job), "progress")

    monkeypatch.setattr(web, "run_web_job", fake_run_web_job)
    progress = object()

    yielded = list(web.run_persisted_job(str(job), "hf-session-only", progress=progress))

    assert len(yielded) == 1
    args = called["args"]
    assert args[0] == "Tệp trên máy"
    assert args[1] == str(input_path)
    assert args[3] == "Codex WebGPT"
    assert args[4] == "Bật"
    assert args[6] == "hf-session-only"
    assert args[7] is True
    assert args[8] == "Balanced Best"
    assert called["kwargs"]["translation_model"] == "chatgpt-web/gpt-5.6-sol"
    assert called["kwargs"]["translation_effort"] == "medium"
    assert called["kwargs"]["progress"] is progress
    assert called["kwargs"]["config_path_override"] is None
    assert called["kwargs"]["output_path_override"] is None


def test_run_persisted_job_rejects_legacy_non_webgpt_provider(tmp_path: Path) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"video")
    job = tmp_path / "job-legacy-provider"
    job.mkdir()
    update_job_state(
        job,
        status="paused",
        metadata={
            "input_path": str(input_path),
            "translation_provider": "local",
            "profile": "balanced_best",
        },
    )

    with pytest.raises(gr.Error, match="chỉ được resume bằng Codex WebGPT instance 2"):
        list(web.run_persisted_job(str(job), None, progress=lambda *_args, **_kwargs: None))


def test_run_persisted_job_recovers_legacy_input_by_verified_identity(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    work = project / "work"
    source = work / "benchmarks" / "legacy.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"legacy-video")
    identity = fingerprint_file(source)
    job = work / f"job-{identity['sha256'][:16]}"
    job.mkdir()
    (job / "job.json").write_text(
        '{\n'
        '  "version": 1,\n'
        f'  "source": {{"sha256": "{identity["sha256"]}", "size_bytes": {identity["size_bytes"]}}},\n'
        '  "source_name": "legacy.mp4",\n'
        '  "profile": "balanced_best"\n'
        '}\n',
        encoding="utf-8",
    )
    update_job_state(
        job,
        status="paused",
        metadata={
            "input_name": source.name,
            "source_sha256": identity["sha256"],
            "profile": "balanced_best",
        },
    )
    monkeypatch.setattr(web, "PROJECT_ROOT", project)
    monkeypatch.setattr(web, "WORK_DIR", work)
    called: dict[str, tuple] = {}

    def fake_run_web_job(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        yield (None, None, None, "stats", [], "status", str(job), "progress")

    monkeypatch.setattr(web, "run_web_job", fake_run_web_job)

    yielded = list(web.run_persisted_job(str(job), None))

    assert len(yielded) == 1
    assert called["args"][1] == str(source.resolve())
    healed = load_job_state(job)
    assert healed["metadata"]["input_path"] == str(source.resolve())
    assert healed["metadata"]["input_path_recovered"] is True


def test_run_persisted_job_rejects_changed_input_identity(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    work = project / "work"
    source = work / "benchmarks" / "input.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original-content")
    identity = fingerprint_file(source)
    job = work / f"job-{identity['sha256'][:16]}"
    job.mkdir()
    (job / "job.json").write_text(
        '{\n'
        '  "version": 1,\n'
        f'  "source": {{"sha256": "{identity["sha256"]}", "size_bytes": {identity["size_bytes"]}}},\n'
        f'  "source_path": "{str(source).replace(chr(92), chr(92) * 2)}",\n'
        '  "source_name": "input.mp4"\n'
        '}\n',
        encoding="utf-8",
    )
    update_job_state(
        job,
        status="paused",
        metadata={
            "input_path": str(source),
            "input_name": source.name,
            "source_sha256": identity["sha256"],
        },
    )
    source.write_bytes(b"changed-content")
    monkeypatch.setattr(web, "PROJECT_ROOT", project)
    monkeypatch.setattr(web, "WORK_DIR", work)

    with pytest.raises(gr.Error, match="chưa lưu đường dẫn input"):
        list(web.run_persisted_job(str(job), None))


def test_duplicate_start_guard_blocks_same_source_while_live_job_is_running(monkeypatch, tmp_path: Path) -> None:
    work = tmp_path / "work"
    source = tmp_path / "input.mp4"
    source.write_bytes(b"same-source")
    identity = fingerprint_file(source)
    job = work / f"job-{identity['sha256'][:16]}"
    job.mkdir(parents=True)
    update_job_state(
        job,
        status="running",
        stage="translation",
        progress=0.4,
        metadata={"source_sha256": identity["sha256"]},
    )
    monkeypatch.setattr(web, "WORK_DIR", work)

    with claim_job(job):
        with pytest.raises(RuntimeError, match="đang có một job chạy"):
            web._assert_no_duplicate_running_job(source)


def test_run_persisted_job_blocks_live_lease_but_recovers_stale_running_state(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"video")
    job = tmp_path / "job-running"
    job.mkdir()
    update_job_state(
        job,
        status="running",
        stage="translation",
        progress=0.5,
        metadata={"input_path": str(input_path)},
    )

    with claim_job(job):
        with pytest.raises(gr.Error, match="đang chạy"):
            list(web.run_persisted_job(str(job), None))
        assert load_job_state(job)["status"] == "running"

    called: dict[str, tuple] = {}

    def fake_run_web_job(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        yield (None, None, None, "stats", [], "status", str(job), "progress")

    monkeypatch.setattr(web, "run_web_job", fake_run_web_job)
    yielded = list(web.run_persisted_job(str(job), None))

    assert len(yielded) == 1
    assert called["args"][1] == str(input_path)
    recovered = load_job_state(job)
    assert recovered["status"] == "paused"
    assert recovered["metadata"]["recovered_from_stale_running"] is True


def test_select_persisted_job_reconciles_stale_running_state_on_reload(monkeypatch, tmp_path: Path) -> None:
    job = tmp_path / "job-reload"
    job.mkdir()
    update_job_state(job, status="running", stage="tts", progress=0.7)
    monkeypatch.setattr(web, "review_summary", lambda _job: _summary(0))

    selected = web._select_persisted_job(str(job))

    assert load_job_state(job)["status"] == "paused"
    assert "TẠM DỪNG" in selected[1]
    assert "70%" in selected[2]


def test_run_persisted_job_allows_pending_manual_edit_to_rerender(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"video")
    job = tmp_path / "job-e"
    job.mkdir()
    update_job_state(
        job,
        status="paused",
        metadata={"input_path": str(input_path)},
    )
    pending = _summary(1)
    pending["downstream_invalidated"] = 1
    monkeypatch.setattr(web, "review_summary", lambda _job: pending)
    called: dict[str, tuple] = {}

    def fake_run_web_job(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        yield (None, None, None, "stats", [], "status", str(job), "progress")

    monkeypatch.setattr(web, "run_web_job", fake_run_web_job)

    yielded = list(web.run_persisted_job(str(job), None))

    assert len(yielded) == 1
    assert called["args"][1] == str(input_path)
    assert called["args"][7] is True


def test_build_app_smoke(monkeypatch) -> None:
    monkeypatch.setattr(web, "list_job_states", lambda _work_dir: [])
    monkeypatch.setattr(
        web,
        "webgpt_route_info",
        lambda: {"ready": True, "reason": "", "model": "chatgpt-web/gpt-5.6-sol"},
    )
    monkeypatch.setattr(web, "_device_info", lambda: "CUDA")

    app = web.build_app()

    assert isinstance(app, gr.Blocks)


def test_build_app_enables_running_controls_for_run_resume_and_rerender(monkeypatch) -> None:
    monkeypatch.setattr(web, "list_job_states", lambda _work_dir: [])
    monkeypatch.setattr(
        web,
        "webgpt_route_info",
        lambda: {"ready": True, "reason": "", "model": "chatgpt-web/gpt-5.6-sol"},
    )
    monkeypatch.setattr(web, "_device_info", lambda: "CUDA")

    app = web.build_app()
    running_control_indices = [
        index
        for index, block_fn in app.fns.items()
        if getattr(block_fn.fn, "__name__", "") == "_running_job_action_updates"
    ]
    running_control_dependencies = [
        app.config["dependencies"][index]
        for index in running_control_indices
    ]

    assert len(running_control_dependencies) == 3
    assert all(dependency["targets"][0][1] == "click" for dependency in running_control_dependencies)
    assert len({dependency["targets"][0][0] for dependency in running_control_dependencies}) == 3
