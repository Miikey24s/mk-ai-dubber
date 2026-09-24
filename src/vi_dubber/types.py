from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SEGMENT_SCHEMA_VERSION = 2
_SUPPORTED_SEGMENT_SCHEMA_VERSIONS = {1, SEGMENT_SCHEMA_VERSION}


@dataclass(slots=True)
class WordToken:
    text: str
    start: float | None = None
    end: float | None = None
    confidence: float | None = None
    speaker: str | None = None
    overlap: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WordToken":
        start = data.get("start")
        end = data.get("end")
        confidence = data.get("confidence", data.get("score"))
        speaker = data.get("speaker")
        return cls(
            text=str(data.get("text", data.get("word", ""))).strip(),
            start=float(start) if start is not None else None,
            end=float(end) if end is not None else None,
            confidence=float(confidence) if confidence is not None else None,
            speaker=str(speaker) if speaker else None,
            overlap=bool(data.get("overlap", False)),
        )


@dataclass(slots=True)
class Segment:
    id: int
    start: float
    end: float
    text: str
    speaker: str = "SPEAKER_00"
    vi: str = ""
    words: list[WordToken] = field(default_factory=list)
    avg_logprob: float | None = None
    source_segment_ids: list[int] = field(default_factory=list)
    overlap: bool = False
    scene_id: str | None = None
    scene_boundary: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def speaker_visibility(self) -> dict[str, Any]:
        """Return deterministic metadata that keeps speaker/overlap hard cases visible."""
        speakers = {
            str(value).strip()
            for value in [self.speaker, *(word.speaker for word in self.words)]
            if value is not None and str(value).strip()
        }
        ordered_speakers = sorted(speakers)
        overlap = bool(self.overlap or any(word.overlap for word in self.words))
        multi_speaker = len(ordered_speakers) > 1
        return {
            "primary_speaker": self.speaker,
            "speakers": ordered_speakers,
            "speaker_count": len(ordered_speakers),
            "multi_speaker": multi_speaker,
            "overlap": overlap,
            "needs_review": overlap or multi_speaker,
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = SEGMENT_SCHEMA_VERSION
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Segment":
        raw_schema_version = data.get("schema_version", 1)
        try:
            schema_version = int(raw_schema_version)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid segment schema version: {raw_schema_version!r}") from exc
        if schema_version not in _SUPPORTED_SEGMENT_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported segment schema version: {schema_version}")

        raw_scene_id = data.get("scene_id")
        return cls(
            id=int(data["id"]),
            start=float(data["start"]),
            end=float(data["end"]),
            text=str(data.get("text", "")).strip(),
            speaker=str(data.get("speaker") or "SPEAKER_00"),
            vi=str(data.get("vi", "")).strip(),
            words=[
                WordToken.from_dict(item)
                for item in (data.get("words") or [])
                if isinstance(item, dict)
            ],
            avg_logprob=(
                float(data["avg_logprob"])
                if data.get("avg_logprob") is not None
                else None
            ),
            source_segment_ids=[int(value) for value in (data.get("source_segment_ids") or [])],
            overlap=bool(data.get("overlap", False)),
            scene_id=(str(raw_scene_id).strip() or None) if raw_scene_id is not None else None,
            scene_boundary=bool(data.get("scene_boundary", False)),
        )
