from contextlib import contextmanager
import json
from pathlib import Path
import subprocess

import pytest

import vi_dubber.translate as translate_module
from vi_dubber.translate import HybridTranslator, WebGptTranslator, build_translator
from vi_dubber.types import Segment


class _DirectResponse:
    def __init__(self, status_code: int, payload=None, text: str | None = None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _direct_completed(output_text: str) -> dict:
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output_text}],
            }
        ],
    }


def test_build_translator_wires_retry_budget(tmp_path: Path) -> None:
    webgpt = build_translator(
        {},
        tmp_path,
        "webgpt",
        retry_budget=2,
        model_override="chatgpt-web/gpt-5.6-sol-instant",
        effort_override="low",
    )
    hybrid = build_translator({}, tmp_path, "hybrid", retry_budget=2)

    assert isinstance(webgpt, WebGptTranslator)
    assert webgpt.retry_budget == 2
    assert webgpt.model == "chatgpt-web/gpt-5.6-sol-instant"
    assert webgpt.effort == "low"
    assert isinstance(hybrid, HybridTranslator)
    assert hybrid.primary.retry_budget == 2


def test_webgpt_catalog_preserves_live_reasoning_efforts(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "data": [
                    {
                        "id": "chatgpt-web/gpt-5.6-sol",
                        "display_name": "GPT-5.6 Sol (Web)",
                        "reasoning_efforts": ["medium", "high"],
                        "default_reasoning_effort": "high",
                    }
                ]
            }

    monkeypatch.setattr(translate_module.requests, "get", lambda *args, **kwargs: Response())

    catalog = translate_module.webgpt_model_catalog()

    assert catalog["default_model"] == "chatgpt-web/gpt-5.6-sol"
    assert catalog["default_effort"] == "high"
    assert catalog["models"][0]["supported_efforts"] == ["medium", "high"]
    assert catalog["models"][0]["default_effort"] == "high"


def test_webgpt_running_fails_closed_for_model_or_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        translate_module,
        "webgpt_route_info",
        lambda _config: {"ready": True, "reason": ""},
    )
    monkeypatch.setattr(
        translate_module,
        "webgpt_model_catalog",
        lambda _config=None: {
            "models": [
                {
                    "id": "chatgpt-web/gpt-5.6-sol",
                    "supported_efforts": ["medium", "high"],
                }
            ]
        },
    )

    missing = WebGptTranslator({}, tmp_path, model_override="chatgpt-web/missing")
    with pytest.raises(RuntimeError, match="không còn trong live catalog"):
        with missing.running():
            pass

    unsupported = WebGptTranslator(
        {},
        tmp_path,
        model_override="chatgpt-web/gpt-5.6-sol",
        effort_override="max",
    )
    with pytest.raises(RuntimeError, match="không advertise effort"):
        with unsupported.running():
            pass


def test_webgpt_running_validates_the_override_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_configs: list[dict] = []
    catalog_configs: list[dict] = []

    def route_info(config):
        route_configs.append(dict(config))
        return {"ready": True, "reason": ""}

    def catalog(config=None):
        catalog_configs.append(dict(config or {}))
        return {
            "models": [
                {
                    "id": "chatgpt-web/gpt-5.6-sol-instant",
                    "supported_efforts": ["low"],
                }
            ]
        }

    monkeypatch.setattr(translate_module, "webgpt_route_info", route_info)
    monkeypatch.setattr(translate_module, "webgpt_model_catalog", catalog)

    translator = WebGptTranslator(
        {"webgpt_model": "chatgpt-web/stale-model"},
        tmp_path,
        model_override="chatgpt-web/gpt-5.6-sol-instant",
        effort_override="low",
    )
    with translator.running():
        pass

    assert route_configs[0]["webgpt_model"] == "chatgpt-web/gpt-5.6-sol-instant"
    assert catalog_configs[0]["webgpt_model"] == "chatgpt-web/gpt-5.6-sol-instant"


def test_webgpt_rejects_wrong_instance_base_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Dedicated Dubber-WebGPT"):
        WebGptTranslator(
            {"webgpt_base_url": "http://127.0.0.1:17841/v1"},
            tmp_path,
        )


def test_webgpt_catalog_fails_closed_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"data": []}

    monkeypatch.setattr(translate_module.requests, "get", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError, match="catalog đang trống"):
        translate_module.webgpt_model_catalog()


