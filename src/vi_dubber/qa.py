from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from collections.abc import Callable, Iterable
from typing import Any

from rapidfuzz.fuzz import ratio

from .pronunciation import normalize_pronunciation


SEGMENT_QA_POLICY_VERSION = 6


_GROUPED_INTEGER_RE = re.compile(r"(?<!\w)\d{1,3}(?:[.,]\d{3})+(?!\w)")
_GROUPED_INTEGER_SPACES_RE = re.compile(r"(?<!\w)\d{1,3}(?:\s\d{3})+(?!\w)")
_ASR_ZERO_PREFIX_DECIMAL_RE = re.compile(r"(?<!\w)0[.,](\d+)(?!\w)")
_ASR_ZERO_PREFIX_INTEGER_RE = re.compile(r"(?<!\w)0+(\d+)(?!\w)")
_LATIN_TECHNICAL_TERM_RE = re.compile(r"[a-z]+(?:\s+[a-z]+)*")


def normalize_spoken_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_spoken_equivalent(text: str) -> str:
    """Canonicalize deterministic numeric variants for ASR-vs-TTS comparison only."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = _GROUPED_INTEGER_RE.sub(
        lambda match: match.group(0).replace(",", "").replace(".", ""),
        value,
    )
    return normalize_spoken_text(normalize_pronunciation(value, normalize_numbers=True).tts_text)


def _normalize_asr_critical_equivalent(text: str) -> str:
    """Normalize narrow Whisper number-format artifacts for critical-token presence checks."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    # Whisper can render a spoken frame such as "khung mười lăm" as "0.15"
    # and a leading-zero integer such as "04". This alias is intentionally
    # limited to acoustic critical-token matching; semantic number checks keep
    # the stricter normalization above.
    value = _ASR_ZERO_PREFIX_DECIMAL_RE.sub(lambda match: match.group(1), value)
    value = _ASR_ZERO_PREFIX_INTEGER_RE.sub(lambda match: match.group(1), value)
    return normalize_spoken_equivalent(value)


def _critical_token_variants(token: str) -> set[str]:
    raw = normalize_spoken_text(token)
    variants = {raw, normalize_spoken_equivalent(token)}
    if _GROUPED_INTEGER_SPACES_RE.fullmatch(raw):
        collapsed = re.sub(r"\s+", "", raw)
        variants.add(normalize_spoken_equivalent(collapsed))
    return {item for item in variants if item}


def _latin_technical_asr_match(token: str, actual: str) -> bool:
    """Accept narrow Whisper spelling splits for long English technical terms."""
    normalized = normalize_spoken_text(token)
    if not _LATIN_TECHNICAL_TERM_RE.fullmatch(normalized):
        return False
    target = normalized.replace(" ", "")
    if len(target) < 6:
        return False

    target_skeleton = re.sub(r"[aeiou]", "", target)
    words = [
        word
        for word in normalize_spoken_text(actual).split()
        if re.fullmatch(r"[a-z]+", word)
    ]
    for start in range(len(words)):
        candidate = ""
        for end in range(start, min(len(words), start + 3)):
            candidate += words[end]
            if len(candidate) < len(target) - 3:
                continue
            if len(candidate) > len(target) + 3:
                break
            candidate_skeleton = re.sub(r"[aeiou]", "", candidate)
            if not candidate_skeleton or not target_skeleton:
                continue
            raw_score = float(ratio(target, candidate)) / 100.0
            skeleton_score = float(ratio(target_skeleton, candidate_skeleton)) / 100.0
            if raw_score >= 0.75 and skeleton_score >= 0.88:
                return True
    return False


def transcript_similarity(expected: str, actual: str) -> float:
    a = normalize_spoken_text(expected)
    b = normalize_spoken_text(actual)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    raw_ratio = float(ratio(a, b)) / 100.0
    spoken_a = normalize_spoken_equivalent(expected)
    spoken_b = normalize_spoken_equivalent(actual)
    spoken_ratio = float(ratio(spoken_a, spoken_b)) / 100.0 if spoken_a and spoken_b else 0.0
    return max(raw_ratio, spoken_ratio)


_NEGATIONS = {"không", "chẳng", "chưa", "đừng", "khỏi"}
_MODALITY = {"phải", "cần", "nên", "sẽ", "được", "có thể"}


def critical_spoken_tokens(text: str, glossary_terms: Iterable[str] = ()) -> set[str]:
    normalized = normalize_spoken_text(text)
    padded = f" {normalized} "
    critical = {
        normalized_token
        for token in _NEGATIONS | _MODALITY
        if (normalized_token := normalize_spoken_text(token))
        and f" {normalized_token} " in padded
    }
    critical.update(
        normalized_number
        for match in re.findall(r"\b\d+(?:[.,]\d+)?\b", unicodedata.normalize("NFKC", text))
        if (normalized_number := normalize_spoken_text(match))
    )
    for term in glossary_terms:
        normalized_term = normalize_spoken_text(str(term))
        if normalized_term and f" {normalized_term} " in padded:
            critical.add(normalized_term)
    return critical


