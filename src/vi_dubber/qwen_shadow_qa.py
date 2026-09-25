from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .qa import evaluate_segment_qa


QWEN_SHADOW_QA_VERSION = 1
DEFAULT_QWEN_ASR_MODEL = "Qwen/Qwen3-ASR-0.6B"
_ACOUSTIC_REASONS = {
    "missing_critical",
    "severe_transcript_mismatch",
    "transcript_mismatch",
}


@dataclass(frozen=True, slots=True)
class QwenShadowCandidate:
    segment_id: int
    audio_path: Path
    expected: str
    primary_actual: str
    primary_similarity: float
    primary_passed: bool
    primary_reasons: tuple[str, ...]
    actual_duration: float
    critical_terms: tuple[str, ...] = ()


def select_qwen_shadow_candidates(
    segment_qa_report: Mapping[str, Any],
    audio_by_segment: Mapping[int, Path],
    *,
    borderline_similarity: float = 0.90,
    max_segments: int | None = 8,
) -> list[QwenShadowCandidate]:
    """Select acoustic-risk segments for an independent, non-blocking ASR pass."""
    if not 0.0 <= borderline_similarity <= 1.0:
        raise ValueError("borderline_similarity must be between 0 and 1")
    if max_segments is not None and max_segments < 0:
        raise ValueError("max_segments must be >= 0 or None")

    raw_items = segment_qa_report.get("final") or segment_qa_report.get("initial") or []
    candidates: list[QwenShadowCandidate] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        try:
            segment_id = int(item["segment_id"])
            similarity = float(item.get("similarity", 1.0))
        except (KeyError, TypeError, ValueError):
            continue
        reasons = tuple(str(reason) for reason in (item.get("reasons") or ()))
        acoustic_failure = bool(_ACOUSTIC_REASONS.intersection(reasons))
        borderline = similarity < borderline_similarity
        if not acoustic_failure and not borderline:
            continue
        audio_path = audio_by_segment.get(segment_id)
        if audio_path is None:
            continue
        candidates.append(
            QwenShadowCandidate(
                segment_id=segment_id,
                audio_path=Path(audio_path),
                expected=str(item.get("expected") or ""),
                primary_actual=str(item.get("actual") or ""),
                primary_similarity=similarity,
                primary_passed=bool(item.get("passed", False)),
                primary_reasons=reasons,
                actual_duration=max(0.001, float(item.get("actual_duration") or 0.001)),
                critical_terms=tuple(str(term) for term in (item.get("critical_terms") or ())),
            )
        )

    candidates.sort(
        key=lambda item: (
            0 if "missing_critical" in item.primary_reasons else 1,
            0 if not item.primary_passed else 1,
            item.primary_similarity,
            item.segment_id,
        )
    )
    if max_segments is not None:
        candidates = candidates[:max_segments]
    return candidates


def _transcribe_qwen_files(paths: Sequence[Path], config: Mapping[str, Any]) -> list[str]:
    """Run the optional Qwen3-ASR engine without adding it to production dependencies."""
    if not paths:
        return []
    try:
        import torch
        from qwen_asr import Qwen3ASRModel
    except ImportError as exc:
        raise RuntimeError(
            "Qwen3-ASR shadow QA requires the optional 'qwen-asr' package. "
            "Install it in an isolated benchmark environment; the production vi-dubber "
            "environment intentionally does not depend on it."
        ) from exc

    device = str(config.get("device", "cuda:0"))
    dtype_name = str(config.get("dtype", "float16"))
    dtype = getattr(torch, dtype_name, None)
    if dtype is None:
        raise ValueError(f"Unsupported Qwen3-ASR dtype: {dtype_name}")
    model_name = str(config.get("model", DEFAULT_QWEN_ASR_MODEL))
    batch_size = max(1, int(config.get("batch_size", 4)))
    max_new_tokens = max(1, int(config.get("max_new_tokens", 512)))
    model = Qwen3ASRModel.from_pretrained(
        model_name,
        dtype=dtype,
        device_map=device,
        max_inference_batch_size=batch_size,
        max_new_tokens=max_new_tokens,
    )
    language = config.get("language", "Vietnamese")
    requested_language: str | list[str] | None
    if language in (None, "", "auto"):
        requested_language = None
    else:
        requested_language = [str(language)] * len(paths)
    results = model.transcribe(
        audio=[str(path) for path in paths],
        language=requested_language,
    )
    texts = [str(getattr(result, "text", "")).strip() for result in results]
    if len(texts) != len(paths):
        raise RuntimeError(
            f"Qwen3-ASR returned {len(texts)} results for {len(paths)} requested files"
        )
    return texts


def run_qwen_shadow_qa(
    candidates: Sequence[QwenShadowCandidate],
    config: Mapping[str, Any] | None = None,
    *,
    transcriber: Callable[[Sequence[Path], Mapping[str, Any]], Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Compare an independent Qwen3-ASR pass with primary WhisperX QA observations.

    This function is deliberately shadow-only: it returns evidence and never routes a
    repair or changes a production decision.
    """
    settings = dict(config or {})
    model_name = str(settings.get("model", DEFAULT_QWEN_ASR_MODEL))
    ordered = list(candidates)
    if not ordered:
        return {
            "version": QWEN_SHADOW_QA_VERSION,
            "kind": "qwen3_asr_shadow_qa",
            "mode": "shadow",
            "blocking": False,
            "model": model_name,
            "items": [],
            "summary": {
                "segments_checked": 0,
                "confirmed_primary_failures": 0,
                "disputed_primary_failures": 0,
                "shadow_only_failures": 0,
            },
        }

    infer = transcriber or _transcribe_qwen_files
    texts = list(infer([item.audio_path for item in ordered], settings))
    if len(texts) != len(ordered):
        raise RuntimeError(
            f"Qwen3-ASR shadow transcriber returned {len(texts)} texts for {len(ordered)} segments"
        )

    results: list[dict[str, Any]] = []
    for candidate, qwen_text in zip(ordered, texts, strict=True):
        independent = evaluate_segment_qa(
            candidate.segment_id,
            candidate.expected,
            str(qwen_text),
            target_duration=candidate.actual_duration,
            actual_duration=candidate.actual_duration,
            glossary_terms=candidate.critical_terms,
        )
        if not candidate.primary_passed and not independent.passed:
            disposition = "confirmed_primary_failure"
        elif not candidate.primary_passed and independent.passed:
            disposition = "disputed_primary_failure"
        elif candidate.primary_passed and not independent.passed:
            disposition = "shadow_only_failure"
        else:
            disposition = "confirmed_clean"
        results.append(
            {
                **asdict(candidate),
                "audio_path": str(candidate.audio_path),
                "qwen_actual": str(qwen_text).strip(),
                "qwen_similarity": independent.similarity,
                "qwen_missing_critical": independent.missing_critical,
                "qwen_passed": independent.passed,
                "disposition": disposition,
            }
        )

    return {
        "version": QWEN_SHADOW_QA_VERSION,
        "kind": "qwen3_asr_shadow_qa",
        "mode": "shadow",
        "blocking": False,
        "model": model_name,
        "items": results,
        "summary": {
            "segments_checked": len(results),
            "confirmed_primary_failures": sum(
                item["disposition"] == "confirmed_primary_failure" for item in results
            ),
            "disputed_primary_failures": sum(
                item["disposition"] == "disputed_primary_failure" for item in results
            ),
            "shadow_only_failures": sum(
                item["disposition"] == "shadow_only_failure" for item in results
            ),
        },
    }
