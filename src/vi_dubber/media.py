from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .runtime import ffmpeg_exe, ffprobe_exe
from .types import Segment


# Keep a margin below the 32-bit RIFF data-size limit.  The extracted audio is
# stereo 24-bit PCM at 48 kHz for the historical WAV path, so the estimate is
# deterministic and independent of the compressed source container.
WAV_SAFE_BYTES = 3_500_000_000
EXTRACT_AUDIO_POLICY_VERSION = 2
EXTRACT_AUDIO_SAMPLE_RATE = 48_000
EXTRACT_AUDIO_CHANNELS = 2
EXTRACT_AUDIO_PCM_BYTES_PER_SAMPLE = 3
_EXTRACTED_AUDIO_MAX_GAP_SECONDS = 5.0
_EXTRACTED_AUDIO_MAX_RELATIVE_GAP = 0.001


@dataclass(frozen=True, slots=True)
class _VoiceSegmentPlan:
    audio_path: Path
    start_sample: int
    end_sample: int
    fade_samples: int


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [str(ffmpeg_exe()), "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr.strip()}")


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [
            str(ffprobe_exe()),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _video_codec_name(path: Path) -> str:
    result = subprocess.run(
        [
            str(ffprobe_exe()),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(result.stdout).get("streams") or []
    if not streams or not streams[0].get("codec_name"):
        raise RuntimeError(f"Không xác định được video codec: {path}")
    return str(streams[0]["codec_name"]).strip().lower()


def _mux_video_codec_args(video: Path, output: Path) -> list[str]:
    if output.suffix.lower() != ".mp4":
        return ["-c:v", "copy"]
    if _video_codec_name(video) in {"h264", "hevc", "av1", "mpeg4"}:
        return ["-c:v", "copy"]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]


def audio_duration(path: Path) -> float:
    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate)


def extracted_audio_container(
    duration_seconds: float,
    *,
    sample_rate: int = EXTRACT_AUDIO_SAMPLE_RATE,
    channels: int = EXTRACT_AUDIO_CHANNELS,
    bytes_per_sample: int = EXTRACT_AUDIO_PCM_BYTES_PER_SAMPLE,
) -> str:
    """Select a seekable extraction container from the expected timeline length."""

    if not math.isfinite(float(duration_seconds)) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be a finite positive number")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    if isinstance(channels, bool) or not isinstance(channels, int) or channels <= 0:
        raise ValueError("channels must be a positive integer")
    if isinstance(bytes_per_sample, bool) or not isinstance(bytes_per_sample, int) or bytes_per_sample <= 0:
        raise ValueError("bytes_per_sample must be a positive integer")
    estimated_pcm_bytes = duration_seconds * sample_rate * channels * bytes_per_sample
    return "FLAC" if estimated_pcm_bytes >= WAV_SAFE_BYTES else "WAV"


def extracted_audio_path(job_dir: Path, duration_seconds: float) -> Path:
    """Return the deterministic cache path for the selected extraction container."""

    container = extracted_audio_container(duration_seconds)
    return Path(job_dir) / f"original.{container.lower()}"


def audio_extraction_spec(duration_seconds: float) -> dict[str, Any]:
    """Return the versioned extraction identity used by the pipeline cache."""

    container = extracted_audio_container(duration_seconds)
    return {
        "policy": EXTRACT_AUDIO_POLICY_VERSION,
        "container": container,
        "codec": "pcm_s24le" if container == "WAV" else "flac",
        "sample_rate": EXTRACT_AUDIO_SAMPLE_RATE,
        "channels": EXTRACT_AUDIO_CHANNELS,
    }


def validate_extracted_audio(
    path: Path,
    expected_duration_seconds: float,
    *,
    max_gap_seconds: float = _EXTRACTED_AUDIO_MAX_GAP_SECONDS,
    max_relative_gap: float = _EXTRACTED_AUDIO_MAX_RELATIVE_GAP,
) -> float:
    """Probe an extraction and reject truncated/header-only output."""

    if not math.isfinite(float(expected_duration_seconds)) or expected_duration_seconds <= 0:
        raise ValueError("expected_duration_seconds must be a finite positive number")
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Extracted audio is missing or empty: {path}")
    try:
        probed_duration = media_duration(path)
    except Exception as exc:
        raise RuntimeError(f"Extracted audio probe failed: {path}") from exc
    try:
        decoded_duration = audio_duration(path)
    except Exception as exc:
        raise RuntimeError(f"Extracted audio decode probe failed: {path}") from exc
    if (
        not math.isfinite(probed_duration)
        or not math.isfinite(decoded_duration)
        or probed_duration <= 0
        or decoded_duration <= 0
    ):
        raise RuntimeError(f"Extracted audio has no valid duration: {path}")
    tolerance = max(float(max_gap_seconds), expected_duration_seconds * float(max_relative_gap))
    if probed_duration + tolerance < expected_duration_seconds or decoded_duration + tolerance < expected_duration_seconds:
        raise RuntimeError(
            "Extracted audio is truncated: "
            f"expected at least {expected_duration_seconds:.3f}s, "
            f"probed {probed_duration:.3f}s, decoded {decoded_duration:.3f}s"
        )
    return min(probed_duration, decoded_duration)


def validate_separation_stems(
    vocals: Path,
    background: Path,
    expected_duration_seconds: float,
) -> tuple[float, float]:
    """Reject separator stems that are missing, header-only, or truncated."""

    validated: list[float] = []
    for label, path in (("vocals", vocals), ("background", background)):
        try:
            validated.append(validate_extracted_audio(path, expected_duration_seconds))
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"Separation {label} stem failed duration validation: {path}") from exc
    return validated[0], validated[1]


