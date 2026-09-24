from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

import vi_dubber.semantic_qa as semantic_qa
from vi_dubber.pronunciation import (
    integer_to_vietnamese,
    normalize_pronunciation,
)
from vi_dubber.translate import (
    LocalTranslator,
    WebGptTranslator,
    build_translation_payload,
    duration_fit_hint,
    estimate_spoken_duration,
)
from vi_dubber.types import Segment


def _context_segments() -> list[Segment]:
    return [
        Segment(id=10, start=0.0, end=1.0, text="First line", vi="Câu đầu"),
        Segment(id=11, start=1.0, end=2.4, text="Second line", speaker="SPEAKER_01"),
        Segment(id=12, start=2.4, end=4.0, text="Third line"),
    ]


def test_translation_payload_carries_bounded_context_and_duration() -> None:
    payload = build_translation_payload(
        _context_segments(),
        offset=1,
        count=1,
        context_window=1,
    )

    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == 11
    assert item["speaker"] == "SPEAKER_01"
    assert item["target_duration_sec"] == 1.4
    assert [entry["id"] for entry in item["context_before"]] == [10]
    assert item["context_before"][0]["translation_vi"] == "Câu đầu"
    assert [entry["id"] for entry in item["context_after"]] == [12]


def test_duration_proxy_is_monotonic_and_exposes_uncertainty() -> None:
    short = estimate_spoken_duration("Xin chào")
    long = estimate_spoken_duration("Xin chào, đây là một câu dài hơn đáng kể.")
    fit = duration_fit_hint("Một câu khá dài để đọc", 0.3)

    assert long["expected_seconds"] > short["expected_seconds"] > 0
    assert long["uncertainty_seconds"] > 0
    assert fit["fit"] == "likely_overflow"
    assert fit["expected_to_target_ratio"] > 1.0
    assert fit["upper_to_target_ratio"] > fit["expected_to_target_ratio"]


def test_duration_proxy_estimates_tts_normalized_numbers_and_uses_uncertainty() -> None:
    plain = estimate_spoken_duration("Giá là 33")
    spelled = estimate_spoken_duration("Giá là ba mươi ba")
    tight = duration_fit_hint("Xin chào bạn", 0.80, overflow_ratio=1.10)

    assert plain["expected_seconds"] == spelled["expected_seconds"]
    assert tight["expected_to_target_ratio"] <= 1.10
    assert tight["upper_to_target_ratio"] > 1.10
    assert tight["fit"] == "likely_overflow"


def test_webgpt_translation_prompt_uses_context_without_domain_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = WebGptTranslator({"codex_segments_per_batch": 3, "context_window": 1}, tmp_path)
    seen_prompt = ""

    def fake_run_json(prompt: str, schema: dict, label: str):
        nonlocal seen_prompt
        del schema, label
        seen_prompt = prompt
        return {
            "translations": [
                {"id": 10, "vi": "Một"},
                {"id": 11, "vi": "Hai"},
                {"id": 12, "vi": "Ba"},
            ]
        }

    monkeypatch.setattr(translator, "_run_json", fake_run_json)
    translator.translate_segments(_context_segments(), {})

    assert '"context_before"' in seen_prompt
    assert '"context_after"' in seen_prompt
    assert "meaning > critical facts > natural spoken Vietnamese" in seen_prompt
    assert "trading" not in seen_prompt.lower()


