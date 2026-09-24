from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .runtime import ffmpeg_exe, ffprobe_exe
from .types import Segment


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


def audio_duration(path: Path) -> float:
    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate)


def extract_audio(video: Path, output: Path, sample_rate: int = 48000) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i",
            str(video),
            "-vn",
            "-ac",
            "2",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s24le",
            str(output),
        ]
    )
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
    filter_graph = build_mix_filter_graph(
        background_gain_db=background_gain_db,
        voice_gain_db=voice_gain_db,
        final_lufs=final_lufs,
        true_peak_db=true_peak_db,
        duck_background=duck_background,
        loudnorm_measurements=loudnorm_measurements,
        output_peak_ceiling_db=true_peak_db - _AAC_TRUE_PEAK_HEADROOM_DB,
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
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output),
        ]
    )
    return output


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
