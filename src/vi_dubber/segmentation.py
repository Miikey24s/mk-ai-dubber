from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .types import Segment, WordToken


SEGMENTATION_POLICY_VERSION = 2
_STRONG_END = (".", "?", "!", ";", ":")
_SOFT_END = (",",)


@dataclass(slots=True)
class _Atom:
    text: str
    start: float
    end: float
    speaker: str
    source_segment_id: int
    overlap: bool
    scene_id: str | None
    scene_boundary: bool
    word: WordToken | None = None


def _effective_word_bounds(segment: Segment, index: int) -> tuple[float, float]:
    word = segment.words[index]
    start = word.start
    end = word.end

    if start is None:
        previous_end = next(
            (item.end for item in reversed(segment.words[:index]) if item.end is not None),
            None,
        )
        start = previous_end if previous_end is not None else segment.start
    if end is None:
        next_start = next(
            (item.start for item in segment.words[index + 1 :] if item.start is not None),
            None,
        )
        end = next_start if next_start is not None else segment.end

    start = min(max(float(start), segment.start), segment.end)
    end = min(max(float(end), start), segment.end)
    return start, end


def _atoms(segments: list[Segment]) -> list[_Atom]:
    result: list[_Atom] = []
    for segment in segments:
        if segment.words:
            for index, word in enumerate(segment.words):
                text = word.text.strip()
                if not text:
                    continue
                start, end = _effective_word_bounds(segment, index)
                result.append(
                    _Atom(
                        text=text,
                        start=start,
                        end=end,
                        speaker=word.speaker or segment.speaker,
                        source_segment_id=segment.id,
                        overlap=segment.overlap or word.overlap,
                        scene_id=segment.scene_id,
                        scene_boundary=segment.scene_boundary,
                        word=word,
                    )
                )
        else:
            text = segment.text.strip()
            if text:
                result.append(
                    _Atom(
                        text=text,
                        start=segment.start,
                        end=segment.end,
                        speaker=segment.speaker,
                        source_segment_id=segment.id,
                        overlap=segment.overlap,
                        scene_id=segment.scene_id,
                        scene_boundary=segment.scene_boundary,
                    )
                )
    return result


def _join_text(parts: list[str]) -> str:
    text = " ".join(part.strip() for part in parts if part.strip())
    return re.sub(r"\s+([,.;:!?])", r"\1", text).strip()


def _protected_boundary(previous: _Atom, atom: _Atom) -> bool:
    if atom.speaker != previous.speaker:
        return True
    crosses_source_segment = atom.source_segment_id != previous.source_segment_id
    scene_changed = (
        crosses_source_segment
        and previous.scene_id is not None
        and atom.scene_id is not None
        and previous.scene_id != atom.scene_id
    )
    if atom.overlap != previous.overlap:
        return True
    return crosses_source_segment and (
        atom.scene_boundary or atom.overlap or previous.overlap or scene_changed
    )


def _compact_short_groups(
    groups: list[list[_Atom]],
    *,
    min_duration: float,
    max_duration: float,
    pause_threshold: float,
    max_words: int | None,
) -> list[list[_Atom]]:
    if min_duration <= 0 or len(groups) < 2:
        return groups

    def duration(group: list[_Atom]) -> float:
        return max(0.0, group[-1].end - group[0].start)

    def merge_score(left: list[_Atom], right: list[_Atom]) -> tuple[int, float] | None:
        if _protected_boundary(left[-1], right[0]):
            return None
        if max_words is not None and len(left) + len(right) > max_words:
            return None
        gap = max(0.0, right[0].start - left[-1].end)
        if gap > max(pause_threshold, min_duration):
            return None
        if right[-1].end - left[0].start > max_duration:
            return None
        shares_source = bool(
            {atom.source_segment_id for atom in left}
            & {atom.source_segment_id for atom in right}
        )
        return (0 if shares_source else 1, gap)

    compacted = [list(group) for group in groups]
    index = 0
    while index < len(compacted):
        if duration(compacted[index]) >= min_duration:
            index += 1
            continue

        candidates: list[tuple[tuple[int, float], str]] = []
        if index > 0:
            score = merge_score(compacted[index - 1], compacted[index])
            if score is not None:
                candidates.append((score, "previous"))
        if index + 1 < len(compacted):
            score = merge_score(compacted[index], compacted[index + 1])
            if score is not None:
                candidates.append((score, "next"))
        if not candidates:
            index += 1
            continue

        _, direction = min(candidates, key=lambda item: item[0])
        if direction == "previous":
            compacted[index - 1].extend(compacted[index])
            compacted.pop(index)
            index = max(0, index - 1)
        else:
            compacted[index].extend(compacted[index + 1])
            compacted.pop(index + 1)

    return compacted


