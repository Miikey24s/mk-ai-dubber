from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time

import pytest

from vi_dubber.media import _atempo_chain, write_srt
from vi_dubber.qa import transcript_similarity
from vi_dubber.semantic_qa import evaluate_semantic_qa, run_semantic_qa_stage
from vi_dubber.translate import (
    PINNED_WEBGPT_MODEL,
    HybridTranslator,
    LocalTranslator,
    WebGptTranslator,
    _extract_json,
    build_global_translation_context,
    build_translator,
)
from vi_dubber.terminology import TerminologyEntry, TerminologyGlossary
from vi_dubber.types import Segment
from vi_dubber.youtube import is_youtube_url


def test_segment_round_trip() -> None:
    original = Segment(id=7, start=1.25, end=3.5, text="Hello", speaker="SPEAKER_01", vi="Xin chao")
    restored = Segment.from_dict(original.to_dict())
    assert restored == original
    assert restored.duration == 2.25


def test_atempo_chain_stays_in_ffmpeg_range() -> None:
    chain = _atempo_chain(4.8)
    factors = [float(item.split("=")[1]) for item in chain.split(",")]
    assert all(0.5 <= factor <= 2.0 for factor in factors)
    product = 1.0
    for factor in factors:
        product *= factor
    assert abs(product - 4.8) < 1e-6


def test_qa_ignores_punctuation_and_case() -> None:
    score = transcript_similarity("Xin chao, ban!", "xin chao ban")
    assert score == 1.0


def test_semantic_qa_parses_choice_and_noul(monkeypatch: pytest.MonkeyPatch) -> None:
    import vi_dubber.semantic_qa as semantic_qa

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "model": "jev-test",
                "answers": {
                    "faithfulness_7": {
                        "type": "choice",
                        "choice": "faithful",
                        "probabilities": {"faithful": 0.91, "partial": 0.08, "wrong": 0.01},
                        "confidence": 0.87,
                    },
                    "critical_facts_7": {"type": "noul", "noul": 0.95},
                },
                "usage": {"input_tokens": 123, "output_tokens": 17},
            }

    monkeypatch.setattr(semantic_qa.requests, "post", lambda *args, **kwargs: FakeResponse())
    result = evaluate_semantic_qa(
        [Segment(id=7, start=0.0, end=1.0, text="I will not sell.", vi="Tôi sẽ không bán.")],
        {},
        stage="translated",
        api_key="test-key",
    )
    assert result["status"] == "ok"
    assert result["needs_review"] == 0
    assert result["items"][0]["choice"] == "faithful"
    assert result["items"][0]["critical_facts_probability"] == 0.95
    assert result["usage"] == {"requests": 1, "input_tokens": 123, "output_tokens": 17}


def test_semantic_qa_flags_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    import vi_dubber.semantic_qa as semantic_qa

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "model": "jev-test",
                "answers": {
                    "faithfulness_1": {
                        "type": "choice",
                        "choice": "faithful",
                        "probabilities": {"faithful": 0.56, "partial": 0.43, "wrong": 0.01},
                        "confidence": 0.33,
                    },
                    "critical_facts_1": {"type": "noul", "noul": 0.90},
                },
                "usage": {},
            }

    monkeypatch.setattr(semantic_qa.requests, "post", lambda *args, **kwargs: FakeResponse())
    result = evaluate_semantic_qa(
        [Segment(id=1, start=0.0, end=1.0, text="A nuanced sentence.", vi="Một câu khá sát nghĩa.")],
        {},
        stage="rewritten",
        api_key="test-key",
    )
    assert result["needs_review"] == 1
    assert result["items"][0]["needs_review"] is True


