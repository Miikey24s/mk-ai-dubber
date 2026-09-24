from __future__ import annotations

import json
from pathlib import Path

import pytest

import vi_dubber.semantic_qa as semantic_qa
from vi_dubber.semantic_qa import run_semantic_qa_stage, semantic_qa_stage_cache_path
from vi_dubber.types import Segment


def _segments() -> list[Segment]:
    return [
        Segment(
            id=0,
            start=0.0,
            end=1.0,
            text="Do not sell five shares.",
            vi="Không bán năm cổ phiếu.",
        )
    ]


def _fake_success(
    segments: list[Segment],
    config: dict[str, object],
    *,
    stage: str,
    api_key: str,
) -> dict[str, object]:
    del api_key
    model = str(config.get("model", "jev-latest"))
    fingerprint = semantic_qa._fingerprint(segments, stage, model, config)
    raw = {
        "status": "ok",
        "stage": stage,
        "mode": "shadow",
        "requested_model": model,
        "response_models": ["jev-test"],
        "fingerprint": fingerprint,
        "segments_checked": 1,
        "verdicts": {"faithful": 1, "partial": 0, "wrong": 0, "other": 0},
        "usage": {"requests": 1, "input_tokens": 10, "output_tokens": 2},
        "items": [
            {
                "id": 0,
                "source_en": segments[0].text,
                "translation_vi": segments[0].vi,
                "choice": "faithful",
                "probabilities": {"faithful": 0.70, "partial": 0.29, "wrong": 0.01},
                "confidence": 0.80,
                "critical_facts_probability": 0.90,
            }
        ],
    }
    return semantic_qa._apply_review_policy(raw, config)