def extract_audio(
    video: Path,
    output: Path,
    sample_rate: int = EXTRACT_AUDIO_SAMPLE_RATE,
    *,
    container: str | None = None,
    expected_duration_seconds: float | None = None,
) -> Path:
    """Extract audio atomically as WAV for short input or FLAC for long input."""

    selected_container = str(container or ("FLAC" if output.suffix.lower() == ".flac" else "WAV")).upper()
    if selected_container not in {"WAV", "FLAC"}:
        raise ValueError(f"unsupported extraction container: {selected_container}")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep the real extension so FFmpeg selects the intended muxer while the
    # leading dot and ``.partial`` marker keep incomplete output out of the
    # canonical cache path.
    partial = output.with_name(f".{output.stem}.partial{output.suffix}")
    partial.unlink(missing_ok=True)
    codec = "pcm_s24le" if selected_container == "WAV" else "flac"
    try:
        _run_ffmpeg(
            [
                "-i",
                str(video),
                "-vn",
                "-ac",
                str(EXTRACT_AUDIO_CHANNELS),
                "-ar",
                str(sample_rate),
                "-c:a",
                codec,
                str(partial),
            ]
        )
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg produced no extracted audio: {partial}")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    if expected_duration_seconds is not None:
        validate_extracted_audio(output, expected_duration_seconds)
    return output


