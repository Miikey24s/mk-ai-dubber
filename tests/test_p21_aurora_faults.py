from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from vi_dubber import translate
from vi_dubber.artifacts import stage_fingerprint
from vi_dubber.translate import HybridTranslator, WebGptTranslator
from vi_dubber.types import Segment


MODEL_ID = "gpt-test-sol"
MODEL_DISPLAY_NAME = "GPT Test Sol"


def _aurora_translator_cls():
    cls = getattr(translate, "AuroraTranslator", None)
    assert cls is not None, (
        "P21 contract expects vi_dubber.translate.AuroraTranslator; align this test helper "
        "if the production adapter intentionally uses a different public class name"
    )
    return cls


def _aurora_config(base_url: str) -> dict[str, Any]:
    # Include explicit aliases so the contract does not care whether the adapter
    # keeps Aurora settings flat or under transport-specific names.
    return {
        "provider": "aurora",
        "base_url": base_url,
        "aurora_base_url": base_url,
        "model": MODEL_ID,
        "model_id": MODEL_ID,
        "aurora_model": MODEL_ID,
        "model_display_name": MODEL_DISPLAY_NAME,
        "effort": "high",
        "reasoning_effort": "high",
        "aurora_effort": "high",
        "api_key": "local-p21-test-token",
        "aurora_api_key": "local-p21-test-token",
        "timeout_seconds": 2,
        "aurora_timeout_seconds": 2,
        "retry_backoff_seconds": 0.01,
        "retry_backoff_max_seconds": 0.02,
        "aurora_retry_backoff_seconds": 0.01,
        "aurora_retry_backoff_max_seconds": 0.02,
        "segments_per_batch": 1,
        "codex_segments_per_batch": 1,
    }


def _segment() -> Segment:
    return Segment(id=1, start=0.0, end=2.0, text="Hello world")


class _AuroraFixture:
    def __init__(self) -> None:
        self.catalog_status = 200
        self.models = [MODEL_ID]
        self.catalog_revision = "catalog-r1"
        self.post_statuses: list[int] = [200]
        self.requests: list[dict[str, Any]] = []

    def catalog_payload(self) -> dict[str, Any]:
        models = [
            {
                "id": model_id,
                "name": MODEL_DISPLAY_NAME if model_id == MODEL_ID else model_id,
                "display_name": MODEL_DISPLAY_NAME if model_id == MODEL_ID else model_id,
                "capabilities": {"reasoning_effort": ["low", "high"]},
                "supported_efforts": ["low", "high"],
            }
            for model_id in self.models
        ]
        return {
            "data": models,
            "models": models,
            "revision": self.catalog_revision,
            "updated_at": "2026-09-23T09:00:00Z",
        }

    def translation_payload(self) -> dict[str, Any]:
        result = {"translations": [{"id": 1, "vi": "Xin chao the gioi"}]}
        content = json.dumps(result, ensure_ascii=False)
        return {
            "choices": [{"message": {"content": content}}],
            "output_text": content,
            "output": [{"content": [{"type": "output_text", "text": content}]}],
            "result": result,
        }


