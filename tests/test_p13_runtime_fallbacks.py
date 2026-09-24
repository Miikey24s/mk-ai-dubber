import sys
import types
from pathlib import Path

import pytest

import vi_dubber.asr as asr_module
import vi_dubber.separation as separation_module


def test_cuda_oom_retries_with_conservative_batch_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    cache_releases = 0

    class FakeModel:
        def transcribe(self, audio, batch_size):
            calls.append(batch_size)
            if batch_size > 4:
                raise RuntimeError("CUDA out of memory while allocating tensor")
            return {"segments": [{"text": "ok"}]}

    def release_cuda(*objects: object) -> None:
        nonlocal cache_releases
        cache_releases += 1

    monkeypatch.setattr(asr_module, "_release_cuda", release_cuda)

    result = asr_module._transcribe_with_batch_fallback(
        FakeModel(),
        [0.0],
        batch_size=6,
        device="cuda",
    )

    assert result["segments"][0]["text"] == "ok"
    assert calls == [6, 4]
    assert cache_releases == 1


def test_batch_fallback_does_not_hide_non_oom_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class FakeModel:
        def transcribe(self, audio, batch_size):
            calls.append(batch_size)
            raise RuntimeError("decoder contract failed")

    monkeypatch.setattr(
        asr_module,
        "_release_cuda",
        lambda *objects: (_ for _ in ()).throw(AssertionError("must not clear cache")),
    )

    with pytest.raises(RuntimeError, match="decoder contract failed"):
        asr_module._transcribe_with_batch_fallback(
            FakeModel(),
            [0.0],
            batch_size=8,
            device="cuda",
        )

    assert calls == [8]


def test_batch_fallback_rejects_non_positive_batch_size() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        asr_module._transcribe_with_batch_fallback(
            object(),
            [0.0],
            batch_size=0,
            device="cuda",
        )


def test_transcribe_and_align_releases_loaded_model_when_alignment_load_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[object] = []

    class FakeModel:
        def transcribe(self, audio, batch_size):
            return {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]}

    model = FakeModel()
    fake_whisperx = types.ModuleType("whisperx")
    fake_whisperx.load_model = lambda *args, **kwargs: model
    fake_whisperx.load_audio = lambda path: [0.0]
    fake_whisperx.load_align_model = lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("alignment load failed")
    )
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setattr(asr_module, "configure_runtime", lambda: None)
    monkeypatch.setattr(asr_module, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_module, "_release_cuda", lambda *objects: released.extend(objects))

    with pytest.raises(RuntimeError, match="alignment load failed"):
        asr_module.transcribe_and_align(
            tmp_path / "audio.wav",
            {"device": "cpu", "compute_type": "int8", "model": "tiny", "batch_size": 1},
        )

    assert released == [model, None, None]


def test_diarization_without_token_fails_before_runtime_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        asr_module,
        "configure_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("runtime should not initialize")),
    )

    with pytest.raises(RuntimeError, match="HUGGINGFACE_TOKEN is missing"):
        asr_module.transcribe_and_align(
            tmp_path / "audio.wav",
            {"model": "large-v3"},
            diarize=True,
        )


def test_separator_cleans_cuda_cache_when_separation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gc_calls = 0
    empty_cache_calls = 0

    class FakeSeparator:
        def __init__(self, **kwargs):
            pass

        def load_model(self, model_filename):
            pass

        def separate(self, audio_file_path):
            raise RuntimeError("separator failed")

    fake_package = types.ModuleType("audio_separator")
    fake_separator_module = types.ModuleType("audio_separator.separator")
    fake_separator_module.Separator = FakeSeparator
    fake_torch = types.ModuleType("torch")

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def empty_cache() -> None:
            nonlocal empty_cache_calls
            empty_cache_calls += 1

    fake_torch.cuda = FakeCuda()
    monkeypatch.setitem(sys.modules, "audio_separator", fake_package)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", fake_separator_module)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    def collect() -> int:
        nonlocal gc_calls
        gc_calls += 1
        return 0

    monkeypatch.setattr(separation_module.gc, "collect", collect)

    with pytest.raises(RuntimeError, match="separator failed"):
        separation_module.separate_dialogue(
            tmp_path / "input.wav",
            tmp_path / "out",
            tmp_path / "models",
            "model.ckpt",
        )

    assert gc_calls == 1
    assert empty_cache_calls == 1
