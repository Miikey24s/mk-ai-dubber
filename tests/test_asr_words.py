import sys
import types
from pathlib import Path
from typing import Any

import pytest

from vi_dubber.asr import (
    _diarization_overlap_ranges,
    _diarization_speaker_hints,
    _mark_diarization_overlaps,
    _segment_from_aligned,
    transcribe_and_align_chunks,
    transcribe_text,
    transcribe_text_files,
)
from vi_dubber.longform import MacroChunk
from vi_dubber.types import SEGMENT_SCHEMA_VERSION, Segment, WordToken


def test_segment_from_aligned_preserves_word_metadata() -> None:
    segment = _segment_from_aligned(
        {
            "start": 1.0,
            "end": 3.0,
            "text": " Hello world ",
            "speaker": "SPEAKER_01",
            "avg_logprob": -0.23,
            "words": [
                {
                    "word": " Hello",
                    "start": 1.05,
                    "end": 1.52,
                    "score": 0.91,
                    "speaker": "SPEAKER_01",
                    "overlap": True,
                },
                {
                    "word": " world",
                    "start": 1.60,
                    "end": 2.12,
                    "score": 0.83,
                    "speaker": "SPEAKER_02",
                },
            ],
        },
        7,
    )

    assert segment.id == 7
    assert segment.start == 1.0
    assert segment.end == 3.0
    assert segment.text == "Hello world"
    assert segment.speaker == "SPEAKER_01"
    assert segment.avg_logprob == -0.23
    assert [(word.text, word.start, word.end, word.confidence, word.speaker) for word in segment.words] == [
        ("Hello", 1.05, 1.52, 0.91, "SPEAKER_01"),
        ("world", 1.60, 2.12, 0.83, "SPEAKER_02"),
    ]
    assert segment.words[0].overlap is True
    assert segment.words[1].overlap is False
    assert segment.overlap is True


def test_segment_from_aligned_preserves_optional_boundary_metadata() -> None:
    segment = _segment_from_aligned(
        {
            "start": 0.0,
            "end": 1.0,
            "text": "overlap",
            "overlap": True,
            "scene_id": "scene-2",
            "scene_boundary": True,
        },
        2,
    )

    assert segment.overlap is True
    assert segment.scene_id == "scene-2"
    assert segment.scene_boundary is True


def test_segment_from_aligned_derives_dominant_speaker_from_words() -> None:
    segment = _segment_from_aligned(
        {
            "text": "one two three",
            "words": [
                {"word": "one", "start": 4.0, "end": 4.2, "speaker": "SPEAKER_02"},
                {"word": "two", "start": 4.3, "end": 4.5, "speaker": "SPEAKER_01"},
                {"word": "three", "start": 4.6, "end": 4.9, "speaker": "SPEAKER_02"},
            ],
        },
        0,
    )

    assert segment.speaker == "SPEAKER_02"
    assert segment.start == 4.0
    assert segment.end == 4.9


def test_segment_from_aligned_tolerates_missing_alignment_fields() -> None:
    segment = _segment_from_aligned(
        {
            "text": "partially aligned",
            "words": [
                {"word": "partially"},
                {"word": "aligned", "start": 2.5},
            ],
        },
        3,
    )

    assert segment.start == 2.5
    assert segment.end == 2.5
    assert segment.speaker == "SPEAKER_00"
    assert segment.avg_logprob is None
    assert segment.words[0].start is None
    assert segment.words[0].end is None
    assert segment.words[0].confidence is None
    assert segment.words[1].start == 2.5
    assert segment.words[1].end is None
    assert segment.words[1].confidence is None


def test_segment_from_aligned_uses_segment_times_when_words_are_unaligned() -> None:
    segment = _segment_from_aligned(
        {
            "start": 8.0,
            "end": 9.5,
            "text": "unaligned words",
            "words": [{"word": "unaligned"}, {"word": "words"}],
        },
        1,
    )

    assert segment.start == 8.0
    assert segment.end == 9.5
    assert len(segment.words) == 2


def test_segment_serialization_is_versioned_and_legacy_compatible() -> None:
    original = Segment(
        id=4,
        start=1.0,
        end=2.5,
        text="hello world",
        speaker="SPEAKER_01",
        words=[WordToken("hello", 1.0, 1.4, 0.92, "SPEAKER_01")],
        source_segment_ids=[1, 2],
        overlap=True,
        scene_id="scene-7",
        scene_boundary=True,
    )

    payload = original.to_dict()
    assert payload["schema_version"] == SEGMENT_SCHEMA_VERSION
    assert Segment.from_dict(payload) == original

    payload.pop("schema_version")
    assert Segment.from_dict(payload) == original


def test_segment_loader_rejects_unknown_future_schema() -> None:
    payload = Segment(id=1, start=0.0, end=1.0, text="hello").to_dict()
    payload["schema_version"] = SEGMENT_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="Unsupported segment schema version"):
        Segment.from_dict(payload)


