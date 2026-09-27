from __future__ import annotations

import gc
import logging
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .media import WAV_SAFE_BYTES


ProgressCallback = Callable[[float, str], None]
_PROGRESS_STEP = 0.01
_LOCAL_SEPARATOR_LOCK = threading.RLock()
# Classic RIFF/WAV uses a 32-bit data-size field. Keep a safety margin below
# the nominal 4 GiB limit because separator output can retain the input's
# channel count and sample subtype. FLAC is lossless and has no RIFF-size cap.
_WAV_SAFE_BYTES = WAV_SAFE_BYTES


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
        with _mdxc_progress_adapter(progress_callback):
            filenames = separator.separate(str(input_audio))
        paths = [output_dir / name for name in filenames]

        vocals = next((p for p in paths if "vocal" in p.name.lower()), None)
        instrumental = next((p for p in paths if "instrument" in p.name.lower()), None)
        if vocals is None or instrumental is None:
            raise RuntimeError(
                "Source separation did not produce both Vocals and Instrumental stems. "
                f"Outputs: {[p.name for p in paths]}"
            )
        return vocals, instrumental
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
