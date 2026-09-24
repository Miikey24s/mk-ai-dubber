from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import vi_dubber.tts as tts
from vi_dubber.media import assemble_voice_track, voice_track_metrics
from vi_dubber.metrics import MetricsRecorder
from vi_dubber.qa import evaluate_segment_qa, selective_repair_ids
from vi_dubber.reference import rank_reference_candidates
from vi_dubber.timing import allocate_timing_windows, timing_action
from vi_dubber.types import Segment


def test_reference_ranking_rejects_overlap_and_prefers_clean_audio(tmp_path: Path) -> None:
    sample_rate = 48000
    seconds = 12
    audio = np.zeros(sample_rate * seconds, dtype=np.float32)
    rng = np.random.default_rng(7)
    audio[: sample_rate * 5] = rng.normal(0.0, 0.002, sample_rate * 5)
    t = np.arange(sample_rate * 5, dtype=np.float32) / sample_rate
    audio[: sample_rate * 5] += 0.20 * np.sin(2 * np.pi * 190 * t)
    audio[sample_rate * 6 : sample_rate * 11] = 0.999
    vocals = tmp_path / "vocals.wav"
    sf.write(vocals, audio, sample_rate)

    segments = [
        Segment(id=1, start=0.0, end=5.0, text="clean", speaker="A"),
        Segment(id=2, start=6.0, end=11.0, text="clipped", speaker="A"),
    ]
    ranked = rank_reference_candidates(vocals, segments)
    assert ranked[0].segment_id == 1
    assert ranked[0].clipping_ratio < ranked[1].clipping_ratio


def test_reference_clip_selector_uses_acoustic_rank_and_3_to_8_second_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_rate = 48000
    audio = np.zeros(sample_rate * 14, dtype=np.float32)
    audio[: sample_rate * 5] = 0.999
    t = np.arange(sample_rate * 4, dtype=np.float32) / sample_rate
    audio[sample_rate * 6 : sample_rate * 10] = 0.2 * np.sin(2 * np.pi * 180 * t)
    vocals = tmp_path / "vocals.wav"
    sf.write(vocals, audio, sample_rate)

    clipped_longer = Segment(id=1, start=0.0, end=5.0, text="clipped", speaker="A")
    clean = Segment(id=2, start=6.0, end=10.0, text="clean", speaker="A")
    too_short = Segment(id=3, start=10.0, end=12.9, text="short", speaker="A")
    too_long = Segment(id=4, start=0.0, end=8.1, text="long", speaker="B")
    clips: list[tuple[float, float, Path]] = []

    def fake_clip(_source, target, *, start, duration):
        clips.append((start, duration, target))
        target.write_bytes(b"reference")

    monkeypatch.setattr(tts, "clip_audio", fake_clip)
    receipts: dict[str, object] = {}
    refs = tts.build_reference_clips(
        vocals,
        [clipped_longer, clean, too_short, too_long],
        tmp_path / "refs",
        selection_receipts=receipts,
    )

    assert set(refs) == {"A"}
    assert clips[0][0] == pytest.approx(6.1)
    assert clips[0][1] == pytest.approx(3.8)
    assert receipts["A"]["selected_segment_id"] == 2
    assert receipts["A"]["fallback"] is None
    assert receipts["A"]["candidates"][0]["segment_id"] == 2
    assert receipts["B"]["selected_segment_id"] is None


def test_timing_allocator_borrows_only_adjacent_silence() -> None:
    segments = [
        Segment(id=1, start=1.0, end=2.0, text="a"),
        Segment(id=2, start=2.6, end=3.6, text="b"),
        Segment(id=3, start=3.5, end=4.5, text="c", overlap=True),
    ]
    windows = allocate_timing_windows(segments, max_borrow_seconds=0.2)
    assert windows[1].borrowed_after == pytest.approx(0.2)
    assert windows[2].borrowed_before == pytest.approx(0.2)
    assert windows[3].borrowed_before == 0.0
    assert timing_action(1.4, 1.0, max_speedup=1.25, rewrite_threshold=1.30) == "rewrite"


