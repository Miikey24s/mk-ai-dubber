from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .types import Segment


@dataclass(slots=True)
class ReferenceScore:
    segment_id: int
    speaker: str
    duration: float
    score: float
    speech_ratio: float
    silence_ratio: float
    clipping_ratio: float
    snr_db: float
    overlap: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _frame_rms(audio: np.ndarray, frame_size: int) -> np.ndarray:
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)
    frame_size = max(1, int(frame_size))
    usable = (len(audio) // frame_size) * frame_size
    if usable == 0:
        return np.asarray([float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))], dtype=np.float32)
    frames = audio[:usable].reshape(-1, frame_size)
    return np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1)).astype(np.float32)


def _read_audio_window(path: Path, *, start: float, duration: float) -> tuple[np.ndarray, int]:
    with sf.SoundFile(str(path)) as handle:
        sample_rate = int(handle.samplerate)
        handle.seek(max(0, int(round(start * sample_rate))))
        frames = max(1, int(round(duration * sample_rate)))
        audio = handle.read(frames, dtype="float32", always_2d=True)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32), sample_rate
    return audio.mean(axis=1), sample_rate


def score_reference_candidate(vocals: Path, segment: Segment) -> ReferenceScore:
    audio, sample_rate = _read_audio_window(vocals, start=segment.start, duration=segment.duration)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    clipping_ratio = float(np.mean(np.abs(audio) >= 0.995)) if audio.size else 1.0

    rms = _frame_rms(audio, max(1, int(round(sample_rate * 0.02))))
    if rms.size:
        noise_floor = float(np.percentile(rms, 20))
        active_floor = max(1e-4, noise_floor * 2.5, float(np.max(rms)) * 0.04)
        active = rms >= active_floor
        speech_ratio = float(np.mean(active))
        silence_ratio = 1.0 - speech_ratio
        active_rms = float(np.sqrt(np.mean(np.square(rms[active], dtype=np.float64)))) if np.any(active) else 0.0
        quiet = rms[~active]
        noise_rms = float(np.sqrt(np.mean(np.square(quiet, dtype=np.float64)))) if quiet.size else max(noise_floor, 1e-6)
        snr_db = 20.0 * float(np.log10(max(active_rms, 1e-6) / max(noise_rms, 1e-6)))
    else:
        speech_ratio = 0.0
        silence_ratio = 1.0
        snr_db = 0.0

    duration_score = max(0.0, 1.0 - abs(segment.duration - 5.0) / 3.0)
    speech_score = min(1.0, speech_ratio / 0.80)
    snr_score = min(1.0, max(0.0, snr_db) / 24.0)
    confidence_score = 0.5
    if segment.avg_logprob is not None:
        confidence_score = min(1.0, max(0.0, (float(segment.avg_logprob) + 1.5) / 1.5))

    score = (
        0.28 * duration_score
        + 0.32 * speech_score
        + 0.24 * snr_score
        + 0.16 * confidence_score
        - 2.0 * clipping_ratio
        - 0.35 * max(0.0, silence_ratio - 0.25)
        - (1.0 if segment.overlap else 0.0)
        - (0.25 if peak < 0.01 else 0.0)
    )
    return ReferenceScore(
        segment_id=segment.id,
        speaker=segment.speaker,
        duration=segment.duration,
        score=float(score),
        speech_ratio=speech_ratio,
        silence_ratio=silence_ratio,
        clipping_ratio=clipping_ratio,
        snr_db=snr_db,
        overlap=segment.overlap,
    )


def rank_reference_candidates(vocals: Path, segments: list[Segment]) -> list[ReferenceScore]:
    scored = [score_reference_candidate(vocals, segment) for segment in segments]
    return sorted(scored, key=lambda item: (-item.score, item.segment_id))
