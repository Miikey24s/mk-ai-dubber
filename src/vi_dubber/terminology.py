from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml

from .types import Segment


TERMINOLOGY_POLICY_VERSION = 1
STRICT_POLICIES = frozenset({"KEEP_EN", "PREFER_EN", "VI"})
SUPPORTED_POLICIES = STRICT_POLICIES | {"CONTEXTUAL"}


@dataclass(frozen=True, slots=True)
class TerminologyEntry:
    source: str
    policy: str
    display: str
    spoken: str | None = None
    first_mention: str | None = None
    aliases: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    domain: str | None = None
    status: str = "approved"

    def to_prompt_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, (), "")}


class TerminologyGlossary(dict[str, str]):
    """Backward-compatible display glossary with optional policy metadata."""

    def __init__(
        self,
        terms: Mapping[str, str] | None = None,
        *,
        entries: Iterable[TerminologyEntry] = (),
        profile: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__((str(key), str(value)) for key, value in (terms or {}).items())
        self.entries = tuple(entries)
        self.profile = {str(key): str(value) for key, value in (profile or {}).items()}

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "display_map": dict(self),
            "audience_profile": dict(self.profile),
            "terminology_policy_version": TERMINOLOGY_POLICY_VERSION,
            "terminology": [entry.to_prompt_dict() for entry in self.entries],
        }

    def spoken_map(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for entry in self.entries:
            spoken = str(entry.spoken or "").strip()
            if spoken and spoken.casefold() != entry.display.casefold():
                result[entry.display] = spoken
        return result


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (_require_text(value, field),)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a string or list")
    return tuple(_require_text(item, field) for item in value)


def load_terminology_glossary(path: Path | None) -> TerminologyGlossary:
    if path is None or not path.exists():
        return TerminologyGlossary()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Glossary root must be an object: {path}")
    raw_terms = raw.get("terms", raw)
    if not isinstance(raw_terms, dict):
        raise ValueError(f"Glossary terms must be an object: {path}")
    terms = {str(key): str(value) for key, value in raw_terms.items()}

    raw_profile = raw.get("profile", {}) if "terms" in raw else {}
    if raw_profile is None:
        raw_profile = {}
    if not isinstance(raw_profile, dict):
        raise ValueError("glossary.profile must be an object")

    raw_policy = raw.get("terminology", {}) if "terms" in raw else {}
    if raw_policy is None:
        raw_policy = {}
    if not isinstance(raw_policy, dict):
        raise ValueError("glossary.terminology must be an object")

    entries: list[TerminologyEntry] = []
    for source, metadata in raw_policy.items():
        source_text = _require_text(source, "terminology source")
        if not isinstance(metadata, dict):
            raise ValueError(f"terminology.{source_text} must be an object")
        policy = _require_text(metadata.get("policy"), f"terminology.{source_text}.policy").upper()
        if policy not in SUPPORTED_POLICIES:
            raise ValueError(
                f"terminology.{source_text}.policy must be one of {sorted(SUPPORTED_POLICIES)}"
            )
        configured_display = metadata.get("display")
        display = str(configured_display or terms.get(source_text) or source_text).strip()
        if not display:
            raise ValueError(f"terminology.{source_text}.display must be non-empty")
        if source_text in terms and configured_display is not None:
            if terms[source_text].casefold() != display.casefold():
                raise ValueError(
                    f"terminology.{source_text}.display conflicts with terms mapping"
                )
        terms.setdefault(source_text, display)
        entries.append(
            TerminologyEntry(
                source=source_text,
                policy=policy,
                display=display,
                spoken=(str(metadata["spoken"]).strip() if metadata.get("spoken") else None),
                first_mention=(
                    str(metadata["first_mention"]).strip()
                    if metadata.get("first_mention")
                    else None
                ),
                aliases=_string_tuple(metadata.get("aliases"), f"terminology.{source_text}.aliases"),
                rejected=_string_tuple(
                    metadata.get("rejected"), f"terminology.{source_text}.rejected"
                ),
                domain=(str(metadata["domain"]).strip() if metadata.get("domain") else None),
                status=str(metadata.get("status") or "approved").strip(),
            )
        )
    return TerminologyGlossary(terms, entries=entries, profile=raw_profile)


def glossary_prompt_json(glossary: Mapping[str, str]) -> str:
    payload: Any = (
        glossary.prompt_payload()
        if isinstance(glossary, TerminologyGlossary) and glossary.entries
        else dict(glossary)
    )
    return json.dumps(payload, ensure_ascii=False)


def merge_pronunciation_map(
    configured: Mapping[str, str] | None,
    glossary: Mapping[str, str],
) -> dict[str, str]:
    merged: dict[str, str] = {}
    if isinstance(glossary, TerminologyGlossary):
        merged.update(glossary.spoken_map())
    merged.update({str(key): str(value) for key, value in (configured or {}).items()})
    return merged


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    prefix = r"(?<!\w)" if term and term[0].isalnum() else ""
    suffix = r"(?!\w)" if term and term[-1].isalnum() else ""
    return re.compile(f"{prefix}{escaped}{suffix}", flags=re.IGNORECASE)


def _contains(text: str, term: str) -> bool:
    return bool(term and _term_pattern(term).search(text))


def _entry_applies(entry: TerminologyEntry, source_text: str) -> bool:
    return any(_contains(source_text, term) for term in (entry.source, *entry.aliases))


def validate_terminology_candidate(
    segment: Segment,
    candidate_text: str,
    glossary: Mapping[str, str],
) -> dict[str, Any]:
    if not isinstance(glossary, TerminologyGlossary) or not glossary.entries:
        return {
            "policy_version": TERMINOLOGY_POLICY_VERSION,
            "segment_id": segment.id,
            "checked_terms": 0,
            "passed": True,
            "violations": [],
        }
    violations: list[dict[str, Any]] = []
    checked = 0
    candidate = str(candidate_text or "")
    for entry in glossary.entries:
        if entry.status.casefold() != "approved" or not _entry_applies(entry, segment.text):
            continue
        checked += 1
        rejected_hits = [term for term in entry.rejected if _contains(candidate, term)]
        if rejected_hits:
            violations.append(
                {
                    "source": entry.source,
                    "policy": entry.policy,
                    "code": "rejected_form",
                    "found": rejected_hits,
                }
            )
        if entry.policy in STRICT_POLICIES and not _contains(candidate, entry.display):
            violations.append(
                {
                    "source": entry.source,
                    "policy": entry.policy,
                    "code": "missing_approved_display",
                    "expected": entry.display,
                }
            )
    return {
        "policy_version": TERMINOLOGY_POLICY_VERSION,
        "segment_id": segment.id,
        "checked_terms": checked,
        "passed": not violations,
        "violations": violations,
    }


def validate_terminology_segments(
    segments: Iterable[Segment],
    glossary: Mapping[str, str],
) -> dict[str, Any]:
    items = [validate_terminology_candidate(segment, segment.vi, glossary) for segment in segments]
    failed = [item for item in items if not item["passed"]]
    return {
        "policy_version": TERMINOLOGY_POLICY_VERSION,
        "passed": not failed,
        "segments_checked": len(items),
        "terms_checked": sum(int(item["checked_terms"]) for item in items),
        "failed_segments": len(failed),
        "items": items,
    }
