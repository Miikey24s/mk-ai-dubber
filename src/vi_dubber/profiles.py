from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


DEFAULT_PROFILE = "balanced_best"

PROFILE_ALIASES = {
    "fast": "fast",
    "balanced": "balanced_best",
    "balanced_best": "balanced_best",
    "balanced best": "balanced_best",
    "max": "max_quality",
    "max_quality": "max_quality",
    "max quality": "max_quality",
}


PROFILE_OVERRIDES: dict[str, dict[str, Any]] = {
    "fast": {
        "tts": {
            "batch_size": 1,
        },
        "qa": {
            "enabled": False,
            "semantic": {
                "enabled": False,
            },
        },
        "timing": {
            "rewrite_threshold": 1.32,
            "aggressive_rewrite_threshold": 1.48,
        },
        "reliability": {
            "retry_budget": 1,
            "final_full_qa": False,
        },
    },
    "balanced_best": {
        "tts": {
            "batch_size": 1,
        },
        "qa": {
            "enabled": True,
            "semantic": {
                "enabled": True,
            },
        },
        "reliability": {
            "retry_budget": 3,
            "final_full_qa": True,
        },
    },
    "max_quality": {
        "tts": {
            "batch_size": 1,
        },
        "qa": {
            "enabled": True,
            "semantic": {
                "enabled": True,
                "review_faithful_below": 0.72,
                "review_critical_below": 0.82,
                "review_confidence_below": 0.60,
            },
        },
        "timing": {
            "rewrite_threshold": 1.18,
            "aggressive_rewrite_threshold": 1.32,
        },
        "reliability": {
            "retry_budget": 4,
            "final_full_qa": True,
        },
    },
}


PROFILE_POLICIES: dict[str, dict[str, Any]] = {
    "fast": {
        "translation_fanout": 1,
        "translation_fanout_scope": "single",
        "translation_prefit": False,
        "typesafe_policy": "critical_gate",
        "tts_batch_policy": "throughput",
        "qa_depth": "minimal",
        "optional_lip_sync": False,
        "overlap_assembly_policy": "equal_power",
        "hard_case_review": "overlap_or_multi_speaker",
    },
    "balanced_best": {
        "translation_fanout": 1,
        "translation_fanout_scope": "single",
        "translation_prefit": True,
        "typesafe_policy": "verify_escalate",
        "tts_batch_policy": "quality_safe",
        "qa_depth": "standard",
        "optional_lip_sync": False,
        "overlap_assembly_policy": "equal_power",
        "hard_case_review": "overlap_or_multi_speaker",
    },
    "max_quality": {
        "translation_fanout": 2,
        "translation_fanout_scope": "hard_segments",
        "translation_prefit": True,
        "typesafe_policy": "strict_verify_escalate",
        "tts_batch_policy": "quality_safe",
        "qa_depth": "strict",
        "optional_lip_sync": False,
        "overlap_assembly_policy": "equal_power",
        "hard_case_review": "overlap_or_multi_speaker",
    },
}


def normalize_profile(value: str | None) -> str:
    if value is None or not str(value).strip():
        return DEFAULT_PROFILE
    key = str(value).strip().lower().replace("-", "_")
    try:
        return PROFILE_ALIASES[key]
    except KeyError as exc:
        supported = ", ".join(sorted(PROFILE_OVERRIDES))
        raise ValueError(f"profile không hợp lệ: {value!r}; hỗ trợ: {supported}") from exc


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def resolve_profile(config: Mapping[str, Any], profile: str | None = None) -> dict[str, Any]:
    """Return an isolated config snapshot with deterministic profile overrides applied."""
    resolved = deepcopy(dict(config))
    selected = normalize_profile(profile or str(resolved.get("profile") or DEFAULT_PROFILE))
    _deep_merge(resolved, PROFILE_OVERRIDES[selected])
    resolved["profile"] = selected
    policy = deepcopy(PROFILE_POLICIES[selected])
    policy.update(
        {
            "contract_version": 1,
            "name": selected,
            "tts_batch_size": int(resolved.get("tts", {}).get("batch_size", 1)),
            "semantic_qa_enabled": bool(
                resolved.get("qa", {}).get("semantic", {}).get("enabled", False)
            ),
            "final_full_qa": bool(resolved.get("reliability", {}).get("final_full_qa", True)),
            "retry_budget": int(resolved.get("reliability", {}).get("retry_budget", 3)),
        }
    )
    resolved["profile_policy"] = policy
    return resolved


def validate_config(config: Mapping[str, Any]) -> None:
    required_sections = (
        "asr",
        "separation",
        "translation",
        "tts",
        "timing",
        "mix",
        "qa",
        "diarization",
    )
    for section in required_sections:
        if not isinstance(config.get(section), Mapping):
            raise ValueError(f"config.{section} phải là object/map")

    normalize_profile(str(config.get("profile") or DEFAULT_PROFILE))

    segmentation = config.get("segmentation", {})
    if segmentation and not isinstance(segmentation, Mapping):
        raise ValueError("config.segmentation phải là object/map")
    mode = str(segmentation.get("mode", "legacy")).lower()
    if mode not in {"legacy", "smart"}:
        raise ValueError("segmentation.mode phải là 'legacy' hoặc 'smart'")

    asr_batch = int(config["asr"].get("batch_size", 1))
    if asr_batch < 1:
        raise ValueError("asr.batch_size phải >= 1")

    timing = config["timing"]
    max_speedup = float(timing.get("max_speedup", 1.0))
    max_slowdown = float(timing.get("max_slowdown", 1.0))
    rewrite_threshold = float(timing.get("rewrite_threshold", 1.0))
    aggressive = float(timing.get("aggressive_rewrite_threshold", rewrite_threshold))
    overlap_policy = str(timing.get("overlap_policy", "equal_power")).strip().lower()
    if max_speedup < 1.0:
        raise ValueError("timing.max_speedup phải >= 1.0")
    if not 0.5 <= max_slowdown <= 1.0:
        raise ValueError("timing.max_slowdown phải nằm trong [0.5, 1.0]")
    if rewrite_threshold <= 1.0 or aggressive < rewrite_threshold:
        raise ValueError("timing rewrite threshold không hợp lệ")
    if overlap_policy not in {"equal_power", "sum"}:
        raise ValueError("timing.overlap_policy phải là 'equal_power' hoặc 'sum'")

    mix = config["mix"]
    final_lufs = float(mix.get("final_lufs", -14.0))
    true_peak = float(mix.get("final_true_peak_db", -1.5))
    if not -30.0 <= final_lufs <= -5.0:
        raise ValueError("mix.final_lufs nằm ngoài range an toàn [-30, -5]")
    if true_peak > 0.0:
        raise ValueError("mix.final_true_peak_db phải <= 0")

    semantic = config["qa"].get("semantic", {})
    if semantic and not isinstance(semantic, Mapping):
        raise ValueError("qa.semantic phải là object/map")
    attempts = int(semantic.get("max_attempts", 3))
    if attempts < 1 or attempts > 10:
        raise ValueError("qa.semantic.max_attempts phải nằm trong [1, 10]")

    reliability = config.get("reliability", {})
    if reliability and not isinstance(reliability, Mapping):
        raise ValueError("config.reliability phải là object/map")
    retry_budget = int(reliability.get("retry_budget", 3))
    if retry_budget < 0 or retry_budget > 10:
        raise ValueError("reliability.retry_budget phải nằm trong [0, 10]")
