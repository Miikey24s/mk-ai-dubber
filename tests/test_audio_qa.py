from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import vi_dubber.media as media
from vi_dubber.media import (
    analyze_mix_loudnorm,
    assemble_voice_track,
    build_mix_filter_graph,
    measure_mix_metrics,
    mux_dubbed_video,
)
from vi_dubber.qa import (
    SegmentQAObservation,
    evaluate_segment_qa,
    run_segment_qa_cycle,
    select_acoustic_qa_segment_ids,
    selective_repair_plan,
)
from vi_dubber.types import Segment


def _write_constant(path: Path, value: float, seconds: float, sample_rate: int) -> Path:
    sf.write(path, np.full(int(seconds * sample_rate), value, dtype=np.float32), sample_rate)
    return path


def test_assembly_preserves_exact_timeline_and_equal_power_overlap(tmp_path: Path) -> None:
    sample_rate = 8000
    first = _write_constant(tmp_path / "first.wav", 0.4, 0.75, sample_rate)
    second = _write_constant(tmp_path / "second.wav", 0.4, 0.75, sample_rate)
    segments = [
        (Segment(id=1, start=0.0, end=0.75, text="one"), first),
        (Segment(id=2, start=0.25, end=1.0, text="two", overlap=True), second),
    ]

    output = assemble_voice_track(
        segments,
        tmp_path / "voice.wav",
        total_duration=1.0,
        sample_rate=sample_rate,
        overlap_policy="equal_power",
    )
    rendered, sr = sf.read(output, dtype="float32")

    assert sr == sample_rate
    assert len(rendered) == sample_rate
    assert rendered[int(0.1 * sample_rate)] == pytest.approx(0.4, abs=2e-4)
    assert rendered[int(0.5 * sample_rate)] == pytest.approx(0.8 / np.sqrt(2), abs=2e-4)


def test_assembly_rejects_unknown_overlap_policy(tmp_path: Path) -> None:
    source = _write_constant(tmp_path / "speech.wav", 0.2, 0.2, 8000)
    with pytest.raises(ValueError, match="Unsupported overlap policy"):
        assemble_voice_track(
            [(Segment(id=1, start=0.0, end=0.2, text="one"), source)],
            tmp_path / "voice.wav",
            total_duration=0.2,
            sample_rate=8000,
            overlap_policy="overwrite",
        )


def test_assembly_never_spills_non_overlap_audio_into_next_speaker(tmp_path: Path) -> None:
    sample_rate = 8000
    first = _write_constant(tmp_path / "first.wav", 0.4, 0.9, sample_rate)
    second = _write_constant(tmp_path / "second.wav", 0.2, 0.4, sample_rate)
    segments = [
        (Segment(id=1, start=0.0, end=0.6, text="one", speaker="A"), first),
        (Segment(id=2, start=0.6, end=1.0, text="two", speaker="B"), second),
    ]

    output = assemble_voice_track(
        segments,
        tmp_path / "voice.wav",
        total_duration=1.0,
        sample_rate=sample_rate,
        overlap_policy="sum",
    )
    rendered, _sr = sf.read(output, dtype="float32")

    assert rendered[int(0.5 * sample_rate)] == pytest.approx(0.4, abs=2e-4)
    assert rendered[int(0.7 * sample_rate)] == pytest.approx(0.2, abs=2e-4)


def test_assembly_uses_bounded_blocks_for_long_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_rate = 1000
    source = _write_constant(tmp_path / "speech.wav", 0.2, 0.5, sample_rate)
    segment = Segment(id=1, start=5.0, end=5.5, text="one")
    allocations: list[int] = []
    original_zeros = media.np.zeros

    def tracked_zeros(shape, *args, **kwargs):
        if isinstance(shape, int):
            allocations.append(shape)
        return original_zeros(shape, *args, **kwargs)

    monkeypatch.setattr(media.np, "zeros", tracked_zeros)

    output = assemble_voice_track(
        [(segment, source)],
        tmp_path / "voice.wav",
        total_duration=10.0,
        sample_rate=sample_rate,
        block_seconds=0.25,
    )
    rendered, sr = sf.read(output, dtype="float32")

    assert sr == sample_rate
    assert len(rendered) == sample_rate * 10
    assert max(allocations) <= 250
    assert rendered[int(5.1 * sample_rate)] == pytest.approx(0.2, abs=2e-4)