def test_semantic_qa_missing_key_is_shadow_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    cache_path = tmp_path / "semantic_qa.json"
    result = run_semantic_qa_stage(
        [Segment(id=0, start=0.0, end=1.0, text="Hello", vi="Xin chào")],
        {"enabled": True},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "missing_TYPESAFE_API_KEY"
    assert cache_path.exists()


def test_semantic_qa_success_is_reused_from_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import vi_dubber.semantic_qa as semantic_qa

    calls = 0

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "model": "jev-test",
                "answers": {
                    "faithfulness_0": {
                        "type": "choice",
                        "choice": "faithful",
                        "probabilities": {"faithful": 0.95, "partial": 0.04, "wrong": 0.01},
                        "confidence": 0.93,
                    },
                    "critical_facts_0": {"type": "noul", "noul": 0.96},
                },
                "usage": {},
            }

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(semantic_qa.requests, "post", fake_post)
    segments = [Segment(id=0, start=0.0, end=1.0, text="Hello", vi="Xin chào")]
    cache_path = tmp_path / "semantic_qa.json"
    first = run_semantic_qa_stage(
        segments,
        {"enabled": True},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    second = run_semantic_qa_stage(
        segments,
        {"enabled": True},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    assert first["status"] == "ok"
    assert first["_runtime_cache_hit"] is False
    assert second["_runtime_cache_hit"] is True
    assert {k: v for k, v in second.items() if k != "_runtime_cache_hit"} == {
        k: v for k, v in first.items() if k != "_runtime_cache_hit"
    }
    assert calls == 1


def test_semantic_qa_threshold_change_reuses_raw_inference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import vi_dubber.semantic_qa as semantic_qa

    calls = 0

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "model": "jev-test",
                "answers": {
                    "faithfulness_0": {
                        "type": "choice",
                        "choice": "faithful",
                        "probabilities": {"faithful": 0.70, "partial": 0.29, "wrong": 0.01},
                        "confidence": 0.55,
                    },
                    "critical_facts_0": {"type": "noul", "noul": 0.90},
                },
                "usage": {},
            }

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(semantic_qa.requests, "post", fake_post)
    segments = [Segment(id=0, start=0.0, end=1.0, text="Hello", vi="Xin chào")]
    cache_path = tmp_path / "semantic_qa.json"
    first = run_semantic_qa_stage(
        segments,
        {"enabled": True, "review_faithful_below": 0.65},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    second = run_semantic_qa_stage(
        segments,
        {"enabled": True, "review_faithful_below": 0.75},
        stage="translated",
        cache_path=cache_path,
        resume=True,
    )
    assert first["needs_review"] == 0
    assert second["needs_review"] == 1
    assert calls == 1
    assert first["fingerprint"] == second["fingerprint"]


def test_semantic_qa_retries_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import vi_dubber.semantic_qa as semantic_qa

    calls = 0

    class RateLimitedResponse:
        status_code = 429
        text = "rate limited"

    class SuccessResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "model": "jev-test",
                "answers": {
                    "faithfulness_0": {
                        "type": "choice",
                        "choice": "faithful",
                        "probabilities": {"faithful": 0.95, "partial": 0.04, "wrong": 0.01},
                        "confidence": 0.93,
                    },
                    "critical_facts_0": {"type": "noul", "noul": 0.96},
                },
                "usage": {},
            }

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return RateLimitedResponse() if calls == 1 else SuccessResponse()

    monkeypatch.setattr(semantic_qa.requests, "post", fake_post)
    monkeypatch.setattr(semantic_qa.time, "sleep", lambda *_: None)
    result = evaluate_semantic_qa(
        [Segment(id=0, start=0.0, end=1.0, text="Hello", vi="Xin chào")],
        {"max_attempts": 2},
        stage="translated",
        api_key="test-key",
    )
    assert result["status"] == "ok"
    assert calls == 2


def test_semantic_qa_http_error_does_not_escape_shadow_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vi_dubber.semantic_qa as semantic_qa

    class FakeResponse:
        status_code = 500
        text = "temporary failure"

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(semantic_qa.requests, "post", lambda *args, **kwargs: FakeResponse())
    result = run_semantic_qa_stage(
        [Segment(id=0, start=0.0, end=1.0, text="Hello", vi="Xin chào")],
        {"enabled": True, "max_attempts": 1},
        stage="translated",
        cache_path=tmp_path / "semantic_qa.json",
        resume=False,
    )
    assert result["status"] == "error"
    assert "HTTP 500" in result["reason"]


def test_write_srt(tmp_path: Path) -> None:
    segments = [Segment(id=0, start=0.0, end=1.234, text="Hello", vi="Xin chao")]
    target = tmp_path / "nested" / "out.srt"
    write_srt(segments, target, translated=True)
    text = target.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,234" in text
    assert "Xin chao" in text


def test_youtube_url_validation() -> None:
    assert is_youtube_url("https://www.youtube.com/watch?v=abc123")
    assert is_youtube_url("https://youtu.be/abc123")
    assert not is_youtube_url("https://example.com/watch?v=abc123")


def test_translation_provider_factory(tmp_path: Path) -> None:
    config = {"provider": "webgpt", "webgpt_model": PINNED_WEBGPT_MODEL}
    assert isinstance(build_translator(config, tmp_path, "webgpt"), WebGptTranslator)
    assert isinstance(build_translator(config, tmp_path, "codex"), WebGptTranslator)
    assert isinstance(build_translator(config, tmp_path, "local"), LocalTranslator)
    assert isinstance(build_translator(config, tmp_path, "hybrid"), HybridTranslator)
    with pytest.raises(ValueError):
        build_translator(config, tmp_path, "unknown")


def test_hybrid_falls_back_to_local_and_reports_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    translator = HybridTranslator({"webgpt_model": PINNED_WEBGPT_MODEL}, tmp_path)

    @contextmanager
    def running():
        yield

    monkeypatch.setattr(translator.primary, "running", running)
    monkeypatch.setattr(translator.fallback, "running", running)

    def fail_webgpt(*args, **kwargs):
        raise RuntimeError("web route unavailable")

    def translate_local(segments, glossary, progress_callback=None):
        segments[0].vi = "Xin chào từ local"
        translator.fallback.translation_batches += 1
        if progress_callback is not None:
            progress_callback(1.0, "Local translation 1/1")
        return segments

    monkeypatch.setattr(translator.primary, "translate_segments", fail_webgpt)
    monkeypatch.setattr(translator.fallback, "translate_segments", translate_local)

    segments = [Segment(id=0, start=0.0, end=1.0, text="Hello")]
    with translator.running():
        result = translator.translate_segments(segments, {})

    stats = translator.stats()
    assert result[0].vi == "Xin chào từ local"
    assert stats["requested"] == "hybrid"
    assert stats["used"] == "local"
    assert stats["fallback_used"] is True
    assert "web route unavailable" in stats["fallback_reason"]


def test_webgpt_defaults_to_current_sol_model_and_allows_catalog_model_override(tmp_path: Path) -> None:
    translator = WebGptTranslator({"webgpt_model": PINNED_WEBGPT_MODEL}, tmp_path)
    assert translator.model == PINNED_WEBGPT_MODEL
    instant = WebGptTranslator({"webgpt_model": "chatgpt-web/gpt-5.6-sol-instant"}, tmp_path)
    assert instant.model == "chatgpt-web/gpt-5.6-sol-instant"
    with pytest.raises(ValueError, match="instance 2"):
        WebGptTranslator({"webgpt_base_url": "http://127.0.0.1:17841/v1"}, tmp_path)


def test_global_translation_context_is_compact_and_domain_aware() -> None:
    glossary = TerminologyGlossary(
        {"order block": "order block"},
        entries=(
            TerminologyEntry(
                source="order block",
                policy="PREFER_EN",
                display="order block",
                domain="trading",
            ),
            TerminologyEntry(
                source="GPU",
                policy="KEEP_EN",
                display="GPU",
                domain="ai/software",
            ),
        ),
        profile={
            "audience": "Vietnamese traders",
            "register": "conversational/explanatory",
        },
    )
    segments = [
        Segment(id=index, start=float(index), end=float(index + 1), text=f"Segment {index}")
        for index in range(9)
    ]
    segments[4].text = "Wait for price to revisit the order block before entry."

    context = build_global_translation_context(
        segments,
        glossary,
        sample_count=3,
        max_source_chars=80,
    )

    assert context["audience_profile"]["audience"] == "Vietnamese traders"
    assert context["video"]["segment_count"] == 9
    assert [item["source"] for item in context["active_terminology"]] == ["order block"]
    assert len(context["source_samples"]) == 3
    assert sum(len(item["source_en"]) for item in context["source_samples"]) <= 80


def test_webgpt_translation_request_identity_ignores_concurrency_setting(tmp_path: Path) -> None:
    prompt = "translate this batch"
    schema = {"type": "object"}
    serial = WebGptTranslator({"webgpt_concurrency": 1}, tmp_path / "serial")
    parallel = WebGptTranslator({"webgpt_concurrency": 3}, tmp_path / "parallel")

    assert serial._translation_request_identity(prompt, schema) == parallel._translation_request_identity(prompt, schema)


def test_webgpt_parallel_translation_is_bounded_and_preserves_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator(
        {
            "codex_segments_per_batch": 1,
            "context_window": 0,
            "webgpt_concurrency": 2,
            "global_context_enabled": True,
        },
        tmp_path,
    )
    lock = threading.Lock()
    active = 0
    peak_active = 0
    seen_global_contexts: list[str] = []

    def fake_run_json(prompt: str, schema: dict, label: str):
        nonlocal active, peak_active
        del schema, label
        global_text = prompt.split("GlobalContextPack=", 1)[1].split("\nUse the global context", 1)[0]
        payload = json.loads(prompt.split("INPUT=", 1)[1])
        with lock:
            active += 1
            peak_active = max(peak_active, active)
            seen_global_contexts.append(global_text)
        time.sleep(0.04)
        with lock:
            active -= 1
        return {
            "translations": [
                {"id": item["id"], "vi": f"VI-{item['id']}"}
                for item in payload
            ]
        }

    monkeypatch.setattr(translator, "_run_json", fake_run_json)
    segments = [
        Segment(id=index, start=float(index), end=float(index + 1), text=f"source {index}")
        for index in range(4)
    ]

    result = translator.translate_segments(segments, {})

    assert peak_active == 2
    assert [item.vi for item in result] == ["VI-0", "VI-1", "VI-2", "VI-3"]
    assert len(set(seen_global_contexts)) == 1
    assert translator.stats()["translation_concurrency"] == 2


def test_webgpt_concurrency_is_capped_at_three(tmp_path: Path) -> None:
    translator = WebGptTranslator({"webgpt_concurrency": 99}, tmp_path)
    assert translator.concurrency == 3


def _seed_webgpt_translation_receipt(
    translator: WebGptTranslator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_text: str = "Buy the ETF.",
    glossary: dict[str, str] | None = None,
) -> None:
    response = {"translations": [{"id": 7, "vi": "Mua quỹ ETF."}]}

    def fake_run_json(prompt: str, schema: dict, label: str):
        del prompt, schema
        webgpt_dir = tmp_path / "webgpt"
        webgpt_dir.mkdir(parents=True, exist_ok=True)
        output_path = webgpt_dir / f"{label}-seed.json"
        output_path.write_text(json.dumps(response), encoding="utf-8")
        translator._last_output_path = output_path
        return response

    monkeypatch.setattr(translator, "_run_json", fake_run_json)
    translator.translate_segments(
        [Segment(id=7, start=0.0, end=1.0, text=source_text)],
        glossary or {"ETF": "quỹ ETF"},
    )
    (tmp_path / "translations_cache.json").unlink()


def test_webgpt_translation_receipt_reuses_matching_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"codex_segments_per_batch": 1, "context_window": 1}
    first = WebGptTranslator(config, tmp_path)
    _seed_webgpt_translation_receipt(first, tmp_path, monkeypatch)

    second = WebGptTranslator(dict(config), tmp_path)

    def fail_run_json(*args, **kwargs):
        raise AssertionError("matching receipt should have satisfied the translation request")

    monkeypatch.setattr(second, "_run_json", fail_run_json)
    result = second.translate_segments(
        [Segment(id=7, start=0.0, end=1.0, text="Buy the ETF.")],
        {"ETF": "quỹ ETF"},
    )

    assert result[0].vi == "Mua quỹ ETF."