def test_local_rewrite_prompt_is_domain_neutral(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translator = LocalTranslator({}, tmp_path)
    seen_prompt = ""

    def fake_chat(prompt: str, max_tokens: int = 4096) -> str:
        nonlocal seen_prompt
        del max_tokens
        seen_prompt = prompt
        return '[{"id": 1, "vi": "Bản ngắn"}]'

    monkeypatch.setattr(translator, "_chat", fake_chat)
    segment = Segment(id=1, start=0.0, end=1.0, text="A generic source", vi="Bản hiện tại")
    result = translator.rewrite_batch([(segment, 1.0, 1.8, 1.8, 12)], {})

    assert result == {1: "Bản ngắn"}
    assert "trading" not in seen_prompt.lower()
    assert "negation" in seen_prompt.lower()


def test_semantic_questions_include_atomic_issue_heads() -> None:
    segment = Segment(id=3, start=0.0, end=1.0, text="Do not sell", vi="Đừng bán")
    questions = semantic_qa._questions_for([segment], stage="translated")

    for head in semantic_qa.SEMANTIC_ISSUE_HEADS:
        question = questions[f"{head}_3"]
        assert question["type"] == "noul"
        assert "issue" in question["criteria"]["true"].lower()


def test_semantic_rewrite_stage_adds_material_change_head() -> None:
    segment = Segment(id=3, start=0.0, end=1.0, text="Do not sell", vi="Đừng bán")

    translated = semantic_qa._questions_for([segment], stage="translated")
    rewritten = semantic_qa._questions_for([segment], stage="rewrite_candidate_00003")

    rewrite_key = f"{semantic_qa.REWRITE_SEMANTIC_ISSUE_HEAD}_3"
    assert rewrite_key not in translated
    assert rewrite_key in rewritten
    assert rewritten[rewrite_key]["type"] == "noul"


def test_semantic_evaluation_retains_raw_heads_and_derived_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload: dict = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            answers = {
                "faithfulness_4": {
                    "type": "choice",
                    "choice": "faithful",
                    "probabilities": {"faithful": 0.94, "partial": 0.05, "wrong": 0.01},
                    "confidence": 0.91,
                },
                "critical_facts_4": {"type": "noul", "noul": 0.96},
            }
            answers.update(
                {
                    f"{head}_4": {"type": "noul", "noul": 0.08}
                    for head in semantic_qa.SEMANTIC_ISSUE_HEADS
                }
            )
            return {
                "model": "jev-test",
                "answers": answers,
                "usage": {"input_tokens": 80, "output_tokens": 20},
            }

    def fake_post(*args, **kwargs):
        del args
        captured_payload.update(kwargs["json"])
        return FakeResponse()

    monkeypatch.setattr(semantic_qa.requests, "post", fake_post)
    segments = [
        Segment(id=3, start=0.0, end=1.0, text="Context", vi="Ngữ cảnh"),
        Segment(id=4, start=1.0, end=2.0, text="Do not sell", vi="Đừng bán"),
    ]
    result = semantic_qa.evaluate_semantic_qa(
        [segments[1]],
        {"review_issue_above": 0.8},
        stage="translated",
        api_key="offline-test-key",
    )

    assert result["question_policy_version"] == semantic_qa.SEMANTIC_QA_QUESTION_VERSION
    assert result["items"][0]["issue_probabilities"]["material_meaning_lost"] == 0.08
    assert result["items"][0]["decision"] == "pass"
    assert result["decision_counts"] == {"pass": 1, "review": 0, "retry": 0}
    assert result["latency_seconds"] >= 0
    assert result["request_receipts"][0]["input_tokens"] == 80
    assert "context_before" in captured_payload["state"]["segments"][0]

    raw = semantic_qa._raw_result(result)
    assert "decision_counts" not in raw
    assert "thresholds" not in raw
    assert "decision" not in raw["items"][0]
    assert raw["items"][0]["issue_probabilities"]["spoken_vi_awkward"] == 0.08


def test_semantic_atomic_issue_head_routes_review_without_extra_config() -> None:
    raw = {
        "status": "ok",
        "items": [
            {
                "choice": "faithful",
                "probabilities": {"faithful": 0.96},
                "confidence": 0.93,
                "critical_facts_probability": 0.95,
                "issue_probabilities": {"negation_or_modality_changed": 0.94},
            }
        ],
    }

    result = semantic_qa._apply_review_policy(raw, {})

    assert result["items"][0]["decision"] == "review"
    assert result["items"][0]["decision_reasons"] == ["issue:negation_or_modality_changed"]
    assert result["thresholds"]["review_issue_above"] == semantic_qa.DEFAULT_REVIEW_ISSUE_ABOVE


def test_semantic_calibration_golden_covers_plan_classes_and_costed_threshold() -> None:
    fixture_dir = Path(__file__).parent / "fixtures"
    labels = json.loads(
        (fixture_dir / "semantic_calibration_labels.json").read_text(encoding="utf-8")
    )
    probabilities = json.loads(
        (fixture_dir / "semantic_calibration_probabilities.json").read_text(encoding="utf-8")
    )
    raw = probabilities["raw_result"]
    by_id = {item["id"]: item for item in raw["items"]}
    samples = [{**case, **by_id[case["id"]]} for case in labels["cases"]]

    assert {case["class"] for case in labels["cases"]} == set(labels["required_plan_classes"])
    assert {case["should_pass"] for case in labels["cases"]} == {True, False}
    assert {case["should_retry"] for case in labels["cases"]} == {True, False}
    assert any(case["severe_semantic_error"] for case in labels["cases"])
    assert any(case["awkward_but_semantically_correct"] for case in labels["cases"])

    result = semantic_qa.select_semantic_retry_threshold(samples)

    assert result["cost_policy"] == {
        "false_pass_cost": 5.0,
        "false_retry_cost": 1.0,
        "meaning": (
            "false_pass = should_retry labeled sample allowed through; "
            "false_retry = non-retry sample sent to retry"
        ),
    }
    assert result["selected"]["threshold"] == pytest.approx(0.72)
    assert result["selected"]["confusion"] == {
        "true_positive": 4,
        "false_positive": 0,
        "true_negative": 3,
        "false_negative": 0,
    }
    assert result["selected"]["precision"] == 1.0
    assert result["selected"]["recall"] == 1.0
    awkward = next(sample for sample in samples if sample["class"] == "telegraphic_vietnamese")
    assert awkward["issue_probabilities"]["spoken_vi_awkward"] == 0.96
    assert semantic_qa.semantic_retry_risk_score(awkward) < result["selected"]["threshold"]


def test_semantic_retry_retries_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class ServerError:
        status_code = 503
        text = "temporary"

    class Success:
        status_code = 200
        text = ""

        def json(self):
            answers = {
                "faithfulness_1": {
                    "type": "choice",
                    "choice": "faithful",
                    "probabilities": {"faithful": 0.9, "partial": 0.09, "wrong": 0.01},
                    "confidence": 0.8,
                },
                "critical_facts_1": {"type": "noul", "noul": 0.95},
            }
            answers.update(
                {f"{head}_1": {"type": "noul", "noul": 0.1} for head in semantic_qa.SEMANTIC_ISSUE_HEADS}
            )
            return {"model": "jev-test", "answers": answers, "usage": {}}

    def fake_post(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return ServerError() if calls == 1 else Success()

    monkeypatch.setattr(semantic_qa.requests, "post", fake_post)
    monkeypatch.setattr(semantic_qa.time, "sleep", lambda *_: None)
    result = semantic_qa.evaluate_semantic_qa(
        [Segment(id=1, start=0.0, end=1.0, text="Hello", vi="Xin chào")],
        {"max_attempts": 2},
        stage="translated",
        api_key="offline-test-key",
    )

    assert result["status"] == "ok"
    assert calls == 2


def test_typesafe_transport_retries_real_local_429_and_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = [429, 503, 200]
    receipts: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            index = len(receipts)
            status = statuses[index]
            body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            receipts.append(
                {
                    "status": status,
                    "authorization": self.headers.get("Authorization"),
                    "payload": json.loads(body.decode("utf-8")),
                }
            )
            response = {"ok": True, "attempt": index + 1} if status == 200 else {"temporary": True}
            encoded = json.dumps(response).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(semantic_qa.time, "sleep", lambda *_: None)
    try:
        result = semantic_qa._post_with_retry(
            f"http://127.0.0.1:{server.server_port}/typesafe-fixture",
            {"fixture": "retry"},
            "local-test-key",
            timeout=2.0,
            max_attempts=3,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result == {"ok": True, "attempt": 3}
    assert [item["status"] for item in receipts] == [429, 503, 200]
    assert all(item["authorization"] == "Bearer local-test-key" for item in receipts)
    assert all(item["payload"] == {"fixture": "retry"} for item in receipts)


def test_pronunciation_normalization_preserves_display_and_protected_tokens() -> None:
    source = "FVG tăng 12.5% trong 5 km, xem https://example.com/a/123 và a1@test.com"
    result = normalize_pronunciation(source, {"FVG": "ép vi gi"})

    assert result.display_text == source
    assert "ép vi gi" in result.tts_text
    assert "mười hai phẩy năm phần trăm" in result.tts_text
    assert "năm ki lô mét" in result.tts_text
    assert "https://example.com/a/123" in result.tts_text
    assert "a1@test.com" in result.tts_text
    assert {item.category for item in result.replacements} >= {"mapping", "percent", "unit"}


def test_pronunciation_prefers_longest_explicit_mapping_and_reads_integers() -> None:
    result = normalize_pronunciation(
        "order block 1005",
        {"order": "o đờ", "order block": "ô đờ bờ lóc"},
    )

    assert result.tts_text == "ô đờ bờ lóc một nghìn không trăm lẻ năm"
    assert integer_to_vietnamese(25) == "hai mươi lăm"


def test_pronunciation_reads_signed_critical_numbers() -> None:
    result = normalize_pronunciation("Giảm -12.5%, tăng +3 km.")

    assert "âm mười hai phẩy năm phần trăm" in result.tts_text
    assert "dương ba ki lô mét" in result.tts_text
