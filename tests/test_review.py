import json
from pathlib import Path

import pytest

from vi_dubber.artifacts import build_stage_manifest, write_stage_manifest
from vi_dubber.review import (
    ReviewDataError,
    load_review_overrides,
    load_review_rows,
    review_summary,
    update_segment_review,
)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _segment(segment_id: int, *, vi: str, speaker: str = "SPEAKER_00") -> dict:
    return {
        "id": segment_id,
        "start": float(segment_id),
        "end": float(segment_id) + 0.8,
        "text": f"source {segment_id}",
        "speaker": speaker,
        "vi": vi,
        "words": [],
        "source_segment_ids": [],
    }


def _commit(job: Path, stage: str, artifacts: list[Path]) -> Path:
    manifest = build_stage_manifest(job, stage, inputs={"fixture": stage}, artifacts=artifacts)
    path = job / "manifests" / f"{stage}.json"
    write_stage_manifest(path, manifest)
    return path


def _completed_job(tmp_path: Path) -> tuple[Path, Path]:
    job = tmp_path / "job"
    job.mkdir()
    _write_json(
        job / "job.json",
        {"version": 1, "source": {"sha256": "a" * 64, "size_bytes": 1234}},
    )
    _write_json(job / "metrics.json", {"schema_version": 1, "status": "complete"})

    source = [_segment(0, vi=""), _segment(1, vi="")]
    translated = [_segment(0, vi="Xin chao"), _segment(1, vi="Qua dai")]
    final = [_segment(0, vi="Xin chao"), _segment(1, vi="Ngan lai")]
    _write_json(job / "segments_source.json", source)
    _write_json(job / "segments_translated.json", translated)
    _write_json(job / "segments_vi.json", final)

    _write_json(
        job / "tts_stats.json",
        [
            {
                "segment_id": 0,
                "speaker": "SPEAKER_00",
                "target_duration": 0.8,
                "generated_duration": 0.7,
                "final_duration": 0.7,
                "tempo": 1.0,
                "rewrites": 0,
                "used_clone": True,
            },
            {
                "segment_id": 1,
                "speaker": "SPEAKER_00",
                "target_duration": 0.8,
                "generated_duration": 1.1,
                "final_duration": 0.9,
                "tempo": 1.2,
                "rewrites": 1,
                "used_clone": True,
            },
        ],
    )
    _write_json(job / "tts_translation_meta.json", {"requested_provider": "fixture", "stats": {}})

    semantic_stage = job / "semantic_qa.translated.json"
    _write_json(
        semantic_stage,
        {
            "kind": "semantic_qa_stage",
            "raw_result": {
                "items": [
                    {
                        "id": 0,
                        "choice": "faithful",
                        "confidence": 0.9,
                        "critical_facts_probability": 0.9,
                        "probabilities": {"faithful": 0.95},
                    },
                    {
                        "id": 1,
                        "choice": "partial",
                        "confidence": 0.4,
                        "critical_facts_probability": 0.6,
                        "probabilities": {"faithful": 0.5},
                    },
                ]
            },
        },
    )
    _write_json(
        job / "semantic_qa.json",
        {
            "version": 2,
            "kind": "semantic_qa_index",
            "stages": {
                "translated": {
                    "status": "ok",
                    "artifact": "semantic_qa.translated.json",
                    "thresholds": {
                        "review_confidence_below": 0.5,
                        "review_critical_below": 0.75,
                        "review_faithful_below": 0.65,
                    },
                }
            },
        },
    )

    speech_turns = job / "speech_turns.json"
    _write_json(speech_turns, {"version": 1, "turns": [0, 1]})
    _commit(job, "asr", [job / "segments_source.json"])
    _commit(job, "segmentation", [speech_turns])
    _commit(job, "translation", [job / "segments_translated.json"])

    tts_dir = job / "tts"
    tts_dir.mkdir()
    rendered = []
    for segment_id in (0, 1):
        (tts_dir / f"{segment_id:05d}_raw.wav").write_bytes(b"raw-audio")
        fitted = tts_dir / f"{segment_id:05d}.wav"
        fitted.write_bytes(b"fitted-audio")
        rendered.append(fitted)
    _commit(
        job,
        "tts",
        [job / "segments_vi.json", job / "tts_stats.json", job / "tts_translation_meta.json", *rendered],
    )

    voice = job / "voice_vi.wav"
    voice.write_bytes(b"voice")
    _commit(job, "timing_assembly", [voice])
    dubbed = job / "dubbed.mp4"
    dubbed.write_bytes(b"video")
    _commit(job, "mix_mux", [dubbed])
    qa = job / "qa_raw.json"
    _write_json(qa, {"similarity": 0.9, "expected": "x", "actual": "x"})
    _commit(job, "acoustic_qa", [qa])

    external_output = tmp_path / "user-final.mp4"
    external_output.write_bytes(b"user-output")
    return job, external_output


