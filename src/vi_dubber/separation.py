from __future__ import annotations

import gc
import logging
from pathlib import Path


def separate_dialogue(
    input_audio: Path,
    output_dir: Path,
    model_dir: Path,
    model_filename: str,
) -> tuple[Path, Path]:
    from audio_separator.separator import Separator

    output_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    separator = None
    try:
        separator = Separator(
            log_level=logging.INFO,
            model_file_dir=str(model_dir),
            output_dir=str(output_dir),
            output_format="WAV",
            sample_rate=48000,
            use_soundfile=True,
            use_autocast=True,
        )
        separator.load_model(model_filename=model_filename)
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
