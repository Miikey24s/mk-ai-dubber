import sys
import types
from pathlib import Path

import pytest

import vi_dubber.asr as asr_module
import vi_dubber.media as media_module
import vi_dubber.separation as separation_module


def test_long_audio_uses_flac_to_avoid_riff_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = tmp_path / "long-input.wav"
    audio_path.write_bytes(b"fixture")
    monkeypatch.setattr(separation_module, "_WAV_SAFE_BYTES", 4)

    assert separation_module._output_format_for_audio(audio_path) == "FLAC"


def test_short_audio_keeps_wav_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = tmp_path / "short-input.wav"
    audio_path.write_bytes(b"x")
    monkeypatch.setattr(separation_module, "_WAV_SAFE_BYTES", 4)

    assert separation_module._output_format_for_audio(audio_path) == "WAV"


def test_flac_input_keeps_separator_output_out_of_riff() -> None:
    assert separation_module._output_format_for_audio(Path("original.flac")) == "FLAC"


def test_long_separator_uses_bounded_chunks_and_reuses_loaded_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_audio = tmp_path / "original.flac"
    input_audio.write_bytes(b"fixture")
    output_dir = tmp_path / "out"
    calls: list[Path] = []
    extracted: list[tuple[float, float, Path]] = []
    concatenated: list[tuple[list[Path], Path]] = []
    monkeypatch.setattr(separation_module, "media_duration", lambda _path: 25.0)
    monkeypatch.setattr(separation_module, "SEPARATION_CHUNK_THRESHOLD_SECONDS", 5.0)
    monkeypatch.setattr(separation_module, "SEPARATION_CHUNK_SECONDS", 10.0)

    def fake_extract(source: Path, output: Path, start: float, duration: float) -> None:
        extracted.append((start, duration, output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"chunk")

    def fake_concat(parts: list[Path], output: Path) -> None:
        concatenated.append((parts, output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"joined")

    monkeypatch.setattr(separation_module, "_audio_window_to_flac", fake_extract)
    monkeypatch.setattr(separation_module, "_concat_flac", fake_concat)

    fake_package = types.ModuleType("audio_separator")
    fake_separator_module = types.ModuleType("audio_separator.separator")
    fake_torch = types.ModuleType("torch")

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakeSeparator:
        instances = 0

        def __init__(self, **kwargs):
            type(self).instances += 1
            self.output_dir = kwargs["output_dir"]

        def load_model(self, model_filename):
            return None

        def separate(self, audio_file_path):
            calls.append(Path(audio_file_path))
            return ["Vocals.flac", "Instrumental.flac"]

    fake_separator_module.Separator = FakeSeparator
    fake_torch.cuda = FakeCuda()
    monkeypatch.setitem(sys.modules, "audio_separator", fake_package)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", fake_separator_module)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    progress: list[float] = []
    vocals, instrumental = separation_module.separate_dialogue(
        input_audio,
        output_dir,
        tmp_path / "models",
        "model.ckpt",
        progress_callback=lambda value, _message: progress.append(value),
    )

    assert FakeSeparator.instances == 1
    assert [(start, duration) for start, duration, _path in extracted] == [
        (0.0, 10.0),
        (10.0, 10.0),
        (20.0, 5.0),
    ]
    assert len(calls) == 3
    assert len(concatenated) == 2
    assert vocals.name == "original_(Vocals).flac"
    assert instrumental.name == "original_(Instrumental).flac"
    # The fake separator does not expose audio-separator's internal tqdm loop;
    # progress behavior remains covered by the existing adapter tests below.


def test_extraction_spec_and_path_are_deterministic_for_short_and_long_input(tmp_path: Path) -> None:
    short = media_module.audio_extraction_spec(60.0)
    long = media_module.audio_extraction_spec(43_000.0)

    assert short["policy"] == media_module.EXTRACT_AUDIO_POLICY_VERSION
    assert short["container"] == "WAV"
    assert short["codec"] == "pcm_s24le"
    assert long["container"] == "FLAC"
    assert long["codec"] == "flac"
    assert media_module.extracted_audio_path(tmp_path, 60.0).name == "original.wav"
    assert media_module.extracted_audio_path(tmp_path, 43_000.0).name == "original.flac"
    assert media_module.audio_extraction_spec(43_000.0) == long


def test_extract_audio_uses_atomic_container_specific_output_and_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    calls: list[list[str]] = []
    probes: list[tuple[Path, float]] = []

    def fake_ffmpeg(args: list[str]) -> None:
        calls.append(args)
        Path(args[-1]).write_bytes(b"encoded")

    def fake_probe(path: Path, expected: float) -> float:
        probes.append((path, expected))
        return expected

    monkeypatch.setattr(media_module, "_run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(media_module, "validate_extracted_audio", fake_probe)

    flac_output = media_module.extract_audio(
        video,
        tmp_path / "original.flac",
        container="FLAC",
        expected_duration_seconds=43_000.0,
    )
    wav_output = media_module.extract_audio(video, tmp_path / "short.wav")

    assert flac_output.suffix == ".flac"
    assert wav_output.suffix == ".wav"
    assert flac_output.read_bytes() == b"encoded"
    assert wav_output.read_bytes() == b"encoded"
    assert not list(tmp_path.glob(".*.partial.*"))
    assert [args[args.index("-c:a") + 1] for args in calls] == ["flac", "pcm_s24le"]
    assert probes == [(flac_output, 43_000.0)]


def test_validate_extracted_audio_rejects_truncated_or_unprobeable_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "header-only.wav"
    audio.write_bytes(b"header-only")
    monkeypatch.setattr(media_module, "media_duration", lambda _path: 14_913.0)
    monkeypatch.setattr(media_module, "audio_duration", lambda _path: 14_913.0)
    with pytest.raises(RuntimeError, match="truncated"):
        media_module.validate_extracted_audio(audio, 42_831.0)

    def fail_probe(_path: Path) -> float:
        raise RuntimeError("ffprobe failed")

    monkeypatch.setattr(media_module, "media_duration", fail_probe)
    with pytest.raises(RuntimeError, match="probe failed"):
        media_module.validate_extracted_audio(audio, 42_831.0)


def test_validate_separation_stems_rejects_a_truncated_cached_stem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vocals = tmp_path / "vocals.wav"
    background = tmp_path / "background.wav"
    vocals.write_bytes(b"vocals")
    background.write_bytes(b"background")

    def fake_validate(path: Path, expected: float) -> float:
        if path == background:
            raise RuntimeError("truncated")
        return expected

    monkeypatch.setattr(media_module, "validate_extracted_audio", fake_validate)
    with pytest.raises(RuntimeError, match="background stem"):
        media_module.validate_separation_stems(vocals, background, 42_831.0)


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


def test_separator_reports_mdxc_progress_and_restores_dependency_tqdm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress_events: list[tuple[float, str]] = []

    def original_tqdm(iterable, *args, **kwargs):
        return iterable

    fake_package = types.ModuleType("audio_separator")
    fake_separator_module = types.ModuleType("audio_separator.separator")
    fake_architectures_module = types.ModuleType("audio_separator.separator.architectures")
    fake_mdxc_module = types.ModuleType("audio_separator.separator.architectures.mdxc_separator")
    fake_mdxc_module.tqdm = original_tqdm
    fake_architectures_module.mdxc_separator = fake_mdxc_module

    class FakeSeparator:
        def __init__(self, **kwargs):
            pass

        def load_model(self, model_filename):
            pass

        def separate(self, audio_file_path):
            for _ in fake_mdxc_module.tqdm(range(200)):
                pass
            return ["Vocals.wav", "Instrumental.wav"]

    fake_separator_module.Separator = FakeSeparator
    fake_torch = types.ModuleType("torch")

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    fake_torch.cuda = FakeCuda()
    monkeypatch.setitem(sys.modules, "audio_separator", fake_package)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", fake_separator_module)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(
        sys.modules,
        "audio_separator.separator.architectures",
        fake_architectures_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "audio_separator.separator.architectures.mdxc_separator",
        fake_mdxc_module,
    )

    vocals, instrumental = separation_module.separate_dialogue(
        tmp_path / "input.wav",
        tmp_path / "out",
        tmp_path / "models",
        "model.ckpt",
        progress_callback=lambda value, message: progress_events.append((value, message)),
    )

    assert vocals.name == "Vocals.wav"
    assert instrumental.name == "Instrumental.wav"
    assert progress_events
    assert progress_events[-1][0] == pytest.approx(1.0)
    assert "200/200" in progress_events[-1][1]
    assert all(
        later[0] >= earlier[0]
        for earlier, later in zip(progress_events, progress_events[1:])
    )
    assert fake_mdxc_module.tqdm is original_tqdm


def test_separator_progress_callback_failure_does_not_fail_separation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def original_tqdm(iterable, *args, **kwargs):
        return iterable

    fake_package = types.ModuleType("audio_separator")
    fake_separator_module = types.ModuleType("audio_separator.separator")
    fake_architectures_module = types.ModuleType("audio_separator.separator.architectures")
    fake_mdxc_module = types.ModuleType("audio_separator.separator.architectures.mdxc_separator")
    fake_mdxc_module.tqdm = original_tqdm
    fake_architectures_module.mdxc_separator = fake_mdxc_module

    class FakeSeparator:
        def __init__(self, **kwargs):
            pass

        def load_model(self, model_filename):
            pass

        def separate(self, audio_file_path):
            for _ in fake_mdxc_module.tqdm(range(100)):
                pass
            return ["Vocals.wav", "Instrumental.wav"]

    fake_separator_module.Separator = FakeSeparator
    fake_torch = types.ModuleType("torch")

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    fake_torch.cuda = FakeCuda()
    monkeypatch.setitem(sys.modules, "audio_separator", fake_package)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", fake_separator_module)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(
        sys.modules,
        "audio_separator.separator.architectures",
        fake_architectures_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "audio_separator.separator.architectures.mdxc_separator",
        fake_mdxc_module,
    )

    vocals, instrumental = separation_module.separate_dialogue(
        tmp_path / "input.wav",
        tmp_path / "out",
        tmp_path / "models",
        "model.ckpt",
        progress_callback=lambda *_args: (_ for _ in ()).throw(RuntimeError("ui down")),
    )

    assert vocals.name == "Vocals.wav"
    assert instrumental.name == "Instrumental.wav"
    assert fake_mdxc_module.tqdm is original_tqdm
