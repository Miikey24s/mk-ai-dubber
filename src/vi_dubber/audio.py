from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any

import numpy as np
import soundfile as sf

from .runtime import ffmpeg_exe
from .types import Segment


def evaluate_reference_clarity(wav_path: Path) -> dict[str, float]:
    """Measure energy variance, zero-crossing rate, and estimated SNR for reference audio.

    P07 acoustic clarity estimation for smart voice reference selection.
    """
    path = Path(wav_path)
    if not path.is_file():
        raise FileNotFoundError(f"Reference audio file not found: {wav_path}")

    try:
        audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:
        raise ValueError(f"Failed to read audio file {wav_path}: {exc}") from exc

    if audio.size == 0 or len(audio) == 0:
        return {
            "energy_variance": 0.0,
            "zero_crossing_rate": 0.0,
            "estimated_snr": 0.0,
            "snr_db": 0.0,
            "clarity_score": 0.0,
            "speech_ratio": 0.0,
            "clipping_ratio": 0.0,
        }

    # Convert multi-channel to mono
    mono = audio.mean(axis=1) if audio.ndim > 1 else audio

    # Zero-crossing rate (rate of sign-changes along the signal)
    sign_changes = np.abs(np.diff(np.signbit(mono).astype(np.int8)))
    zcr = float(np.mean(sign_changes)) if len(sign_changes) > 0 else 0.0

    # Clipping ratio (|x| >= 0.995)
    clipping_ratio = float(np.mean(np.abs(mono) >= 0.995))

    # Short-time framing for energy & RMS
    frame_size = max(1, int(round(sample_rate * 0.025)))  # 25 ms
    hop_size = max(1, int(round(sample_rate * 0.010)))    # 10 ms

    usable = len(mono)
    if usable < frame_size:
        frame_energies = np.asarray([float(np.mean(np.square(mono, dtype=np.float64)))], dtype=np.float32)
        frame_rms = np.sqrt(frame_energies)
    else:
        num_frames = 1 + (usable - frame_size) // hop_size
        frames = np.lib.stride_tricks.sliding_window_view(
            mono[: (num_frames - 1) * hop_size + frame_size], frame_size
        )[::hop_size]
        frame_energies = np.mean(np.square(frames, dtype=np.float64), axis=1).astype(np.float32)
        frame_rms = np.sqrt(np.maximum(1e-12, frame_energies))

    energy_variance = float(np.var(frame_energies)) if frame_energies.size > 0 else 0.0

    # Estimated SNR using percentile energy floor tracking
    if frame_rms.size > 0:
        noise_floor = float(np.percentile(frame_rms, 20))
        peak_rms = float(np.max(frame_rms))
        active_floor = max(1e-5, noise_floor * 2.5, peak_rms * 0.04)
        active = frame_rms >= active_floor
        speech_ratio = float(np.mean(active))

        if np.any(active):
            active_rms = float(np.sqrt(np.mean(np.square(frame_rms[active], dtype=np.float64))))
        else:
            active_rms = 0.0

        quiet = frame_rms[~active]
        if quiet.size > 0:
            noise_rms = float(np.sqrt(np.mean(np.square(quiet, dtype=np.float64))))
        else:
            noise_rms = max(noise_floor, 1e-6)

        snr_raw = 20.0 * float(np.log10(max(active_rms, 1e-6) / max(noise_rms, 1e-6)))
        estimated_snr = float(np.clip(snr_raw, -10.0, 60.0))
    else:
        speech_ratio = 0.0
        estimated_snr = 0.0

    # Composite acoustic clarity score in [0.0, 1.0]
    if speech_ratio <= 0.0 or float(np.max(np.abs(mono))) < 1e-4:
        clarity_score = 0.0
    else:
        snr_factor = float(np.clip(estimated_snr / 25.0, 0.0, 1.0))
        zcr_factor = float(np.clip(1.0 - abs(zcr - 0.08) / 0.15, 0.0, 1.0))
        speech_factor = float(np.clip(speech_ratio / 0.70, 0.0, 1.0))
        clarity_score = float(
            np.clip(
                0.40 * snr_factor + 0.35 * speech_factor + 0.25 * zcr_factor - 2.0 * clipping_ratio,
                0.0,
                1.0,
            )
        )

    return {
        "energy_variance": energy_variance,
        "zero_crossing_rate": zcr,
        "estimated_snr": estimated_snr,
        "snr_db": estimated_snr,
        "clarity_score": clarity_score,
        "speech_ratio": speech_ratio,
        "clipping_ratio": clipping_ratio,
    }


