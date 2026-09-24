from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from .artifacts import atomic_write_json, fingerprint_data
from .types import Segment


DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
SEMANTIC_QA_QUESTION_VERSION = 3
SEMANTIC_QA_CACHE_VERSION = 3
_STAGE_CACHE_KIND = "semantic_qa_stage"
_CACHE_INDEX_KIND = "semantic_qa_index"
SEMANTIC_ISSUE_HEADS = (
    "material_meaning_lost",
    "negation_or_modality_changed",
    "action_direction_changed",
    "spoken_vi_awkward",
    "register_or_context_inconsistent",
)
REWRITE_SEMANTIC_ISSUE_HEAD = "rewrite_changed_material_meaning"
DEFAULT_REVIEW_ISSUE_ABOVE = 0.90
CALIBRATION_RETRY_ISSUE_HEADS = (
    "material_meaning_lost",
    "negation_or_modality_changed",
    "action_direction_changed",
    REWRITE_SEMANTIC_ISSUE_HEAD,
)
DEFAULT_FALSE_PASS_COST = 5.0
DEFAULT_FALSE_RETRY_COST = 1.0
_VERSIONED_JEV_MODEL = re.compile(r"^jev-\d+\.\d+\.\d+$")


def _issue_heads_for_stage(stage: str) -> tuple[str, ...]:
    heads = SEMANTIC_ISSUE_HEADS
    if "rewrite" in stage.casefold() or "prefit_candidate" in stage.casefold():
        heads += (REWRITE_SEMANTIC_ISSUE_HEAD,)
    return heads