def test_load_review_rows_merges_translation_tts_and_semantic_qa(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)

    rows = load_review_rows(job)

    assert [row["id"] for row in rows] == [0, 1]
    assert rows[0]["source_en"] == "source 0"
    assert rows[0]["translated_vi"] == "Xin chao"
    assert rows[0]["selected_vi"] == "Xin chao"
    assert rows[0]["flagged"] is False
    assert rows[1]["rewritten"] is True
    assert rows[1]["overflow"] is True
    assert rows[1]["semantic_flag"] is True
    assert rows[1]["flagged"] is True
    assert rows[1]["tts"]["tempo"] == 1.2
    assert rows[1]["semantic_qa"]["choice"] == "partial"


def test_review_uses_smart_segmentation_turn_lineage(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    source = [
        _segment(0, vi=""),
        _segment(1, vi=""),
        _segment(2, vi=""),
    ]
    turns = [
        _segment(0, vi=""),
        _segment(1, vi=""),
    ]
    turns[0]["text"] = "source 0 source 1"
    turns[0]["source_segment_ids"] = [0, 1]
    turns[1]["text"] = "source 2"
    turns[1]["source_segment_ids"] = [2]
    _write_json(job / "segments_source.json", source)
    _write_json(job / "segments_turns.json", turns)

    rows = load_review_rows(job)

    assert [row["id"] for row in rows] == [0, 1]
    assert rows[0]["source_en"] == "source 0 source 1"
    assert rows[1]["source_en"] == "source 2"

    receipt = update_segment_review(job, 0, review_status="accepted")

    assert receipt["kind"] == "status_update"
    assert load_review_rows(job)[0]["review_status"] == "accepted"


def test_review_falls_back_to_source_when_stale_turn_ids_do_not_match(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    stale_turns = [_segment(10, vi=""), _segment(11, vi="")]
    _write_json(job / "segments_turns.json", stale_turns)

    rows = load_review_rows(job)

    assert [row["id"] for row in rows] == [0, 1]
    assert rows[0]["source_en"] == "source 0"
    assert rows[1]["source_en"] == "source 1"


def test_review_reports_source_alignment_when_no_candidate_matches(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    _write_json(job / "segments_source.json", [_segment(5, vi=""), _segment(6, vi="")])
    _write_json(job / "segments_turns.json", [_segment(10, vi=""), _segment(11, vi="")])

    with pytest.raises(ReviewDataError, match=r"expected=2, turns=2, source=2"):
        load_review_rows(job)


def test_review_accepts_semantic_qa_index_v3(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    semantic_index = json.loads((job / "semantic_qa.json").read_text(encoding="utf-8"))
    semantic_index["version"] = 3
    semantic_index["stages"]["prefit_candidate_00001"] = {
        "status": "ok",
        "artifact": "semantic_qa.prefit_candidate_00001.json",
        "thresholds": semantic_index["stages"]["translated"]["thresholds"],
    }
    _write_json(job / "semantic_qa.json", semantic_index)

    rows = load_review_rows(job)

    assert [row["id"] for row in rows] == [0, 1]
    assert rows[1]["semantic_qa"]["choice"] == "partial"


def test_edit_preserves_upstream_and_invalidates_only_downstream(tmp_path: Path) -> None:
    job, external_output = _completed_job(tmp_path)
    preserved = [
        job / "segments_source.json",
        job / "segments_translated.json",
        job / "speech_turns.json",
        job / "semantic_qa.json",
        job / "semantic_qa.translated.json",
        job / "manifests" / "asr.json",
        job / "manifests" / "segmentation.json",
        job / "manifests" / "translation.json",
    ]
    before = {path: path.read_bytes() for path in preserved}

    receipt = update_segment_review(
        job,
        1,
        text="Ban rut gon",
        speaker="SPEAKER_01",
        review_status="reviewed",
    )

    final = json.loads((job / "segments_vi.json").read_text(encoding="utf-8"))
    assert final[1]["vi"] == "Ban rut gon"
    assert final[1]["speaker"] == "SPEAKER_01"
    assert final[1]["review_status"] == "reviewed"
    assert receipt["before"]["text"] == "Ngan lai"
    assert receipt["after"]["text"] == "Ban rut gon"
    assert receipt["invalidated_stages"] == ["tts", "timing_assembly", "mix_mux", "acoustic_qa"]

    for path, content in before.items():
        assert path.read_bytes() == content

    for stage in ("tts", "timing_assembly", "mix_mux", "acoustic_qa"):
        assert not (job / "manifests" / f"{stage}.json").exists()
    assert (job / "tts").is_dir()
    assert (job / "tts" / "00000_raw.wav").is_file()
    assert (job / "tts" / "00001_raw.wav").is_file()
    assert not (job / "tts" / "00000.wav").exists()
    assert not (job / "tts" / "00001.wav").exists()
    assert not (job / "tts_stats.json").exists()
    assert not (job / "tts_translation_meta.json").exists()
    assert not (job / "voice_vi.wav").exists()
    assert not (job / "dubbed.mp4").exists()
    assert not (job / "qa_raw.json").exists()
    assert external_output.read_bytes() == b"user-output"

    history = json.loads((job / "review_history.json").read_text(encoding="utf-8"))
    assert history["version"] == 1
    assert history["edits"] == [receipt]
    assert (job / "review_receipts" / f"{receipt['edit_id']}.json").is_file()
    assert load_review_overrides(job) == {
        1: {"text": "Ban rut gon", "speaker": "SPEAKER_01", "review_status": "reviewed"}
    }

    rows = load_review_rows(job)
    assert rows[0]["downstream_invalidated"] is False
    assert rows[1]["selected_vi"] == "Ban rut gon"
    assert rows[1]["review_status"] == "reviewed"
    assert rows[1]["tts"] is None
    assert rows[1]["downstream_invalidated"] is True
    assert review_summary(job)["downstream_invalidated"] == 1


def test_status_only_review_preserves_rendered_artifacts(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    preserved = [
        job / "tts" / "00000.wav",
        job / "voice_vi.wav",
        job / "dubbed.mp4",
        job / "qa_raw.json",
        job / "manifests" / "tts.json",
        job / "manifests" / "timing_assembly.json",
        job / "manifests" / "mix_mux.json",
        job / "manifests" / "acoustic_qa.json",
    ]
    before = {path: path.read_bytes() for path in preserved}

    receipt = update_segment_review(job, 1, review_status="accepted")

    assert receipt["kind"] == "status_update"
    assert receipt["invalidated_stages"] == []
    assert receipt["invalidated_artifacts"] == []
    for path, content in before.items():
        assert path.read_bytes() == content
    rows = load_review_rows(job)
    assert rows[1]["review_status"] == "accepted"
    assert rows[1]["downstream_invalidated"] is False
    assert load_review_overrides(job) == {}


def test_review_summary_reports_actionable_counts(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    update_segment_review(job, 0, review_status="accepted")

    summary = review_summary(job)

    assert summary == {
        "total": 2,
        "flagged": 1,
        "rewritten": 1,
        "overflow": 1,
        "semantic_flag": 1,
        "speaker_overlap": 0,
        "multi_speaker": 0,
        "reviewed": 1,
        "accepted": 1,
        "downstream_invalidated": 0,
    }


def test_review_rows_surface_overlap_and_word_level_multi_speaker(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    final = json.loads((job / "segments_vi.json").read_text(encoding="utf-8"))
    final[0]["overlap"] = True
    final[0]["words"] = [
        {"text": "a", "start": 0.0, "end": 0.2, "speaker": "SPEAKER_00"},
        {"text": "b", "start": 0.2, "end": 0.4, "speaker": "SPEAKER_01", "overlap": True},
    ]
    _write_json(job / "segments_vi.json", final)

    rows = load_review_rows(job)

    assert rows[0]["speaker_overlap"] is True
    assert rows[0]["multi_speaker"] is True
    assert rows[0]["flagged"] is True
    summary = review_summary(job)
    assert summary["speaker_overlap"] == 1
    assert summary["multi_speaker"] == 1


def test_noop_review_update_is_rejected_without_invalidation(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)

    with pytest.raises(ReviewDataError, match="does not change"):
        update_segment_review(job, 0, text="Xin chao", review_status="unreviewed")

    assert (job / "manifests" / "tts.json").is_file()
    assert (job / "tts" / "00000.wav").is_file()


@pytest.mark.parametrize("broken_file", ["segments_translated.json", "tts_stats.json", "semantic_qa.translated.json"])
def test_review_rows_fail_closed_on_corrupt_required_artifact(tmp_path: Path, broken_file: str) -> None:
    job, _ = _completed_job(tmp_path)
    (job / broken_file).write_text("{broken", encoding="utf-8")

    with pytest.raises(ReviewDataError):
        load_review_rows(job)


def test_update_fails_closed_before_invalidation_when_segment_schema_is_corrupt(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    (job / "segments_vi.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ReviewDataError):
        update_segment_review(job, 0, text="Khong duoc ghi")

    for stage in ("tts", "timing_assembly", "mix_mux", "acoustic_qa"):
        assert (job / "manifests" / f"{stage}.json").is_file()
    assert (job / "tts" / "00000_raw.wav").is_file()


def test_missing_completed_job_file_is_rejected(tmp_path: Path) -> None:
    job, _ = _completed_job(tmp_path)
    (job / "tts_stats.json").unlink()

    with pytest.raises(ReviewDataError, match="tts_stats"):
        load_review_rows(job)
