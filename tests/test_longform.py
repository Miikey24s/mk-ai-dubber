import pytest

from vi_dubber.longform import MacroChunkPolicy, SpeechInterval, plan_macro_chunks


def _policy(**overrides: float) -> MacroChunkPolicy:
    values = {
        "target_seconds": 100.0,
        "min_seconds": 70.0,
        "max_seconds": 140.0,
        "boundary_search_seconds": 20.0,
        "context_seconds": 3.0,
        "single_chunk_threshold_seconds": 60.0,
    }
    values.update(overrides)
    return MacroChunkPolicy(**values)


def test_short_media_stays_one_work_unit() -> None:
    chunks = plan_macro_chunks(45.0, [SpeechInterval(0.0, 44.0)], policy=_policy())

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "chunk_0001"
    assert chunks[0].source_start == 0.0
    assert chunks[0].source_end == 45.0
    assert chunks[0].boundary_reason == "single_chunk"


def test_planner_prefers_silence_near_target_instead_of_cutting_speech() -> None:
    speech = [SpeechInterval(0.0, 94.0), SpeechInterval(108.0, 210.0)]

    chunks = plan_macro_chunks(210.0, speech, policy=_policy())

    assert len(chunks) == 2
    assert chunks[0].source_end == pytest.approx(101.0)
    assert chunks[0].boundary_reason == "silence_near_target"
    assert chunks[1].source_start == chunks[0].source_end


def test_planner_uses_silence_inside_bounds_when_search_window_has_none() -> None:
    speech = [SpeechInterval(0.0, 72.0), SpeechInterval(78.0, 190.0)]

    chunks = plan_macro_chunks(190.0, speech, policy=_policy(boundary_search_seconds=5.0))

    assert chunks[0].source_end == pytest.approx(75.0)
    assert chunks[0].boundary_reason == "silence_in_range"


def test_planner_falls_back_deterministically_when_no_silence_exists() -> None:
    chunks = plan_macro_chunks(240.0, [SpeechInterval(0.0, 240.0)], policy=_policy())

    assert [chunk.source_end for chunk in chunks] == [100.0, 240.0]
    assert [chunk.boundary_reason for chunk in chunks] == ["safe_cut", "end"]
    assert all(chunk.duration <= 140.0 for chunk in chunks)


def test_source_ranges_cover_global_timeline_exactly_and_context_is_bounded() -> None:
    chunks = plan_macro_chunks(330.0, policy=_policy())

    assert chunks[0].source_start == 0.0
    assert chunks[-1].source_end == 330.0
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.source_end == current.source_start
        assert current.context_start <= current.source_start
        assert previous.context_end >= previous.source_end
    assert all(0.0 <= chunk.context_start <= chunk.context_end <= 330.0 for chunk in chunks)


def test_chunk_local_timestamp_maps_back_to_global_timeline() -> None:
    chunks = plan_macro_chunks(240.0, [SpeechInterval(0.0, 240.0)], policy=_policy())

    assert chunks[1].local_to_global(12.5) == pytest.approx(112.5)
    with pytest.raises(ValueError):
        chunks[1].local_to_global(chunks[1].duration + 0.1)


def test_speech_intervals_are_sorted_merged_and_clipped_before_planning() -> None:
    speech = [
        SpeechInterval(108.0, 220.0),
        SpeechInterval(0.0, 80.0),
        SpeechInterval(70.0, 94.0),
    ]

    chunks = plan_macro_chunks(210.0, speech, policy=_policy())

    assert chunks[0].source_end == pytest.approx(101.0)


def test_invalid_policy_and_duration_fail_closed() -> None:
    with pytest.raises(ValueError):
        MacroChunkPolicy(min_seconds=100.0, target_seconds=50.0, max_seconds=120.0)
    with pytest.raises(ValueError):
        plan_macro_chunks(0.0)