def test_mix_graph_can_enable_gentle_sidechain_ducking() -> None:
    graph = build_mix_filter_graph(
        background_gain_db=0.0,
        voice_gain_db=1.5,
        final_lufs=-14.0,
        true_peak_db=-1.5,
        duck_background=True,
    )
    assert "asplit=2[vo_mix][vo_key]" in graph
    assert "sidechaincompress=" in graph
    assert "loudnorm=I=-14.0:TP=-1.5" in graph


def test_mix_graph_can_use_measured_two_pass_loudnorm() -> None:
    graph = build_mix_filter_graph(
        background_gain_db=-2.0,
        voice_gain_db=1.0,
        final_lufs=-14.0,
        true_peak_db=-1.5,
        loudnorm_measurements={
            "input_i": -20.4,
            "input_tp": -3.2,
            "input_lra": 4.1,
            "input_thresh": -31.0,
            "target_offset": 0.2,
        },
    )

    assert "measured_I=-20.4" in graph
    assert "measured_TP=-3.2" in graph
    assert "measured_LRA=4.1" in graph
    assert "measured_thresh=-31.0" in graph
    assert "offset=0.2:linear=true" in graph


def test_analyze_mix_loudnorm_parses_first_pass_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stderr = '''
        {
          "input_i" : "-20.40",
          "input_tp" : "-3.20",
          "input_lra" : "4.10",
          "input_thresh" : "-31.00",
          "target_offset" : "0.20"
        }
        '''

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = dict(kwargs)
        return Result()

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    background = tmp_path / "background.wav"
    voice = tmp_path / "voice.wav"

    measurements = analyze_mix_loudnorm(
        background,
        voice,
        background_gain_db=-2.0,
        voice_gain_db=1.0,
        final_lufs=-14.0,
        true_peak_db=-1.5,
        duck_background=True,
    )

    assert measurements["input_i"] == pytest.approx(-20.4)
    assert measurements["target_offset"] == pytest.approx(0.2)
    command = " ".join(str(value) for value in captured["args"])
    assert "print_format=json" in command
    assert "sidechaincompress=" in command
    assert "[0:a]volume=-2.0dB[bg]" in command
    assert "[1:a]volume=1.0dB,asplit=2[vo_mix][vo_key]" in command
    assert "[2:a]" not in command


def test_measure_mix_metrics_reports_loudness_and_true_peak(tmp_path: Path) -> None:
    sample_rate = 48000
    time = np.arange(sample_rate, dtype=np.float32) / sample_rate
    source = tmp_path / "tone.wav"
    sf.write(source, 0.1 * np.sin(2 * np.pi * 440 * time), sample_rate)

    metrics = measure_mix_metrics(source)

    assert np.isfinite(float(metrics["integrated_lufs"]))
    assert np.isfinite(float(metrics["true_peak_db"]))
    assert float(metrics["true_peak_db"]) < 0.0
    assert metrics["clipping_risk"] is False


def test_mux_reserves_aac_true_peak_headroom_without_changing_final_target_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, float | list[str] | None] = {}

    def fake_analyze(*_args, true_peak_db: float, **_kwargs):
        seen["analysis_true_peak_db"] = true_peak_db
        return {
            "input_i": -20.4,
            "input_tp": -3.2,
            "input_lra": 4.1,
            "input_thresh": -31.0,
            "target_offset": 0.2,
        }

    def fake_graph(*, true_peak_db: float, output_peak_ceiling_db: float | None = None, **_kwargs):
        seen["graph_true_peak_db"] = true_peak_db
        seen["output_peak_ceiling_db"] = output_peak_ceiling_db
        return "[1:a][2:a]amix=inputs=2[outa]"

    monkeypatch.setattr(media, "analyze_mix_loudnorm", fake_analyze)
    monkeypatch.setattr(media, "build_mix_filter_graph", fake_graph)
    monkeypatch.setattr(media, "_run_ffmpeg", lambda args: seen.setdefault("ffmpeg", list(args)))

    mux_dubbed_video(
        tmp_path / "video.mp4",
        tmp_path / "background.wav",
        tmp_path / "voice.wav",
        tmp_path / "output.mp4",
        background_gain_db=0.0,
        voice_gain_db=1.5,
        final_lufs=-14.0,
        true_peak_db=-1.5,
    )

    assert seen["analysis_true_peak_db"] == pytest.approx(-1.5)
    assert seen["graph_true_peak_db"] == pytest.approx(-1.5)
    assert seen["output_peak_ceiling_db"] == pytest.approx(-2.25)
    assert "aac" in seen["ffmpeg"]