def resolve_loudness_profile(
    profile: str = "youtube",
    custom_lufs: float | None = None,
    custom_true_peak: float | None = None,
) -> tuple[float, float]:
    """Resolve integrated LUFS and true peak target for a given profile.

    - YouTube default: -14.0 LUFS, -1.5 dBTP
    - Broadcast default (EBU R128): -23.0 LUFS, -1.0 dBTP
    """
    prof = profile.strip().lower()
    if prof in {"broadcast", "ebu_r128", "ebu", "tv"}:
        default_lufs = -23.0
        default_tp = -1.0
    else:
        default_lufs = -14.0
        default_tp = -1.5

    target_lufs = float(custom_lufs if custom_lufs is not None else default_lufs)
    target_tp = float(custom_true_peak if custom_true_peak is not None else default_tp)
    return target_lufs, target_tp


_LOUDNORM_JSON_RE = re.compile(r"\{\s*\"input_i\".*?\}", re.DOTALL)


def normalize_loudness(
    input_path: Path,
    output_path: Path,
    *,
    target_lufs: float | None = None,
    target_true_peak_db: float | None = None,
    profile: str = "youtube",
    loudness_range_target: float = 11.0,
) -> Path:
    """Perform EBU R128 loudness normalization using FFmpeg loudnorm filter (P11).

    Applies two-pass loudnorm (measurement pass followed by linear normalization)
    with true peak and integrated loudness targets.
    """
    resolved_lufs, resolved_tp = resolve_loudness_profile(
        profile=profile,
        custom_lufs=target_lufs,
        custom_true_peak=target_true_peak_db,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Pass 1: Analysis pass
    cmd_measure = [
        str(ffmpeg_exe()),
        "-hide_banner",
        "-nostats",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-af",
        f"loudnorm=I={resolved_lufs}:TP={resolved_tp}:LRA={loudness_range_target}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    res = subprocess.run(cmd_measure, capture_output=True, text=True, check=False)

    measurements: dict[str, float] | None = None
    if res.returncode == 0 and res.stderr:
        matches = _LOUDNORM_JSON_RE.findall(res.stderr)
        if matches:
            try:
                raw = json.loads(matches[-1])
                measurements = {
                    "input_i": float(raw["input_i"]),
                    "input_tp": float(raw["input_tp"]),
                    "input_lra": float(raw["input_lra"]),
                    "input_thresh": float(raw["input_thresh"]),
                    "target_offset": float(raw["target_offset"]),
                }
            except Exception:
                measurements = None

    # Pass 2: Normalization pass
    if measurements is not None:
        loudnorm_filter = (
            f"loudnorm=I={resolved_lufs}:TP={resolved_tp}:LRA={loudness_range_target}:"
            f"measured_I={measurements['input_i']}:measured_TP={measurements['input_tp']}:"
            f"measured_LRA={measurements['input_lra']}:measured_thresh={measurements['input_thresh']}:"
            f"offset={measurements['target_offset']}:linear=true"
        )
    else:
        loudnorm_filter = f"loudnorm=I={resolved_lufs}:TP={resolved_tp}:LRA={loudness_range_target}"

    is_video = input_path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".avi"}
    out_is_video = output_path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}

    if is_video and out_is_video:
        cmd_apply = [
            str(ffmpeg_exe()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-c:v",
            "copy",
            "-af",
            loudnorm_filter,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]
    else:
        cmd_apply = [
            str(ffmpeg_exe()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-af",
            loudnorm_filter,
            "-ar",
            "48000",
            str(output_path),
        ]

    apply_res = subprocess.run(cmd_apply, capture_output=True, text=True, check=False)
    if apply_res.returncode != 0:
        raise RuntimeError(f"FFmpeg loudnorm failed:\n{apply_res.stderr.strip()}")

    return output_path