@pytest.mark.parametrize("changed_input", ["source", "glossary", "config", "model"])
def test_webgpt_translation_receipt_rejects_stale_request_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
) -> None:
    base_config = {"codex_segments_per_batch": 1, "context_window": 1}
    first = WebGptTranslator(base_config, tmp_path)
    _seed_webgpt_translation_receipt(first, tmp_path, monkeypatch)

    source_text = "Buy the ETF."
    glossary = {"ETF": "quỹ ETF"}
    next_config = dict(base_config)
    if changed_input == "source":
        source_text = "Sell the ETF."
    elif changed_input == "glossary":
        glossary = {"ETF": "ETF"}
    elif changed_input == "config":
        next_config["context_window"] = 2

    second = WebGptTranslator(next_config, tmp_path)
    if changed_input == "model":
        second.model = f"{PINNED_WEBGPT_MODEL}-next"

    calls = 0

    def fake_run_json(prompt: str, schema: dict, label: str):
        nonlocal calls
        del prompt, schema, label
        calls += 1
        return {"translations": [{"id": 7, "vi": "Bản dịch mới."}]}

    monkeypatch.setattr(second, "_run_json", fake_run_json)
    result = second.translate_segments(
        [Segment(id=7, start=0.0, end=1.0, text=source_text)],
        glossary,
    )

    assert calls == 1
    assert result[0].vi == "Bản dịch mới."


