from __future__ import annotations

from pathlib import Path

import pytest

from vi_dubber.preflight import PreflightCheck, raise_for_preflight, run_preflight


def _config() -> dict:
    return {
        "profile": "balanced_best",
        "reliability": {"min_free_disk_gb": 0.0},
        "tts": {"device": "cpu"},
        "asr": {"device": "cpu"},
        "qa": {"semantic": {"enabled": False}},
    }


def test_preflight_local_provider_reports_missing_input(tmp_path: Path, monkeypatch) -> None:
    import vi_dubber.preflight as preflight

    model = tmp_path / "models" / "llm" / "Qwen--Qwen3-14B-GGUF" / "Qwen3-14B-Q4_K_M.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(preflight, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(preflight, "WORK_DIR", work)
    monkeypatch.setattr(preflight, "configure_runtime", lambda: None)
    monkeypatch.setattr(
        preflight,
        "webgpt_route_info",
        lambda: (_ for _ in ()).throw(AssertionError("local preflight must not probe WebGPT")),
    )

    checks = run_preflight(
        _config(),
        translation_provider="local",
        input_path=tmp_path / "missing.mp4",
    )
    by_name = {item.name: item for item in checks}
    assert by_name["input"].status == "error"
    assert by_name["translation"].status == "ok"
    assert by_name["retry_policy"].status == "ok"


def test_preflight_strict_typesafe_requires_key(tmp_path: Path, monkeypatch) -> None:
    import vi_dubber.preflight as preflight

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(preflight, "WORK_DIR", work)
    monkeypatch.setattr(preflight, "configure_runtime", lambda: None)
    monkeypatch.setattr(preflight, "webgpt_route_info", lambda _config=None: {"ready": True, "model": "test"})
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    config = _config()
    config["profile"] = "max_quality"
    config["qa"]["semantic"]["enabled"] = True

    checks = run_preflight(config, translation_provider="webgpt")
    assert {item.name: item.status for item in checks}["typesafe"] == "error"


def test_raise_for_preflight_only_blocks_errors() -> None:
    raise_for_preflight([PreflightCheck("warn", "warning", "degraded")])
    with pytest.raises(RuntimeError, match="disk"):
        raise_for_preflight([PreflightCheck("disk", "error", "low")])


def test_preflight_hybrid_warns_when_local_fallback_is_missing(tmp_path: Path, monkeypatch) -> None:
    import vi_dubber.preflight as preflight

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(preflight, "WORK_DIR", work)
    monkeypatch.setattr(preflight, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(preflight, "configure_runtime", lambda: None)
    monkeypatch.setattr(preflight, "webgpt_route_info", lambda _config=None: {"ready": True, "model": "test"})

    checks = run_preflight(_config(), translation_provider="hybrid")
    translation = {item.name: item for item in checks}["translation"]

    assert translation.status == "warning"
    assert "thiếu local fallback" in translation.detail


def test_preflight_turns_route_probe_failure_into_actionable_error(tmp_path: Path, monkeypatch) -> None:
    import vi_dubber.preflight as preflight

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(preflight, "WORK_DIR", work)
    monkeypatch.setattr(preflight, "configure_runtime", lambda: None)
    monkeypatch.setattr(
        preflight,
        "webgpt_route_info",
        lambda _config=None: (_ for _ in ()).throw(RuntimeError("route probe failed")),
    )

    checks = run_preflight(_config(), translation_provider="webgpt")
    translation = {item.name: item for item in checks}["translation"]

    assert translation.status == "error"
    assert "route probe failed" in translation.detail


def test_preflight_rejects_unbounded_retry_budget(tmp_path: Path, monkeypatch) -> None:
    import vi_dubber.preflight as preflight

    work = tmp_path / "work"
    work.mkdir()
    model = tmp_path / "models" / "llm" / "Qwen--Qwen3-14B-GGUF" / "Qwen3-14B-Q4_K_M.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    monkeypatch.setattr(preflight, "WORK_DIR", work)
    monkeypatch.setattr(preflight, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(preflight, "configure_runtime", lambda: None)
    config = _config()
    config["reliability"]["retry_budget"] = 99

    checks = run_preflight(config, translation_provider="local")
    retry = {item.name: item for item in checks}["retry_policy"]

    assert retry.status == "error"
    with pytest.raises(RuntimeError, match="retry_policy"):
        raise_for_preflight(checks)