def test_mix_graph_can_add_codec_peak_limiter_after_loudnorm() -> None:
    graph = build_mix_filter_graph(
        background_gain_db=0.0,
        voice_gain_db=1.5,
        final_lufs=-14.0,
        true_peak_db=-1.5,
        output_peak_ceiling_db=-2.5,
    )

    assert "loudnorm=I=-14.0:TP=-1.5" in graph
    assert "aresample=48000,alimiter=limit=0.74989421" in graph
    assert ":attack=0.1:release=10:level=false:latency=true[outa]" in graph


def test_segment_qa_plan_preserves_repair_reason_and_priority() -> None:
    timing = evaluate_segment_qa(
        2,
        "Xin chào bạn",
        "Xin chào bạn",
        target_duration=1.0,
        actual_duration=1.2,
    )
    critical = evaluate_segment_qa(
        1,
        "Bạn không được bán 25,5 BTC",
        "Bạn được bán 25 5 BTC",
        target_duration=2.0,
        actual_duration=2.0,
        glossary_terms=["BTC"],
    )

    plan = selective_repair_plan([timing, critical])

    assert [item["segment_id"] for item in plan] == [1, 2]
    assert plan[0]["action"] == "pronunciation_retry"
    assert plan[0]["reasons"] == ["missing_critical"]
    assert plan[0]["missing_critical"] == ["không"]
    assert plan[1]["reasons"] == ["timing_overflow"]


@pytest.mark.parametrize(
    ("expected", "actual", "glossary_terms", "missing_token"),
    [
        ("Bạn không được bán BTC", "Bạn được bán BTC", ["BTC"], "không"),
        ("Bạn cần đóng vị thế", "Bạn đóng vị thế", [], "cần"),
        ("Giữ 25 BTC", "Giữ BTC", ["BTC"], "25"),
        ("Mô hình Wyckoff đã xác nhận", "Mô hình đã xác nhận", ["Wyckoff"], "wyckoff"),
    ],
)
def test_segment_qa_catches_known_bad_critical_omissions(
    expected: str,
    actual: str,
    glossary_terms: list[str],
    missing_token: str,
) -> None:
    result = evaluate_segment_qa(
        1,
        expected,
        actual,
        target_duration=1.0,
        actual_duration=1.0,
        glossary_terms=glossary_terms,
    )

    assert result.passed is False
    assert result.action == "pronunciation_retry"
    assert "missing_critical" in result.reasons
    assert missing_token in result.missing_critical


def test_segment_qa_per_observation_spoken_critical_terms_detect_real_omission() -> None:
    report = run_segment_qa_cycle(
        [
            SegmentQAObservation(
                segment_id=1,
                expected="ép vi gi hai mươi lăm",
                actual="ép vi gi",
                target_duration=1.0,
                actual_duration=1.0,
                critical_terms=("ép vi gi", "hai mươi lăm"),
            )
        ],
        repair_actions=(),
    )

    item = report["initial"][0]
    assert item["action"] == "pronunciation_retry"
    assert item["missing_critical"] == ["hai mươi lăm"]
    assert item["critical_terms"] == ["ép vi gi", "hai mươi lăm"]