@dataclass(slots=True)
class SegmentQAResult:
    segment_id: int
    similarity: float
    missing_critical: list[str]
    timing_ratio: float
    action: str
    passed: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SegmentQAObservation:
    segment_id: int
    expected: str
    actual: str
    target_duration: float
    actual_duration: float
    critical_terms: tuple[str, ...] = ()


def evaluate_segment_qa(
    segment_id: int,
    expected: str,
    actual: str,
    *,
    target_duration: float,
    actual_duration: float,
    glossary_terms: Iterable[str] = (),
    min_similarity: float = 0.78,
    severe_similarity: float = 0.55,
    max_timing_ratio: float = 1.03,
) -> SegmentQAResult:
    similarity = transcript_similarity(expected, actual)
    expected_critical = critical_spoken_tokens(expected, glossary_terms)
    actual_normalized = normalize_spoken_text(actual)
    actual_padded = f" {actual_normalized} "
    actual_spoken = normalize_spoken_equivalent(actual)
    actual_spoken_padded = f" {actual_spoken} "
    actual_asr_critical = _normalize_asr_critical_equivalent(actual)
    actual_asr_critical_padded = f" {actual_asr_critical} "
    missing = sorted(
        token
        for token in expected_critical
        if not any(
            f" {variant} " in actual_padded
            or f" {variant} " in actual_spoken_padded
            or f" {variant} " in actual_asr_critical_padded
            for variant in _critical_token_variants(token)
        )
        and not _latin_technical_asr_match(token, actual_normalized)
    )
    timing_ratio = actual_duration / max(0.001, target_duration)
    reasons: list[str] = []

    if missing:
        reasons.append("missing_critical")
    if similarity < severe_similarity:
        reasons.append("severe_transcript_mismatch")
    if timing_ratio > max_timing_ratio:
        reasons.append("timing_overflow")
    if severe_similarity <= similarity < min_similarity:
        reasons.append("transcript_mismatch")

    # This stage compares the rendered audio (via re-ASR) against the already
    # approved TTS script. A missing critical token here is therefore an
    # acoustic/rendering failure, not evidence that the translation text needs
    # to be rewritten. Semantic text repair is owned by the semantic QA gate.
    if missing or similarity < severe_similarity:
        action = "pronunciation_retry"
    elif timing_ratio > max_timing_ratio:
        action = "timing_rewrite"
    elif similarity < min_similarity:
        action = "pronunciation_retry"
    else:
        action = "pass"
    return SegmentQAResult(
        segment_id=segment_id,
        similarity=similarity,
        missing_critical=missing,
        timing_ratio=timing_ratio,
        action=action,
        passed=action == "pass",
        reasons=reasons,
    )


def selective_repair_ids(results: Iterable[SegmentQAResult]) -> dict[str, list[int]]:
    routed: dict[str, list[int]] = {}
    for result in results:
        if result.action == "pass":
            continue
        routed.setdefault(result.action, []).append(result.segment_id)
    return routed