def clip_audio(source: Path, output: Path, start: float, duration: float) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{max(0.2, duration):.3f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    return output


def clip_audio_window(
    source: Path,
    output: Path,
    *,
    start: float,
    duration: float,
    sample_rate: int = 48000,
) -> Path:
    """Clip an audio timeline window while preserving the source channel layout."""
    if duration <= 0:
        raise ValueError("duration must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-vn",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    return output


def clip_video_exact(
    source: Path,
    output: Path,
    *,
    start: float,
    duration: float,
) -> Path:
    """Create an exact-timeline preview clip; re-encode avoids keyframe seek drift."""
    if duration <= 0:
        raise ValueError("duration must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return output


def detect_speech_intervals(
    source: Path,
    *,
    duration_seconds: float,
    noise_db: float = -40.0,
    min_silence_seconds: float = 0.35,
) -> list[tuple[float, float]]:
    """Return speech ranges using FFmpeg's streaming silencedetect filter.

    The detector never loads the whole waveform into Python memory. Returned ranges
    are only used to choose macro-chunk boundaries; WhisperX still owns ASR/VAD.
    """
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if min_silence_seconds <= 0:
        raise ValueError("min_silence_seconds must be positive")

    result = subprocess.run(
        [
            str(ffmpeg_exe()),
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-af",
            f"silencedetect=noise={float(noise_db):.1f}dB:d={float(min_silence_seconds):.3f}",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg silencedetect failed:\n{result.stderr.strip()}")

    silence_ranges: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in result.stderr.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.eE+-]+)", line)
        if start_match:
            pending_start = max(0.0, float(start_match.group(1)))
            continue
        end_match = re.search(r"silence_end:\s*([0-9.eE+-]+)", line)
        if end_match:
            silence_end = min(duration_seconds, float(end_match.group(1)))
            silence_start = 0.0 if pending_start is None else pending_start
            if silence_end > silence_start:
                silence_ranges.append((silence_start, silence_end))
            pending_start = None
    if pending_start is not None and pending_start < duration_seconds:
        silence_ranges.append((pending_start, duration_seconds))

    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for silence_start, silence_end in sorted(silence_ranges):
        silence_start = max(cursor, min(duration_seconds, silence_start))
        silence_end = max(silence_start, min(duration_seconds, silence_end))
        if silence_start > cursor:
            speech.append((cursor, silence_start))
        cursor = max(cursor, silence_end)
    if cursor < duration_seconds:
        speech.append((cursor, duration_seconds))
    return speech


def _atempo_chain(tempo: float) -> str:
    factors: list[float] = []
    remaining = tempo
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.6f}" for factor in factors)


def fit_audio_to_window(
    source: Path,
    output: Path,
    target_duration: float,
    max_speedup: float,
    min_tempo: float = 1.0,
) -> tuple[Path, float, float]:
    source_duration = audio_duration(source)
    if target_duration <= 0:
        if source.resolve() != output.resolve():
            _run_ffmpeg(["-i", str(source), "-c:a", "pcm_s16le", str(output)])
        return output, source_duration, 1.0

    requested = source_duration / target_duration
    if source_duration <= target_duration:
        slowdown_floor = min(1.0, max(0.5, float(min_tempo)))
        if requested < 1.0 and requested >= slowdown_floor:
            _run_ffmpeg(
                [
                    "-i",
                    str(source),
                    "-filter:a",
                    _atempo_chain(requested),
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ]
            )
            return output, audio_duration(output), requested
        if source.resolve() != output.resolve():
            _run_ffmpeg(["-i", str(source), "-c:a", "pcm_s16le", str(output)])
        return output, source_duration, 1.0

    tempo = min(requested, max_speedup)
    _run_ffmpeg(
        [
            "-i",
            str(source),
            "-filter:a",
            _atempo_chain(tempo),
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    return output, audio_duration(output), tempo


def assemble_voice_track(
    segments: list[tuple[Segment, Path]],
    output: Path,
    total_duration: float,
    sample_rate: int = 48000,
    fade_ms: float = 0.0,
    overlap_policy: str = "equal_power",
    block_seconds: float = 30.0,
) -> Path:
    if overlap_policy not in {"equal_power", "sum"}:
        raise ValueError(f"Unsupported overlap policy: {overlap_policy}")
    if block_seconds <= 0:
        raise ValueError("block_seconds must be > 0")

    total_samples = max(0, int(round(total_duration * sample_rate)))
    ordered_segments = sorted(
        (segment for segment, _audio_path in segments),
        key=lambda item: (item.start, item.end, item.id),
    )
    hard_end_by_id: dict[int, float] = {}
    for current, following in zip(ordered_segments, ordered_segments[1:]):
        if (
            current.speaker != following.speaker
            and not current.overlap
            and not following.overlap
            and current.start <= following.start
        ):
            hard_end_by_id[current.id] = following.start

    plans: list[_VoiceSegmentPlan] = []
    for segment, audio_path in segments:
        info = sf.info(str(audio_path))
        if info.samplerate != sample_rate:
            raise ValueError(
                f"Unexpected TTS sample rate {info.samplerate}; expected {sample_rate}"
            )
        start = max(0, int(round(segment.start * sample_rate)))
        end = min(total_samples, start + int(info.frames))
        hard_end = hard_end_by_id.get(segment.id)
        if hard_end is not None:
            end = min(end, max(start, int(round(hard_end * sample_rate))))
        if end <= start:
            continue
        rendered_samples = end - start
        fade_samples = min(
            rendered_samples // 2,
            int(round(max(0.0, fade_ms) * sample_rate / 1000.0)),
        )
        plans.append(
            _VoiceSegmentPlan(
                audio_path=audio_path,
                start_sample=start,
                end_sample=end,
                fade_samples=fade_samples,
            )
        )

    block_samples = max(1, int(round(block_seconds * sample_rate)))

    def render_block(block_start: int, block_end: int) -> np.ndarray:
        block = np.zeros(block_end - block_start, dtype=np.float32)
        overlap_count = np.zeros(block_end - block_start, dtype=np.uint16)
        for plan in plans:
            start = max(block_start, plan.start_sample)
            end = min(block_end, plan.end_sample)
            if end <= start:
                continue

            audio_start = start - plan.start_sample
            requested_frames = end - start
            audio, sr = sf.read(
                str(plan.audio_path),
                start=audio_start,
                frames=requested_frames,
                dtype="float32",
                always_2d=True,
            )
            if sr != sample_rate:
                raise ValueError(f"Unexpected TTS sample rate {sr}; expected {sample_rate}")
            mono = audio.mean(axis=1)
            if mono.size == 0:
                continue
            if len(mono) < requested_frames:
                end = start + len(mono)

            rendered = mono[: end - start].copy()
            if plan.fade_samples > 0:
                positions = np.arange(
                    audio_start,
                    audio_start + len(rendered),
                    dtype=np.int64,
                )
                denominator = max(1, plan.fade_samples - 1)
                fade_in = positions < plan.fade_samples
                if np.any(fade_in):
                    rendered[fade_in] *= (
                        positions[fade_in].astype(np.float32) / denominator
                    )
                rendered_samples = plan.end_sample - plan.start_sample
                fade_out_start = rendered_samples - plan.fade_samples
                fade_out = positions >= fade_out_start
                if np.any(fade_out):
                    rendered[fade_out] *= (
                        (rendered_samples - 1 - positions[fade_out]).astype(np.float32)
                        / denominator
                    )

            local_start = start - block_start
            local_end = local_start + len(rendered)
            block[local_start:local_end] += rendered
            overlap_count[local_start:local_end] += 1

        if overlap_policy == "equal_power":
            overlapping = overlap_count > 1
            if np.any(overlapping):
                block[overlapping] /= np.sqrt(
                    overlap_count[overlapping].astype(np.float32)
                )
        return block

    peak = 0.0
    for block_start in range(0, total_samples, block_samples):
        block_end = min(total_samples, block_start + block_samples)
        block = render_block(block_start, block_end)
        if block.size:
            peak = max(peak, float(np.max(np.abs(block))))

    gain = 0.98 / peak if peak > 0.98 else 1.0
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    partial.unlink(missing_ok=True)
    try:
        with sf.SoundFile(
            str(partial),
            mode="w",
            samplerate=sample_rate,
            channels=1,
            subtype="PCM_16",
        ) as sink:
            for block_start in range(0, total_samples, block_samples):
                block_end = min(total_samples, block_start + block_samples)
                sink.write(render_block(block_start, block_end) * gain)
        partial.replace(output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return output


def voice_track_metrics(path: Path) -> dict[str, float | int]:
    audio, _sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = audio.mean(axis=1) if audio.size else np.zeros(0, dtype=np.float32)
    if mono.size == 0:
        return {"peak": 0.0, "peak_dbfs": float("-inf"), "clipped_samples": 0, "clipping_ratio": 0.0}
    peak = float(np.max(np.abs(mono)))
    clipped = int(np.count_nonzero(np.abs(mono) >= 0.999))
    return {
        "peak": peak,
        "peak_dbfs": 20.0 * math.log10(max(peak, 1e-12)),
        "clipped_samples": clipped,
        "clipping_ratio": clipped / len(mono),
    }


def _build_mix_prefix(
    *,
    background_gain_db: float,
    voice_gain_db: float,
    background_input: str = "1:a",
    voice_input: str = "2:a",
    duck_background: bool = False,
    duck_threshold: float = 0.025,
    duck_ratio: float = 4.0,
    duck_attack_ms: float = 15.0,
    duck_release_ms: float = 250.0,
) -> str:
    if duck_background:
        return (
            f"[{background_input}]volume={background_gain_db}dB[bg];"
            f"[{voice_input}]volume={voice_gain_db}dB,asplit=2[vo_mix][vo_key];"
            f"[bg][vo_key]sidechaincompress=threshold={duck_threshold}:ratio={duck_ratio}:"
            f"attack={duck_attack_ms}:release={duck_release_ms}[bgduck];"
            f"[bgduck][vo_mix]amix=inputs=2:duration=longest:dropout_transition=0[mix]"
        )
    return (
        f"[{background_input}]volume={background_gain_db}dB[bg];"
        f"[{voice_input}]volume={voice_gain_db}dB[vo];"
        f"[bg][vo]amix=inputs=2:duration=longest:dropout_transition=0[mix]"
    )


def build_mix_filter_graph(
    *,
    background_gain_db: float,
    voice_gain_db: float,
    final_lufs: float,
    true_peak_db: float,
    duck_background: bool = False,
    duck_threshold: float = 0.025,
    duck_ratio: float = 4.0,
    duck_attack_ms: float = 15.0,
    duck_release_ms: float = 250.0,
    loudnorm_measurements: dict[str, float] | None = None,
    output_peak_ceiling_db: float | None = None,
) -> str:
    prefix = _build_mix_prefix(
        background_gain_db=background_gain_db,
        voice_gain_db=voice_gain_db,
        duck_background=duck_background,
        duck_threshold=duck_threshold,
        duck_ratio=duck_ratio,
        duck_attack_ms=duck_attack_ms,
        duck_release_ms=duck_release_ms,
    )
    loudnorm = f"loudnorm=I={final_lufs}:TP={true_peak_db}:LRA=11"
    if loudnorm_measurements is not None:
        loudnorm += (
            f":measured_I={float(loudnorm_measurements['input_i'])}"
            f":measured_TP={float(loudnorm_measurements['input_tp'])}"
            f":measured_LRA={float(loudnorm_measurements['input_lra'])}"
            f":measured_thresh={float(loudnorm_measurements['input_thresh'])}"
            f":offset={float(loudnorm_measurements['target_offset'])}"
            ":linear=true"
        )
    if output_peak_ceiling_db is None:
        return f"{prefix};[mix]{loudnorm}[outa]"
    limiter = 10.0 ** (output_peak_ceiling_db / 20.0)
    return (
        f"{prefix};[mix]{loudnorm},"
        f"aresample=48000,alimiter=limit={limiter:.8f}:"
        "attack=0.1:release=10:level=false:latency=true[outa]"
    )


_LOUDNORM_JSON_RE = re.compile(r"\{\s*\"input_i\".*?\}", re.DOTALL)
_AAC_TRUE_PEAK_HEADROOM_DB = 0.75
_AAC_TRUE_PEAK_RETRY_MARGIN_DB = 0.25
_AAC_TRUE_PEAK_MAX_ATTEMPTS = 3


def _parse_loudnorm_measurements(stderr: str) -> dict[str, float]:
    matches = _LOUDNORM_JSON_RE.findall(stderr)
    if not matches:
        raise RuntimeError("FFmpeg loudness analysis did not return loudnorm JSON")
    raw = json.loads(matches[-1])
    return {
        "input_i": float(raw["input_i"]),
        "input_tp": float(raw["input_tp"]),
        "input_lra": float(raw["input_lra"]),
        "input_thresh": float(raw["input_thresh"]),
        "target_offset": float(raw["target_offset"]),
    }


def analyze_mix_loudnorm(
    background: Path,
    voice_track: Path,
    *,
    background_gain_db: float,
    voice_gain_db: float,
    final_lufs: float,
    true_peak_db: float,
    duck_background: bool = False,
) -> dict[str, float]:
    prefix = _build_mix_prefix(
        background_gain_db=background_gain_db,
        voice_gain_db=voice_gain_db,
        background_input="0:a",
        voice_input="1:a",
        duck_background=duck_background,
    )
    result = subprocess.run(
        [
            str(ffmpeg_exe()),
            "-hide_banner",
            "-nostats",
            "-i",
            str(background),
            "-i",
            str(voice_track),
            "-filter_complex",
            (
                f"{prefix};[mix]loudnorm=I={final_lufs}:TP={true_peak_db}:"
                "LRA=11:print_format=json[outa]"
            ),
            "-map",
            "[outa]",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg mix loudness analysis failed:\n{result.stderr.strip()}")
    return _parse_loudnorm_measurements(result.stderr)


def measure_mix_metrics(
    path: Path,
    *,
    target_lufs: float = -14.0,
    target_true_peak_db: float = -1.5,
    loudness_tolerance_lu: float = 0.5,
) -> dict[str, float | bool]:
    result = subprocess.run(
        [
            str(ffmpeg_exe()),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vn",
            "-af",
            f"loudnorm=I={target_lufs}:TP={target_true_peak_db}:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg loudness analysis failed:\n{result.stderr.strip()}")
    raw = _parse_loudnorm_measurements(result.stderr)
    integrated_lufs = float(raw["input_i"])
    true_peak_db = float(raw["input_tp"])
    loudness_range_lu = float(raw["input_lra"])
    threshold_lufs = float(raw["input_thresh"])
    target_offset_db = float(raw["target_offset"])
    loudness_delta_lu = integrated_lufs - target_lufs
    return {
        "integrated_lufs": integrated_lufs,
        "true_peak_db": true_peak_db,
        "loudness_range_lu": loudness_range_lu,
        "threshold_lufs": threshold_lufs,
        "target_offset_db": target_offset_db,
        "target_lufs": target_lufs,
        "target_true_peak_db": target_true_peak_db,
        "loudness_delta_lu": loudness_delta_lu,
        "passes_loudness": math.isfinite(integrated_lufs)
        and abs(loudness_delta_lu) <= loudness_tolerance_lu,
        "passes_true_peak": math.isfinite(true_peak_db) and true_peak_db <= target_true_peak_db,
        "clipping_risk": not math.isfinite(true_peak_db) or true_peak_db >= 0.0,
    }


def mux_dubbed_video(
    video: Path,
    background: Path,
    voice_track: Path,
    output: Path,
    background_gain_db: float,
    voice_gain_db: float,
    final_lufs: float,
    true_peak_db: float,
    duck_background: bool = False,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    loudnorm_measurements = analyze_mix_loudnorm(
        background,
        voice_track,
        background_gain_db=background_gain_db,
        voice_gain_db=voice_gain_db,
        final_lufs=final_lufs,
        true_peak_db=true_peak_db,
        duck_background=duck_background,
    )
    video_codec_args = _mux_video_codec_args(video, output)
    headroom_db = _AAC_TRUE_PEAK_HEADROOM_DB
    for attempt in range(_AAC_TRUE_PEAK_MAX_ATTEMPTS):
        filter_graph = build_mix_filter_graph(
            background_gain_db=background_gain_db,
            voice_gain_db=voice_gain_db,
            final_lufs=final_lufs,
            true_peak_db=true_peak_db,
            duck_background=duck_background,
            loudnorm_measurements=loudnorm_measurements,
            output_peak_ceiling_db=true_peak_db - headroom_db,
        )
        _run_ffmpeg(
            [
                "-i",
                str(video),
                "-i",
                str(background),
                "-i",
                str(voice_track),
                "-filter_complex",
                filter_graph,
                "-map",
                "0:v:0",
                "-map",
                "[outa]",
                *video_codec_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(output),
            ]
        )
        encoded_metrics = measure_mix_metrics(
            output,
            target_lufs=final_lufs,
            target_true_peak_db=true_peak_db,
        )
        if bool(encoded_metrics["passes_true_peak"]):
            break
        if attempt + 1 >= _AAC_TRUE_PEAK_MAX_ATTEMPTS:
            break
        encoded_true_peak = float(encoded_metrics["true_peak_db"])
        if not math.isfinite(encoded_true_peak):
            break
        overshoot_db = max(0.0, encoded_true_peak - true_peak_db)
        headroom_db += overshoot_db + _AAC_TRUE_PEAK_RETRY_MARGIN_DB
    return output


def build_chunk_preview(
    video: Path,
    background: Path,
    voice_track: Path,
    output: Path,
    *,
    start: float,
    duration: float,
    background_gain_db: float,
    voice_gain_db: float,
    final_lufs: float,
    true_peak_db: float,
    duck_background: bool = False,
) -> Path:
    """Atomically publish one accurate macro-chunk preview MP4."""
    if duration <= 0:
        raise ValueError("duration must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output.parent / f".{output.stem}.preview-work"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    video_clip = work_dir / "video.mp4"
    background_clip = work_dir / "background.wav"
    voice_clip = work_dir / "voice.wav"
    partial = output.with_name(f".{output.stem}.partial{output.suffix}")
    partial.unlink(missing_ok=True)
    try:
        clip_video_exact(video, video_clip, start=start, duration=duration)
        clip_audio_window(
            background,
            background_clip,
            start=start,
            duration=duration,
        )
        clip_audio_window(
            voice_track,
            voice_clip,
            start=start,
            duration=duration,
        )
        mux_dubbed_video(
            video_clip,
            background_clip,
            voice_clip,
            partial,
            background_gain_db=background_gain_db,
            voice_gain_db=voice_gain_db,
            final_lufs=final_lufs,
            true_peak_db=true_peak_db,
            duck_background=duck_background,
        )
        os.replace(partial, output)
        return output
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)


def _srt_time(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(segments: list[Segment], output: Path, translated: bool) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, segment in enumerate(segments, 1):
        text = segment.vi if translated else segment.text
        lines.extend(
            [
                str(index),
                f"{_srt_time(segment.start)} --> {_srt_time(segment.end)}",
                text.strip(),
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