def test_diarization_speaker_hints_validate_exact_and_range() -> None:
    assert _diarization_speaker_hints({"num_speakers": "2"}) == {"num_speakers": 2}
    assert _diarization_speaker_hints({"min_speakers": 2, "max_speakers": 4}) == {
        "min_speakers": 2,
        "max_speakers": 4,
    }
    assert _diarization_speaker_hints({"unrelated": 99}) == {}


@pytest.mark.parametrize(
    "config, message",
    [
        ({"num_speakers": 0}, "positive integer"),
        ({"num_speakers": True}, "positive integer"),
        ({"min_speakers": 1.5}, "positive integer"),
        ({"num_speakers": 2, "max_speakers": 3}, "cannot be combined"),
        ({"min_speakers": 4, "max_speakers": 2}, "must be <="),
    ],
)
def test_diarization_speaker_hints_reject_invalid_contract(config: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _diarization_speaker_hints(config)


def test_diarization_overlap_ranges_only_mark_distinct_speaker_overlap() -> None:
    diarized = [
        {"start": 0.0, "end": 2.0, "speaker": "A"},
        {"start": 1.25, "end": 2.5, "speaker": "B"},
        {"start": 2.6, "end": 3.0, "speaker": "B"},
        {"start": 2.8, "end": 3.2, "speaker": "B"},
    ]
    assert _diarization_overlap_ranges(diarized) == [(1.25, 2.0)]


def test_mark_diarization_overlaps_preserves_word_and_segment_visibility() -> None:
    result = {
        "segments": [
            {
                "start": 0.0,
                "end": 2.5,
                "text": "one two",
                "words": [
                    {"word": "one", "start": 0.0, "end": 0.8},
                    {"word": "two", "start": 1.4, "end": 1.8},
                ],
            }
        ]
    }
    diarized = [
        {"start": 0.0, "end": 2.0, "speaker": "A"},
        {"start": 1.2, "end": 2.2, "speaker": "B"},
    ]

    _mark_diarization_overlaps(result, diarized)

    segment = result["segments"][0]
    assert segment["overlap"] is True
    assert "overlap" not in segment["words"][0]
    assert segment["words"][1]["overlap"] is True


def test_transcribe_and_align_forwards_validated_diarization_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vi_dubber.asr as asr_module

    seen_hints: list[dict[str, int]] = []

    class FakeModel:
        def transcribe(self, audio, batch_size):
            return {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]}

    class FakeDiarizationPipeline:
        def __init__(self, **kwargs):
            pass

        def __call__(self, audio, **kwargs):
            seen_hints.append(kwargs)
            return [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    fake_whisperx = types.ModuleType("whisperx")
    fake_whisperx.load_model = lambda *args, **kwargs: FakeModel()
    fake_whisperx.load_audio = lambda path: [0.0]
    fake_whisperx.load_align_model = lambda **kwargs: (object(), {})
    fake_whisperx.align = lambda segments, *args, **kwargs: {"segments": segments}
    fake_whisperx.assign_word_speakers = lambda diarized, result: result

    fake_diarize = types.ModuleType("whisperx.diarize")
    fake_diarize.DiarizationPipeline = FakeDiarizationPipeline
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setitem(sys.modules, "whisperx.diarize", fake_diarize)
    monkeypatch.setattr(asr_module, "configure_runtime", lambda: None)
    monkeypatch.setattr(asr_module, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_module, "_release_cuda", lambda *objects: None)

    segments = asr_module.transcribe_and_align(
        tmp_path / "audio.wav",
        {"device": "cpu", "compute_type": "int8", "model": "tiny", "batch_size": 1},
        hf_token="token",
        diarize=True,
        diarization_config={"min_speakers": 2, "max_speakers": 3},
    )

    assert seen_hints == [{"min_speakers": 2, "max_speakers": 3}]
    assert len(segments) == 1


def test_transcribe_text_files_loads_model_once_and_preserves_input_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vi_dubber.asr as asr_module

    model_loads = 0
    loaded_audio: list[str] = []
    released: list[object] = []

    class FakeModel:
        def transcribe(self, audio, batch_size):
            assert batch_size == 3
            return {"segments": [{"text": f" text-{audio} "}, {"text": "done"}]}

    model = FakeModel()

    def load_model(*args, **kwargs):
        nonlocal model_loads
        model_loads += 1
        assert args[:2] == ("tiny", "cpu")
        assert kwargs["compute_type"] == "int8"
        assert kwargs["language"] == "vi"
        return model

    fake_whisperx = types.ModuleType("whisperx")
    fake_whisperx.load_model = load_model

    def load_audio(path: str):
        loaded_audio.append(path)
        return Path(path).stem

    fake_whisperx.load_audio = load_audio
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setattr(asr_module, "configure_runtime", lambda: None)
    monkeypatch.setattr(asr_module, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_module, "_release_cuda", lambda *objects: released.extend(objects))

    paths = [tmp_path / "first.wav", tmp_path / "second.wav", tmp_path / "third.wav"]
    texts = transcribe_text_files(
        paths,
        {"device": "cpu", "compute_type": "int8", "model": "tiny", "batch_size": 3},
    )

    assert texts == ["text-first done", "text-second done", "text-third done"]
    assert loaded_audio == [str(path) for path in paths]
    assert model_loads == 1
    assert released == [model]


def test_transcribe_and_align_chunks_reuses_model_and_restores_global_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vi_dubber.asr as asr_module

    model_loads = 0
    align_loads = 0
    callbacks: list[tuple[str, list[str]]] = []

    class FakeModel:
        def transcribe(self, audio, batch_size):
            if "chunk_0001" in str(audio):
                return {
                    "language": "en",
                    "segments": [
                        {"start": 1.0, "end": 2.0, "text": "first"},
                        {"start": 10.5, "end": 11.5, "text": "belongs-next"},
                    ],
                }
            return {
                "language": "en",
                "segments": [{"start": 2.5, "end": 3.5, "text": "second"}],
            }

    def load_model(*args, **kwargs):
        nonlocal model_loads
        model_loads += 1
        return FakeModel()

    def load_align_model(**kwargs):
        nonlocal align_loads
        align_loads += 1
        return object(), {}

    fake_whisperx = types.ModuleType("whisperx")
    fake_whisperx.load_model = load_model
    fake_whisperx.load_audio = lambda path: path
    fake_whisperx.load_align_model = load_align_model
    fake_whisperx.align = lambda segments, *args, **kwargs: {"segments": segments}
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setattr(asr_module, "configure_runtime", lambda: None)
    monkeypatch.setattr(asr_module, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_module, "_release_cuda", lambda *objects: None)

    def fake_clip(_source, output, _start, _duration):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"audio")
        return output

    monkeypatch.setattr(asr_module, "clip_audio", fake_clip)
    chunks = [
        MacroChunk("chunk_0001", 0, 0.0, 10.0, 0.0, 12.0, "safe_cut"),
        MacroChunk("chunk_0002", 1, 10.0, 20.0, 8.0, 20.0, "end"),
    ]

    result = transcribe_and_align_chunks(
        tmp_path / "vocals.wav",
        chunks,
        tmp_path / "chunks",
        {"device": "cpu", "compute_type": "int8", "model": "tiny", "batch_size": 1},
        on_chunk=lambda chunk, segments: callbacks.append(
            (chunk.chunk_id, [segment.text for segment in segments])
        ),
    )

    assert model_loads == 1
    assert align_loads == 1
    assert [segment.text for segment in result["chunk_0001"]] == ["first"]
    assert [segment.text for segment in result["chunk_0002"]] == ["second"]
    assert result["chunk_0001"][0].start == pytest.approx(1.0)
    assert result["chunk_0002"][0].start == pytest.approx(10.5)
    assert callbacks == [("chunk_0001", ["first"]), ("chunk_0002", ["second"])]
    assert not (tmp_path / "chunks" / "chunk_0001" / "asr-window.wav").exists()


