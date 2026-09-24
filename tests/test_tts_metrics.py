from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import vi_dubber.tts as tts
from vi_dubber.metrics import MetricsRecorder
from vi_dubber.types import Segment


class _Translator:
    def rewrite_batch(self, batch, _glossary):
        return {segment.id: "ngan gon" for segment, *_rest in batch}


def _install_fake_vieneu(monkeypatch: pytest.MonkeyPatch, durations: dict[str, float], *, fail=False):
    class FakeVieneu:
        def __init__(self, **_kwargs):
            pass

        def infer(self, text, **_kwargs):
            if fail:
                raise RuntimeError("synthetic tts failure")
            return text

        def save(self, audio, target):
            path = Path(target)
            path.write_bytes((str(audio).encode("utf-8") + b"x" * 2048))
            duration = 2.0 if str(audio) != "ngan gon" else 0.8
            durations[str(path)] = duration
            if path.stem.endswith(".partial"):
                final = path.with_name(f"{path.stem.removesuffix('.partial')}{path.suffix}")
                durations[str(final)] = duration

    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=FakeVieneu))


def test_synthesize_segments_records_non_overlapping_tts_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    _install_fake_vieneu(monkeypatch, durations)
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, min(generated, target_duration), min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)

    segment = Segment(id=1, start=0.0, end=1.0, text="source", vi="cau rat dai")
    metrics = MetricsRecorder(enable_cuda=False)
    rendered, stats = tts.synthesize_segments(
        [segment],
        tmp_path / "tts",
        {"backend": "onnx", "device": "cpu", "precision": "fp32"},
        {
            "segment_pad_ms": 0,
            "rewrite_threshold": 1.25,
            "rewrite_batch_size": 24,
            "allow_batch_rewrite": True,
            "max_speedup": 1.25,
        },
        _Translator(),
        {},
        {},
        metrics=metrics,
    )
    metrics.finish()
    snapshot = metrics.snapshot()

    assert rendered[0][0].vi == "ngan gon"
    assert stats[0].rewrites == 1
    assert set(snapshot["stages"]) == {
        "tts_pass_1",
        "rewrite_generation",
        "tts_rewrite",
        "timing_fit",
    }
    assert all(item["calls"] == 1 for item in snapshot["stages"].values())
    assert all(item["failed_calls"] == 0 for item in snapshot["stages"].values())
    assert snapshot["counters"]["tts_inferences"] == 2
    assert int(snapshot["counters"].get("tts_batches", 0)) == 0


def test_manual_review_locked_segment_is_not_automatically_rewritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    _install_fake_vieneu(monkeypatch, durations)
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, min(generated, target_duration), min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    segment = Segment(id=7, start=0.0, end=1.0, text="source", vi="ban sua cua nguoi dung")

    rendered, stats = tts.synthesize_segments(
        [segment],
        tmp_path / "tts",
        {"backend": "onnx", "device": "cpu", "precision": "fp32"},
        {
            "segment_pad_ms": 0,
            "rewrite_threshold": 1.25,
            "rewrite_batch_size": 24,
            "allow_batch_rewrite": True,
            "max_speedup": 1.25,
        },
        _Translator(),
        {},
        {},
        locked_segment_ids={7},
    )

    assert rendered[0][0].vi == "ban sua cua nguoi dung"
    assert stats[0].rewrites == 0


def test_initial_tts_failure_is_counted_and_propagated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    _install_fake_vieneu(monkeypatch, durations, fail=True)
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])
    metrics = MetricsRecorder(enable_cuda=False)
    segment = Segment(id=1, start=0.0, end=1.0, text="source", vi="xin chao")

    with pytest.raises(RuntimeError, match="synthetic tts failure"):
        tts.synthesize_segments(
            [segment],
            tmp_path / "tts",
            {"backend": "onnx", "device": "cpu", "precision": "fp32"},
            {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25},
            _Translator(),
            {},
            {},
            metrics=metrics,
        )

    stage = metrics.snapshot()["stages"]["tts_pass_1"]
    assert stage["calls"] == 1
    assert stage["failed_calls"] == 1