@contextmanager
def _serve_aurora(fixture: _AuroraFixture) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status == 429:
                self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            fixture.requests.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                }
            )
            if "model" in self.path.lower():
                self._send(fixture.catalog_status, fixture.catalog_payload())
                return
            self._send(200, {"ok": True, "status": "ready"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw.decode("utf-8", errors="replace")}
            fixture.requests.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "payload": payload,
                }
            )
            status = fixture.post_statuses.pop(0) if fixture.post_statuses else 200
            if status == 200:
                self._send(status, fixture.translation_payload())
            else:
                self._send(status, {"error": {"message": f"fixture HTTP {status}"}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _new_aurora(base_url: str, work_dir: Path, *, retry_budget: int = 2):
    cls = _aurora_translator_cls()
    config = _aurora_config(base_url)
    try:
        return cls(config, work_dir, retry_budget=retry_budget)
    except TypeError:
        # Keep the fault tests compatible with an adapter that owns retry budget
        # inside its config while preserving the same external behavior.
        config["retry_budget"] = retry_budget
        return cls(config, work_dir)


def _run_one(translator: Any) -> list[Segment]:
    segment = _segment()
    with translator.running():
        result = translator.translate_segments([segment], {})
    return result


@pytest.mark.parametrize("catalog_status", [401, 403, 503])
def test_p21_auth_or_catalog_failure_is_actionable_and_never_generates(
    tmp_path: Path,
    catalog_status: int,
) -> None:
    fixture = _AuroraFixture()
    fixture.catalog_status = catalog_status
    with _serve_aurora(fixture) as base_url:
        translator = _new_aurora(base_url, tmp_path, retry_budget=1)
        with pytest.raises(Exception) as exc_info:
            _run_one(translator)

    assert str(exc_info.value).strip()
    assert not [request for request in fixture.requests if request["method"] == "POST"]


def test_p21_selected_model_unavailable_fails_closed_without_substitution(tmp_path: Path) -> None:
    fixture = _AuroraFixture()
    fixture.models = ["different-model"]
    with _serve_aurora(fixture) as base_url:
        translator = _new_aurora(base_url, tmp_path, retry_budget=1)
        with pytest.raises(Exception) as exc_info:
            _run_one(translator)

    assert MODEL_ID.lower() in str(exc_info.value).lower() or "model" in str(exc_info.value).lower()
    assert not [request for request in fixture.requests if request["method"] == "POST"]


def test_p21_429_retry_is_bounded_and_keeps_selected_model(tmp_path: Path) -> None:
    fixture = _AuroraFixture()
    fixture.post_statuses = [429, 429, 200]
    with _serve_aurora(fixture) as base_url:
        translator = _new_aurora(base_url, tmp_path, retry_budget=2)
        translated = _run_one(translator)

    posts = [request for request in fixture.requests if request["method"] == "POST"]
    assert len(posts) == 3
    assert translated[0].vi == "Xin chao the gioi"
    for request in posts:
        payload = request["payload"]
        requested_model = payload.get("model") or payload.get("model_id")
        assert requested_model == MODEL_ID
    stats = translator.stats()
    retry_count = (
        stats.get("aurora_retry_attempts")
        or stats.get("retry_attempts")
        or stats.get("retries")
    )
    assert int(retry_count) == 2


def test_p21_disconnect_or_restart_does_not_commit_partial_translation(tmp_path: Path) -> None:
    fixture = _AuroraFixture()
    fixture.post_statuses = [503]
    segment = _segment()
    with _serve_aurora(fixture) as base_url:
        translator = _new_aurora(base_url, tmp_path / "failed", retry_budget=0)
        with pytest.raises(Exception):
            with translator.running():
                translator.translate_segments([segment], {})
    assert segment.vi == ""

    recovered = _AuroraFixture()
    with _serve_aurora(recovered) as base_url:
        translator = _new_aurora(base_url, tmp_path / "recovered", retry_budget=1)
        translated = _run_one(translator)
    assert translated[0].vi == "Xin chao the gioi"


def test_p21_selection_snapshot_is_stable_and_fingerprints_model_effort_catalog() -> None:
    selection = {
        "provider": "aurora",
        "model_id": MODEL_ID,
        "display_name": MODEL_DISPLAY_NAME,
        "effort": "high",
        "catalog_revision": "catalog-r1",
    }
    frozen = dict(selection)
    first = stage_fingerprint(
        "translation",
        inputs={"source_segments": "segments-v1"},
        model=frozen,
    )

    live_catalog_selection = dict(selection)
    live_catalog_selection["model_id"] = "new-default-model"
    live_catalog_selection["display_name"] = "New Default Model"

    assert frozen == selection
    assert stage_fingerprint(
        "translation",
        inputs={"source_segments": "segments-v1"},
        model=frozen,
    ) == first
    assert stage_fingerprint(
        "translation",
        inputs={"source_segments": "segments-v1"},
        model=live_catalog_selection,
    ) != first
    assert stage_fingerprint(
        "translation",
        inputs={"source_segments": "segments-v1"},
        model={**frozen, "effort": "low"},
    ) != first
    assert stage_fingerprint(
        "translation",
        inputs={"source_segments": "segments-v1"},
        model={**frozen, "catalog_revision": "catalog-r2"},
    ) != first


def test_p21_qwen_fallback_and_legacy_rollback_keep_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hybrid = HybridTranslator({"webgpt_retry_backoff_seconds": 0.0}, tmp_path, retry_budget=0)

    @contextmanager
    def local_running():
        yield hybrid.fallback

    monkeypatch.setattr(hybrid.primary, "running", lambda: (_ for _ in ()).throw(RuntimeError("aurora unavailable")))
    monkeypatch.setattr(hybrid.fallback, "running", local_running)
    monkeypatch.setattr(
        hybrid.fallback,
        "translate_segments",
        lambda segments, _glossary, _progress=None: [
            Segment(
                id=item.id,
                start=item.start,
                end=item.end,
                text=item.text,
                speaker=item.speaker,
                vi="fallback qwen",
            )
            for item in segments
        ],
    )

    with hybrid.running():
        translated = hybrid.translate_segments([_segment()], {})
    stats = hybrid.stats()
    assert translated[0].vi == "fallback qwen"
    assert stats["requested"] == "hybrid"
    assert stats["used"] == "local"
    assert stats["fallback_used"] is True
    assert stats["fallback_reason"] == "aurora unavailable"
    assert stats["model"] == hybrid.fallback.model_id

    legacy = WebGptTranslator({}, tmp_path / "legacy", retry_budget=0).stats()
    assert legacy["requested"] == "webgpt"
    assert legacy["used"] == "webgpt"
    assert legacy["model"] == translate.PINNED_WEBGPT_MODEL
    assert legacy["fallback_used"] is False