def build_speech_turns(
    segments: list[Segment],
    config: dict[str, Any] | None = None,
) -> list[Segment]:
    """Build deterministic speech turns while preserving word-level provenance."""
    if not segments:
        return []

    cfg = config or {}
    min_duration = max(0.0, float(cfg.get("min_duration", 1.2)))
    target_duration = max(min_duration, float(cfg.get("target_duration", 3.6)))
    max_duration = max(target_duration, float(cfg.get("max_duration", 7.0)))
    pause_threshold = max(0.0, float(cfg.get("pause_threshold", 0.80)))
    soft_pause_threshold = min(
        pause_threshold,
        max(0.0, float(cfg.get("soft_pause_threshold", 0.28))),
    )
    min_words = max(1, int(cfg.get("min_words", 3)))
    raw_max_words = cfg.get("max_words")
    max_words = (
        None
        if raw_max_words is None or raw_max_words == "" or raw_max_words == 0 or raw_max_words == "0"
        else max(1, int(raw_max_words))
    )

    source_by_id = {segment.id: segment for segment in segments}
    atoms = _atoms(segments)
    if not atoms:
        return []

    groups: list[list[_Atom]] = []
    current: list[_Atom] = []

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for atom in atoms:
        if not current:
            current.append(atom)
            continue

        previous = current[-1]
        gap = max(0.0, atom.start - previous.end)
        candidate_duration = max(0.0, atom.end - current[0].start)
        word_limit_reached = max_words is not None and len(current) >= max_words
        current_duration = max(0.0, previous.end - current[0].start)
        hard_pause = gap > max(pause_threshold, min_duration)

        if (
            _protected_boundary(previous, atom)
            or word_limit_reached
            or hard_pause
            or (gap > pause_threshold and current_duration >= min_duration)
            or candidate_duration > max_duration
        ):
            flush()
            current.append(atom)
            continue

        current.append(atom)
        duration = max(0.0, current[-1].end - current[0].start)
        enough_words = len(current) >= min_words
        punctuation_break = previous.text.endswith(_STRONG_END)
        soft_break = previous.text.endswith(_SOFT_END) and duration >= target_duration
        silence_break = gap >= soft_pause_threshold and duration >= min_duration
        target_break = duration >= target_duration and (punctuation_break or soft_break or silence_break)

        if duration >= min_duration and enough_words and target_break:
            # The just-added atom belongs after the boundary represented by
            # `previous`, so move it into the next turn.
            tail = current.pop()
            flush()
            current.append(tail)

    flush()
    groups = _compact_short_groups(
        groups,
        min_duration=min_duration,
        max_duration=max_duration,
        pause_threshold=pause_threshold,
        max_words=max_words,
    )

    turns: list[Segment] = []
    for turn_id, group in enumerate(groups):
        source_ids = list(dict.fromkeys(atom.source_segment_id for atom in group))
        avg_logprobs = [
            source_by_id[source_id].avg_logprob
            for source_id in source_ids
            if source_by_id[source_id].avg_logprob is not None
        ]
        words = [atom.word for atom in group if atom.word is not None]
        scene_ids = {atom.scene_id for atom in group if atom.scene_id is not None}
        turns.append(
            Segment(
                id=turn_id,
                start=group[0].start,
                end=group[-1].end,
                text=_join_text([atom.text for atom in group]),
                speaker=group[0].speaker,
                words=words,
                avg_logprob=(sum(avg_logprobs) / len(avg_logprobs) if avg_logprobs else None),
                source_segment_ids=source_ids,
                overlap=any(atom.overlap for atom in group),
                scene_id=next(iter(scene_ids)) if len(scene_ids) == 1 else None,
                scene_boundary=group[0].scene_boundary,
            )
        )
    return turns


def segmentation_summary(segments: list[Segment]) -> dict[str, Any]:
    durations = [segment.duration for segment in segments]
    ordered = sorted(durations)

    def percentile(fraction: float) -> float:
        if not ordered:
            return 0.0
        index = round((len(ordered) - 1) * fraction)
        return ordered[index]

    return {
        "segments": len(segments),
        "under_1s": sum(duration < 1.0 for duration in durations),
        "under_1s_rate": (
            sum(duration < 1.0 for duration in durations) / len(durations)
            if durations
            else 0.0
        ),
        "mean_duration": sum(durations) / len(durations) if durations else 0.0,
        "min_duration": ordered[0] if ordered else 0.0,
        "p50_duration": percentile(0.50),
        "p90_duration": percentile(0.90),
        "max_duration": ordered[-1] if ordered else 0.0,
    }