def test_assembly_optional_fade_and_metrics(tmp_path: Path) -> None:
    sample_rate = 48000
    source = tmp_path / "speech.wav"
    sf.write(source, np.ones(sample_rate, dtype=np.float32) * 0.5, sample_rate)
    segment = Segment(id=1, start=0.0, end=1.0, text="a")
    output = assemble_voice_track([(segment, source)], tmp_path / "voice.wav", 1.0, sample_rate, fade_ms=10)
    rendered, _ = sf.read(output, dtype="float32")
    assert abs(float(rendered[0])) < 0.01
    assert abs(float(rendered[500])) > abs(float(rendered[1]))
    metrics = voice_track_metrics(output)
    assert metrics["clipped_samples"] == 0
    assert 0.45 < float(metrics["peak"]) < 0.55


def test_segment_qa_routes_critical_and_timing_repairs() -> None:
    critical = evaluate_segment_qa(
        1,
        "Bạn không được bán 25 cổ phiếu",
        "Bạn được bán cổ phiếu",
        target_duration=2.0,
        actual_duration=2.0,
    )
    timing = evaluate_segment_qa(
        2,
        "Xin chào bạn",
        "Xin chào bạn",
        target_duration=1.0,
        actual_duration=1.2,
    )
    routed = selective_repair_ids([critical, timing])
    assert critical.action == "pronunciation_retry"
    assert "không" in critical.missing_critical
    assert timing.action == "timing_rewrite"
    assert routed == {"pronunciation_retry": [1], "timing_rewrite": [2]}


class _Translator:
    def __init__(self, candidate: str = "rut gon") -> None:
        self.candidate = candidate

    def rewrite_batch(self, batch, _glossary):
        return {segment.id: self.candidate for segment, *_rest in batch}


def _install_batch_vieneu(
    monkeypatch: pytest.MonkeyPatch,
    durations: dict[str, float],
    calls: list[tuple[str, int]],
    *,
    fail_batch: bool = False,
):
    class FakeVieneu:
        def __init__(self, **_kwargs):
            pass

        def infer(self, text, **_kwargs):
            calls.append(("single", 1))
            return text

        def infer_batch(self, texts, **_kwargs):
            calls.append(("batch", len(texts)))
            if fail_batch:
                raise RuntimeError("synthetic batch failure")
            return list(texts)

        def save(self, audio, target):
            path = Path(target)
            path.write_bytes(str(audio).encode("utf-8") + b"x" * 2048)
            duration = 0.8 if str(audio) == "spoken override" else 2.0
            durations[str(path)] = duration
            if path.stem.endswith(".partial"):
                final = path.with_name(f"{path.stem.removesuffix('.partial')}{path.suffix}")
                durations[str(final)] = duration

    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=FakeVieneu))


def test_tts_batch_hook_rewrite_verifier_and_pronunciation_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    calls: list[tuple[str, int]] = []
    _install_batch_vieneu(monkeypatch, durations, calls)
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, min(generated, target_duration), min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    metrics = MetricsRecorder(enable_cuda=False)
    segments = [
        Segment(id=1, start=0.0, end=1.0, text="source", vi="subtitle one", speaker="A"),
        Segment(id=2, start=1.2, end=2.2, text="source", vi="subtitle two", speaker="A"),
    ]
    tts.synthesize_segments(
        segments,
        tmp_path / "tts",
        {"backend": "onnx", "device": "cpu", "precision": "fp32", "batch_size": 2},
        {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25},
        _Translator("candidate rejected"),
        {},
        {},
        rewrite_verifier=lambda _segment, candidate: candidate != "candidate rejected",
        tts_text_mapper={1: "spoken override", 2: "spoken override"},
        metrics=metrics,
    )
    assert ("batch", 2) in calls
    assert [segment.vi for segment in segments] == ["subtitle one", "subtitle two"]
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["tts_batches"] == 1
    assert snapshot["counters"]["tts_inferences"] == 2