def test_webgpt_retries_transient_execution_failure_then_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_retry_backoff_seconds": 0.25},
        tmp_path,
        retry_budget=2,
        model_override="chatgpt-web/gpt-5.6-sol",
        effort_override="high",
    )
    calls = 0
    sleeps: list[float] = []

    monkeypatch.setattr(translate_module, "find_codex_exe", lambda: "codex")
    monkeypatch.setattr(translate_module.time, "sleep", sleeps.append)

    def fake_run(cmd, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        assert 'model_provider="codex_local_access"' in cmd
        assert 'model_providers.codex_local_access.base_url="http://127.0.0.1:17850/v1"' in cmd
        assert 'model_reasoning_effort="high"' in cmd
        assert cmd[cmd.index("--model") + 1] == "chatgpt-web/gpt-5.6-sol"
        if calls == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="temporary route failure")
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text('{"ok": true}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(translate_module.subprocess, "run", fake_run)

    assert translator._run_json("test", {"type": "object"}, "translate") == {"ok": True}
    stats = translator.stats()
    assert calls == 2
    assert sleeps == [0.25]
    assert stats["webgpt_attempts"] == 2
    assert stats["webgpt_retry_attempts"] == 1
    assert stats["webgpt_failures"] == 1
    assert stats["webgpt_retries_exhausted"] == 0


def test_webgpt_retry_budget_exhaustion_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_retry_backoff_seconds": 0.0},
        tmp_path,
        retry_budget=2,
    )
    calls = 0

    monkeypatch.setattr(translate_module, "find_codex_exe", lambda: "codex")

    def fake_run(cmd, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="temporary execution failure")

    monkeypatch.setattr(translate_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="temporary execution failure"):
        translator._run_json("test", {"type": "object"}, "translate")

    stats = translator.stats()
    assert calls == 3
    assert stats["webgpt_attempts"] == 3
    assert stats["webgpt_retry_attempts"] == 2
    assert stats["webgpt_failures"] == 3
    assert stats["webgpt_retries_exhausted"] == 1


def test_webgpt_capacity_failure_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_retry_backoff_seconds": 0.0},
        tmp_path,
        retry_budget=3,
    )
    calls = 0

    monkeypatch.setattr(translate_module, "find_codex_exe", lambda: "codex")

    def fake_run(cmd, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="ERROR: Selected model is at capacity. Please try a different model.",
        )

    monkeypatch.setattr(translate_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="at capacity"):
        translator._run_json("test", {"type": "object"}, "translate")

    stats = translator.stats()
    assert calls == 1
    assert stats["webgpt_attempts"] == 1
    assert stats["webgpt_retry_attempts"] == 0
    assert stats["webgpt_failures"] == 1
    assert stats["webgpt_pressure_failures"] == 1
    assert stats["webgpt_retries_exhausted"] == 0