def _fingerprint(
    segments: list[Segment],
    stage: str,
    model: str,
    config: dict[str, Any],
) -> str:
    payload = {
        "question_version": SEMANTIC_QA_QUESTION_VERSION,
        "stage": stage,
        "model": model,
        "endpoint": str(config.get("endpoint", DEFAULT_ENDPOINT)),
        # Batch membership is part of the inference state, so changing it can
        # change judgments even though it is otherwise a performance knob.
        "batch_size": max(1, int(config.get("batch_size", 32))),
        "context_window": max(0, int(config.get("context_window", 2))),
        "segments": [
            {"id": item.id, "source_en": item.text, "translation_vi": item.vi}
            for item in segments
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _apply_review_policy(result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Apply local routing thresholds to reusable raw TypeSafe judgments."""
    if result.get("status") != "ok":
        return dict(result)

    review_faithful_below = float(config.get("review_faithful_below", 0.65))
    review_critical_below = float(config.get("review_critical_below", 0.75))
    review_confidence_below = float(config.get("review_confidence_below", 0.50))
    review_issue_above_raw = config.get("review_issue_above", DEFAULT_REVIEW_ISSUE_ABOVE)
    review_issue_above = float(review_issue_above_raw)
    retry_issue_above_raw = config.get("retry_issue_above")
    retry_issue_above = (
        float(retry_issue_above_raw) if retry_issue_above_raw is not None else None
    )
    items = result.get("items")
    if not isinstance(items, list):
        return dict(result)

    evaluated = dict(result)
    evaluated_items: list[dict[str, Any]] = []
    needs_review = 0
    decision_counts = {"pass": 0, "review": 0, "retry": 0}
    for item in items:
        if not isinstance(item, dict):
            continue
        evaluated_item = dict(item)
        probabilities = evaluated_item.get("probabilities")
        probabilities = probabilities if isinstance(probabilities, dict) else {}
        faithful_probability = float(probabilities.get("faithful", 0.0))
        critical_probability = float(evaluated_item.get("critical_facts_probability") or 0.0)
        confidence = float(evaluated_item.get("confidence") or 0.0)
        issue_probabilities = evaluated_item.get("issue_probabilities")
        issue_probabilities = issue_probabilities if isinstance(issue_probabilities, dict) else {}
        reasons: list[str] = []
        choice = str(evaluated_item.get("choice") or "")
        if choice != "faithful":
            reasons.append(f"faithfulness_choice:{choice or 'missing'}")
        if faithful_probability < review_faithful_below:
            reasons.append("faithful_probability_below_threshold")
        if critical_probability < review_critical_below:
            reasons.append("critical_facts_probability_below_threshold")
        if confidence < review_confidence_below:
            reasons.append("choice_confidence_below_threshold")
        for head, probability in issue_probabilities.items():
            if float(probability) >= review_issue_above:
                reasons.append(f"issue:{head}")

        retry = choice == "wrong"
        if retry_issue_above is not None and any(
            float(probability) >= retry_issue_above
            for probability in issue_probabilities.values()
        ):
            retry = True
        flagged = bool(reasons)
        decision = "retry" if retry else ("review" if flagged else "pass")
        evaluated_item["needs_review"] = flagged
        evaluated_item["decision"] = decision
        evaluated_item["decision_reasons"] = reasons
        evaluated_items.append(evaluated_item)
        needs_review += int(flagged)
        decision_counts[decision] += 1

    evaluated["items"] = evaluated_items
    evaluated["needs_review"] = needs_review
    evaluated["decision_counts"] = decision_counts
    evaluated["thresholds"] = {
        "review_faithful_below": review_faithful_below,
        "review_critical_below": review_critical_below,
        "review_confidence_below": review_confidence_below,
        "review_issue_above": review_issue_above,
        "retry_issue_above": retry_issue_above,
    }
    return evaluated


def semantic_retry_risk_score(item: dict[str, Any]) -> float:
    """Return an offline-calibration score for material semantic retry risk.

    Style-only heads are intentionally excluded: awkward but semantically correct
    Vietnamese can require review without being a semantic retry target.
    """
    probabilities = item.get("probabilities")
    probabilities = probabilities if isinstance(probabilities, dict) else {}
    faithful_probability = float(probabilities.get("faithful", 0.0))
    wrong_probability = float(probabilities.get("wrong", 0.0))
    critical_probability = float(item.get("critical_facts_probability") or 0.0)
    issue_probabilities = item.get("issue_probabilities")
    issue_probabilities = issue_probabilities if isinstance(issue_probabilities, dict) else {}
    issue_risk = max(
        (float(issue_probabilities.get(head, 0.0)) for head in CALIBRATION_RETRY_ISSUE_HEADS),
        default=0.0,
    )
    return max(
        0.0,
        min(
            1.0,
            max(
                1.0 - faithful_probability,
                wrong_probability,
                1.0 - critical_probability,
                issue_risk,
            ),
        ),
    )


def semantic_calibration_metrics(
    samples: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    """Evaluate a retry threshold against labeled offline calibration samples."""
    true_positive = false_positive = true_negative = false_negative = 0
    for sample in samples:
        expected_retry = bool(sample.get("should_retry", False))
        predicted_retry = semantic_retry_risk_score(sample) >= threshold
        if expected_retry and predicted_retry:
            true_positive += 1
        elif expected_retry:
            false_negative += 1
        elif predicted_retry:
            false_positive += 1
        else:
            true_negative += 1

    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = true_positive / precision_denominator if precision_denominator else 1.0
    recall = true_positive / recall_denominator if recall_denominator else 1.0
    f1_denominator = precision + recall
    f1 = 2.0 * precision * recall / f1_denominator if f1_denominator else 0.0
    return {
        "threshold": float(threshold),
        "confusion": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
        },
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def select_semantic_retry_threshold(
    samples: list[dict[str, Any]],
    *,
    false_pass_cost: float = DEFAULT_FALSE_PASS_COST,
    false_retry_cost: float = DEFAULT_FALSE_RETRY_COST,
) -> dict[str, Any]:
    """Choose an offline candidate threshold with an explicit asymmetric cost."""
    if not samples:
        raise ValueError("semantic calibration requires at least one labeled sample")
    if false_pass_cost < 0 or false_retry_cost < 0:
        raise ValueError("semantic calibration costs must be non-negative")

    candidates = sorted(
        {0.0, 1.0, *(semantic_retry_risk_score(sample) for sample in samples)}
    )
    evaluated: list[dict[str, Any]] = []
    for threshold in candidates:
        metrics = semantic_calibration_metrics(samples, threshold)
        confusion = metrics["confusion"]
        cost = (
            confusion["false_negative"] * false_pass_cost
            + confusion["false_positive"] * false_retry_cost
        )
        evaluated.append({**metrics, "cost": cost})

    # Cost is primary. Then prefer fewer false passes, fewer false retries, and
    # finally the higher threshold to avoid unnecessary retries on an exact tie.
    selected = min(
        evaluated,
        key=lambda item: (
            item["cost"],
            item["confusion"]["false_negative"],
            item["confusion"]["false_positive"],
            -item["threshold"],
        ),
    )
    return {
        "cost_policy": {
            "false_pass_cost": float(false_pass_cost),
            "false_retry_cost": float(false_retry_cost),
            "meaning": (
                "false_pass = should_retry labeled sample allowed through; "
                "false_retry = non-retry sample sent to retry"
            ),
        },
        "selected": selected,
        "candidates": evaluated,
    }


def semantic_model_pin_status(
    raw_result: dict[str, Any],
    *,
    label_provenance: str,
    probability_source: str,
) -> dict[str, Any]:
    """Fail closed unless calibration evidence is suitable for a production pin."""
    requested_model = str(raw_result.get("requested_model") or "")
    response_models_raw = raw_result.get("response_models")
    response_models = (
        sorted({str(value) for value in response_models_raw if str(value)})
        if isinstance(response_models_raw, list)
        else []
    )
    versioned_model = (
        response_models[0]
        if len(response_models) == 1 and _VERSIONED_JEV_MODEL.fullmatch(response_models[0])
        else None
    )
    labels_are_human = label_provenance == "human"
    artifact_is_recorded = probability_source == "recorded_typesafe_artifact"
    ready = bool(versioned_model and labels_are_human and artifact_is_recorded)

    blockers: list[str] = []
    if versioned_model is None:
        blockers.append("requires_exactly_one_versioned_response_model")
    if not labels_are_human:
        blockers.append("requires_human_labels")
    if not artifact_is_recorded:
        blockers.append("requires_recorded_typesafe_artifact")
    return {
        "requested_model": requested_model,
        "response_models": response_models,
        "versioned_model": versioned_model,
        "production_pinning_ready": ready,
        "suggested_pinned_model": versioned_model if ready else None,
        "blockers": blockers,
    }


def _raw_result(result: dict[str, Any]) -> dict[str, Any]:
    """Remove local decision policy so cached inference stays threshold-independent."""
    raw = {
        key: value
        for key, value in result.items()
        if key not in {"needs_review", "decision_counts", "thresholds", "_runtime_cache_hit"}
    }
    items = raw.get("items")
    if isinstance(items, list):
        raw["items"] = [
            {
                key: value
                for key, value in item.items()
                if key not in {"needs_review", "decision", "decision_reasons"}
            }
            for item in items
            if isinstance(item, dict)
        ]
    return raw


def semantic_qa_stage_cache_path(cache_path: Path, stage: str) -> Path:
    """Return the independently manifestable raw cache artifact for one QA stage."""
    if not stage or not all(char.isalnum() or char in "._-" for char in stage):
        raise ValueError("semantic QA stage must be a non-empty filename-safe identifier")
    if cache_path.suffix:
        return cache_path.with_name(f"{cache_path.stem}.{stage}{cache_path.suffix}")
    return cache_path.with_name(f"{cache_path.name}.{stage}.json")


def _stage_cache_content(
    *,
    stage: str,
    fingerprint: str,
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": SEMANTIC_QA_CACHE_VERSION,
        "kind": _STAGE_CACHE_KIND,
        "stage": stage,
        "fingerprint": fingerprint,
        "raw_result": raw_result,
    }


def _write_stage_cache(
    path: Path,
    *,
    stage: str,
    fingerprint: str,
    result: dict[str, Any],
) -> None:
    raw = _raw_result(result)
    content = _stage_cache_content(stage=stage, fingerprint=fingerprint, raw_result=raw)
    atomic_write_json(path, {**content, "content_sha256": fingerprint_data(content)})


def _read_stage_cache(
    path: Path,
    *,
    expected_stage: str,
    expected_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    content = {
        key: data.get(key)
        for key in ("version", "kind", "stage", "fingerprint", "raw_result")
    }
    if content["version"] != SEMANTIC_QA_CACHE_VERSION or content["kind"] != _STAGE_CACHE_KIND:
        return None
    if content["stage"] != expected_stage:
        return None
    fingerprint = content["fingerprint"]
    if not isinstance(fingerprint, str):
        return None
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        return None
    content_sha256 = data.get("content_sha256")
    try:
        calculated_sha256 = fingerprint_data(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(content_sha256, str) or content_sha256 != calculated_sha256:
        return None

    raw = content["raw_result"]
    if not isinstance(raw, dict):
        return None
    if raw.get("stage") != expected_stage or raw.get("fingerprint") != fingerprint:
        return None
    if "thresholds" in raw or "needs_review" in raw or "decision_counts" in raw:
        return None
    items = raw.get("items")
    if isinstance(items, list) and any(
        isinstance(item, dict)
        and any(key in item for key in ("needs_review", "decision", "decision_reasons"))
        for item in items
    ):
        return None
    return raw


def _index_content(stages: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": SEMANTIC_QA_CACHE_VERSION,
        "kind": _CACHE_INDEX_KIND,
        "stages": stages,
    }


def _read_index(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    stages = data.get("stages")
    if not isinstance(stages, dict):
        return None
    content = _index_content(stages)
    if data.get("version") != SEMANTIC_QA_CACHE_VERSION or data.get("kind") != _CACHE_INDEX_KIND:
        return None
    content_sha256 = data.get("content_sha256")
    try:
        calculated_sha256 = fingerprint_data(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(content_sha256, str) or content_sha256 != calculated_sha256:
        return None
    return data


def _write_index_entry(
    cache_path: Path,
    *,
    stage: str,
    fingerprint: str,
    result: dict[str, Any],
    artifact_path: Path | None,
) -> None:
    current = _read_index(cache_path)
    stages = dict(current.get("stages", {})) if current is not None else {}
    entry = {
        "status": str(result.get("status") or "unknown"),
        "fingerprint": fingerprint,
        "segments_checked": int(result.get("segments_checked") or 0),
        "needs_review": int(result.get("needs_review") or 0),
        "thresholds": dict(result.get("thresholds") or {}),
        "artifact": artifact_path.name if artifact_path is not None else None,
    }
    if result.get("reason"):
        entry["reason"] = str(result["reason"])
    stages[stage] = entry
    content = _index_content(stages)
    atomic_write_json(cache_path, {**content, "content_sha256": fingerprint_data(content)})


def _questions_for(
    segments: list[Segment],
    *,
    stage: str = "translated",
) -> dict[str, dict[str, Any]]:
    questions: dict[str, dict[str, Any]] = {}
    for segment in segments:
        target = f"the object in `segments` whose `id` is {segment.id}"
        questions[f"faithfulness_{segment.id}"] = {
            "type": "choice",
            "instructions": (
                f"Compare `source_en` and `translation_vi` for {target}. "
                "How faithfully does the Vietnamese preserve the English meaning?"
            ),
            "criteria": {
                "faithful": (
                    "Preserves the essential meaning, facts, logical direction, and intent. "
                    "Natural compression or wording changes are acceptable."
                ),
                "partial": (
                    "Mostly correct, but drops, weakens, adds, or changes at least one material detail "
                    "that a viewer should know."
                ),
                "wrong": (
                    "Contradicts, reverses, substantially mistranslates, or changes the core meaning "
                    "or action of the English."
                ),
            },
        }
        questions[f"critical_facts_{segment.id}"] = {
            "type": "noul",
            "instructions": (
                f"For {target}, does the Vietnamese preserve all critical factual and logical constraints "
                "from the English, including numbers, quantities, negation, names, modality such as "
                "must/may/can, and the direction of actions?"
            ),
            "criteria": {
                "true": (
                    "No critical fact or logical constraint is changed, dropped, reversed, or invented. "
                    "If the English has none of these special constraints, the translation is still "
                    "factually and logically consistent with it."
                ),
                "false": (
                    "At least one critical fact or logical constraint is changed, dropped, reversed, "
                    "or invented."
                ),
            },
        }
        issue_questions = {
            "material_meaning_lost": (
                "Does the Vietnamese lose, add, weaken, or materially change meaning that a viewer "
                "needs in order to understand the English?"
            ),
            "negation_or_modality_changed": (
                "Does the Vietnamese change negation or modality such as must, should, may, can, "
                "cannot, likely, or conditional meaning from the English?"
            ),
            "action_direction_changed": (
                "Does the Vietnamese reverse or materially change who does what to whom, an action "
                "direction, comparison, increase/decrease, before/after, buy/sell, input/output, or "
                "other directional relationship from the English?"
            ),
            "spoken_vi_awkward": (
                "Would this Vietnamese sound materially awkward, telegraphic, or unnatural when spoken "
                "aloud for dubbing, beyond harmless style preference?"
            ),
            "register_or_context_inconsistent": (
                "Given this object's `context_before`, `context_after`, and speaker, is the Vietnamese "
                "materially inconsistent in terminology, pronouns, register, or local discourse context?"
            ),
            REWRITE_SEMANTIC_ISSUE_HEAD: (
                "For this rewrite candidate, does compression or rephrasing change, omit, weaken, or add "
                "material meaning compared with the English source?"
            ),
        }
        for head in _issue_heads_for_stage(stage):
            instructions = issue_questions[head]
            questions[f"{head}_{segment.id}"] = {
                "type": "noul",
                "instructions": f"For {target}: {instructions}",
                "criteria": {
                    "true": "There is a real issue matching the question and the item should be escalated.",
                    "false": "There is no material issue matching the question; natural compression is acceptable.",
                },
            }
    return questions


def _semantic_state_item(
    segments: list[Segment],
    index: int,
    *,
    context_window: int,
) -> dict[str, Any]:
    segment = segments[index]

    def context_item(item: Segment) -> dict[str, Any]:
        return {
            "id": item.id,
            "speaker": item.speaker,
            "source_en": item.text,
            "translation_vi": item.vi,
        }

    return {
        "id": segment.id,
        "speaker": segment.speaker,
        "source_en": segment.text,
        "translation_vi": segment.vi,
        "context_before": [
            context_item(item)
            for item in segments[max(0, index - context_window) : index]
        ],
        "context_after": [
            context_item(item)
            for item in segments[index + 1 : min(len(segments), index + 1 + context_window)]
        ],
    }


def _post_with_retry(
    endpoint: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: float,
    max_attempts: int,
) -> dict[str, Any]:
    attempts = max(1, max_attempts)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(2**attempt)
            continue

        if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < attempts:
            time.sleep(2**attempt)
            continue
        if response.status_code >= 400:
            detail = response.text.strip().replace("\n", " ")[:1000]
            raise RuntimeError(f"TypeSafe API lỗi HTTP {response.status_code}: {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("TypeSafe API trả về dữ liệu không phải JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("TypeSafe API trả về payload không hợp lệ")
        return data

    raise RuntimeError(f"Không thể kết nối TypeSafe API: {last_error}")


def evaluate_semantic_qa(
    segments: list[Segment],
    config: dict[str, Any],
    *,
    stage: str,
    api_key: str,
) -> dict[str, Any]:
    model = str(config.get("model", "jev-latest"))
    endpoint = str(config.get("endpoint", DEFAULT_ENDPOINT))
    timeout = float(config.get("timeout_seconds", 30.0))
    max_attempts = int(config.get("max_attempts", 3))
    batch_size = max(1, int(config.get("batch_size", 32)))
    context_window = max(0, int(config.get("context_window", 2)))

    items: list[dict[str, Any]] = []
    input_tokens = 0
    output_tokens = 0
    response_models: set[str] = set()
    request_receipts: list[dict[str, Any]] = []
    total_latency = 0.0

    for offset in range(0, len(segments), batch_size):
        batch = segments[offset : offset + batch_size]
        payload = {
            "state": {
                "segments": [
                    _semantic_state_item(
                        segments,
                        index,
                        context_window=context_window,
                    )
                    for index in range(offset, min(len(segments), offset + len(batch)))
                ]
            },
            "model": model,
            "questions": _questions_for(batch, stage=stage),
        }
        started = time.perf_counter()
        data = _post_with_retry(endpoint, payload, api_key, timeout, max_attempts)
        request_latency = time.perf_counter() - started
        total_latency += request_latency
        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise RuntimeError("TypeSafe API thiếu trường answers")

        response_model = str(data.get("model") or model)
        response_models.add(response_model)
        usage = data.get("usage") or {}
        batch_input_tokens = int(usage.get("input_tokens") or 0)
        batch_output_tokens = int(usage.get("output_tokens") or 0)
        input_tokens += batch_input_tokens
        output_tokens += batch_output_tokens
        request_receipts.append(
            {
                "offset": offset,
                "segments": len(batch),
                "model": response_model,
                "latency_seconds": round(request_latency, 6),
                "input_tokens": batch_input_tokens,
                "output_tokens": batch_output_tokens,
            }
        )

        for segment in batch:
            faithfulness = answers.get(f"faithfulness_{segment.id}")
            critical_facts = answers.get(f"critical_facts_{segment.id}")
            if not isinstance(faithfulness, dict) or not isinstance(critical_facts, dict):
                raise RuntimeError(f"TypeSafe API thiếu câu trả lời cho segment {segment.id}")

            probabilities_raw = faithfulness.get("probabilities") or {}
            probabilities = {str(k): float(v) for k, v in dict(probabilities_raw).items()}
            choice = str(faithfulness.get("choice") or "")
            confidence = float(faithfulness.get("confidence") or 0.0)
            critical_probability = float(critical_facts.get("noul") or 0.0)
            issue_probabilities: dict[str, float] = {}
            for head in _issue_heads_for_stage(stage):
                answer = answers.get(f"{head}_{segment.id}")
                if isinstance(answer, dict) and answer.get("noul") is not None:
                    issue_probabilities[head] = float(answer["noul"])
            items.append(
                {
                    "id": segment.id,
                    "source_en": segment.text,
                    "translation_vi": segment.vi,
                    "choice": choice,
                    "probabilities": probabilities,
                    "confidence": confidence,
                    "critical_facts_probability": critical_probability,
                    "issue_probabilities": issue_probabilities,
                }
            )

    verdicts = {"faithful": 0, "partial": 0, "wrong": 0, "other": 0}
    for item in items:
        key = item["choice"] if item["choice"] in verdicts else "other"
        verdicts[key] += 1

    fingerprint = _fingerprint(segments, stage, model, config)
    result = {
        "status": "ok",
        "stage": stage,
        "mode": "shadow",
        "question_policy_version": SEMANTIC_QA_QUESTION_VERSION,
        "requested_model": model,
        "response_models": sorted(response_models),
        "fingerprint": fingerprint,
        "state_fingerprint": fingerprint,
        "segments_checked": len(items),
        "verdicts": verdicts,
        "latency_seconds": round(total_latency, 6),
        "usage": {
            "requests": (len(segments) + batch_size - 1) // batch_size,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "request_receipts": request_receipts,
        "items": items,
    }
    return _apply_review_policy(result, config)


def run_semantic_qa_stage(
    segments: list[Segment],
    config: dict[str, Any],
    *,
    stage: str,
    cache_path: Path,
    resume: bool,
) -> dict[str, Any]:
    model = str(config.get("model", "jev-latest"))
    fingerprint = _fingerprint(segments, stage, model, config)
    stage_cache_path = semantic_qa_stage_cache_path(cache_path, stage)

    if not bool(config.get("enabled", False)):
        result = {
            "status": "disabled",
            "stage": stage,
            "mode": "shadow",
            "fingerprint": fingerprint,
            "segments_checked": 0,
            "needs_review": 0,
            "items": [],
        }
    elif not segments:
        result = {
            "status": "not_applicable",
            "stage": stage,
            "mode": "shadow",
            "fingerprint": fingerprint,
            "segments_checked": 0,
            "needs_review": 0,
            "items": [],
        }
    else:
        existing = (
            _read_stage_cache(
                stage_cache_path,
                expected_stage=stage,
                expected_fingerprint=fingerprint,
            )
            if resume
            else None
        )
        if isinstance(existing, dict) and existing.get("status") == "ok":
            result = _apply_review_policy(existing, config)
            _write_index_entry(
                cache_path,
                stage=stage,
                fingerprint=fingerprint,
                result=result,
                artifact_path=stage_cache_path,
            )
            returned = dict(result)
            returned["_runtime_cache_hit"] = True
            return returned

        api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            result = {
                "status": "skipped",
                "reason": "missing_TYPESAFE_API_KEY",
                "stage": stage,
                "mode": "shadow",
                "fingerprint": fingerprint,
                "segments_checked": 0,
                "needs_review": 0,
                "items": [],
            }
        else:
            try:
                result = evaluate_semantic_qa(segments, config, stage=stage, api_key=api_key)
            except Exception as exc:
                result = {
                    "status": "error",
                    "reason": str(exc)[:1200],
                    "stage": stage,
                    "mode": "shadow",
                    "fingerprint": fingerprint,
                    "segments_checked": 0,
                    "needs_review": 0,
                    "items": [],
                }

    artifact_path: Path | None = None
    if result.get("status") == "ok":
        _write_stage_cache(
            stage_cache_path,
            stage=stage,
            fingerprint=fingerprint,
            result=result,
        )
        artifact_path = stage_cache_path
    _write_index_entry(
        cache_path,
        stage=stage,
        fingerprint=fingerprint,
        result=result,
        artifact_path=artifact_path,
    )
    returned = dict(result)
    returned["_runtime_cache_hit"] = False
    return returned


def semantic_qa_summary(cache_path: Path) -> dict[str, Any] | None:
    if not cache_path.exists():
        return None

    index = _read_index(cache_path)
    if index is not None:
        stages = index.get("stages") or {}
        if not stages:
            return None

        def summary_entry(stage: str) -> dict[str, Any]:
            entry = stages.get(stage)
            if not isinstance(entry, dict):
                return {}
            if entry.get("status") != "ok":
                return entry
            raw = _read_stage_cache(
                semantic_qa_stage_cache_path(cache_path, stage),
                expected_stage=stage,
                expected_fingerprint=str(entry.get("fingerprint") or ""),
            )
            return entry if raw is not None else {"status": "invalid_cache"}

        translated = summary_entry("translated")
        rewritten = summary_entry("rewritten")
    else:
        # Legacy combined caches remain readable for reporting, but are not trusted
        # for resume because they have no independent content hash per stage.
        try:
            legacy = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(legacy, dict) or not isinstance(legacy.get("stages"), dict):
            return None
        stages = legacy["stages"]
        translated = stages.get("translated") or {}
        rewritten = stages.get("rewritten") or {}

    return {
        "mode": "shadow",
        "translated_status": translated.get("status"),
        "translated_checked": int(translated.get("segments_checked") or 0),
        "translated_needs_review": int(translated.get("needs_review") or 0),
        "rewritten_status": rewritten.get("status"),
        "rewritten_checked": int(rewritten.get("segments_checked") or 0),
        "rewritten_needs_review": int(rewritten.get("needs_review") or 0),
        "artifact": str(cache_path),
    }
