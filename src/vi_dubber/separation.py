from __future__ import annotations

import gc
import logging
import math
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .media import WAV_SAFE_BYTES, media_duration
from .runtime import ffmpeg_exe

ProgressCallback = Callable[[float, str], None]
_PROGRESS_STEP = 0.01
_LOCAL_SEPARATOR_LOCK = threading.RLock()
# Classic RIFF/WAV uses a 32-bit data-size field. Keep a safety margin below
# the nominal 4 GiB limit because separator output can retain the input's
# channel count and sample subtype. FLAC is lossless and has no RIFF-size cap.
_WAV_SAFE_BYTES = WAV_SAFE_BYTES
# audio-separator materializes a full-track overlap-add buffer. Keep its
# per-call input bounded; this policy is part of the pipeline fingerprint.
SEPARATION_POLICY_VERSION = 3
SEPARATION_CHUNK_SECONDS = 15 * 60
SEPARATION_CHUNK_THRESHOLD_SECONDS = 30 * 60
SEPARATION_SAMPLE_RATE = 48_000
SEPARATION_CHANNELS = 2


def _output_format_for_audio(input_audio: Path) -> str:
    """Choose a lossless container that remains valid for long audio files."""

    # FLAC extraction is the long-input policy.  Its compressed size can be
    # below the RIFF threshold even when the decoded timeline is many hours,
    # so suffix is a stronger signal than file size here.
    if input_audio.suffix.lower() == ".flac":
        return "FLAC"
    try:
        size_bytes = input_audio.stat().st_size
    except OSError:
        # Preserve the historical short-file behavior when the dependency will
        # provide a clearer input error later in the pipeline.
        return "WAV"
    return "FLAC" if size_bytes >= _WAV_SAFE_BYTES else "WAV"


def _run_ffmpeg(args: list[str]) -> None:
    command = [str(ffmpeg_exe()), "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr.strip()}")


def _audio_window_to_flac(source: Path, output: Path, start: float, duration: float) -> None:
    if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
        raise ValueError("audio window must have a finite non-negative start and positive duration")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.partial")
    partial.unlink(missing_ok=True)
    try:
        _run_ffmpeg(
            [
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(source),
                "-vn",
                "-ac",
                str(SEPARATION_CHANNELS),
                "-ar",
                str(SEPARATION_SAMPLE_RATE),
                "-c:a",
                "flac",
                "-f",
                "flac",
                str(partial),
            ]
        )
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg produced an empty separation chunk: {partial}")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)


def _concat_flac(parts: list[Path], output: Path) -> None:
    if not parts:
        raise RuntimeError("cannot concatenate an empty separation stem")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".concat-", dir=output.parent) as temp_dir:
        list_path = Path(temp_dir) / "inputs.txt"
        lines = []
        for part in parts:
            if not part.is_file() or part.stat().st_size <= 0:
                raise RuntimeError(f"separation chunk is missing or empty: {part}")
            escaped = str(part.resolve()).replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        partial = output.with_name(f".{output.name}.partial")
        partial.unlink(missing_ok=True)
        try:
            _run_ffmpeg(
                [
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-vn",
                    "-c:a",
                    "flac",
                    "-f",
                    "flac",
                    str(partial),
                ]
            )
            if not partial.is_file() or partial.stat().st_size <= 0:
                raise RuntimeError(f"FFmpeg produced an empty concatenated stem: {partial}")
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)


def _stem_paths(output_dir: Path, filenames: list[str]) -> tuple[Path, Path]:
    paths = [output_dir / name for name in filenames]
    vocals = next((path for path in paths if "vocal" in path.name.lower()), None)
    instrumental = next((path for path in paths if "instrument" in path.name.lower()), None)
    if vocals is None or instrumental is None:
        raise RuntimeError(
            "Source separation did not produce both Vocals and Instrumental stems. "
            f"Outputs: {[path.name for path in paths]}"
        )
    return vocals, instrumental


