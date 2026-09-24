from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Callable, Mapping
import json
from typing import Any

from .artifacts import atomic_write_json, fingerprint_data, fingerprint_file
from .media import audio_duration, clip_audio, fit_audio_to_window
from .reference import rank_reference_candidates
from .timing import TimingWindow, rebalance_timing_windows, timing_action
from .translate import ProgressCallback
from .types import Segment
from .versions import runtime_versions


@dataclass(slots=True)
class TTSStat:
    segment_id: int
    speaker: str
    target_duration: float
    generated_duration: float
    final_duration: float
    tempo: float
    rewrites: int
    used_clone: bool
    timing_action: str = "keep"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_reference_clips(
    vocals: Path,
    segments: list[Segment],
    output_dir: Path,
    selection_receipts: dict[str, Any] | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    by_speaker: dict[str, list[Segment]] = {}
    for segment in segments:
        by_speaker.setdefault(segment.speaker, []).append(segment)

    references: dict[str, Path] = {}
    for speaker, items in by_speaker.items():
        candidates = [item for item in items if 3.0 <= item.duration <= 8.0 and not item.overlap]
        if not candidates:
            if selection_receipts is not None:
                selection_receipts[speaker] = {
                    "selected_segment_id": None,
                    "fallback": "no_eligible_3_to_8_second_non_overlap_candidate",
                    "candidates": [],
                }
            continue
        ranked_receipt: list[dict[str, Any]] = []
        fallback_reason: str | None = None
        try:
            ranked = rank_reference_candidates(vocals, candidates)
            ranked_receipt = [item.to_dict() for item in ranked]
            selected_id = ranked[0].segment_id
            candidate = next(item for item in candidates if item.id == selected_id)
        except Exception as exc:
            # Keep reference generation usable for non-WAV fixtures or unusual
            # separator outputs; acoustic ranking is an enhancement, not a gate.
            candidate = max(candidates, key=lambda item: (min(item.duration, 6.0), -item.id))
            fallback_reason = str(exc).strip() or exc.__class__.__name__
        duration = min(8.0, max(3.0, candidate.duration - 0.2))
        start = candidate.start + max(0.0, (candidate.duration - duration) / 2.0)
        target = output_dir / f"{speaker}.wav"
        clip_audio(vocals, target, start=start, duration=duration)
        references[speaker] = target
        if selection_receipts is not None:
            selection_receipts[speaker] = {
                "selected_segment_id": candidate.id,
                "selected_start": start,
                "selected_duration": duration,
                "fallback": fallback_reason,
                "candidates": ranked_receipt,
            }
    return references


def synthesize_segments(
    segments: list[Segment],
    output_dir: Path,
    tts_config: dict[str, Any],
    timing_config: dict[str, Any],
    translator: Any,
    glossary: dict[str, str],
    references: dict[str, Path],
    voice_ref: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    metrics: Any | None = None,
    rewrite_verifier: Callable[[Segment, str], bool] | None = None,
    tts_text_mapper: Callable[[Segment], str] | dict[int, str] | None = None,
    timing_windows: Mapping[int, TimingWindow] | None = None,
    locked_segment_ids: set[int] | None = None,
) -> tuple[list[tuple[Segment, Path]], list[TTSStat]]:
    from vieneu import Vieneu

    output_dir.mkdir(parents=True, exist_ok=True)
    backend = str(tts_config.get("backend", "onnx"))
    device = str(tts_config.get("device", "cpu"))
    precision = str(tts_config.get("precision", "fp32"))
    engine_kwargs: dict[str, Any] = {
        "mode": "v3turbo",
        "backend": backend,
        "device": device,
        "precision": precision,
    }
    if "dtype" in tts_config:
        engine_kwargs["dtype"] = str(tts_config["dtype"])
    elif backend.lower() == "pytorch" and "cuda" in device.lower():
        # VieNeu v3 Turbo uses `dtype`, not `precision`, for the PyTorch path.
        engine_kwargs["dtype"] = "float16"

    allow_cpu_fallback = backend.lower() == "pytorch" or "cuda" in device.lower()
    cpu_fallback_engine: Any | None = None
    engine_is_cpu_fallback = False

    def _new_cpu_fallback_engine():
        return Vieneu(
            mode="v3turbo",
            backend="onnx",
            device="cpu",
            precision="fp32",
        )

    try:
        engine = Vieneu(**engine_kwargs)
    except Exception:
        if not allow_cpu_fallback:
            raise
        engine = _new_cpu_fallback_engine()
        cpu_fallback_engine = engine
        engine_is_cpu_fallback = True
        if metrics is not None:
            metrics.increment("tts_cpu_fallbacks")

    pad = max(0.0, float(timing_config.get("segment_pad_ms", 35))) / 1000.0
    preset = str(tts_config.get("preset_voice", "Minh Quân"))
    max_speedup = float(timing_config.get("max_speedup", 1.25))
    max_slowdown = float(timing_config.get("max_slowdown", 0.92))
    rewrite_threshold = float(timing_config.get("rewrite_threshold", timing_config.get("translation_retry_ratio", 1.25)))
    aggressive_threshold = float(timing_config.get("aggressive_rewrite_threshold", 1.40))
    batch_size = max(1, int(timing_config.get("rewrite_batch_size", 24)))
    allow_batch_rewrite = bool(timing_config.get("allow_batch_rewrite", True))
    tts_batch_size = max(1, int(tts_config.get("batch_size", 1)))
    preferred_tempo = max(1.0, float(timing_config.get("preferred_tempo", 1.20)))
    elastic_max_borrow = max(0.0, float(timing_config.get("elastic_max_borrow_seconds", 0.35)))
    total_segments = len(segments)
    locked_ids = set(locked_segment_ids or ())
    engine_versions = runtime_versions("vieneu", "torch")
    resolved_timing_windows = dict(timing_windows) if timing_windows is not None else None

    def _target_duration(segment: Segment) -> float:
        if resolved_timing_windows is not None and segment.id in resolved_timing_windows:
            return max(0.25, float(resolved_timing_windows[segment.id].target_duration))
        return max(0.25, segment.duration - pad)

    def _placement_segment(segment: Segment) -> Segment:
        if resolved_timing_windows is None or segment.id not in resolved_timing_windows:
            return segment
        window = resolved_timing_windows[segment.id]
        placed = Segment.from_dict(segment.to_dict())
        placed.start = float(window.start)
        placed.end = float(window.end)
        return placed

    def _tts_text(segment: Segment) -> str:
        if callable(tts_text_mapper):
            mapped = str(tts_text_mapper(segment) or "").strip()
            return mapped or segment.vi
        if isinstance(tts_text_mapper, dict):
            mapped = str(tts_text_mapper.get(segment.id) or "").strip()
            return mapped or segment.vi
        return segment.vi

    def _reference_for(speaker: str) -> Path | None:
        return voice_ref or references.get(speaker)

    def _raw_meta_path(raw_path: Path) -> Path:
        return raw_path.with_name(f"{raw_path.stem}.meta.json")

    def _raw_identity(segment: Segment, text: str) -> str:
        ref = _reference_for(segment.speaker)
        ref_identity = fingerprint_file(ref) if ref is not None and ref.is_file() else None
        return fingerprint_data(
            {
                "version": 2,
                "text": text,
                "speaker": segment.speaker,
                "reference": ref_identity,
                "tts": tts_config,
                "runtime": engine_versions,
            }
        )

    def _read_raw_identity(raw_path: Path) -> str | None:
        meta_path = _raw_meta_path(raw_path)
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("version") != 2:
            return None
        value = raw.get("fingerprint")
        return str(value) if isinstance(value, str) else None

    def _commit_raw_identity(raw_path: Path, segment: Segment, text: str) -> None:
        atomic_write_json(
            _raw_meta_path(raw_path),
            {"version": 2, "fingerprint": _raw_identity(segment, text)},
        )

    def _get_cpu_fallback_engine():
        nonlocal cpu_fallback_engine
        if cpu_fallback_engine is None:
            cpu_fallback_engine = _new_cpu_fallback_engine()
        return cpu_fallback_engine

    def _infer(current_engine: Any, text: str, speaker: str):
        ref = _reference_for(speaker)
        if ref is not None:
            return current_engine.infer(text, ref_audio=str(ref))
        try:
            return current_engine.infer(text, voice=preset)
        except Exception:
            return current_engine.infer(text)

    def _save(current_engine: Any, audio: Any, target_file: Path) -> None:
        temp_file = target_file.with_name(f"{target_file.stem}.partial{target_file.suffix}")
        try:
            current_engine.save(audio, str(temp_file))
            temp_file.replace(target_file)
        finally:
            temp_file.unlink(missing_ok=True)

    def _infer_and_save(text: str, speaker: str, target_file: Path) -> None:
        try:
            audio = _infer(engine, text, speaker)
            _save(engine, audio, target_file)
        except Exception:
            if not allow_cpu_fallback or engine_is_cpu_fallback:
                raise
            fallback = _get_cpu_fallback_engine()
            audio = _infer(fallback, text, speaker)
            _save(fallback, audio, target_file)
            if metrics is not None:
                metrics.increment("tts_cpu_fallbacks")
        if metrics is not None:
            metrics.increment("tts_inferences")

    def _infer_batch_and_save(items: list[tuple[Segment, str, Path]]) -> None:
        if (
            len(items) <= 1
            or tts_batch_size <= 1
            or engine_is_cpu_fallback
            or not hasattr(engine, "infer_batch")
        ):
            for segment, text, target in items:
                _infer_and_save(text, segment.speaker, target)
                _commit_raw_identity(target, segment, text)
            return

        grouped: dict[tuple[str, str], list[tuple[Segment, str, Path]]] = {}
        for item in items:
            segment = item[0]
            ref = _reference_for(segment.speaker)
            key = (segment.speaker, str(ref) if ref is not None else "")
            grouped.setdefault(key, []).append(item)

        for group in grouped.values():
            for offset in range(0, len(group), tts_batch_size):
                chunk = group[offset : offset + tts_batch_size]
                texts = [text for _segment, text, _target in chunk]
                ref = _reference_for(chunk[0][0].speaker)
                try:
                    if ref is not None:
                        audios = engine.infer_batch(texts, ref_audio=str(ref), batch_size=len(chunk))
                    else:
                        try:
                            audios = engine.infer_batch(texts, voice=preset, batch_size=len(chunk))
                        except Exception:
                            audios = engine.infer_batch(texts, batch_size=len(chunk))
                    if len(audios) != len(chunk):
                        raise RuntimeError("VieNeu infer_batch returned an unexpected output count")
                    for audio, (segment, text, target) in zip(audios, chunk, strict=True):
                        _save(engine, audio, target)
                        _commit_raw_identity(target, segment, text)
                    if metrics is not None:
                        metrics.increment("tts_batches")
                        metrics.increment("tts_inferences", len(chunk))
                except Exception:
                    for segment, text, target in chunk:
                        if (
                            target.exists()
                            and target.stat().st_size > 1000
                            and _read_raw_identity(target) == _raw_identity(segment, text)
                        ):
                            continue
                        _infer_and_save(text, segment.speaker, target)
                        _commit_raw_identity(target, segment, text)

    def _metric_stage(name: str):
        if metrics is None:
            return nullcontext()
        return metrics.stage(name)

    # PASS 1: Generate initial raw audio for all segments (Checkpoint supported)
    raw_durations: dict[int, float] = {}

    with _metric_stage("tts_pass_1"):
        pending: list[tuple[Segment, str, Path]] = []
        for segment in segments:
            raw_path = output_dir / f"{segment.id:05d}_raw.wav"
            text = _tts_text(segment)
            reusable = (
                raw_path.exists()
                and raw_path.stat().st_size > 1000
                and _read_raw_identity(raw_path) == _raw_identity(segment, text)
            )
            if reusable:
                try:
                    raw_durations[segment.id] = audio_duration(raw_path)
                except Exception:
                    pending.append((segment, text, raw_path))
            else:
                pending.append((segment, text, raw_path))

        _infer_batch_and_save(pending)
        for index, segment in enumerate(segments, 1):
            raw_path = output_dir / f"{segment.id:05d}_raw.wav"
            text = _tts_text(segment)
            if segment.id not in raw_durations:
                raw_durations[segment.id] = audio_duration(raw_path)

            if progress_callback is not None:
                progress_callback(
                    0.65 * (index / max(1, total_segments)),
                    f"Đang tổng hợp giọng nói (Pass 1): {index}/{total_segments} đoạn",
                )

    if resolved_timing_windows is not None:
        resolved_timing_windows = rebalance_timing_windows(
            segments,
            resolved_timing_windows,
            raw_durations,
            preferred_tempo=preferred_tempo,
            max_extra_borrow_seconds=elastic_max_borrow,
        )

    # Filter segments exceeding threshold: duration / target_duration > rewrite_threshold
    to_rewrite: list[tuple[Segment, float, float, float, int]] = []
    for segment in segments:
        if segment.id in locked_ids:
            continue
        target_duration = _target_duration(segment)
        measured_duration = raw_durations[segment.id]
        ratio = measured_duration / target_duration
        if ratio > rewrite_threshold:
            target_chars = max(8, int(len(segment.vi) * (target_duration / measured_duration) * 0.95))
            to_rewrite.append((segment, target_duration, measured_duration, ratio, target_chars))

    rewritten_ids: set[int] = set()

    # PASS 2: Batched Rewrite for segments exceeding threshold
    with _metric_stage("rewrite_generation"):
        if to_rewrite and allow_batch_rewrite:
            if hasattr(translator, "rewrite_batch"):
                total_batches = (len(to_rewrite) + batch_size - 1) // batch_size
                for b_idx in range(0, len(to_rewrite), batch_size):
                    chunk = to_rewrite[b_idx : b_idx + batch_size]
                    b_num = (b_idx // batch_size) + 1
                    if progress_callback is not None:
                        progress_callback(
                            0.65 + 0.20 * (b_num / max(1, total_batches)),
                            f"Đang tối ưu thời lượng theo batch ({b_num}/{total_batches}): {len(chunk)} câu",
                        )
                    try:
                        rewritten_dict = translator.rewrite_batch(chunk, glossary)
                    except Exception:
                        if metrics is not None:
                            metrics.increment("rewrite_generation_failures")
                        continue
                    for seg, target_dur, meas_dur, ratio, target_chars in chunk:
                        if seg.id not in rewritten_dict or not rewritten_dict[seg.id]:
                            continue
                        new_vi = rewritten_dict[seg.id]
                        try:
                            accepted = rewrite_verifier is None or bool(rewrite_verifier(seg, new_vi))
                        except Exception:
                            if metrics is not None:
                                metrics.increment("rewrite_generation_failures")
                            continue
                        if accepted and new_vi != seg.vi:
                            seg.vi = new_vi
                            rewritten_ids.add(seg.id)
            elif hasattr(translator, "rewrite_shorter"):
                for seg, target_dur, meas_dur, ratio, target_chars in to_rewrite:
                    try:
                        new_vi = translator.rewrite_shorter(seg, meas_dur, glossary)
                        accepted = rewrite_verifier is None or bool(rewrite_verifier(seg, new_vi))
                        if accepted and new_vi != seg.vi:
                            seg.vi = new_vi
                            rewritten_ids.add(seg.id)
                    except Exception:
                        if metrics is not None:
                            metrics.increment("rewrite_generation_failures")

    with _metric_stage("tts_rewrite"):
        for segment in segments:
            if segment.id not in rewritten_ids:
                continue
            raw_path = output_dir / f"{segment.id:05d}_raw.wav"
            try:
                text = _tts_text(segment)
                _infer_and_save(text, segment.speaker, raw_path)
                raw_durations[segment.id] = audio_duration(raw_path)
                _commit_raw_identity(raw_path, segment, text)
            except Exception:
                if metrics is not None:
                    metrics.increment("tts_rewrite_failures")

    # PASS 3: Audio window fitting with atempo
    rendered: list[tuple[Segment, Path]] = []
    stats: list[TTSStat] = []

    with _metric_stage("timing_fit"):
        for index, segment in enumerate(segments, 1):
            target_duration = _target_duration(segment)
            raw_path = output_dir / f"{segment.id:05d}_raw.wav"
            fitted_path = output_dir / f"{segment.id:05d}.wav"
            generated = raw_durations.get(segment.id, audio_duration(raw_path))
            action = timing_action(
                generated,
                target_duration,
                max_speedup=max_speedup,
                rewrite_threshold=rewrite_threshold,
                max_slowdown=max_slowdown,
            )

            fit_kwargs = {
                "target_duration": target_duration,
                "max_speedup": max_speedup,
            }
            if action == "slowdown":
                fit_kwargs["min_tempo"] = max_slowdown
            fitted_path, final_duration, tempo = fit_audio_to_window(raw_path, fitted_path, **fit_kwargs)
            rendered.append((_placement_segment(segment), fitted_path))
            stats.append(
                TTSStat(
                    segment_id=segment.id,
                    speaker=segment.speaker,
                    target_duration=target_duration,
                    generated_duration=generated,
                    final_duration=final_duration,
                    tempo=tempo,
                    rewrites=1 if segment.id in rewritten_ids else 0,
                    used_clone=voice_ref is not None or segment.speaker in references,
                    timing_action=action,
                )
            )
            if progress_callback is not None:
                progress_callback(
                    0.85 + 0.15 * (index / max(1, total_segments)),
                    f"Đang căn chỉnh khớp thời lượng (Pass 3): {index}/{total_segments} đoạn",
                )

    return rendered, stats