def test_webgpt_translation_legacy_receipt_without_identity_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webgpt_dir = tmp_path / "webgpt"
    webgpt_dir.mkdir()
    (webgpt_dir / "translate-legacy.json").write_text(
        json.dumps({"translations": [{"id": 7, "vi": "Bản dịch stale."}]}),
        encoding="utf-8",
    )
    translator = WebGptTranslator({"codex_segments_per_batch": 1}, tmp_path)

    monkeypatch.setattr(
        translator,
        "_run_json",
        lambda prompt, schema, label: {"translations": [{"id": 7, "vi": "Bản dịch mới."}]},
    )
    result = translator.translate_segments(
        [Segment(id=7, start=0.0, end=1.0, text="Buy the ETF.")],
        {"ETF": "quỹ ETF"},
    )

    assert result[0].vi == "Bản dịch mới."


def test_webgpt_json_repairs_escaped_structural_brackets() -> None:
    payload = r'{"translations":\[{"id":0,"vi":"Xin chao"}\]}'
    result = _extract_json(payload, array=False)
    assert result["translations"][0]["vi"] == "Xin chao"


def test_webgpt_json_repairs_unescaped_inner_quotes() -> None:
    payload = r'{"translations":[{"id":305,"vi":"Tôi sẽ sửa thành "mỗi ETF", nhé."}]}'
    result = _extract_json(payload, array=False)
    assert result["translations"][0]["vi"] == 'Tôi sẽ sửa thành "mỗi ETF", nhé.'

    rewrite_payload = r'{"vi":"Tôi sửa "ngắn hơn""}'
    assert _extract_json(rewrite_payload, array=False)["vi"] == 'Tôi sửa "ngắn hơn"'


