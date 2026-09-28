from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isclose, isfinite
from typing import Any, Iterable

from .types import Segment


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

    def __post_init__(self) -> None:
        """Reject malformed persisted chunk rows before they reach a worker.

        Chunk plans are durable resume input.  Keeping the invariant at the
        value-object boundary prevents a hand-edited or truncated plan from
        silently producing invalid audio ranges later in the pipeline.
        """
        if not isinstance(self.chunk_id, str) or len(self.chunk_id) != len("chunk_0001"):
            raise ValueError(f"invalid macro chunk id: {self.chunk_id!r}")
        suffix = self.chunk_id.removeprefix("chunk_")
        if not suffix.isdigit() or int(suffix) <= 0:
            raise ValueError(f"invalid macro chunk id: {self.chunk_id!r}")
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 0:
            raise ValueError("macro chunk index must be a non-negative integer")
        values = {
            "source_start": self.source_start,
            "source_end": self.source_end,
            "context_start": self.context_start,
            "context_end": self.context_end,
        }
        if any(not isfinite(float(value)) for value in values.values()):
            raise ValueError("macro chunk timestamps must be finite")
        if self.source_start < 0 or self.source_end <= self.source_start:
            raise ValueError("macro chunk source range must satisfy 0 <= start < end")
        if self.context_start < 0 or self.context_end < self.context_start:
            raise ValueError("macro chunk context range must satisfy 0 <= start <= end")
        if self.context_start > self.source_start or self.context_end < self.source_end:
            raise ValueError("macro chunk context range must contain its source range")
        if not isinstance(self.boundary_reason, str) or not self.boundary_reason.strip():
            raise ValueError("macro chunk boundary_reason must be a non-empty string")

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MacroChunk":
        if not isinstance(data, dict):
            raise ValueError("macro chunk row must be an object")
        return cls(
            chunk_id=str(data["chunk_id"]),
            index=int(data["index"]),
            source_start=float(data["source_start"]),
            source_end=float(data["source_end"]),
            context_start=float(data["context_start"]),
            context_end=float(data["context_end"]),
            boundary_reason=str(data["boundary_reason"]),
        )


def validate_macro_chunk_plan(
    chunks: Iterable[MacroChunk],
    *,
    duration_seconds: float | None = None,
) -> list[MacroChunk]:
    """Validate ordering, coverage and bounds of a persisted macro-chunk plan.

    A valid plan owns the source timeline exactly once: the first chunk starts
    at zero, each next chunk starts where the previous one ends, and an
    optional media duration closes the final boundary.  Context windows may
    overlap but must remain inside the media when a duration is supplied.
    """
    ordered = list(chunks)
    if not ordered:
        raise ValueError("macro chunk plan must contain at least one chunk")
    if duration_seconds is not None:
        if not isfinite(float(duration_seconds)) or duration_seconds <= 0:
            raise ValueError("duration_seconds must be a positive finite value")
        duration = float(duration_seconds)
    else:
        duration = None

    seen_ids: set[str] = set()
    previous_end: float | None = None
    for expected_index, chunk in enumerate(ordered):
        if not isinstance(chunk, MacroChunk):
            raise ValueError("macro chunk plan contains a non-MacroChunk row")
        if chunk.index != expected_index:
            raise ValueError(
                f"macro chunk indexes must be contiguous from zero; expected {expected_index}, got {chunk.index}"
            )
        if chunk.chunk_id in seen_ids:
            raise ValueError(f"macro chunk ids must be unique: {chunk.chunk_id!r}")
        seen_ids.add(chunk.chunk_id)
        if previous_end is None:
            if not isclose(chunk.source_start, 0.0, abs_tol=1e-6):
                raise ValueError("macro chunk plan must start at source time zero")
        elif not isclose(chunk.source_start, previous_end, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError("macro chunk source ranges must be contiguous without gaps or overlaps")
        if duration is not None:
            if chunk.source_end > duration + 1e-6:
                raise ValueError("macro chunk source range exceeds media duration")
            if chunk.context_end > duration + 1e-6:
                raise ValueError("macro chunk context range exceeds media duration")
        previous_end = chunk.source_end

    if duration is not None and not isclose(previous_end or 0.0, duration, rel_tol=1e-9, abs_tol=1e-6):
        raise ValueError("macro chunk plan does not cover the complete media timeline")
    return ordered


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


def segments_for_macro_chunk(segments: Iterable[Segment], chunk: MacroChunk) -> list[Segment]:
    """Assign each segment to one macro chunk by global midpoint ownership."""
    owned: list[Segment] = []
    for segment in segments:
        midpoint = (float(segment.start) + float(segment.end)) / 2.0
        if chunk.source_start <= midpoint < chunk.source_end:
            owned.append(segment)
    return owned