def test_resume_reuses_verified_stage_artifact_without_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_evaluate(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _fake_success(*args, **kwargs)

    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-key")
    monkeypatch.setattr(semantic_qa, "evaluate_semantic_qa", fake_evaluate)
    cache_path = tmp_path / "semantic_qa.json"

    first = run_semantic_qa_stage(
        _segments(),
        {"enabled": True},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    second = run_semantic_qa_stage(
        _segments(),
        {"enabled": True},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )

    assert calls == 1
    assert first["_runtime_cache_hit"] is False
    assert second["_runtime_cache_hit"] is True
    stage_path = semantic_qa_stage_cache_path(cache_path, "translated")
    assert stage_path.exists()
    artifact = json.loads(stage_path.read_text(encoding="utf-8"))
    assert artifact["kind"] == "semantic_qa_stage"
    assert "thresholds" not in artifact["raw_result"]
    assert "needs_review" not in artifact["raw_result"]
    assert "needs_review" not in artifact["raw_result"]["items"][0]


def test_threshold_change_re_evaluates_locally_without_rewriting_raw_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_evaluate(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _fake_success(*args, **kwargs)

    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-key")
    monkeypatch.setattr(semantic_qa, "evaluate_semantic_qa", fake_evaluate)
    cache_path = tmp_path / "semantic_qa.json"
    stage_path = semantic_qa_stage_cache_path(cache_path, "translated")

    first = run_semantic_qa_stage(
        _segments(),
        {"enabled": True, "review_faithful_below": 0.65},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    artifact_before = stage_path.read_bytes()
    second = run_semantic_qa_stage(
        _segments(),
        {"enabled": True, "review_faithful_below": 0.75},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )

    assert calls == 1
    assert first["needs_review"] == 0
    assert second["needs_review"] == 1
    assert second["_runtime_cache_hit"] is True
    assert stage_path.read_bytes() == artifact_before


@pytest.mark.parametrize("mutation", ["invalid_json", "tampered_payload"])
def test_corrupted_or_tampered_stage_cache_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    calls = 0

    def fake_evaluate(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _fake_success(*args, **kwargs)

    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-key")
    monkeypatch.setattr(semantic_qa, "evaluate_semantic_qa", fake_evaluate)
    cache_path = tmp_path / "semantic_qa.json"
    stage_path = semantic_qa_stage_cache_path(cache_path, "translated")

    run_semantic_qa_stage(
        _segments(),
        {"enabled": True},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    if mutation == "invalid_json":
        stage_path.write_text("{not-json", encoding="utf-8")
    else:
        artifact = json.loads(stage_path.read_text(encoding="utf-8"))
        artifact["raw_result"]["items"][0]["choice"] = "wrong"
        stage_path.write_text(json.dumps(artifact), encoding="utf-8")

    rerun = run_semantic_qa_stage(
        _segments(),
        {"enabled": True},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )

    assert calls == 2
    assert rerun["_runtime_cache_hit"] is False
    assert rerun["items"][0]["choice"] == "faithful"


def test_disabled_and_error_paths_do_not_touch_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(semantic_qa.requests, "post", fail_network)
    cache_path = tmp_path / "semantic_qa.json"

    disabled = run_semantic_qa_stage(
        _segments(),
        {"enabled": False},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    assert disabled["status"] == "disabled"
    assert not semantic_qa_stage_cache_path(cache_path, "translated").exists()

    def fail_evaluation(*args, **kwargs):
        raise RuntimeError("synthetic offline failure")

    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-key")
    monkeypatch.setattr(semantic_qa, "evaluate_semantic_qa", fail_evaluation)
    errored = run_semantic_qa_stage(
        _segments(),
        {"enabled": True},
        stage="rewritten",
        cache_path=cache_path,
        resume=False,
    )

    assert errored["status"] == "error"
    assert errored["reason"] == "synthetic offline failure"
    assert not semantic_qa_stage_cache_path(cache_path, "rewritten").exists()


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict[str, object]:
        return self._payload


def test_typesafe_timeout_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_post(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            raise semantic_qa.requests.Timeout("synthetic timeout")
        return _FakeResponse(200, {"answers": {}})

    monkeypatch.setattr(semantic_qa.requests, "post", fake_post)
    monkeypatch.setattr(semantic_qa.time, "sleep", sleeps.append)

    result = semantic_qa._post_with_retry(
        "https://typesafe.invalid/test",
        {"state": {}},
        "test-key",
        timeout=0.01,
        max_attempts=2,
    )

    assert result == {"answers": {}}
    assert calls == 2
    assert sleeps == [1]


def test_typesafe_429_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_post(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            return _FakeResponse(429, text="rate limited")
        return _FakeResponse(200, {"answers": {}})

    monkeypatch.setattr(semantic_qa.requests, "post", fake_post)
    monkeypatch.setattr(semantic_qa.time, "sleep", sleeps.append)

    result = semantic_qa._post_with_retry(
        "https://typesafe.invalid/test",
        {"state": {}},
        "test-key",
        timeout=0.01,
        max_attempts=2,
    )

    assert result == {"answers": {}}
    assert calls == 2
    assert sleeps == [1]


def test_typesafe_5xx_retry_budget_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_post(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return _FakeResponse(503, text="synthetic outage")

    monkeypatch.setattr(semantic_qa.requests, "post", fake_post)
    monkeypatch.setattr(semantic_qa.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="HTTP 503: synthetic outage"):
        semantic_qa._post_with_retry(
            "https://typesafe.invalid/test",
            {"state": {}},
            "test-key",
            timeout=0.01,
            max_attempts=3,
        )

    assert calls == 3
    assert sleeps == [1, 2]


def test_semantic_model_pinning_requires_human_recorded_versioned_evidence() -> None:
    raw = {
        "requested_model": "jev-latest",
        "response_models": ["jev-1.13.0"],
    }

    synthetic = semantic_qa.semantic_model_pin_status(
        raw,
        label_provenance="synthetic_regression_labels",
        probability_source="synthetic_fixture_probabilities",
    )
    assert synthetic["versioned_model"] == "jev-1.13.0"
    assert synthetic["production_pinning_ready"] is False
    assert synthetic["suggested_pinned_model"] is None
    assert synthetic["blockers"] == [
        "requires_human_labels",
        "requires_recorded_typesafe_artifact",
    ]

    recorded = semantic_qa.semantic_model_pin_status(
        raw,
        label_provenance="human",
        probability_source="recorded_typesafe_artifact",
    )
    assert recorded["production_pinning_ready"] is True
    assert recorded["suggested_pinned_model"] == "jev-1.13.0"


@pytest.mark.parametrize(
    "response_models",
    [
        ["jev-latest"],
        ["jev-1.13.0", "jev-1.14.0"],
        [],
    ],
)
def test_semantic_model_pinning_fails_closed_without_one_versioned_response_model(
    response_models: list[str],
) -> None:
    status = semantic_qa.semantic_model_pin_status(
        {"requested_model": "jev-latest", "response_models": response_models},
        label_provenance="human",
        probability_source="recorded_typesafe_artifact",
    )

    assert status["production_pinning_ready"] is False
    assert status["suggested_pinned_model"] is None
    assert "requires_exactly_one_versioned_response_model" in status["blockers"]
