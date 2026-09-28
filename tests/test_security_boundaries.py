from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

import vi_dubber.api as api_mod
import vi_dubber.translate as translate_mod
from vi_dubber.api import create_app
from vi_dubber.runtime import validate_server_bind_host
from vi_dubber.translate import WebGptTranslator, _safe_provider_detail


def test_provider_detail_does_not_persist_arbitrary_response_body() -> None:
    secret_body = (
        'prompt="Do not persist this source" '
        'Bearer super-secret-token response={"message":"private content"}'
    )

    detail = _safe_provider_detail(secret_body)

    assert detail == "provider detail redacted"
    assert "super-secret-token" not in detail
    assert "private content" not in detail


def test_provider_detail_keeps_only_known_operational_signal() -> None:
    assert _safe_provider_detail("ERROR: Selected model is at capacity") == (
        "selected model is at capacity"
    )


@pytest.mark.parametrize(
    ("provider_message", "expected"),
    [
        (
            "ChatGPT web login is expired or the Temporary Chat surface is unavailable",
            "chatgpt web login is expired",
        ),
        (
            "Temporary Chat surface is unavailable",
            "temporary chat surface is unavailable",
        ),
    ],
)
def test_provider_detail_keeps_dedicated_webgpt_login_gate(
    provider_message: str,
    expected: str,
) -> None:
    assert _safe_provider_detail(provider_message) == expected


def test_direct_provider_body_is_not_exposed_in_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        ok = False
        status_code = 502
        text = 'prompt="private source" Bearer body-secret {"error":"echoed"}'

    monkeypatch.setattr(translate_mod.requests, "post", lambda *args, **kwargs: Response())
    translator = WebGptTranslator(
        {"webgpt_transport": "direct-responses"},
        tmp_path,
        retry_budget=0,
    )

    with pytest.raises(RuntimeError) as exc_info:
        translator._run_json("source must never appear", {"type": "object"}, "translate")

    message = str(exc_info.value)
    assert "body-secret" not in message
    assert "private source" not in message
    assert "provider detail redacted" in message


def test_codex_failure_removes_untrusted_output_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(translate_mod, "find_codex_exe", lambda: "codex")

    def fake_run(cmd, **kwargs):
        del kwargs
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("provider echoed private source", encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="Bearer stderr-secret provider failure",
        )

    monkeypatch.setattr(translate_mod.subprocess, "run", fake_run)
    translator = WebGptTranslator({}, tmp_path, retry_budget=0)

    with pytest.raises(RuntimeError) as exc_info:
        translator._run_json("source must never appear", {"type": "object"}, "translate")

    assert "stderr-secret" not in str(exc_info.value)
    assert not list((tmp_path / "webgpt").glob("translate-*.json"))


def test_default_cors_does_not_allow_arbitrary_origins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(api_mod, "WORK_DIR", work)
    monkeypatch.delenv("VI_DUBBER_CORS_ORIGINS", raising=False)
    client = TestClient(create_app())

    response = client.options(
        "/api/health",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_default_cors_allows_local_dev_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(api_mod, "WORK_DIR", work)
    monkeypatch.delenv("VI_DUBBER_CORS_ORIGINS", raising=False)
    client = TestClient(create_app())

    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:7860",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:7860"


def test_remote_bind_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VI_DUBBER_ALLOW_REMOTE_BIND", raising=False)
    with pytest.raises(ValueError, match="chỉ bind localhost"):
        validate_server_bind_host("0.0.0.0")

    monkeypatch.setenv("VI_DUBBER_ALLOW_REMOTE_BIND", "1")
    assert validate_server_bind_host("0.0.0.0") == "0.0.0.0"
