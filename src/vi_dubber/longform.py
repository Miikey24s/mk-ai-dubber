from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class SpeechInterval:
    start: float
    end: float

    def __post_init__(self) -> None:
        if not isfinite(self.start) or not isfinite(self.end):
            raise ValueError("speech interval timestamps must be finite")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("speech interval must satisfy 0 <= start < end")


@dataclass(frozen=True, slots=True)
class MacroChunkPolicy:
    target_seconds: float = 25 * 60
    min_seconds: float = 15 * 60
    max_seconds: float = 40 * 60
    boundary_search_seconds: float = 60.0
    context_seconds: float = 2.0
    single_chunk_threshold_seconds: float = 15 * 60

    def __post_init__(self) -> None:
        values = {
            "target_seconds": self.target_seconds,
            "min_seconds": self.min_seconds,
            "max_seconds": self.max_seconds,
            "boundary_search_seconds": self.boundary_search_seconds,
            "context_seconds": self.context_seconds,
            "single_chunk_threshold_seconds": self.single_chunk_threshold_seconds,
        }
        if any(not isfinite(value) for value in values.values()):
            raise ValueError("macro chunk policy values must be finite")
        if self.min_seconds <= 0:
            raise ValueError("min_seconds must be positive")
        if not self.min_seconds <= self.target_seconds <= self.max_seconds:
            raise ValueError("target_seconds must be between min_seconds and max_seconds")
        if self.boundary_search_seconds < 0 or self.context_seconds < 0:
            raise ValueError("boundary search and context must be non-negative")
        if self.single_chunk_threshold_seconds <= 0:
            raise ValueError("single_chunk_threshold_seconds must be positive")


@dataclass(frozen=True, slots=True)
class MacroChunk:
    chunk_id: str
    index: int
    source_start: float
    source_end: float
    context_start: float
    context_end: float
    boundary_reason: str

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start

    def local_to_global(self, local_seconds: float) -> float:
        if not isfinite(local_seconds):
            raise ValueError("local timestamp must be finite")
        if local_seconds < 0 or local_seconds > self.duration:
            raise ValueError("local timestamp must stay inside chunk source range")
        return self.source_start + local_seconds

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_speech_intervals(
    intervals: Iterable[SpeechInterval],
    *,
    duration_seconds: float,
) -> list[SpeechInterval]:
    clipped: list[SpeechInterval] = []
    for interval in intervals:
        start = max(0.0, min(duration_seconds, float(interval.start)))
        end = max(0.0, min(duration_seconds, float(interval.end)))
        if end > start:
            clipped.append(SpeechInterval(start, end))
    clipped.sort(key=lambda item: (item.start, item.end))

    merged: list[SpeechInterval] = []
    for interval in clipped:
        if not merged or interval.start > merged[-1].end:
            merged.append(interval)
            continue
        previous = merged[-1]
        merged[-1] = SpeechInterval(previous.start, max(previous.end, interval.end))
    return merged


def _silence_candidates(intervals: list[SpeechInterval], duration_seconds: float) -> list[tuple[float, float]]:
    candidates: list[tuple[float, float]] = []
    cursor = 0.0
    for interval in intervals:
        if interval.start > cursor:
            candidates.append((cursor, interval.start))
        cursor = max(cursor, interval.end)
    if cursor < duration_seconds:
        candidates.append((cursor, duration_seconds))
    return candidates


def _choose_boundary(
    *,
    source_start: float,
    duration_seconds: float,
    silence_ranges: list[tuple[float, float]],
    policy: MacroChunkPolicy,
) -> tuple[float, str]:
    min_end = min(duration_seconds, source_start + policy.min_seconds)
    target_end = min(duration_seconds, source_start + policy.target_seconds)
    max_end = min(duration_seconds, source_start + policy.max_seconds)
    if max_end >= duration_seconds:
        return duration_seconds, "end"

    preferred_low = max(min_end, target_end - policy.boundary_search_seconds)
    preferred_high = min(max_end, target_end + policy.boundary_search_seconds)

    def candidates_between(low: float, high: float) -> list[tuple[float, float, float]]:
        result: list[tuple[float, float, float]] = []
        for silence_start, silence_end in silence_ranges:
            candidate_start = max(low, silence_start)
            candidate_end = min(high, silence_end)
            if candidate_end <= candidate_start:
                continue
            midpoint = (candidate_start + candidate_end) / 2.0
            gap = silence_end - silence_start
            result.append((midpoint, gap, abs(midpoint - target_end)))
        return result

    preferred = candidates_between(preferred_low, preferred_high)
    if preferred:
        midpoint, _gap, _distance = min(preferred, key=lambda item: (item[2], -item[1], item[0]))
        return midpoint, "silence_near_target"

    bounded = candidates_between(min_end, max_end)
    if bounded:
        midpoint, _gap, _distance = min(bounded, key=lambda item: (item[2], -item[1], item[0]))
        return midpoint, "silence_in_range"

    return target_end, "safe_cut"


def plan_macro_chunks(
    duration_seconds: float,
    speech_intervals: Iterable[SpeechInterval] = (),
    *,
    policy: MacroChunkPolicy | None = None,
) -> list[MacroChunk]:
    """Plan deterministic source ranges while preferring silence around target boundaries.

    Source ranges exactly cover the global timeline. Context ranges may overlap and are
    metadata for future VAD/ASR windowing; callers must not use them for final assembly.
    """
    if not isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be a positive finite value")
    policy = policy or MacroChunkPolicy()
    intervals = _normalize_speech_intervals(speech_intervals, duration_seconds=duration_seconds)
    silence_ranges = _silence_candidates(intervals, duration_seconds)

    if duration_seconds <= policy.single_chunk_threshold_seconds:
        return [
            MacroChunk(
                chunk_id="chunk_0001",
                index=0,
                source_start=0.0,
                source_end=duration_seconds,
                context_start=0.0,
                context_end=duration_seconds,
                boundary_reason="single_chunk",
            )
        ]

    chunks: list[MacroChunk] = []
    source_start = 0.0
    while source_start < duration_seconds:
        source_end, reason = _choose_boundary(
            source_start=source_start,
            duration_seconds=duration_seconds,
            silence_ranges=silence_ranges,
            policy=policy,
        )
        if source_end <= source_start:
            raise RuntimeError("macro chunk planner did not advance")
        index = len(chunks)
        chunks.append(
            MacroChunk(
                chunk_id=f"chunk_{index + 1:04d}",
                index=index,
                source_start=source_start,
                source_end=source_end,
                context_start=max(0.0, source_start - policy.context_seconds),
                context_end=min(duration_seconds, source_end + policy.context_seconds),
                boundary_reason=reason,
            )
        )
        source_start = source_end
    return chunks
