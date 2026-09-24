from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Mapping

from .types import Segment


TIMING_POLICY_VERSION = 3


@dataclass(slots=True)
class TimingWindow:
    segment_id: int
    start: float
    end: float
    target_duration: float
    borrowed_before: float = 0.0
    borrowed_after: float = 0.0


def allocate_timing_windows(
    segments: list[Segment],
    *,
    pad_seconds: float = 0.0,
    max_borrow_seconds: float = 0.35,
) -> dict[int, TimingWindow]:
    """Allocate conservative per-segment windows using only adjacent silence.

    Borrowing never crosses another segment. Overlapping source segments keep
    their original windows because there is no unambiguous silence to borrow.
    """
    borrow_cap = max(0.0, float(max_borrow_seconds))
    pad = max(0.0, float(pad_seconds))
    ordered = sorted(segments, key=lambda item: (item.start, item.end, item.id))
    result: dict[int, TimingWindow] = {}
    for index, segment in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        borrow_before = 0.0
        borrow_after = 0.0
        if not segment.overlap:
            if previous is not None and previous.end <= segment.start:
                gap = max(0.0, segment.start - previous.end)
                borrow_before = min(borrow_cap, gap * 0.5)
            if following is not None and segment.end <= following.start:
                gap = max(0.0, following.start - segment.end)
                borrow_after = min(borrow_cap, gap * 0.5)

        start = max(0.0, segment.start - borrow_before)
        end = max(start, segment.end + borrow_after - pad)
        result[segment.id] = TimingWindow(
            segment_id=segment.id,
            start=start,
            end=end,
            target_duration=max(0.25, end - start),
            borrowed_before=borrow_before,
            borrowed_after=borrow_after,
        )
    return result


def rebalance_timing_windows(
    segments: list[Segment],
    windows: Mapping[int, TimingWindow],
    measured_durations: Mapping[int, float],
    *,
    preferred_tempo: float = 1.20,
    max_extra_borrow_seconds: float = 0.35,
) -> dict[int, TimingWindow]:
    """Use measured TTS duration to reclaim safe adjacent silence.

    Existing timing windows remain the baseline. A phrase may first consume
    unused real silence, then reclaim a neighbour's borrowed silence only when
    that neighbour still fits at the preferred tempo. Source segment bounds are
    hard anchors, so the adjusted windows never cross another phrase.
    """
    tempo = max(1.0, float(preferred_tempo))
    borrow_cap = max(0.0, float(max_extra_borrow_seconds))
    ordered = sorted(segments, key=lambda item: (item.start, item.end, item.id))
    result = {segment_id: replace(window) for segment_id, window in windows.items()}
    borrowed_extra: dict[int, float] = {segment.id: 0.0 for segment in ordered}

    def _duration(segment: Segment) -> float | None:
        value = measured_durations.get(segment.id)
        if value is None:
            return None
        return max(0.0, float(value))

    def _required_target(segment: Segment) -> float | None:
        duration = _duration(segment)
        if duration is None:
            return None
        return duration / tempo

    def _refresh(segment: Segment) -> None:
        window = result[segment.id]
        window.target_duration = max(0.25, window.end - window.start)
        window.borrowed_before = max(0.0, segment.start - window.start)
        window.borrowed_after = max(0.0, window.end - segment.end)

    def _compatible(left: Segment, right: Segment) -> bool:
        return (
            left.speaker == right.speaker
            and not left.overlap
            and not right.overlap
            and left.end <= right.start
        )

    # Two passes let a segment use slack exposed by an earlier boundary shift
    # without turning this into an unbounded optimizer.
    for _pass in range(2):
        changed = False
        for index, segment in enumerate(ordered):
            window = result.get(segment.id)
            required = _required_target(segment)
            if window is None or required is None or window.target_duration >= required - 1e-9:
                continue

            remaining = min(
                required - window.target_duration,
                max(0.0, borrow_cap - borrowed_extra[segment.id]),
            )
            if remaining <= 1e-9:
                continue

            previous = ordered[index - 1] if index else None
            if previous is not None and previous.id in result and _compatible(previous, segment):
                previous_window = result[previous.id]

                unused = max(0.0, window.start - previous_window.end)
                take = min(remaining, unused)
                if take > 0.0:
                    window.start -= take
                    borrowed_extra[segment.id] += take
                    remaining -= take
                    _refresh(segment)
                    changed = True

                if remaining > 1e-9:
                    previous_required = _required_target(previous)
                    if previous_required is not None:
                        tempo_spare = max(0.0, previous_window.target_duration - previous_required)
                        anchor_spare = max(0.0, previous_window.end - previous.end)
                        take = min(remaining, tempo_spare, anchor_spare)
                        if take > 0.0:
                            previous_window.end -= take
                            window.start -= take
                            borrowed_extra[segment.id] += take
                            remaining -= take
                            _refresh(previous)
                            _refresh(segment)
                            changed = True

            following = ordered[index + 1] if index + 1 < len(ordered) else None
            if (
                remaining > 1e-9
                and following is not None
                and following.id in result
                and _compatible(segment, following)
            ):
                following_window = result[following.id]

                unused = max(0.0, following_window.start - window.end)
                take = min(remaining, unused)
                if take > 0.0:
                    window.end += take
                    borrowed_extra[segment.id] += take
                    remaining -= take
                    _refresh(segment)
                    changed = True

                if remaining > 1e-9:
                    following_required = _required_target(following)
                    if following_required is not None:
                        tempo_spare = max(0.0, following_window.target_duration - following_required)
                        anchor_spare = max(0.0, following.start - following_window.start)
                        take = min(remaining, tempo_spare, anchor_spare)
                        if take > 0.0:
                            window.end += take
                            following_window.start += take
                            borrowed_extra[segment.id] += take
                            _refresh(segment)
                            _refresh(following)
                            changed = True

        if not changed:
            break

    return result


def timing_action(
    generated_duration: float,
    target_duration: float,
    *,
    max_speedup: float,
    rewrite_threshold: float,
    max_slowdown: float = 0.92,
) -> str:
    if target_duration <= 0:
        return "keep"
    ratio = max(0.0, generated_duration) / target_duration
    speedup_limit = max(1.0, float(max_speedup))
    rewrite_limit = max(1.0, float(rewrite_threshold))
    slowdown_limit = min(1.0, max(0.0, float(max_slowdown)))
    if ratio > rewrite_limit:
        return "rewrite"
    if ratio > 1.0:
        return "speedup" if ratio <= speedup_limit else "rewrite"
    if ratio < slowdown_limit:
        return "preserve_pause"
    if ratio < 1.0:
        return "slowdown"
    return "keep"