def test_cuda_pytorch_uses_float16_and_falls_back_to_cpu_onnx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    init_calls: list[dict[str, object]] = []

    class FakeVieneu:
        def __init__(self, **kwargs):
            self.backend = str(kwargs["backend"])
            init_calls.append(dict(kwargs))

        def infer(self, text, **_kwargs):
            if self.backend == "pytorch":
                raise RuntimeError("synthetic cuda inference failure")
            return text

        def save(self, audio, target):
            path = Path(target)
            path.write_bytes(str(audio).encode("utf-8") + b"x" * 2048)
            durations[str(path)] = 1.0
            if path.stem.endswith(".partial"):
                final = path.with_name(f"{path.stem.removesuffix('.partial')}{path.suffix}")
                durations[str(final)] = 1.0

    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=FakeVieneu))
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, generated, min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    metrics = MetricsRecorder(enable_cuda=False)
    segment = Segment(id=1, start=0.0, end=3.0, text="source", vi="xin chao", speaker="A")

    rendered, _stats = tts.synthesize_segments(
        [segment],
        tmp_path / "tts",
        {"backend": "pytorch", "device": "cuda", "precision": "fp16", "batch_size": 4},
        {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25},
        _Translator(),
        {},
        {},
        metrics=metrics,
    )

    assert init_calls[0]["backend"] == "pytorch"
    assert init_calls[0]["device"] == "cuda"
    assert init_calls[0]["dtype"] == "float16"
    assert init_calls[1] == {
        "mode": "v3turbo",
        "backend": "onnx",
        "device": "cpu",
        "precision": "fp32",
    }
    assert metrics.snapshot()["counters"]["tts_cpu_fallbacks"] == 1
    assert rendered[0][1].exists()


def test_cuda_engine_init_failure_falls_back_to_cpu_onnx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    init_calls: list[dict[str, object]] = []

    class FakeVieneu:
        def __init__(self, **kwargs):
            self.backend = str(kwargs["backend"])
            init_calls.append(dict(kwargs))
            if self.backend == "pytorch":
                raise RuntimeError("synthetic cuda init failure")

        def infer(self, text, **_kwargs):
            return text

        def save(self, audio, target):
            path = Path(target)
            path.write_bytes(str(audio).encode("utf-8") + b"x" * 2048)
            durations[str(path)] = 1.0
            if path.stem.endswith(".partial"):
                final = path.with_name(f"{path.stem.removesuffix('.partial')}{path.suffix}")
                durations[str(final)] = 1.0

    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=FakeVieneu))
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, generated, min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    segment = Segment(id=1, start=0.0, end=3.0, text="source", vi="xin chao", speaker="A")

    rendered, _stats = tts.synthesize_segments(
        [segment],
        tmp_path / "tts",
        {"backend": "pytorch", "device": "cuda", "batch_size": 4},
        {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25},
        _Translator(),
        {},
        {},
    )

    assert init_calls[0]["dtype"] == "float16"
    assert init_calls[1]["backend"] == "onnx"
    assert init_calls[1]["device"] == "cpu"
    assert rendered[0][1].exists()


def test_raw_checkpoint_reuses_matching_text_and_invalidates_changed_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    infer_calls: list[str] = []

    class FakeVieneu:
        def __init__(self, **_kwargs):
            pass

        def infer(self, text, **_kwargs):
            infer_calls.append(str(text))
            return text

        def save(self, audio, target):
            path = Path(target)
            path.write_bytes(str(audio).encode("utf-8") + b"x" * 2048)
            durations[str(path)] = 1.0
            if path.stem.endswith(".partial"):
                final = path.with_name(f"{path.stem.removesuffix('.partial')}{path.suffix}")
                durations[str(final)] = 1.0

    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=FakeVieneu))
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, generated, min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    output_dir = tmp_path / "tts"
    config = {"backend": "onnx", "device": "cpu", "precision": "fp32"}
    timing = {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25}
    first = Segment(id=1, start=0.0, end=3.0, text="source", vi="xin chao", speaker="A")

    tts.synthesize_segments([first], output_dir, config, timing, _Translator(), {}, {})
    assert infer_calls == ["xin chao"]

    same = Segment(id=1, start=0.0, end=3.0, text="source", vi="xin chao", speaker="A")
    tts.synthesize_segments([same], output_dir, config, timing, _Translator(), {}, {})
    assert infer_calls == ["xin chao"]

    changed = Segment(id=1, start=0.0, end=3.0, text="source", vi="xin chao moi", speaker="A")
    tts.synthesize_segments([changed], output_dir, config, timing, _Translator(), {}, {})
    assert infer_calls == ["xin chao", "xin chao moi"]