@pytest.mark.parametrize(
    ("expected", "actual", "critical_terms"),
    [
        ("Nếu hồi tôi hai mươi tuổi", "Nếu hồi tôi 20 tuổi", ("hai mươi",)),
        ("Chúa cho ta bảy mươi năm", "Chúa cho ta 70 năm", ("bảy mươi",)),
        (
            "mỗi ngày 1.440 phút, mỗi tuần một trăm sáu mươi tám giờ",
            "mỗi ngày 1440 phút, mỗi tuần 168 giờ",
            ("một trăm sáu mươi tám",),
        ),
    ],
)
def test_segment_qa_treats_digit_and_spoken_number_as_asr_equivalents(
    expected: str,
    actual: str,
    critical_terms: tuple[str, ...],
) -> None:
    report = run_segment_qa_cycle(
        [
            SegmentQAObservation(
                segment_id=1,
                expected=expected,
                actual=actual,
                target_duration=2.0,
                actual_duration=2.0,
                critical_terms=critical_terms,
            )
        ],
        repair_actions=(),
    )

    item = report["final"][0]
    assert item["missing_critical"] == []
    assert item["similarity"] >= 0.95
    assert item["passed"] is True


@pytest.mark.parametrize(
    ("expected", "actual", "critical_terms"),
    [
        (
            "Chuyển sang khung mười lăm phút cho nhanh, giá tiến rất sát take profit.",
            "Chuyển sang 0.15 phút cho nhanh, giá tiến rất sát take profit.",
            ("mười lăm",),
        ),
        (
            "Nếu chuyển sang khung bốn giờ, đôi khi bạn sẽ thấy trường hợp thế này.",
            "Nếu chuyển sang 04 giờ, đôi khi bạn sẽ thấy trường hợp thế này.",
            ("bốn",),
        ),
    ],
)
def test_segment_qa_treats_whisper_zero_prefix_as_critical_number_alias(
    expected: str,
    actual: str,
    critical_terms: tuple[str, ...],
) -> None:
    result = evaluate_segment_qa(
        1,
        expected,
        actual,
        target_duration=2.0,
        actual_duration=2.0,
        glossary_terms=critical_terms,
    )

    assert result.missing_critical == []
    assert result.similarity >= 0.78
    assert result.passed is True


@pytest.mark.parametrize("actual", ["thoát ở break event", "thoát ở break a van"])
def test_segment_qa_treats_narrow_whisper_english_split_as_critical_alias(actual: str) -> None:
    result = evaluate_segment_qa(
        1,
        "thoát ở breakeven",
        actual,
        target_duration=2.0,
        actual_duration=2.0,
        glossary_terms=["breakeven"],
    )

    assert result.missing_critical == []
    assert result.passed is True


def test_segment_qa_does_not_confuse_different_english_technical_term() -> None:
    result = evaluate_segment_qa(
        1,
        "thoát ở breakeven",
        "thoát ở breakout",
        target_duration=2.0,
        actual_duration=2.0,
        glossary_terms=["breakeven"],
    )

    assert result.missing_critical == ["breakeven"]
    assert result.passed is False


@pytest.mark.parametrize(
    ("expected", "actual", "glossary_terms"),
    [
        ("BẠN không nên bán.", "bạn không nên bán", []),
        ("Giá là 25,5 BTC.", "giá là 25 5 btc", ["BTC"]),
        ("Fair Value Gap đã được xác nhận!", "fair value gap đã được xác nhận", ["Fair Value Gap"]),
        ("Bạn có thể giữ vị thế", "Bạn có thể giữ vị thế.", []),
    ],
)
def test_segment_qa_keeps_clean_asr_variants_unflagged(
    expected: str,
    actual: str,
    glossary_terms: list[str],
) -> None:
    result = evaluate_segment_qa(
        1,
        expected,
        actual,
        target_duration=1.0,
        actual_duration=1.0,
        glossary_terms=glossary_terms,
    )

    assert result.passed is True
    assert result.action == "pass"
    assert result.reasons == []