def test_webgpt_rewrite_batch_json() -> None:
    payload = r'{"rewrites":[{"id":7,"vi":"Không chiến lược nào bất bại."},{"id":8,"vi":"Không có lãi mọi lúc."}]}'
    result = _extract_json(payload, array=False)
    assert len(result["rewrites"]) == 2
    assert result["rewrites"][0]["id"] == 7
    assert result["rewrites"][0]["vi"] == "Không chiến lược nào bất bại."


def test_web_job_streams_pipeline_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import vi_dubber.web as web

    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")
    seen_provider: list[str] = []

    def fake_pipeline(*, input_path, output_path, translation_provider, progress_callback, **kwargs):
        seen_provider.append(translation_provider)
        progress_callback(0.34, "Đang dịch bằng Codex WebGPT instance 2")
        progress_callback(0.84, "Đang mix âm thanh và ghép video cuối")
        return {
            "output": str(output_path),
            "subtitle": str(output_path.with_suffix(".vi.srt")),
            "work_dir": str(tmp_path / "job"),
            "duration_seconds": 10.0,
            "elapsed_seconds": 5.0,
            "real_time_factor": 0.5,
            "segments": 2,
            "speaker_count": 1,
            "overflow_segments": 0,
            "rewritten_segments": 0,
            "cloned_segments": 0,
            "average_tempo": 1.0,
            "max_tempo": 1.0,
            "translation": {
                "requested": "webgpt",
                "used": "webgpt",
                "model": "chatgpt-web/gpt-5.6-sol",
                "translation_batches": 1,
                "rewrite_calls": 0,
                "fallback_used": False,
            },
            "qa": None,
        }

    monkeypatch.setattr(web, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(web, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)

    progress_events: list[tuple[float, str]] = []
    updates = list(
        web.run_web_job(
            "Tệp trên máy",
            str(source),
            "",
            "Codex WebGPT",
            "Tắt",
            None,
            "",
            False,
            translation_model="chatgpt-web/gpt-5.6-sol",
            translation_catalog={
                "status": "ready",
                "models": [{"id": "chatgpt-web/gpt-5.6-sol", "display_name": "GPT-5.6 Sol", "efforts": []}],
                "default_model": "chatgpt-web/gpt-5.6-sol",
                "revision": "fixture-r1",
            },
            progress=lambda value, desc: progress_events.append((value, desc)),
        )
    )

    assert seen_provider == ["webgpt"]
    assert any("Đang dịch" in desc for _, desc in progress_events)
    assert any("Đang mix" in desc for _, desc in progress_events)
    assert any("Tiến trình xử lý" in update[-1] for update in updates)
    assert updates[-1][0].endswith("sample_vi.mp4")
    assert "100%" in updates[-1][-1]