def test_webgpt_malformed_result_is_retried_then_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_retry_backoff_seconds": 0.0},
        tmp_path,
        retry_budget=2,
    )
    calls = 0

    monkeypatch.setattr(translate_module, "find_codex_exe", lambda: "codex")

    def fake_run(cmd, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text(
            "not json" if calls == 1 else '{"ok": true}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(translate_module.subprocess, "run", fake_run)

    assert translator._run_json("test", {"type": "object"}, "translate") == {"ok": True}

    stats = translator.stats()
    assert calls == 2
    assert stats["webgpt_attempts"] == 2
    assert stats["webgpt_retry_attempts"] == 1
    assert stats["webgpt_failures"] == 1


def test_webgpt_malformed_result_retry_exhaustion_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_retry_backoff_seconds": 0.0},
        tmp_path,
        retry_budget=1,
    )
    calls = 0

    monkeypatch.setattr(translate_module, "find_codex_exe", lambda: "codex")

    def fake_run(cmd, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("not json", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(translate_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="JSON không hợp lệ"):
        translator._run_json("test", {"type": "object"}, "translate")

    stats = translator.stats()
    assert calls == 2
    assert stats["webgpt_attempts"] == 2
    assert stats["webgpt_retry_attempts"] == 1
    assert stats["webgpt_failures"] == 2
    assert stats["webgpt_retries_exhausted"] == 1


def test_webgpt_direct_responses_is_opt_in_and_posts_tool_free_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = WebGptTranslator({}, tmp_path / "default")
    direct = WebGptTranslator(
        {"webgpt_transport": "direct-responses"},
        tmp_path / "direct",
        model_override="chatgpt-web/gpt-5.6-sol",
        effort_override="high",
    )
    calls: list[tuple[str, dict]] = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _DirectResponse(200, _direct_completed('{"ok": true}'))

    monkeypatch.setattr(translate_module.requests, "post", fake_post)

    result = direct._run_json("translate this", {"type": "object"}, "translate")

    assert default.transport == "codex-exec"
    assert direct.transport == "direct-responses"
    assert result == {"ok": True}
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:17850/v1/responses"
    assert kwargs["timeout"] == direct.timeout
    payload = kwargs["json"]
    assert payload["model"] == "chatgpt-web/gpt-5.6-sol"
    assert payload["tools"] == []
    assert payload["tool_choice"] == "none"
    assert payload["parallel_tool_calls"] is False
    assert payload["stream"] is False
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "high", "summary": "none"}
    turn_metadata = json.loads(payload["client_metadata"]["x-codex-turn-metadata"])
    assert kwargs["headers"] == {"Accept": "application/json"}
    assert turn_metadata["thread_id"].startswith("thread_vi_dubber_")
    assert turn_metadata["turn_id"].startswith("turn_vi_dubber_")
    assert payload["prompt_cache_key"] == turn_metadata["thread_id"]
    assert payload["input"][0]["internal_chat_message_metadata_passthrough"] == {
        "turn_id": turn_metadata["turn_id"]
    }
    prompt_text = payload["input"][0]["content"][0]["text"]
    assert "translate this" in prompt_text
    assert "Output JSON schema" in prompt_text


def test_webgpt_direct_responses_retries_transient_http_then_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_transport": "direct-responses", "webgpt_retry_backoff_seconds": 0.0},
        tmp_path,
        retry_budget=1,
    )
    responses = [
        _DirectResponse(503, text="temporary upstream failure"),
        _DirectResponse(200, _direct_completed('{"ok": true}')),
    ]
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        response = responses[calls]
        calls += 1
        return response

    monkeypatch.setattr(translate_module.requests, "post", fake_post)

    assert translator._run_json("test", {"type": "object"}, "translate") == {"ok": True}
    stats = translator.stats()
    assert calls == 2
    assert stats["webgpt_attempts"] == 2
    assert stats["webgpt_retry_attempts"] == 1
    assert stats["webgpt_failures"] == 1


def test_webgpt_direct_disconnect_does_not_commit_partial_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {
            "webgpt_transport": "direct-responses",
            "webgpt_retry_backoff_seconds": 0.0,
            "codex_segments_per_batch": 1,
        },
        tmp_path,
        retry_budget=0,
    )
    segment = Segment(id=0, start=0.0, end=1.0, text="Hello")

    monkeypatch.setattr(
        translate_module.requests,
        "post",
        lambda *args, **kwargs: _DirectResponse(503, text="runtime disconnected"),
    )

    with pytest.raises(RuntimeError, match="HTTP 503"):
        translator.translate_segments([segment], {})

    assert segment.vi == ""
    assert not (tmp_path / "translations_cache.json").exists()


def test_webgpt_concurrent_failure_does_not_commit_successful_sibling_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {
            "webgpt_transport": "direct-responses",
            "webgpt_concurrency": 2,
            "codex_segments_per_batch": 1,
        },
        tmp_path,
        retry_budget=0,
    )
    segments = [
        Segment(id=0, start=0.0, end=1.0, text="First"),
        Segment(id=1, start=1.0, end=2.0, text="Second"),
    ]
    first_finished = translate_module.threading.Event()

    def fake_run_translation_request(prompt, schema, request_identity):
        del schema, request_identity
        input_payload = json.loads(prompt.rsplit("INPUT=", 1)[1])
        current_id = int(input_payload[0]["id"])
        if current_id == 0:
            first_finished.set()
            return {"translations": [{"id": 0, "vi": "Một"}]}
        assert first_finished.wait(timeout=1.0)
        translate_module.time.sleep(0.05)
        raise RuntimeError("simulated concurrent batch failure")

    monkeypatch.setattr(translator, "_run_translation_request", fake_run_translation_request)

    with pytest.raises(RuntimeError, match="simulated concurrent batch failure"):
        translator.translate_segments(segments, {})

    assert [segment.vi for segment in segments] == ["", ""]
    assert not (tmp_path / "translations_cache.json").exists()



@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_DirectResponse(429, text="rate limit exceeded; cooldown active"), "HTTP 429"),
        (
            _DirectResponse(
                200,
                {
                    "status": "failed",
                    "error": {
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                        "message": "Selected model is at capacity",
                    },
                    "output": [],
                },
            ),
            "at capacity",
        ),
    ],
)
def test_webgpt_direct_responses_pressure_failure_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: _DirectResponse,
    message: str,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_transport": "direct-responses", "webgpt_retry_backoff_seconds": 0.0},
        tmp_path,
        retry_budget=3,
    )
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return response

    monkeypatch.setattr(translate_module.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match=message):
        translator._run_json("test", {"type": "object"}, "translate")

    stats = translator.stats()
    assert calls == 1
    assert stats["webgpt_attempts"] == 1
    assert stats["webgpt_retry_attempts"] == 0
    assert stats["webgpt_failures"] == 1
    assert stats["webgpt_pressure_failures"] == 1