def test_segment_qa_cycle_repairs_only_routed_segment() -> None:
    observations = [
        SegmentQAObservation(1, "Xin chao ban", "Xin chao ban", 1.0, 1.0),
        SegmentQAObservation(
            2,
            "Xin chao cac ban hom nay chung ta hoc",
            "Xin chao ban hom nay",
            2.0,
            2.0,
        ),
    ]
    repair_calls: list[list[int]] = []

    def repair(plan: list[dict]) -> list[SegmentQAObservation]:
        repair_calls.append([int(item["segment_id"]) for item in plan])
        return [
            SegmentQAObservation(
                2,
                observations[1].expected,
                observations[1].expected,
                2.0,
                2.0,
            )
        ]

    report = run_segment_qa_cycle(observations, repair=repair, max_repairs=2)

    assert repair_calls == [[2]]
    assert report["summary"] == {
        "segments_checked": 2,
        "initial_failed": 1,
        "repairs_attempted": 1,
        "repairs_completed": 1,
        "final_failed": 0,
        "passed": True,
    }
    assert report["repair_plan"][0]["action"] == "pronunciation_retry"
    assert report["repairs"] == [
        {
            **report["repair_plan"][0],
            "status": "completed",
        }
    ]
    assert [item["segment_id"] for item in report["final"]] == [1, 2]
    assert report["initial"][1]["actual"] == "Xin chao ban hom nay"
    assert report["final"][1]["actual"] == observations[1].expected


def test_segment_qa_cycle_retries_critical_acoustic_error_without_text_repair() -> None:
    calls = 0

    def repair(_plan: list[dict]) -> list[SegmentQAObservation]:
        nonlocal calls
        calls += 1
        return []

    report = run_segment_qa_cycle(
        [
            SegmentQAObservation(
                7,
                "Bạn không được bán 25 BTC",
                "Bạn được bán 25 BTC",
                1.5,
                1.5,
            )
        ],
        glossary_terms=["BTC"],
        repair=repair,
    )

    assert calls == 1
    assert report["repair_plan"][0]["segment_id"] == 7
    assert report["repair_plan"][0]["action"] == "pronunciation_retry"
    assert report["repair_plan"][0]["missing_critical"] == ["không"]
    assert report["repairs"] == [
        {
            **report["repair_plan"][0],
            "status": "no_result",
        }
    ]
    assert report["summary"]["final_failed"] == 1


def test_fit_audio_to_window_slows_only_when_near_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.wav"
    output = tmp_path / "output.wav"
    source.write_bytes(b"source")
    durations = {str(source): 0.95, str(output): 1.0}
    calls: list[list[str]] = []
    monkeypatch.setattr(media, "audio_duration", lambda path: durations[str(path)])
    monkeypatch.setattr(media, "_run_ffmpeg", lambda args: calls.append(list(args)))

    _path, final_duration, tempo = media.fit_audio_to_window(
        source,
        output,
        target_duration=1.0,
        max_speedup=1.25,
        min_tempo=0.92,
    )
    assert final_duration == pytest.approx(1.0)
    assert tempo == pytest.approx(0.95)
    assert any("atempo=0.950000" in value for value in calls[0])

    calls.clear()
    durations[str(source)] = 0.80
    _path, final_duration, tempo = media.fit_audio_to_window(
        source,
        output,
        target_duration=1.0,
        max_speedup=1.25,
        min_tempo=0.92,
    )
    assert final_duration == pytest.approx(0.80)
    assert tempo == pytest.approx(1.0)
    assert all("atempo=" not in value for value in calls[0])

def test_risk_acoustic_qa_selection_keeps_risk_and_small_deterministic_sample() -> None:
    selection = select_acoustic_qa_segment_ids(
        range(20),
        risk_ids={3, 7, 12},
        scope="risk",
        sample_ratio=0.10,
        sample_min=2,
        sample_max=4,
    )

    assert selection["scope"] == "risk"
    assert selection["risk_segment_ids"] == [3, 7, 12]
    assert len(selection["sampled_segment_ids"]) == 2
    assert set(selection["risk_segment_ids"]).issubset(selection["selected_segment_ids"])
    assert len(selection["selected_segment_ids"]) == 5


def test_full_acoustic_qa_selection_preserves_all_segments() -> None:
    selection = select_acoustic_qa_segment_ids(
        [9, 2, 5],
        risk_ids={2},
        scope="all",
    )

    assert selection["selected_segment_ids"] == [9, 2, 5]
    assert selection["sampled_segment_ids"] == []