def test_tts_rewrite_verifier_rejects_candidate_before_segment_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    calls: list[tuple[str, int]] = []
    _install_batch_vieneu(monkeypatch, durations, calls)
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, min(generated, target_duration), min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    segment = Segment(id=1, start=0.0, end=1.0, text="source", vi="original subtitle", speaker="A")
    seen: list[tuple[str, str]] = []

    def verifier(current: Segment, candidate: str) -> bool:
        seen.append((current.vi, candidate))
        return False

    tts.synthesize_segments(
        [segment],
        tmp_path / "tts",
        {"backend": "onnx", "device": "cpu", "precision": "fp32"},
        {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25},
        _Translator("candidate rejected"),
        {},
        {},
        rewrite_verifier=verifier,
    )
    assert seen == [("original subtitle", "candidate rejected")]
    assert segment.vi == "original subtitle"


def test_tts_batch_failure_falls_back_to_serial_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    calls: list[tuple[str, int]] = []
    _install_batch_vieneu(monkeypatch, durations, calls, fail_batch=True)
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, min(generated, target_duration), min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    segments = [
        Segment(id=1, start=0.0, end=3.0, text="source", vi="one", speaker="A"),
        Segment(id=2, start=3.2, end=6.2, text="source", vi="two", speaker="A"),
    ]
    rendered, _stats = tts.synthesize_segments(
        segments,
        tmp_path / "tts",
        {"backend": "onnx", "device": "cpu", "precision": "fp32", "batch_size": 2},
        {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25},
        _Translator(),
        {},
        {},
    )
    assert ("batch", 2) in calls
    assert calls.count(("single", 1)) == 2
    assert all(path.exists() for _segment, path in rendered)


def test_tts_crash_mid_run_preserves_completed_raw_and_resume_skips_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    infer_calls: list[str] = []

    class CrashOnceVieneu:
        failed = False

        def __init__(self, **_kwargs):
            pass

        def infer(self, text, **_kwargs):
            infer_calls.append(str(text))
            return str(text)

        def save(self, audio, target):
            path = Path(target)
            path.write_bytes(str(audio).encode("utf-8") + b"x" * 2048)
            if str(audio) == "two" and not CrashOnceVieneu.failed:
                CrashOnceVieneu.failed = True
                raise RuntimeError("synthetic TTS crash at segment 2")
            durations[str(path)] = 1.0
            if path.stem.endswith(".partial"):
                final = path.with_name(f"{path.stem.removesuffix('.partial')}{path.suffix}")
                durations[str(final)] = 1.0

    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=CrashOnceVieneu))
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, min(generated, target_duration), min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    segments = [
        Segment(id=1, start=0.0, end=2.0, text="source one", vi="one", speaker="A"),
        Segment(id=2, start=2.2, end=4.2, text="source two", vi="two", speaker="A"),
    ]
    output_dir = tmp_path / "tts"
    config = {"backend": "onnx", "device": "cpu", "precision": "fp32", "batch_size": 1}
    timing = {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25}

    with pytest.raises(RuntimeError, match="synthetic TTS crash at segment 2"):
        tts.synthesize_segments(
            segments,
            output_dir,
            config,
            timing,
            _Translator(),
            {},
            {},
        )

    first_raw = output_dir / "00001_raw.wav"
    first_meta = output_dir / "00001_raw.meta.json"
    second_raw = output_dir / "00002_raw.wav"
    second_partial = output_dir / "00002_raw.partial.wav"
    assert first_raw.is_file()
    assert first_meta.is_file()
    assert not second_raw.exists()
    assert not second_partial.exists()
    assert infer_calls == ["one", "two"]

    rendered, _stats = tts.synthesize_segments(
        segments,
        output_dir,
        config,
        timing,
        _Translator(),
        {},
        {},
    )

    assert infer_calls.count("one") == 1
    assert infer_calls.count("two") == 2
    assert second_raw.is_file()
    assert (output_dir / "00002_raw.meta.json").is_file()
    assert all(path.is_file() for _segment, path in rendered)