def test_raw_checkpoint_commits_each_successful_segment_before_later_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations: dict[str, float] = {}
    infer_calls: list[str] = []
    fail_second = {"enabled": True}

    class FakeVieneu:
        def __init__(self, **_kwargs):
            pass

        def infer(self, text, **_kwargs):
            infer_calls.append(str(text))
            if text == "second" and fail_second["enabled"]:
                raise RuntimeError("synthetic second segment failure")
            return text

        def save(self, audio, target):
            path = Path(target)
            path.write_bytes(str(audio).encode("utf-8") + b"x" * 2048)
            durations[str(path)] = 1.0
            if path.stem.endswith(".partial"):
                final = path.with_name(f"{path.stem.removesuffix('.partial')}{path.suffix}")
                durations[str(final)] = 1.0

    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=FakeVieneu))
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])
    monkeypatch.setattr(
        tts,
        "fit_audio_to_window",
        lambda raw_path, fitted_path, **_kwargs: (fitted_path, durations[str(raw_path)], 1.0),
    )
    output_dir = tmp_path / "tts"
    config = {"backend": "onnx", "device": "cpu", "precision": "fp32"}
    timing = {"segment_pad_ms": 0, "rewrite_threshold": 1.25, "max_speedup": 1.25}
    segments = [
        Segment(id=1, start=0.0, end=2.0, text="source", vi="first", speaker="A"),
        Segment(id=2, start=2.2, end=4.2, text="source", vi="second", speaker="A"),
    ]

    with pytest.raises(RuntimeError, match="synthetic second segment failure"):
        tts.synthesize_segments(segments, output_dir, config, timing, _Translator(), {}, {})

    assert (output_dir / "00001_raw.meta.json").is_file()
    assert not (output_dir / "00002_raw.meta.json").exists()
    assert infer_calls.count("first") == 1
    assert infer_calls.count("second") == 2

    fail_second["enabled"] = False
    tts.synthesize_segments(segments, output_dir, config, timing, _Translator(), {}, {})
    assert infer_calls.count("first") == 1
    assert infer_calls.count("second") == 3


def test_rewrite_generation_failure_is_observable_and_keeps_original_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTranslator:
        def rewrite_batch(self, _batch, _glossary):
            raise RuntimeError("synthetic rewrite failure")

    durations: dict[str, float] = {}
    _install_fake_vieneu(monkeypatch, durations)
    monkeypatch.setattr(tts, "audio_duration", lambda path: durations[str(path)])

    def fake_fit(raw_path, fitted_path, *, target_duration, max_speedup):
        fitted_path.write_bytes(raw_path.read_bytes())
        generated = durations[str(raw_path)]
        return fitted_path, min(generated, target_duration), min(max_speedup, generated / target_duration)

    monkeypatch.setattr(tts, "fit_audio_to_window", fake_fit)
    metrics = MetricsRecorder(enable_cuda=False)
    segment = Segment(id=1, start=0.0, end=1.0, text="source", vi="original long text")

    rendered, stats = tts.synthesize_segments(
        [segment],
        tmp_path / "tts",
        {"backend": "onnx", "device": "cpu", "precision": "fp32"},
        {
            "segment_pad_ms": 0,
            "rewrite_threshold": 1.25,
            "allow_batch_rewrite": True,
            "max_speedup": 1.25,
        },
        FailingTranslator(),
        {},
        {},
        metrics=metrics,
    )

    assert rendered[0][0].vi == "original long text"
    assert stats[0].rewrites == 0
    assert metrics.snapshot()["counters"]["rewrite_generation_failures"] == 1
