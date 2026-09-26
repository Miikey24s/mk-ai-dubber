from pathlib import Path

import pytest

from vi_dubber import media


def test_chunk_preview_clips_global_timeline_and_publishes_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "source.mp4"
    background = tmp_path / "background.wav"
    voice = tmp_path / "voice.wav"
    output = tmp_path / "PREVIEW_chunk_0001.mp4"
    for path in (video, background, voice):
        path.write_bytes(b"fixture")

    video_calls: list[tuple[float, float]] = []
    audio_calls: list[tuple[Path, float, float]] = []

    def fake_clip_video(source: Path, target: Path, *, start: float, duration: float) -> Path:
        assert source == video
        video_calls.append((start, duration))
        target.write_bytes(b"video")
        return target

    def fake_clip_audio(
        source: Path,
        target: Path,
        *,
        start: float,
        duration: float,
        sample_rate: int = 48000,
    ) -> Path:
        assert sample_rate == 48000
        audio_calls.append((source, start, duration))
        target.write_bytes(b"audio")
        return target

    def fake_mux(
        _video: Path,
        _background: Path,
        _voice: Path,
        target: Path,
        **_kwargs,
    ) -> Path:
        assert target.name == ".PREVIEW_chunk_0001.partial.mp4"
        target.write_bytes(b"complete-preview")
        return target

    monkeypatch.setattr(media, "clip_video_exact", fake_clip_video)
    monkeypatch.setattr(media, "clip_audio_window", fake_clip_audio)
    monkeypatch.setattr(media, "mux_dubbed_video", fake_mux)

    result = media.build_chunk_preview(
        video,
        background,
        voice,
        output,
        start=120.5,
        duration=45.0,
        background_gain_db=-1.0,
        voice_gain_db=1.5,
        final_lufs=-14.0,
        true_peak_db=-1.0,
    )

    assert result == output
    assert output.read_bytes() == b"complete-preview"
    assert video_calls == [(120.5, 45.0)]
    assert audio_calls == [
        (background, 120.5, 45.0),
        (voice, 120.5, 45.0),
    ]
    assert not (tmp_path / ".PREVIEW_chunk_0001.partial.mp4").exists()
    assert not (tmp_path / ".PREVIEW_chunk_0001.preview-work").exists()


def test_chunk_preview_failure_keeps_previous_published_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "source.mp4"
    background = tmp_path / "background.wav"
    voice = tmp_path / "voice.wav"
    output = tmp_path / "PREVIEW_chunk_0001.mp4"
    for path in (video, background, voice):
        path.write_bytes(b"fixture")
    output.write_bytes(b"old-preview")

    monkeypatch.setattr(
        media,
        "clip_video_exact",
        lambda _source, target, **_kwargs: target.write_bytes(b"video") or target,
    )
    monkeypatch.setattr(
        media,
        "clip_audio_window",
        lambda _source, target, **_kwargs: target.write_bytes(b"audio") or target,
    )

    def fail_mux(_video: Path, _background: Path, _voice: Path, target: Path, **_kwargs) -> Path:
        target.write_bytes(b"partial")
        raise RuntimeError("mux failed")

    monkeypatch.setattr(media, "mux_dubbed_video", fail_mux)

    with pytest.raises(RuntimeError, match="mux failed"):
        media.build_chunk_preview(
            video,
            background,
            voice,
            output,
            start=0.0,
            duration=10.0,
            background_gain_db=0.0,
            voice_gain_db=0.0,
            final_lufs=-14.0,
            true_peak_db=-1.0,
        )

    assert output.read_bytes() == b"old-preview"
    assert not (tmp_path / ".PREVIEW_chunk_0001.partial.mp4").exists()
    assert not (tmp_path / ".PREVIEW_chunk_0001.preview-work").exists()
