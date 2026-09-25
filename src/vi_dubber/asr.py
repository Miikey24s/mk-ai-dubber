from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Any, Callable

from .longform import MacroChunk
from .media import clip_audio
from .runtime import MODELS_DIR, configure_runtime
from .scheduler import BoundedExecutor
from .types import Segment, WordToken


_DIARIZATION_SPEAKER_HINTS = ("num_speakers", "min_speakers", "max_speakers")
_LOGGER = logging.getLogger(__name__)


def _batch_size_candidates(batch_size: int) -> list[int]:
    if batch_size <= 0:
        raise ValueError("asr.batch_size must be a positive integer")

    candidates = [batch_size]
    for fallback in (4, 2, 1):
        if fallback < batch_size and fallback not in candidates:
            candidates.append(fallback)
    return candidates


def _is_cuda_oom(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "cuda" in message and "out of memory" in message


def _transcribe_with_batch_fallback(
    model: Any,
    audio: Any,
    *,
    batch_size: int,
    device: str,
) -> dict[str, Any]:
    """Retry only CUDA OOM failures with smaller conservative batch sizes."""
    candidates = _batch_size_candidates(batch_size)
    for index, candidate in enumerate(candidates):
        try:
            return model.transcribe(audio, batch_size=candidate)
        except Exception as exc:
            has_fallback = index + 1 < len(candidates)
            if device.lower() != "cuda" or not _is_cuda_oom(exc) or not has_fallback:
                raise
            next_batch = candidates[index + 1]
            _LOGGER.warning(
                "WhisperX CUDA OOM at batch_size=%s; retrying with batch_size=%s",
                candidate,
                next_batch,
            )
            _release_cuda()

    raise RuntimeError("WhisperX transcription failed without returning a result")


def _positive_int_hint(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"diarization.{name} must be a positive integer")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"diarization.{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"diarization.{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"diarization.{name} must be a positive integer")
    return parsed


def _diarization_speaker_hints(config: dict[str, Any] | None) -> dict[str, int]:
    """Validate optional pyannote speaker-count hints before model invocation."""
    if not config:
        return {}

    hints: dict[str, int] = {}
    for name in _DIARIZATION_SPEAKER_HINTS:
        raw = config.get(name)
        if raw is None or raw == "":
            continue
        hints[name] = _positive_int_hint(name, raw)

    if "num_speakers" in hints and ("min_speakers" in hints or "max_speakers" in hints):
        raise ValueError(
            "diarization.num_speakers cannot be combined with min_speakers/max_speakers"
        )
    if hints.get("min_speakers", 0) > hints.get("max_speakers", hints.get("min_speakers", 0)):
        raise ValueError("diarization.min_speakers must be <= max_speakers")
    return hints


def _diarization_records(diarized: Any) -> list[dict[str, Any]]:
    if hasattr(diarized, "to_dict"):
        try:
            records = diarized.to_dict(orient="records")
        except TypeError:
            records = diarized.to_dict("records")
    else:
        records = diarized
    if not isinstance(records, list):
        return []
    return [dict(item) for item in records if isinstance(item, dict)]


def _diarization_overlap_ranges(diarized: Any) -> list[tuple[float, float]]:
    """Return time ranges where distinct diarized speakers overlap."""
    spans: list[tuple[float, float, str]] = []
    for item in _diarization_records(diarized):
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        speaker = str(item.get("speaker") or "").strip()
        if speaker and end > start:
            spans.append((start, end, speaker))

    overlaps: list[tuple[float, float]] = []
    active: list[tuple[float, float, str]] = []
    for start, end, speaker in sorted(spans):
        active = [item for item in active if item[1] > start]
        for other_start, other_end, other_speaker in active:
            if speaker == other_speaker:
                continue
            overlap_start = max(start, other_start)
            overlap_end = min(end, other_end)
            if overlap_end > overlap_start:
                overlaps.append((overlap_start, overlap_end))
        active.append((start, end, speaker))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(overlaps):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _mark_diarization_overlaps(result: dict[str, Any], diarized: Any) -> None:
    overlap_ranges = _diarization_overlap_ranges(diarized)
    if not overlap_ranges:
        return

    def overlaps(start: Any, end: Any) -> bool:
        try:
            item_start = float(start)
            item_end = float(end)
        except (TypeError, ValueError):
            return False
        return any(item_start < overlap_end and item_end > overlap_start for overlap_start, overlap_end in overlap_ranges)

    for segment in result.get("segments", []):
        if not isinstance(segment, dict):
            continue
        if overlaps(segment.get("start"), segment.get("end")):
            segment["overlap"] = True
        for word in segment.get("words") or []:
            if isinstance(word, dict) and overlaps(word.get("start"), word.get("end")):
                word["overlap"] = True


def _segment_from_aligned(item: dict[str, Any], segment_id: int) -> Segment:
    words = [
        WordToken.from_dict(word)
        for word in (item.get("words") or [])
        if isinstance(word, dict) and str(word.get("word", word.get("text", ""))).strip()
    ]

    timed_starts = [word.start for word in words if word.start is not None]
    timed_ends = [word.end for word in words if word.end is not None]
    raw_start = item.get("start")
    raw_end = item.get("end")
    start = float(raw_start) if raw_start is not None else (min(timed_starts) if timed_starts else 0.0)
    end = float(raw_end) if raw_end is not None else (max(timed_ends) if timed_ends else start)

    speaker = str(item.get("speaker") or "").strip()
    if not speaker:
        counts: dict[str, int] = {}
        for word in words:
            if word.speaker:
                counts[word.speaker] = counts.get(word.speaker, 0) + 1
        if counts:
            speaker = max(counts, key=counts.get)
        else:
            speaker = "SPEAKER_00"

    avg_logprob = item.get("avg_logprob")
    raw_scene_id = item.get("scene_id")
    return Segment(
        id=segment_id,
        start=start,
        end=end,
        text=str(item.get("text", "")).strip(),
        speaker=speaker,
        words=words,
        avg_logprob=float(avg_logprob) if avg_logprob is not None else None,
        overlap=bool(item.get("overlap", False) or any(word.overlap for word in words)),
        scene_id=(str(raw_scene_id).strip() or None) if raw_scene_id is not None else None,
        scene_boundary=bool(item.get("scene_boundary", False)),
    )


def _release_cuda(*objects: object) -> None:
    for obj in objects:
        del obj
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def transcribe_and_align(
    audio_path: Path,
    config: dict[str, Any],
    hf_token: str | None = None,
    diarize: bool = False,
    diarization_config: dict[str, Any] | None = None,
) -> list[Segment]:
    if diarize and not hf_token:
        raise RuntimeError(
            "Diarization was requested but HUGGINGFACE_TOKEN is missing. "
            "The free pyannote community model requires accepting its model terms and a read token."
        )

    configure_runtime()
    import whisperx

    device = str(config.get("device", "cuda"))
    compute_type = str(config.get("compute_type", "float16"))
    language = config.get("language") or None
    batch_size = int(config.get("batch_size", 4))
    model_name = str(config.get("model", "large-v3"))
    cache_dir = MODELS_DIR / "whisperx"
    cache_dir.mkdir(parents=True, exist_ok=True)

    model = None
    align_model = None
    diarize_model = None
    try:
        model = whisperx.load_model(
            model_name,
            device,
            compute_type=compute_type,
            language=language,
            download_root=str(cache_dir),
        )
        audio = whisperx.load_audio(str(audio_path))
        result = _transcribe_with_batch_fallback(
            model,
            audio,
            batch_size=batch_size,
            device=device,
        )

        align_model, metadata = whisperx.load_align_model(
            language_code=result["language"],
            device=device,
        )
        result = whisperx.align(
            result["segments"],
            align_model,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        )

        if diarize:
            from whisperx.diarize import DiarizationPipeline

            diarize_model = DiarizationPipeline(
                token=hf_token,
                device=device,
                cache_dir=str(MODELS_DIR / "pyannote"),
            )
            diarized = diarize_model(audio, **_diarization_speaker_hints(diarization_config))
            result = whisperx.assign_word_speakers(diarized, result)
            _mark_diarization_overlaps(result, diarized)

        segments: list[Segment] = []
        for idx, item in enumerate(result.get("segments", [])):
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            segments.append(_segment_from_aligned(item, idx))
        return segments
    finally:
        _release_cuda(model, align_model, diarize_model)


def _shift_segment_to_global(segment: Segment, offset: float, segment_id: int) -> Segment:
    return Segment(
        id=segment_id,
        start=segment.start + offset,
        end=segment.end + offset,
        text=segment.text,
        speaker=segment.speaker,
        vi=segment.vi,
        words=[
            WordToken(
                text=word.text,
                start=(word.start + offset) if word.start is not None else None,
                end=(word.end + offset) if word.end is not None else None,
                confidence=word.confidence,
                speaker=word.speaker,
                overlap=word.overlap,
            )
            for word in segment.words
        ],
        avg_logprob=segment.avg_logprob,
        source_segment_ids=list(segment.source_segment_ids),
        overlap=segment.overlap,
        scene_id=segment.scene_id,
        scene_boundary=segment.scene_boundary,
    )


def transcribe_and_align_chunks(
    audio_path: Path,
    chunks: list[MacroChunk],
    output_dir: Path,
    config: dict[str, Any],
    *,
    on_chunk: Callable[[MacroChunk, list[Segment]], None] | None = None,
) -> dict[str, list[Segment]]:
    """Transcribe macro chunks while loading WhisperX/align models only once.

    This path intentionally excludes diarization until cross-chunk speaker identity
    reconciliation exists. Context windows overlap for ASR continuity, while midpoint
    ownership ensures each aligned segment is emitted by only one source chunk.
    """
    if not chunks:
        return {}

    configure_runtime()
    import whisperx

    device = str(config.get("device", "cuda"))
    compute_type = str(config.get("compute_type", "float16"))
    language = config.get("language") or None
    batch_size = int(config.get("batch_size", 4))
    model_name = str(config.get("model", "large-v3"))
    cache_dir = MODELS_DIR / "whisperx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    model = None
    align_models: dict[str, tuple[Any, Any]] = {}
    results: dict[str, list[Segment]] = {}
    try:
        model = whisperx.load_model(
            model_name,
            device,
            compute_type=compute_type,
            language=language,
            download_root=str(cache_dir),
        )
        def extract_window(chunk: MacroChunk) -> Path:
            window_path = output_dir / chunk.chunk_id / "asr-window.wav"
            return clip_audio(
                audio_path,
                window_path,
                chunk.context_start,
                chunk.context_end - chunk.context_start,
            )

        prefetch_enabled = bool(config.get("prefetch_windows", True)) and len(chunks) > 1
        with BoundedExecutor("asr-prefetch", max_workers=1, max_pending=1) as prefetch:
            current_future = prefetch.submit(extract_window, chunks[0])
            for index, chunk in enumerate(chunks):
                window_path = current_future.result()
                next_future = (
                    prefetch.submit(extract_window, chunks[index + 1])
                    if prefetch_enabled and index + 1 < len(chunks)
                    else None
                )
                try:
                    audio = whisperx.load_audio(str(window_path))
                    result = _transcribe_with_batch_fallback(
                        model,
                        audio,
                        batch_size=batch_size,
                        device=device,
                    )
                    result_language = str(result.get("language") or language or "en")
                    align_pair = align_models.get(result_language)
                    if align_pair is None:
                        align_pair = whisperx.load_align_model(
                            language_code=result_language,
                            device=device,
                        )
                        align_models[result_language] = align_pair
                    align_model, metadata = align_pair
                    aligned = whisperx.align(
                        result["segments"],
                        align_model,
                        metadata,
                        audio,
                        device,
                        return_char_alignments=False,
                    )

                    owned: list[Segment] = []
                    for item in aligned.get("segments", []):
                        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                            continue
                        local = _segment_from_aligned(item, len(owned))
                        global_segment = _shift_segment_to_global(local, chunk.context_start, len(owned))
                        midpoint = (global_segment.start + global_segment.end) / 2.0
                        owns_midpoint = chunk.source_start <= midpoint < chunk.source_end
                        if owns_midpoint:
                            global_segment.id = len(owned)
                            owned.append(global_segment)
                    results[chunk.chunk_id] = owned
                    if on_chunk is not None:
                        on_chunk(chunk, owned)
                finally:
                    window_path.unlink(missing_ok=True)
                if next_future is not None:
                    current_future = next_future
                elif index + 1 < len(chunks):
                    current_future = prefetch.submit(extract_window, chunks[index + 1])
        return results
    finally:
        _release_cuda(model, *(pair[0] for pair in align_models.values()))


def transcribe_text(
    audio_path: Path,
    config: dict[str, Any],
    language: str = "vi",
) -> str:
    return transcribe_text_files([audio_path], config, language=language)[0]


def transcribe_text_files(
    paths: list[Path],
    config: dict[str, Any],
    language: str = "vi",
) -> list[str]:
    """Transcribe rendered QA files while reusing one WhisperX model instance."""
    if not paths:
        return []

    configure_runtime()
    import whisperx

    device = str(config.get("device", "cuda"))
    compute_type = str(config.get("compute_type", "float16"))
    model = whisperx.load_model(
        str(config.get("model", "large-v3")),
        device,
        compute_type=compute_type,
        language=language,
        download_root=str(MODELS_DIR / "whisperx"),
    )
    batch_size = int(config.get("batch_size", 4))
    texts: list[str] = []
    try:
        for path in paths:
            audio = whisperx.load_audio(str(path))
            result = _transcribe_with_batch_fallback(
                model,
                audio,
                batch_size=batch_size,
                device=device,
            )
            text = " ".join(
                str(item.get("text", "")).strip()
                for item in result.get("segments", [])
                if isinstance(item, dict)
            )
            texts.append(text.strip())
    finally:
        _release_cuda(model)
    return texts