def _separator_output(
    separator: Any,
    input_audio: Path,
    output_dir: Path,
    progress_callback: ProgressCallback | None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_output_dir = getattr(separator, "output_dir", None)
    # audio-separator 0.47 delegates actual writing to ``model_instance``.
    # Updating only Separator.output_dir leaves the architecture writer pointed
    # at the parent stems directory, which is fatal for bounded long-input
    # separation: the returned names resolve inside the temporary chunk
    # directory but the files were published elsewhere. Keep both objects in
    # sync for the call and restore the model-owned value afterwards.
    model_instance = getattr(separator, "model_instance", None)
    missing = object()
    previous_model_output_dir = getattr(model_instance, "output_dir", missing)
    if previous_output_dir is not None:
        separator.output_dir = str(output_dir)
    if previous_model_output_dir is not missing:
        model_instance.output_dir = str(output_dir)
    try:
        with _mdxc_progress_adapter(progress_callback):
            filenames = separator.separate(str(input_audio))
    finally:
        if previous_output_dir is not None:
            separator.output_dir = previous_output_dir
        if previous_model_output_dir is not missing:
            model_instance.output_dir = previous_model_output_dir
    return _stem_paths(output_dir, filenames)


def _separate_chunked(
    separator: Any,
    input_audio: Path,
    output_dir: Path,
    progress_callback: ProgressCallback | None,
    duration_seconds: float,
) -> tuple[Path, Path]:
    chunk_count = max(1, math.ceil(duration_seconds / SEPARATION_CHUNK_SECONDS))
    final_vocals = output_dir / f"{input_audio.stem}_(Vocals).flac"
    final_instrumental = output_dir / f"{input_audio.stem}_(Instrumental).flac"
    with tempfile.TemporaryDirectory(prefix=".separation-chunks-", dir=output_dir) as temp_dir:
        work_dir = Path(temp_dir)
        vocal_parts: list[Path] = []
        instrumental_parts: list[Path] = []
        for index in range(chunk_count):
            start = index * SEPARATION_CHUNK_SECONDS
            length = min(SEPARATION_CHUNK_SECONDS, duration_seconds - start)
            if length <= 0:
                continue
            chunk_input = work_dir / f"chunk-{index:05d}.flac"
            chunk_output = work_dir / f"output-{index:05d}"
            _audio_window_to_flac(input_audio, chunk_input, start, length)

            def report_chunk(value: float, message: str, *, index: int = index) -> None:
                if progress_callback is not None:
                    progress_callback((index + min(1.0, max(0.0, value))) / chunk_count, message)

            vocals, instrumental = _separator_output(separator, chunk_input, chunk_output, report_chunk)
            vocal_parts.append(vocals)
            instrumental_parts.append(instrumental)

        _concat_flac(vocal_parts, final_vocals)
        _concat_flac(instrumental_parts, final_instrumental)
    return final_vocals, final_instrumental


@contextmanager
def _mdxc_progress_adapter(progress_callback: ProgressCallback | None):
    """Adapt audio-separator's internal tqdm loop without modifying the package.

    audio-separator 0.47 does not expose a public progress callback for local
    MDXC/Roformer inference. Its architecture module does, however, route the
    long inference loop through a module-level ``tqdm`` symbol. Temporarily
    wrapping that symbol lets VI Dubber persist useful progress while keeping
    the dependency untouched. If the dependency layout changes, this adapter
    deliberately degrades to no detailed progress instead of failing a job.
    """

    # The adapter temporarily replaces a dependency module global, so local
    # separation calls must have one owner. This also matches P23's conservative
    # GPU-heavy concurrency=1 policy and prevents cross-job progress leakage.
    with _LOCAL_SEPARATOR_LOCK:
        if progress_callback is None:
            yield
            return

        try:
            from audio_separator.separator.architectures import mdxc_separator
        except Exception:
            yield
            return

        original_tqdm = getattr(mdxc_separator, "tqdm", None)
        if original_tqdm is None:
            yield
            return

        max_fraction = 0.0

        def adapted_tqdm(iterable: Any = None, *args: Any, **kwargs: Any):
            nonlocal max_fraction
            if iterable is None:
                return original_tqdm(*args, **kwargs)

            bar = original_tqdm(iterable, *args, **kwargs)
            try:
                total = len(iterable)
            except (AttributeError, TypeError):
                return bar
            if total <= 0:
                return bar

            def iterate():
                nonlocal max_fraction
                for index, item in enumerate(bar, start=1):
                    fraction = min(1.0, index / total)
                    if fraction >= 1.0 or fraction - max_fraction >= _PROGRESS_STEP:
                        max_fraction = fraction
                        try:
                            progress_callback(
                                fraction,
                                f"Đang tách lời thoại khỏi nhạc và SFX · {index}/{total}",
                            )
                        except Exception:
                            logging.getLogger(__name__).debug(
                                "Ignoring separation progress callback failure",
                                exc_info=True,
                            )
                    yield item

            return iterate()

        mdxc_separator.tqdm = adapted_tqdm
        try:
            yield
        finally:
            if getattr(mdxc_separator, "tqdm", None) is adapted_tqdm:
                mdxc_separator.tqdm = original_tqdm


def separate_dialogue(
    input_audio: Path,
    output_dir: Path,
    model_dir: Path,
    model_filename: str,
    progress_callback: ProgressCallback | None = None,
) -> tuple[Path, Path]:
    from audio_separator.separator import Separator

    output_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    separator = None
    try:
        output_format = _output_format_for_audio(input_audio)
        separator = Separator(
            log_level=logging.INFO,
            model_file_dir=str(model_dir),
            output_dir=str(output_dir),
            output_format=output_format,
            sample_rate=48000,
            use_soundfile=True,
            use_autocast=True,
        )
        separator.load_model(model_filename=model_filename)
        # A full-track RoFormer call allocates overlap-add buffers proportional
        # to the complete decoded timeline. Long inputs therefore use bounded
        # FLAC chunks while reusing one loaded model instance.
        try:
            duration_seconds = media_duration(input_audio)
        except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError):
            duration_seconds = None
        if duration_seconds is not None and duration_seconds > SEPARATION_CHUNK_THRESHOLD_SECONDS:
            return _separate_chunked(
                separator,
                input_audio,
                output_dir,
                progress_callback,
                duration_seconds,
            )
        return _separator_output(separator, input_audio, output_dir, progress_callback)
    finally:
        if separator is not None:
            del separator
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
