from __future__ import annotations

import pytest

from vi_dubber.timing import (
    TIMING_POLICY_VERSION,
    allocate_timing_windows,
    rebalance_timing_windows,
    timing_action,
)
from vi_dubber.types import Segment


def _segment(
    segment_id: int,
    start: float,
    end: float,
    *,
    speaker: str = "A",
    overlap: bool = False,
) -> Segment:
    return Segment(
        id=segment_id,
        start=start,
        end=end,
        text=f"segment {segment_id}",
        speaker=speaker,
        overlap=overlap,
    )


def test_allocator_partitions_shared_silence_without_window_collision() -> None:
    segments = [
        _segment(1, 1.0, 2.0),
        _segment(2, 2.6, 3.6),
    ]

    windows = allocate_timing_windows(
        segments,
        pad_seconds=0.0,
        max_borrow_seconds=0.35,
    )

    assert windows[1].borrowed_after == pytest.approx(0.3)
    assert windows[2].borrowed_before == pytest.approx(0.3)
    assert windows[1].end == pytest.approx(windows[2].start)
    assert windows[1].target_duration == pytest.approx(1.3)
    assert windows[2].target_duration == pytest.approx(1.3)


def test_allocator_keeps_overlap_window_fixed_and_clamps_negative_inputs() -> None:
    segments = [
        _segment(1, 1.0, 2.0, overlap=True),
        _segment(2, 2.8, 3.8),
    ]

    windows = allocate_timing_windows(
        segments,
        pad_seconds=-1.0,
        max_borrow_seconds=-0.5,
    )

    assert windows[1].start == pytest.approx(1.0)
    assert windows[1].end == pytest.approx(2.0)
    assert windows[1].borrowed_before == 0.0
    assert windows[1].borrowed_after == 0.0
    assert windows[2].start == pytest.approx(2.8)
    assert windows[2].end == pytest.approx(3.8)
    assert windows[2].borrowed_before == 0.0


def test_elastic_rebalance_uses_unused_silence_to_reach_preferred_tempo() -> None:
    segments = [
        _segment(1, 0.0, 1.0),
        _segment(2, 2.0, 3.0),
    ]
    baseline = allocate_timing_windows(segments, max_borrow_seconds=0.2)
    before = 1.5 / baseline[1].target_duration

    adjusted = rebalance_timing_windows(
        segments,
        baseline,
        {1: 1.5, 2: 1.0},
        preferred_tempo=1.20,
        max_extra_borrow_seconds=0.35,
    )

    assert before > 1.20
    assert 1.5 / adjusted[1].target_duration == pytest.approx(1.20)
    assert adjusted[1].end <= adjusted[2].start
    assert adjusted[1].end <= segments[1].start


def test_elastic_rebalance_reclaims_neighbor_slack_only_when_neighbor_stays_preferred() -> None:
    segments = [
        _segment(1, 0.0, 1.0),
        _segment(2, 1.4, 2.4),
    ]
    baseline = allocate_timing_windows(segments, max_borrow_seconds=0.2)

    adjusted = rebalance_timing_windows(
        segments,
        baseline,
        {1: 1.5, 2: 1.0},
        preferred_tempo=1.20,
        max_extra_borrow_seconds=0.35,
    )

    assert 1.5 / baseline[1].target_duration > 1.20
    assert 1.5 / adjusted[1].target_duration == pytest.approx(1.20)
    assert 1.0 / adjusted[2].target_duration <= 1.20
    assert adjusted[1].end == pytest.approx(adjusted[2].start)
    assert adjusted[1].end <= segments[1].start


def test_elastic_rebalance_keeps_hard_fallback_when_cross_speaker_borrow_is_impossible() -> None:
    segments = [
        _segment(1, 0.0, 1.0, speaker="A"),
        _segment(2, 1.4, 2.4, speaker="B"),
    ]
    baseline = allocate_timing_windows(segments, max_borrow_seconds=0.2)

    adjusted = rebalance_timing_windows(
        segments,
        baseline,
        {1: 1.5, 2: 1.0},
        preferred_tempo=1.20,
        max_extra_borrow_seconds=0.35,
    )

    ratio = 1.5 / adjusted[1].target_duration
    assert adjusted[1] == baseline[1]
    assert ratio == pytest.approx(1.25)
    assert timing_action(
        1.5,
        adjusted[1].target_duration,
        max_speedup=1.25,
        rewrite_threshold=1.25,
    ) == "speedup"


@pytest.mark.parametrize(
    ("generated", "target", "expected"),
    [
        (1.00, 1.00, "keep"),
        (0.95, 1.00, "slowdown"),
        (0.80, 1.00, "preserve_pause"),
        (1.15, 1.00, "speedup"),
        (1.30, 1.00, "rewrite"),
    ],
)
def test_timing_action_routes_gentle_fit_and_large_mismatch(
    generated: float,
    target: float,
    expected: str,
) -> None:
    assert timing_action(
        generated,
        target,
        max_speedup=1.25,
        rewrite_threshold=1.25,
        max_slowdown=0.92,
    ) == expected


def test_timing_policy_version_is_explicit_for_cache_integration() -> None:
    assert TIMING_POLICY_VERSION >= 3