def test_webgpt_direct_responses_invalid_request_fails_closed_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_transport": "direct-responses", "webgpt_retry_backoff_seconds": 0.0},
        tmp_path,
        retry_budget=3,
    )
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return _DirectResponse(400, text="model not found")

    monkeypatch.setattr(translate_module.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match="HTTP 400"):
        translator._run_json("test", {"type": "object"}, "translate")

    stats = translator.stats()
    assert calls == 1
    assert stats["webgpt_attempts"] == 1
    assert stats["webgpt_retry_attempts"] == 0
    assert stats["webgpt_failures"] == 1
    assert stats["webgpt_pressure_failures"] == 0


def test_webgpt_direct_responses_malformed_output_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {"webgpt_transport": "direct-responses", "webgpt_retry_backoff_seconds": 0.0},
        tmp_path,
        retry_budget=1,
    )
    responses = [
        _DirectResponse(200, _direct_completed("not json")),
        _DirectResponse(200, _direct_completed('{"ok": true}')),
    ]
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        response = responses[calls]
        calls += 1
        return response

    monkeypatch.setattr(translate_module.requests, "post", fake_post)

    assert translator._run_json("test", {"type": "object"}, "translate") == {"ok": True}
    stats = translator.stats()
    assert calls == 2
    assert stats["webgpt_retry_attempts"] == 1
    assert stats["webgpt_failures"] == 1


def test_hybrid_retries_webgpt_before_local_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = HybridTranslator(
        {"webgpt_retry_backoff_seconds": 0.0, "codex_segments_per_batch": 1},
        tmp_path,
        retry_budget=1,
    )
    calls = 0

    @contextmanager
    def running():
        yield

    monkeypatch.setattr(translator.primary, "running", running)
    monkeypatch.setattr(translator.fallback, "running", running)
    monkeypatch.setattr(translate_module, "find_codex_exe", lambda: "codex")

    def fake_run(cmd, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="temporary failure")
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text(
            json.dumps({"translations": [{"id": 0, "vi": "Xin chào"}]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(translate_module.subprocess, "run", fake_run)

    segments = [Segment(id=0, start=0.0, end=1.0, text="Hello")]
    with translator.running():
        result = translator.translate_segments(segments, {})

    stats = translator.stats()
    assert result[0].vi == "Xin chào"
    assert calls == 2
    assert stats["used"] == "webgpt"
    assert stats["fallback_used"] is False
    assert stats["webgpt_retry_attempts"] == 1


def test_hybrid_falls_back_to_local_after_webgpt_retry_budget_is_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = HybridTranslator(
        {"webgpt_retry_backoff_seconds": 0.0, "codex_segments_per_batch": 1},
        tmp_path,
        retry_budget=1,
    )
    webgpt_calls = 0
    fallback_calls = 0

    @contextmanager
    def running():
        yield

    monkeypatch.setattr(translator.primary, "running", running)
    monkeypatch.setattr(translator.fallback, "running", running)
    monkeypatch.setattr(translate_module, "find_codex_exe", lambda: "codex")

    def fail_webgpt(cmd, **kwargs):
        nonlocal webgpt_calls
        del kwargs
        webgpt_calls += 1
        return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="synthetic route down")

    def local_translate(segments, glossary, progress_callback=None):
        nonlocal fallback_calls
        del glossary
        fallback_calls += 1
        segments[0].vi = "Xin chào từ local"
        translator.fallback.translation_batches += 1
        if progress_callback is not None:
            progress_callback(1.0, "Local translation 1/1")
        return segments

    monkeypatch.setattr(translate_module.subprocess, "run", fail_webgpt)
    monkeypatch.setattr(translator.fallback, "translate_segments", local_translate)

    segments = [Segment(id=0, start=0.0, end=1.0, text="Hello")]
    with translator.running():
        result = translator.translate_segments(segments, {})

    stats = translator.stats()
    assert result[0].vi == "Xin chào từ local"
    assert webgpt_calls == 2
    assert fallback_calls == 1
    assert stats["used"] == "local"
    assert stats["fallback_used"] is True
    assert "synthetic route down" in stats["fallback_reason"]
    assert stats["webgpt_attempts"] == 2
    assert stats["webgpt_retry_attempts"] == 1
    assert stats["webgpt_retries_exhausted"] == 1
    assert stats["local_batches"] == 1