def select_acoustic_qa_segment_ids(
    segment_ids: Iterable[int],
    *,
    risk_ids: Iterable[int] = (),
    scope: str = "all",
    sample_ratio: float = 0.08,
    sample_min: int = 2,
    sample_max: int = 6,
) -> dict[str, Any]:
    """Select expensive re-ASR checks while keeping deterministic QA on every segment."""
    ordered = list(dict.fromkeys(int(segment_id) for segment_id in segment_ids))
    normalized_scope = str(scope or "all").strip().casefold()
    if normalized_scope not in {"all", "risk"}:
        raise ValueError("segment QA scope must be 'all' or 'risk'")

    requested_risk = {int(item) for item in risk_ids}
    risk = [segment_id for segment_id in ordered if segment_id in requested_risk]
    if normalized_scope == "all":
        return {
            "scope": "all",
            "risk_segment_ids": risk,
            "sampled_segment_ids": [],
            "selected_segment_ids": ordered,
        }

    ratio = max(0.0, min(1.0, float(sample_ratio)))
    lower = max(0, int(sample_min))
    upper = max(lower, int(sample_max))
    risk_set = set(risk)
    remaining = [segment_id for segment_id in ordered if segment_id not in risk_set]
    target = min(upper, max(lower, int(round(len(ordered) * ratio)))) if remaining else 0
    target = min(target, len(remaining))

    sampled: list[int] = []
    if target == 1:
        sampled = [remaining[len(remaining) // 2]]
    elif target > 1:
        positions = [round(index * (len(remaining) - 1) / (target - 1)) for index in range(target)]
        sampled = list(dict.fromkeys(remaining[position] for position in positions))
        if len(sampled) < target:
            sampled_set = set(sampled)
            for segment_id in remaining:
                if segment_id in sampled_set:
                    continue
                sampled.append(segment_id)
                sampled_set.add(segment_id)
                if len(sampled) >= target:
                    break

    selected_set = risk_set | set(sampled)
    selected = [segment_id for segment_id in ordered if segment_id in selected_set]
    return {
        "scope": "risk",
        "risk_segment_ids": risk,
        "sampled_segment_ids": sampled,
        "selected_segment_ids": selected,
    }


_REPAIR_PRIORITY = {
    "text_repair": 0,
    "timing_rewrite": 1,
    "pronunciation_retry": 2,
}


def selective_repair_plan(
    results: Iterable[SegmentQAResult],
    *,
    max_segments: int | None = None,
) -> list[dict[str, Any]]:
    candidates = [result for result in results if not result.passed]
    candidates.sort(
        key=lambda result: (
            0 if result.missing_critical else _REPAIR_PRIORITY.get(result.action, 99),
            -len(result.missing_critical),
            result.similarity,
            -result.timing_ratio,
            result.segment_id,
        )
    )
    if max_segments is not None:
        candidates = candidates[: max(0, max_segments)]
    return [
        {
            "segment_id": result.segment_id,
            "action": result.action,
            "reasons": list(result.reasons),
            "similarity": result.similarity,
            "missing_critical": list(result.missing_critical),
            "timing_ratio": result.timing_ratio,
        }
        for result in candidates
    ]


def run_segment_qa_cycle(
    observations: Iterable[SegmentQAObservation],
    *,
    glossary_terms: Iterable[str] = (),
    min_similarity: float = 0.78,
    severe_similarity: float = 0.55,
    max_timing_ratio: float = 1.03,
    repair: Callable[[list[dict[str, Any]]], Iterable[SegmentQAObservation]] | None = None,
    repair_actions: Iterable[str] = ("pronunciation_retry", "timing_rewrite"),
    max_repairs: int | None = None,
) -> dict[str, Any]:
    """Evaluate segments and optionally re-evaluate only repaired observations."""
    ordered = list(observations)
    ids = [item.segment_id for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("segment QA observations contain duplicate segment ids")

    glossary = tuple(str(term) for term in glossary_terms)

    def evaluate(item: SegmentQAObservation) -> SegmentQAResult:
        return evaluate_segment_qa(
            item.segment_id,
            item.expected,
            item.actual,
            target_duration=item.target_duration,
            actual_duration=item.actual_duration,
            glossary_terms=(*glossary, *item.critical_terms),
            min_similarity=min_similarity,
            severe_similarity=severe_similarity,
            max_timing_ratio=max_timing_ratio,
        )

    def record(
        result: SegmentQAResult,
        observation: SegmentQAObservation,
    ) -> dict[str, Any]:
        return {
            **result.to_dict(),
            "expected": observation.expected,
            "actual": observation.actual,
            "target_duration": observation.target_duration,
            "actual_duration": observation.actual_duration,
            "critical_terms": list(observation.critical_terms),
        }

    initial = [evaluate(item) for item in ordered]
    routed = selective_repair_plan(initial)
    allowed_actions = set(repair_actions)
    repair_plan = [item for item in routed if item["action"] in allowed_actions]
    if max_repairs is not None:
        repair_plan = repair_plan[: max(0, max_repairs)]

    repaired_results: dict[int, SegmentQAResult] = {}
    repaired_observation_by_id: dict[int, SegmentQAObservation] = {}
    repaired_ids: set[int] = set()
    if repair is not None and repair_plan:
        requested_ids = {int(item["segment_id"]) for item in repair_plan}
        repaired_observations = list(repair(repair_plan))
        returned_ids = [item.segment_id for item in repaired_observations]
        if len(returned_ids) != len(set(returned_ids)):
            raise ValueError("segment QA repair returned duplicate segment ids")
        unexpected_ids = sorted(set(returned_ids) - requested_ids)
        if unexpected_ids:
            raise ValueError(f"segment QA repair returned unrequested ids: {unexpected_ids}")
        repaired_observation_by_id = {item.segment_id: item for item in repaired_observations}
        repaired_results = {item.segment_id: evaluate(item) for item in repaired_observations}
        repaired_ids = set(repaired_results)

    final = [repaired_results.get(item.segment_id, item) for item in initial]
    initial_observation_by_id = {item.segment_id: item for item in ordered}
    repairs = [
        {
            **item,
            "status": "completed" if int(item["segment_id"]) in repaired_ids else "no_result",
        }
        for item in repair_plan
    ]
    return {
        "version": 1,
        "kind": "segment_acoustic_qa",
        "thresholds": {
            "min_similarity": min_similarity,
            "severe_similarity": severe_similarity,
            "max_timing_ratio": max_timing_ratio,
        },
        "initial": [
            record(item, initial_observation_by_id[item.segment_id]) for item in initial
        ],
        "repair_plan": routed,
        "repairs": repairs,
        "final": [
            record(
                item,
                repaired_observation_by_id.get(
                    item.segment_id,
                    initial_observation_by_id[item.segment_id],
                ),
            )
            for item in final
        ],
        "summary": {
            "segments_checked": len(initial),
            "initial_failed": sum(not item.passed for item in initial),
            "repairs_attempted": len(repair_plan),
            "repairs_completed": len(repaired_ids),
            "final_failed": sum(not item.passed for item in final),
            "passed": all(item.passed for item in final),
        },
    }
