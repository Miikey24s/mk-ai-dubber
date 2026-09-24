from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from vi_dubber.audio import (
    evaluate_reference_clarity,
    normalize_loudness,
    resolve_loudness_profile,
)
from vi_dubber.media import measure_mix_metrics


def _create_wav(
    path: Path,
    *,
    duration: float = 3.0,
    sample_rate: int = 48000,
    freq: float = 440.0,
    amplitude: float = 0.5,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0.0, duration, int(round(sample_rate * duration)), endpoint=False, dtype=np.float32)
    # Generate modulated tone to simulate speech dynamics with pause
    modulator = 0.5 * (1.0 + np.sin(2.0 * np.pi * 3.0 * t))
    audio = amplitude * np.sin(2.0 * np.pi * freq * t) * modulator
    # Add quiet tail to ensure noise floor
    audio[-int(sample_rate * 0.5) :] = 1e-4 * np.random.randn(int(sample_rate * 0.5))
    sf.write(str(path), audio, sample_rate, subtype="PCM_16")
    return path


def test_evaluate_reference_clarity_on_synthetic_signal(tmp_path: Path) -> None:
    wav_path = _create_wav(tmp_path / "ref.wav", duration=4.0, freq=300.0, amplitude=0.4)
    result = evaluate_reference_clarity(wav_path)

    assert "energy_variance" in result
    assert "zero_crossing_rate" in result
    assert "estimated_snr" in result
    assert "snr_db" in result
    assert "clarity_score" in result
    assert "speech_ratio" in result
    assert "clipping_ratio" in result

    assert math.isfinite(result["energy_variance"])
    assert result["energy_variance"] > 0.0
    assert 0.0 <= result["zero_crossing_rate"] <= 1.0
    assert result["estimated_snr"] > 0.0
    assert 0.0 <= result["clarity_score"] <= 1.0
    assert result["clipping_ratio"] == 0.0


def test_evaluate_reference_clarity_on_silent_signal(tmp_path: Path) -> None:
    silent_wav = tmp_path / "silence.wav"
    sf.write(str(silent_wav), np.zeros(24000, dtype=np.float32), 48000, subtype="PCM_16")
    result = evaluate_reference_clarity(silent_wav)

    assert result["energy_variance"] == 0.0
    assert result["zero_crossing_rate"] == 0.0
    assert result["estimated_snr"] == 0.0
    assert result["clarity_score"] == 0.0


def test_resolve_loudness_profile() -> None:
    # YouTube profile defaults to -14.0 LUFS, -1.5 dBTP
    yt_lufs, yt_tp = resolve_loudness_profile("youtube")
    assert yt_lufs == -14.0
    assert yt_tp == -1.5

    # Broadcast profile defaults to -23.0 LUFS, -1.0 dBTP
    bc_lufs, bc_tp = resolve_loudness_profile("broadcast")
    assert bc_lufs == -23.0
    assert bc_tp == -1.0

    # Custom overrides take priority
    custom_lufs, custom_tp = resolve_loudness_profile("youtube", custom_lufs=-16.0, custom_true_peak=-2.0)
    assert custom_lufs == -16.0
    assert custom_tp == -2.0


def test_normalize_loudness_produces_valid_audio(tmp_path: Path) -> None:
    src_wav = _create_wav(tmp_path / "input.wav", duration=2.5, amplitude=0.8)
    dst_wav = tmp_path / "normalized.wav"

    out_path = normalize_loudness(src_wav, dst_wav, profile="youtube", target_lufs=-14.0, target_true_peak_db=-1.5)
    assert out_path.is_file()
    assert out_path.stat().st_size > 0

    metrics = measure_mix_metrics(out_path, target_lufs=-14.0, target_true_peak_db=-1.5)
    assert math.isfinite(metrics["integrated_lufs"])
    assert math.isfinite(metrics["true_peak_db"])
