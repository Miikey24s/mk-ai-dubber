import pytest

from vi_dubber.segmentation import build_speech_turns, segmentation_summary
from vi_dubber.types import Segment, WordToken


def _segment(
    segment_id: int,
    start: float,
    end: float,
    text: str,
    *,
    speaker: str = "SPEAKER_00",
    words: list[WordToken] | None = None,
) -> Segment:
    return Segment(
        id=segment_id,
        start=start,
        end=end,
        text=text,
        speaker=speaker,
        words=words or [],
    )


def test_merges_short_same_speaker_segments_with_small_gap() -> None:
    source = [
        _segment(10, 0.0, 0.45, "This is"),
        _segment(11, 0.55, 1.10, "a test"),
        _segment(12, 1.20, 2.20, "of merging."),
    ]

    turns = build_speech_turns(source, {"min_duration": 1.2, "max_duration": 5.0})

    assert len(turns) == 1
    assert turns[0].text == "This is a test of merging."
    assert turns[0].source_segment_ids == [10, 11, 12]
    assert turns[0].start == 0.0
    assert turns[0].end == 2.2


def test_never_merges_across_speaker_change() -> None:
    source = [
        _segment(1, 0.0, 0.6, "Hello", speaker="A"),
        _segment(2, 0.65, 1.2, "there", speaker="B"),
    ]

    turns = build_speech_turns(source)

    assert [(turn.speaker, turn.text) for turn in turns] == [("A", "Hello"), ("B", "there")]


def test_word_speaker_boundary_can_split_inside_source_segment() -> None:
    source = [
        _segment(
            5,
            0.0,
            2.0,
            "yes right",
            words=[
                WordToken("yes", 0.0, 0.7, 0.9, "A"),
                WordToken("right", 0.8, 1.5, 0.9, "B"),
            ],
        )
    ]

    turns = build_speech_turns(source)

    assert len(turns) == 2
    assert turns[0].speaker == "A"
    assert turns[1].speaker == "B"
    assert turns[0].source_segment_ids == [5]
    assert turns[1].source_segment_ids == [5]


def test_long_word_sequence_splits_at_natural_boundary() -> None:
    words = [
        WordToken("one", 0.0, 0.7),
        WordToken("two", 0.8, 1.5),
        WordToken("three.", 1.6, 2.4),
        WordToken("four", 2.55, 3.3),
        WordToken("five", 3.4, 4.2),
        WordToken("six", 4.3, 5.1),
    ]
    source = [_segment(1, 0.0, 5.2, "one two three. four five six", words=words)]

    turns = build_speech_turns(
        source,
        {
            "min_duration": 1.0,
            "target_duration": 2.0,
            "max_duration": 5.0,
            "min_words": 2,
        },
    )

    assert [turn.text for turn in turns] == ["one two three.", "four five six"]
    assert [word.text for word in turns[0].words] == ["one", "two", "three."]


def test_natural_split_does_not_leave_a_short_tail_when_safe_to_rejoin() -> None:
    words = [
        WordToken("one", 0.0, 0.7),
        WordToken("two", 0.8, 1.5),
        WordToken("three.", 1.6, 2.4),
        WordToken("four", 2.55, 2.9),
    ]
    source = [_segment(1, 0.0, 3.0, "one two three. four", words=words)]

    turns = build_speech_turns(
        source,
        {
            "min_duration": 1.0,
            "target_duration": 2.0,
            "max_duration": 5.0,
            "min_words": 2,
        },
    )

    assert [turn.text for turn in turns] == ["one two three. four"]
    assert turns[0].duration == pytest.approx(2.9)


def test_missing_word_timestamps_do_not_crash_or_drop_word() -> None:
    source = [
        _segment(
            1,
            0.0,
            2.0,
            "hello uncertain world",
            words=[
                WordToken("hello", 0.0, 0.5, 0.9),
                WordToken("uncertain", None, None, None),
                WordToken("world", 1.4, 1.9, 0.8),
            ],
        )
    ]

    turns = build_speech_turns(source)

    assert "uncertain" in " ".join(turn.text for turn in turns)
    assert sum(len(turn.words) for turn in turns) == 3