def test_transcribe_text_files_releases_model_when_a_file_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vi_dubber.asr as asr_module

    released: list[object] = []

    class FakeModel:
        def transcribe(self, audio, batch_size):
            if audio == "bad":
                raise RuntimeError("qa transcribe failed")
            return {"segments": [{"text": "ok"}]}

    model = FakeModel()
    fake_whisperx = types.ModuleType("whisperx")
    fake_whisperx.load_model = lambda *args, **kwargs: model
    fake_whisperx.load_audio = lambda path: Path(path).stem
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setattr(asr_module, "configure_runtime", lambda: None)
    monkeypatch.setattr(asr_module, "_release_cuda", lambda *objects: released.extend(objects))

    with pytest.raises(RuntimeError, match="qa transcribe failed"):
        transcribe_text_files(
            [tmp_path / "good.wav", tmp_path / "bad.wav"],
            {"device": "cpu", "compute_type": "int8", "model": "tiny", "batch_size": 1},
        )

    assert released == [model]


def test_transcribe_text_keeps_single_file_api_via_batch_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vi_dubber.asr as asr_module

    seen: list[tuple[list[Path], dict[str, Any], str]] = []

    def fake_batch(paths: list[Path], config: dict[str, Any], language: str = "vi") -> list[str]:
        seen.append((paths, config, language))
        return ["xin chao"]

    monkeypatch.setattr(asr_module, "transcribe_text_files", fake_batch)
    config = {"model": "tiny"}
    path = tmp_path / "one.wav"

    assert transcribe_text(path, config, language="en") == "xin chao"
    assert seen == [([path], config, "en")]


def test_transcribe_text_files_empty_input_skips_runtime_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vi_dubber.asr as asr_module

    monkeypatch.setattr(
        asr_module,
        "configure_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("runtime should not initialize")),
    )
    assert transcribe_text_files([], {"model": "tiny"}) == []