def test_moderate_pause_does_not_orphan_a_sub_minimum_leading_word() -> None:
    source = [
        _segment(
            1,
            0.0,
            2.0,
            "If someone answers",
            words=[
                WordToken("If", 0.0, 0.3),
                WordToken("someone", 1.15, 1.5),
                WordToken("answers", 1.55, 1.95),
            ],
        )
    ]

    turns = build_speech_turns(
        source,
        {
            "min_duration": 1.2,
            "target_duration": 3.6,
            "max_duration": 7.0,
            "pause_threshold": 0.8,
        },
    )

    assert [turn.text for turn in turns] == ["If someone answers"]
    assert turns[0].duration == pytest.approx(1.95)


def test_overlap_signal_prevents_cross_segment_merge() -> None:
    source = [
        Segment(id=1, start=0.0, end=0.8, text="first", speaker="A", overlap=True),
        Segment(id=2, start=0.85, end=1.6, text="second", speaker="A"),
    ]

    turns = build_speech_turns(source, {"min_duration": 0.1, "max_duration": 5.0})

    assert [turn.text for turn in turns] == ["first", "second"]
    assert turns[0].overlap is True


def test_word_overlap_signal_creates_protected_boundaries_inside_segment() -> None:
    source = [
        _segment(
            1,
            0.0,
            1.5,
            "before overlap after",
            words=[
                WordToken("before", 0.0, 0.4, overlap=False),
                WordToken("overlap", 0.5, 0.9, overlap=True),
                WordToken("after", 1.0, 1.4, overlap=False),
            ],
        )
    ]

    turns = build_speech_turns(
        source,
        {"min_duration": 0.0, "target_duration": 10.0, "max_duration": 10.0},
    )

    assert [turn.text for turn in turns] == ["before", "overlap", "after"]
    assert [turn.overlap for turn in turns] == [False, True, False]
    assert all(turn.source_segment_ids == [1] for turn in turns)


def test_scene_signal_prevents_cross_scene_merge() -> None:
    source = [
        Segment(id=1, start=0.0, end=0.8, text="first", speaker="A", scene_id="scene-a"),
        Segment(id=2, start=0.85, end=1.6, text="second", speaker="A", scene_id="scene-b"),
    ]

    turns = build_speech_turns(source, {"min_duration": 0.1, "max_duration": 5.0})

    assert [turn.text for turn in turns] == ["first", "second"]
    assert [turn.scene_id for turn in turns] == ["scene-a", "scene-b"]


def test_configured_max_words_splits_without_reordering_text() -> None:
    words = [
        WordToken("one", 0.0, 0.2),
        WordToken("two", 0.25, 0.45),
        WordToken("three", 0.50, 0.70),
        WordToken("four", 0.75, 0.95),
    ]
    source = [_segment(1, 0.0, 1.0, "one two three four", words=words)]

    turns = build_speech_turns(
        source,
        {"min_duration": 0.0, "target_duration": 10.0, "max_duration": 10.0, "max_words": 2},
    )

    assert [turn.text for turn in turns] == ["one two", "three four"]
    assert " ".join(turn.text for turn in turns) == source[0].text


def test_segmentation_summary_tracks_short_window_rate() -> None:
    summary = segmentation_summary(
        [
            _segment(1, 0.0, 0.5, "a"),
            _segment(2, 1.0, 3.0, "b"),
        ]
    )
    assert summary["segments"] == 2
    assert summary["under_1s"] == 1
    assert summary["under_1s_rate"] == 0.5
    assert summary["min_duration"] == 0.5
    assert summary["p50_duration"] == 0.5
    assert summary["p90_duration"] == 2.0
    assert summary["max_duration"] == 2.0
