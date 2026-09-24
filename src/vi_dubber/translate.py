from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import requests
from huggingface_hub import hf_hub_download

from .pronunciation import normalize_pronunciation
from .runtime import MODELS_DIR, PROJECT_ROOT, find_codex_exe, llama_server_exe
from .terminology import glossary_prompt_json, load_terminology_glossary
from .types import Segment


SYSTEM_PROMPT = """You are a senior English-to-Vietnamese dubbing adapter.
Translate for spoken Vietnamese, not subtitles or literal prose.

Rules:
- Preserve the exact meaning, intent, numbers, negation, named entities, and technical relationships.
- Never add explanations, examples, opinions, or facts that the source did not say.
- Follow the supplied glossary exactly when a term is present.
- Follow terminology policies exactly. KEEP_EN/PREFER_EN keep the approved English display form; VI uses the approved Vietnamese display form; CONTEXTUAL must stay consistent and avoid rejected forms.
- Use Vietnamese grammar around retained English technical terms; do not translate a technical term merely because a literal Vietnamese equivalent exists.
- Keep common technical abbreviations in English when that is natural for Vietnamese viewers.
- Use natural Vietnamese word order and conversational rhythm.
- The Vietnamese line must fit the supplied speaking duration. Prefer concise natural wording over fast speech.
- Return only the requested JSON. Do not use Markdown.
- Do not expose analysis or reasoning.
/no_think
"""

ProgressCallback = Callable[[float, str], None]
TRANSLATION_POLICY_VERSION = 4
WEBGPT_TRANSLATION_RECEIPT_NAMESPACE = "vi-dubber.webgpt.translation.v1"


def load_glossary(path: Path | None) -> dict[str, str]:
    return load_terminology_glossary(path)


def estimate_spoken_duration(
    text: str,
    *,
    chars_per_second: float = 13.0,
    uncertainty_ratio: float = 0.20,
) -> dict[str, float]:
    """Return a lightweight, calibration-friendly speech-duration proxy.

    This is deliberately deterministic: callers may replace the rate with a
    measured speaker/backend-specific value without changing translation flow.
    """
    rate = max(1.0, float(chars_per_second))
    uncertainty_ratio = max(0.0, float(uncertainty_ratio))
    # Estimate what the TTS actually receives. Deterministic number/unit
    # expansion can be much longer than the display token (for example "33").
    tts_safe = normalize_pronunciation(str(text or ""), normalize_numbers=True).tts_text
    normalized = re.sub(r"\s+", " ", tts_safe).strip()
    spoken_chars = sum(1 for char in normalized if not char.isspace())
    punctuation_pause = (
        sum(normalized.count(mark) for mark in ",;") * 0.10
        + sum(normalized.count(mark) for mark in ".!?") * 0.16
        + normalized.count(":") * 0.08
    )
    expected = (spoken_chars / rate) + punctuation_pause if spoken_chars else 0.0
    uncertainty = max(0.12, expected * uncertainty_ratio) if expected else 0.0
    return {
        "expected_seconds": round(expected, 4),
        "uncertainty_seconds": round(uncertainty, 4),
        "chars_per_second": rate,
    }


def duration_fit_hint(
    text: str,
    target_seconds: float,
    *,
    chars_per_second: float = 13.0,
    uncertainty_ratio: float = 0.20,
    overflow_ratio: float = 1.10,
) -> dict[str, Any]:
    """Describe likely duration fit without making the estimate a hard truth."""
    estimate = estimate_spoken_duration(
        text,
        chars_per_second=chars_per_second,
        uncertainty_ratio=uncertainty_ratio,
    )
    target = max(0.0, float(target_seconds))
    expected = float(estimate["expected_seconds"])
    uncertainty = float(estimate["uncertainty_seconds"])
    upper_bound = expected + uncertainty
    if target <= 0.0:
        fit = "unknown"
        ratio = None
        upper_ratio = None
    else:
        ratio = expected / target
        upper_ratio = upper_bound / target
        if upper_bound <= target:
            fit = "comfortable"
        elif upper_ratio <= max(1.0, float(overflow_ratio)):
            fit = "tight"
        else:
            fit = "likely_overflow"
    return {
        **estimate,
        "upper_bound_seconds": round(upper_bound, 4),
        "target_seconds": target,
        "expected_to_target_ratio": round(ratio, 4) if ratio is not None else None,
        "upper_to_target_ratio": round(upper_ratio, 4) if upper_ratio is not None else None,
        "fit": fit,
    }


def _translation_context_item(segment: Segment) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": segment.id,
        "speaker": segment.speaker,
        "source_en": segment.text,
    }
    if segment.vi.strip():
        item["translation_vi"] = segment.vi.strip()
    return item


def build_translation_payload(
    segments: list[Segment],
    *,
    offset: int,
    count: int,
    context_window: int = 2,
) -> list[dict[str, Any]]:
    """Build translation items with bounded nearby context and duration budget."""
    context_window = max(0, int(context_window))
    end = min(len(segments), offset + max(0, int(count)))
    payload: list[dict[str, Any]] = []
    for index in range(max(0, offset), end):
        segment = segments[index]
        before = segments[max(0, index - context_window) : index]
        after = segments[index + 1 : min(len(segments), index + 1 + context_window)]
        payload.append(
            {
                "id": segment.id,
                "source_ids": segment.source_segment_ids or [segment.id],
                "speaker": segment.speaker,
                "target_duration_sec": round(segment.duration, 3),
                "source_en": segment.text,
                "context_before": [_translation_context_item(item) for item in before],
                "context_after": [_translation_context_item(item) for item in after],
                "policy_version": TRANSLATION_POLICY_VERSION,
            }
        )
    return payload


def _extract_json(text: str, array: bool) -> Any:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I).strip()
    opener, closer = ("[", "]") if array else ("{", "}")
    start = cleaned.find(opener)
    end = cleaned.rfind(closer)
    if start < 0 or end < start:
        raise ValueError(f"Model did not return JSON: {cleaned[:500]}")
    payload = cleaned[start : end + 1]
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        pass

    # ChatGPT Web occasionally escapes structural brackets (for example ``\[``),
    # which is never a valid JSON escape. Repair only those impossible escapes.
    repaired = re.sub(r"\\([\[\]{}])", r"\1", payload)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # LLMs occasionally leave unescaped quotes inside string values (e.g. "vi": "... "term" ...")
    # Perform structured regex extraction for translations arrays: {"id": int, "vi": str}
    pattern_id_first = re.compile(
        r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"vi"\s*:\s*"(.*?)"\s*\}\s*(?=,\s*\{|\s*\])',
        re.DOTALL,
    )
    matches = pattern_id_first.findall(repaired)
    if not matches:
        pattern_vi_first = re.compile(
            r'\{\s*"vi"\s*:\s*"(.*?)"\s*,\s*"id"\s*:\s*(\d+)\s*\}\s*(?=,\s*\{|\s*\])',
            re.DOTALL,
        )
        matches = [(m[1], m[0]) for m in pattern_vi_first.findall(repaired)]

    if matches:
        items = []
        for id_str, vi_str in matches:
            cleaned_vi = vi_str.replace('\\"', '"').strip()
            items.append({"id": int(id_str), "vi": cleaned_vi})
        if array:
            return items
        if "rewrites" in repaired:
            return {"rewrites": items}
        return {"translations": items}

    # Handle rewrite line single object {"vi": "..."}
    vi_match = re.search(r'\{\s*"vi"\s*:\s*"(.*?)"\s*\}', repaired, re.DOTALL)
    if vi_match:
        return {"vi": vi_match.group(1).replace('\\"', '"').strip()}

    return json.loads(repaired)


class LocalTranslator:
    def __init__(self, config: dict[str, Any], work_dir: Path):
        self.config = config
        self.work_dir = work_dir
        self.host = str(config.get("host", "127.0.0.1"))
        self.port = int(config.get("port", 8091))
        self.base_url = f"http://{self.host}:{self.port}"
        self.process: subprocess.Popen[str] | None = None
        self._log_handle = None
        self.model_id = "local-model"
        self.translation_batches = 0
        self.rewrite_calls = 0

    def ensure_model(self) -> Path:
        repo = str(self.config["repo"])
        filename = str(self.config["filename"])
        local_dir = MODELS_DIR / "llm" / repo.replace("/", "--")
        local_dir.mkdir(parents=True, exist_ok=True)
        target = local_dir / filename
        if target.exists() and target.stat().st_size > 1_000_000:
            return target
        downloaded = hf_hub_download(
            repo_id=repo,
            filename=filename,
            local_dir=str(local_dir),
        )
        return Path(downloaded)

    def _healthy(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=2)
            return response.ok
        except requests.RequestException:
            return False

    def _discover_model_id(self) -> None:
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=5)
            response.raise_for_status()
            models = response.json().get("data", [])
            if models:
                self.model_id = str(models[0].get("id") or self.model_id)
        except Exception:
            pass

    @contextmanager
    def running(self) -> Iterator["LocalTranslator"]:
        if self._healthy():
            self._discover_model_id()
            yield self
            return

        model_path = self.ensure_model()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.work_dir / "llama-server.log"
        self._log_handle = log_path.open("a", encoding="utf-8")
        cmd = [
            str(llama_server_exe()),
            "-m",
            str(model_path),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "-c",
            str(int(self.config.get("context_size", 8192))),
            "-ngl",
            str(int(self.config.get("gpu_layers", 20))),
            "--jinja",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"llama.cpp exited while loading the model. See {log_path}"
                )
            if self._healthy():
                self._discover_model_id()
                break
            time.sleep(1.0)
        else:
            raise TimeoutError(f"llama.cpp did not become ready. See {log_path}")

        try:
            yield self
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self._log_handle is not None:
                self._log_handle.close()

    def _chat(self, user_prompt: str, max_tokens: int = 4096) -> str:
        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model_id,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": float(self.config.get("temperature", 0.15)),
                "max_tokens": max_tokens,
            },
            timeout=600,
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])

    def translate_segments(
        self,
        segments: list[Segment],
        glossary: dict[str, str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[Segment]:
        batch_size = max(1, int(self.config.get("max_segments_per_batch", 16)))
        context_window = max(0, int(self.config.get("context_window", 2)))
        glossary_text = glossary_prompt_json(glossary)

        cache_path = self.work_dir / "translations_cache.json"
        cached_by_id: dict[int, str] = {}
        if cache_path.exists():
            try:
                raw_cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_by_id.update({int(k): str(v) for k, v in raw_cache.items()})
            except Exception:
                pass

        for item in segments:
            if item.id in cached_by_id and not item.vi:
                item.vi = cached_by_id[item.id]

        for offset in range(0, len(segments), batch_size):
            batch = segments[offset : offset + batch_size]
            if all(bool(item.vi and item.vi.strip()) for item in batch):
                if progress_callback is not None:
                    done = min(offset + len(batch), len(segments))
                    progress_callback(done / max(1, len(segments)), f"Local translation {done}/{len(segments)} (cached)")
                continue

            payload = build_translation_payload(
                segments,
                offset=offset,
                count=len(batch),
                context_window=context_window,
            )
            prompt = (
                "Glossary:\n"
                f"{glossary_text}\n\n"
                "Translate each item below using only its nearby context to keep terminology/register consistent. "
                "Treat target_duration_sec as an approximate speaking budget, never as permission to drop meaning. "
                "Priority: meaning > critical facts > natural spoken Vietnamese > context/register > duration fit. "
                "Return a JSON array with exactly one object per input, "
                "in the same order, schema: {\"id\": integer, \"vi\": string}.\n"
                f"INPUT={json.dumps(payload, ensure_ascii=False)}"
            )
            result = _extract_json(self._chat(prompt), array=True)
            by_id = {int(item["id"]): str(item["vi"]).strip() for item in result}
            missing = [item.id for item in batch if item.id not in by_id]
            if missing:
                raise RuntimeError(f"Translation model omitted segment ids: {missing}")
            for item in batch:
                item.vi = by_id[item.id]
                cached_by_id[item.id] = item.vi
            try:
                cache_path.write_text(
                    json.dumps({str(k): v for k, v in cached_by_id.items()}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            self.translation_batches += 1
            if progress_callback is not None:
                done = min(offset + len(batch), len(segments))
                progress_callback(done / max(1, len(segments)), f"Local translation {done}/{len(segments)}")
        return segments

    def rewrite_shorter(
        self,
        segment: Segment,
        measured_duration: float,
        glossary: dict[str, str],
    ) -> str:
        ratio = max(0.35, min(0.95, segment.duration / max(measured_duration, 0.1)))
        target_chars = max(8, int(len(segment.vi) * ratio * 0.9))
        prompt = (
            "Rewrite ONE Vietnamese dubbing line so it says the same thing but fits a shorter slot. "
            "Do not delete numbers, negation, names, or essential technical meaning. "
            "Return only JSON object {\"vi\": string}.\n"
            f"Glossary={glossary_prompt_json(glossary)}\n"
            f"Source English={json.dumps(segment.text, ensure_ascii=False)}\n"
            f"Current Vietnamese={json.dumps(segment.vi, ensure_ascii=False)}\n"
            f"Target duration={segment.duration:.2f}s; current audio={measured_duration:.2f}s; "
            f"aim for about {target_chars} Vietnamese characters."
        )
        result = _extract_json(self._chat(prompt, max_tokens=768), array=False)
        self.rewrite_calls += 1
        return str(result["vi"]).strip()

    def rewrite_batch(
        self,
        batch_items: list[tuple[Segment, float, float, float, int]],
        glossary: dict[str, str],
    ) -> dict[int, str]:
        if not batch_items:
            return {}
        glossary_text = glossary_prompt_json(glossary)
        payload = [
            {
                "id": seg.id,
                "source_en": seg.text,
                "current_vi": seg.vi,
                "target_sec": round(target_dur, 2),
                "measured_sec": round(meas_dur, 2),
                "target_chars": target_chars,
                "mode": "aggressive" if ratio > 1.40 else "mild",
            }
            for seg, target_dur, meas_dur, ratio, target_chars in batch_items
        ]
        prompt = (
            "Rewrite each Vietnamese dubbing line below so it says the same thing but fits a shorter speaking duration.\n"
            "- Mode 'mild': trim filler words and slightly condense phrasing (aim for target_chars).\n"
            "- Mode 'aggressive': heavily condense into direct, natural phrasing while preserving essential meaning, facts, numbers, names, negation, and modality.\n"
            f"Glossary={glossary_text}\n"
            "Return a JSON array of objects: [{\"id\": integer, \"vi\": string}].\n"
            f"INPUT={json.dumps(payload, ensure_ascii=False)}"
        )
        result = _extract_json(self._chat(prompt, max_tokens=2048), array=True)
        by_id = {int(item["id"]): str(item["vi"]).strip() for item in result if isinstance(item, dict) and "id" in item and "vi" in item}
        self.rewrite_calls += 1
        return by_id

    def stats(self) -> dict[str, Any]:
        return {
            "requested": "local",
            "used": "local",
            "translation_batches": self.translation_batches,
            "rewrite_calls": self.rewrite_calls,
            "fallback_used": False,
        }


def _codex_config_path() -> Path:
    configured = os.getenv("CODEX_HOME", "").strip()
    home = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return home / "config.toml"


WEBGPT_INSTANCE_PORT = 17842
WEBGPT_INSTANCE_BASE_URL = f"http://127.0.0.1:{WEBGPT_INSTANCE_PORT}/v1"
DEFAULT_WEBGPT_MODEL = "chatgpt-web/gpt-5.6-sol"
# Backward-compatible import name used by older tests/artifacts.
PINNED_WEBGPT_MODEL = DEFAULT_WEBGPT_MODEL
AURORA_DEFAULT_BASE_URL = "http://127.0.0.1:18080"


def _webgpt_base_url(config: dict[str, Any] | None = None) -> str:
    configured = str((config or {}).get("webgpt_base_url") or WEBGPT_INSTANCE_BASE_URL).strip().rstrip("/")
    if configured != WEBGPT_INSTANCE_BASE_URL:
        raise ValueError(
            "VI Dubber hiện khóa Codex WebGPT vào instance 2 tại "
            f"{WEBGPT_INSTANCE_BASE_URL}; nhận được {configured or 'base_url trống'}."
        )
    return configured


def _webgpt_root_url(base_url: str) -> str:
    return base_url[:-3] if base_url.endswith("/v1") else base_url


def webgpt_model_catalog(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(config or {})
    base_url = _webgpt_base_url(config)
    timeout = float(config.get("webgpt_catalog_timeout_seconds", 5))
    try:
        response = requests.get(f"{base_url}/models", timeout=max(1.0, timeout))
    except requests.RequestException as exc:
        raise RuntimeError(f"Không thể lấy model catalog từ Codex WebGPT instance 2 tại {base_url}: {exc}") from exc
    if not response.ok:
        detail = response.text.strip()[:600]
        raise RuntimeError(
            f"Codex WebGPT instance 2 model catalog trả HTTP {response.status_code}"
            + (f": {detail}" if detail else "")
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Codex WebGPT instance 2 model catalog trả JSON không hợp lệ.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Codex WebGPT instance 2 model catalog phải là JSON object.")
    raw_models = payload.get("data", payload.get("models", []))
    if not isinstance(raw_models, list):
        raise RuntimeError("Codex WebGPT instance 2 model catalog không có danh sách model hợp lệ.")

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_models:
        item = raw if isinstance(raw, dict) else {"id": raw}
        model_id = str(item.get("id") or item.get("slug") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        raw_efforts = item.get("supported_efforts")
        if not isinstance(raw_efforts, list):
            raw_efforts = item.get("reasoning_efforts")
        if not isinstance(raw_efforts, list):
            capabilities = item.get("capabilities")
            raw_efforts = capabilities.get("reasoning_effort", []) if isinstance(capabilities, dict) else []
        models.append(
            {
                "id": model_id,
                "display_name": str(item.get("display_name") or item.get("name") or model_id).strip() or model_id,
                "supported_efforts": [
                    str(value).strip()
                    for value in raw_efforts
                    if str(value).strip()
                ],
                "default_effort": str(
                    item.get("default_reasoning_effort")
                    or item.get("default_effort")
                    or ""
                ).strip(),
            }
        )
    if not models:
        raise RuntimeError("Codex WebGPT instance 2 model catalog đang trống.")

    configured_model = str(config.get("webgpt_model") or DEFAULT_WEBGPT_MODEL).strip()
    ids = {item["id"] for item in models}
    default_model = configured_model if configured_model in ids else models[0]["id"]
    encoded = json.dumps(models, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "provider": "webgpt",
        "base_url": base_url,
        "models": models,
        "default_model": default_model,
        "default_effort": next(
            (
                str(item.get("default_effort") or "").strip()
                for item in models
                if item.get("id") == default_model and item.get("default_effort")
            ),
            "auto",
        ),
        "revision": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "updated_at": "",
    }


def _aurora_base_url(config: dict[str, Any]) -> str:
    return str(
        config.get("aurora_base_url")
        or config.get("base_url")
        or AURORA_DEFAULT_BASE_URL
    ).strip().rstrip("/")


def _aurora_headers(config: dict[str, Any]) -> dict[str, str]:
    token = str(
        config.get("aurora_api_key")
        or config.get("api_key")
        or os.getenv("VI_DUBBER_AURORA_API_KEY", "")
    ).strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _aurora_catalog_revision(payload: dict[str, Any], models: list[dict[str, Any]]) -> str:
    explicit = str(
        payload.get("revision")
        or payload.get("catalog_revision")
        or payload.get("updated_at")
        or ""
    ).strip()
    if explicit:
        return explicit
    encoded = json.dumps(models, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def aurora_model_catalog(config: dict[str, Any]) -> dict[str, Any]:
    """Fetch Aurora's live model catalog without inventing unavailable models."""
    base_url = _aurora_base_url(config)
    timeout = float(config.get("aurora_catalog_timeout_seconds", config.get("timeout_seconds", 15)))
    try:
        response = requests.get(
            f"{base_url}/v1/models",
            headers=_aurora_headers(config),
            timeout=max(1.0, timeout),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Không thể lấy model catalog từ Aurora tại {base_url}: {exc}") from exc
    if not response.ok:
        detail = response.text.strip()[:600]
        raise RuntimeError(
            f"Aurora model catalog trả HTTP {response.status_code}"
            + (f": {detail}" if detail else "")
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Aurora model catalog trả JSON không hợp lệ.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Aurora model catalog phải là JSON object.")

    raw_models = payload.get("data", payload.get("models", []))
    if not isinstance(raw_models, list):
        raise RuntimeError("Aurora model catalog không có danh sách model hợp lệ.")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_models:
        item = raw if isinstance(raw, dict) else {"id": raw}
        model_id = str(item.get("id") or item.get("slug") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        display_name = str(item.get("display_name") or item.get("name") or model_id).strip()
        efforts = item.get("supported_efforts")
        if not isinstance(efforts, list):
            capabilities = item.get("capabilities")
            efforts = capabilities.get("reasoning_effort", []) if isinstance(capabilities, dict) else []
        models.append(
            {
                "id": model_id,
                "display_name": display_name or model_id,
                "supported_efforts": [str(value).strip() for value in efforts if str(value).strip()],
            }
        )
    if not models:
        raise RuntimeError("Aurora model catalog đang trống; không thể chọn model dịch an toàn.")
    return {
        "provider": "aurora",
        "base_url": base_url,
        "models": models,
        "revision": _aurora_catalog_revision(payload, models),
        "updated_at": str(payload.get("updated_at") or "").strip(),
    }


def translation_model_catalog(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public live catalog used by the product UI from WebGPT instance 2."""
    return webgpt_model_catalog(dict(config or {}))


def webgpt_route_info(config: dict[str, Any] | None = None) -> dict[str, str | bool | int]:
    config = dict(config or {})
    try:
        base_url = _webgpt_base_url(config)
    except Exception as exc:
        return {
            "ready": False,
            "provider": "webgpt",
            "model": str(config.get("webgpt_model") or DEFAULT_WEBGPT_MODEL),
            "base_url": "",
            "port": WEBGPT_INSTANCE_PORT,
            "reason": str(exc),
        }
    model = str(config.get("webgpt_model") or DEFAULT_WEBGPT_MODEL).strip()
    try:
        health = requests.get(f"{_webgpt_root_url(base_url)}/healthz", timeout=3)
        if not health.ok:
            raise RuntimeError(f"healthz HTTP {health.status_code}")
        health_payload = health.json()
        if not isinstance(health_payload, dict):
            raise RuntimeError("healthz không trả JSON object")
        if int(health_payload.get("port") or 0) != WEBGPT_INSTANCE_PORT:
            raise RuntimeError(f"healthz không phải instance 2 port {WEBGPT_INSTANCE_PORT}")
        if not bool(health_payload.get("accepting_turns", False)):
            raise RuntimeError("instance 2 hiện không nhận turn mới")
        catalog = webgpt_model_catalog(config)
        model_ids = {str(item.get("id") or "") for item in catalog.get("models", [])}
        if model not in model_ids:
            raise RuntimeError(f"model {model!r} không có trong live catalog instance 2")
    except Exception as exc:
        return {
            "ready": False,
            "provider": "webgpt",
            "model": model,
            "base_url": base_url,
            "port": WEBGPT_INSTANCE_PORT,
            "reason": f"Codex WebGPT instance 2 chưa sẵn sàng: {exc}",
        }
    return {
        "ready": True,
        "provider": "webgpt",
        "model": model,
        "base_url": base_url,
        "port": WEBGPT_INSTANCE_PORT,
        "reason": "",
    }


class WebGptTranslator:
    def __init__(
        self,
        config: dict[str, Any],
        work_dir: Path,
        retry_budget: int | None = None,
        *,
        model_override: str | None = None,
        effort_override: str | None = None,
    ):
        self.config = config
        self.work_dir = work_dir
        self.base_url = _webgpt_base_url(config)
        self.model = str(
            model_override
            or config.get("webgpt_model")
            or config.get("codex_model")
            or DEFAULT_WEBGPT_MODEL
        ).strip()
        if not self.model:
            raise ValueError("WebGPT model không được để trống.")
        self.effort = str(
            effort_override
            or config.get("webgpt_effort")
            or config.get("reasoning_effort")
            or config.get("effort")
            or ""
        ).strip()
        self.supported_efforts: list[str] = []
        self.timeout = int(config.get("webgpt_timeout_seconds", config.get("codex_timeout_seconds", 900)))
        configured_retry_budget = config.get("retry_budget", 0) if retry_budget is None else retry_budget
        self.retry_budget = int(configured_retry_budget)
        if not 0 <= self.retry_budget <= 10:
            raise ValueError("WebGPT retry_budget phải nằm trong [0, 10]")
        self.retry_backoff_seconds = max(
            0.0,
            float(config.get("webgpt_retry_backoff_seconds", 0.25)),
        )
        self.retry_backoff_max_seconds = max(
            self.retry_backoff_seconds,
            float(config.get("webgpt_retry_backoff_max_seconds", 2.0)),
        )
        self.translation_batches = 0
        self.rewrite_calls = 0
        self.webgpt_attempts = 0
        self.webgpt_retry_attempts = 0
        self.webgpt_failures = 0
        self.webgpt_retries_exhausted = 0
        self._last_output_path: Path | None = None

    @contextmanager
    def running(self) -> Iterator["WebGptTranslator"]:
        route_config = dict(self.config)
        route_config["webgpt_model"] = self.model
        route = webgpt_route_info(route_config)
        if not route["ready"]:
            raise RuntimeError(str(route["reason"]))
        catalog = webgpt_model_catalog(route_config)
        selected = next(
            (item for item in catalog["models"] if item.get("id") == self.model),
            None,
        )
        if selected is None:
            raise RuntimeError(
                f"Model WebGPT đã chọn {self.model!r} không còn trong live catalog; "
                "VI Dubber không tự đổi sang model khác."
            )
        self.supported_efforts = list(selected.get("supported_efforts") or [])
        if self.effort and self.supported_efforts and self.effort not in self.supported_efforts:
            raise RuntimeError(
                f"Model WebGPT {self.model!r} không advertise effort {self.effort!r}; "
                f"supported={self.supported_efforts}."
            )
        yield self

    def _run_json(self, prompt: str, schema: dict[str, Any], label: str) -> Any:
        executable = find_codex_exe() or shutil.which("codex")
        if executable is None:
            raise RuntimeError("Không tìm thấy lệnh Codex trên PATH. Hãy mở/cài Codex rồi chạy lại.")

        webgpt_dir = self.work_dir / "webgpt"
        webgpt_dir.mkdir(parents=True, exist_ok=True)
        prompt = (
            prompt
            + "\nReturn exactly one JSON object and no Markdown or commentary."
            + f"\nOutput JSON schema: {json.dumps(schema, ensure_ascii=False)}"
        )

        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        max_attempts = 1 + self.retry_budget
        for attempt_index in range(max_attempts):
            self.webgpt_attempts += 1
            stamp = f"{int(time.time() * 1000)}-{os.getpid()}-{self.webgpt_attempts:04d}"
            output_path = webgpt_dir / f"{label}-{stamp}.json"
            self._last_output_path = output_path
            cmd = [
                executable,
                "exec",
                "--ephemeral",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "-o",
                str(output_path),
            ]
            cmd.extend(
                [
                    "-c",
                    'model_provider="codex_local_access"',
                    "-c",
                    f'model_providers.codex_local_access.base_url="{self.base_url}"',
                    "-c",
                    'model_providers.codex_local_access.wire_api="responses"',
                    "-c",
                    "model_providers.codex_local_access.requires_openai_auth=true",
                ]
            )
            cmd.extend(["--model", self.model])
            if self.effort:
                cmd.extend(["-c", f'model_reasoning_effort="{self.effort}"'])
            cmd.append("-")

            retryable_error: Exception | None = None
            try:
                result = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    check=False,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                retryable_error = RuntimeError(
                    f"Dịch bằng ChatGPT Web GPT quá thời gian chờ ({self.timeout}s)."
                )
                retryable_error.__cause__ = exc
            except OSError as exc:
                retryable_error = RuntimeError(
                    f"Không thể chạy Codex WebGPT: {exc}"
                )
                retryable_error.__cause__ = exc
            else:
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout).strip()
                    retryable_error = RuntimeError(
                        f"Dịch bằng ChatGPT Web GPT thất bại: {detail[-1200:]}"
                    )
                elif not output_path.exists():
                    retryable_error = RuntimeError(
                        "ChatGPT Web GPT đã chạy xong nhưng không tạo file kết quả."
                    )
                else:
                    try:
                        return _extract_json(
                            output_path.read_text(encoding="utf-8"),
                            array=False,
                        )
                    except (OSError, UnicodeError, ValueError) as exc:
                        retryable_error = RuntimeError(
                            "ChatGPT Web GPT trả kết quả JSON không hợp lệ."
                        )
                        retryable_error.__cause__ = exc

            self.webgpt_failures += 1
            if attempt_index + 1 >= max_attempts:
                self.webgpt_retries_exhausted += 1
                assert retryable_error is not None
                raise retryable_error

            self.webgpt_retry_attempts += 1
            delay = min(
                self.retry_backoff_max_seconds,
                self.retry_backoff_seconds * (2 ** attempt_index),
            )
            if delay > 0:
                time.sleep(delay)

        raise AssertionError("unreachable WebGPT retry loop")

    def _translation_request_identity(self, prompt: str, schema: dict[str, Any]) -> str:
        request = {
            "namespace": WEBGPT_TRANSLATION_RECEIPT_NAMESPACE,
            "provider": "webgpt",
            "model": self.model,
            "config": self.config,
            "schema": schema,
            "prompt": prompt,
        }
        encoded = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _persist_translation_receipt(
        self,
        result: dict[str, Any],
        request_identity: str,
    ) -> None:
        output_path = self._last_output_path
        if output_path is None or not output_path.exists():
            return
        receipt = {
            "namespace": WEBGPT_TRANSLATION_RECEIPT_NAMESPACE,
            "request_identity": request_identity,
            "provider": "webgpt",
            "model": self.model,
            "translations": result.get("translations", []),
        }
        try:
            output_path.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def translate_segments(
        self,
        segments: list[Segment],
        glossary: dict[str, str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[Segment]:
        batch_size = max(1, int(self.config.get("codex_segments_per_batch", 32)))
        context_window = max(0, int(self.config.get("context_window", 2)))
        glossary_text = glossary_prompt_json(glossary)
        schema = {
            "type": "object",
            "properties": {
                "translations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "vi": {"type": "string"}},
                        "required": ["id", "vi"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["translations"],
            "additionalProperties": False,
        }

        cache_path = self.work_dir / "translations_cache.json"
        cached_by_id: dict[int, str] = {}
        if cache_path.exists():
            try:
                raw_cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_by_id.update({int(k): str(v) for k, v in raw_cache.items()})
            except Exception:
                pass

        receipt_cache: dict[str, dict[int, str]] = {}
        webgpt_dir = self.work_dir / "webgpt"
        if webgpt_dir.exists():
            for json_file in sorted(webgpt_dir.glob("translate-*.json")):
                try:
                    data = _extract_json(json_file.read_text(encoding="utf-8"), array=False)
                    if data.get("namespace") != WEBGPT_TRANSLATION_RECEIPT_NAMESPACE:
                        continue
                    request_identity = str(data.get("request_identity") or "").strip()
                    if not request_identity:
                        continue
                    receipt_cache[request_identity] = {
                        int(item["id"]): str(item["vi"]).strip()
                        for item in data.get("translations", [])
                    }
                except Exception:
                    pass

        for item in segments:
            if item.id in cached_by_id and not item.vi:
                item.vi = cached_by_id[item.id]

        for offset in range(0, len(segments), batch_size):
            batch = segments[offset : offset + batch_size]
            if all(bool(item.vi and item.vi.strip()) for item in batch):
                if progress_callback is not None:
                    done = min(offset + len(batch), len(segments))
                    progress_callback(done / max(1, len(segments)), f"Đang dịch bằng Codex WebGPT: {done}/{len(segments)} đoạn (sử dụng cache)")
                continue

            payload = build_translation_payload(
                segments,
                offset=offset,
                count=len(batch),
                context_window=context_window,
            )
            prompt = (
                SYSTEM_PROMPT.replace("/no_think\n", "")
                + "\nYou are running as a translation backend. Do not inspect files or call tools.\n"
                + f"Glossary={glossary_text}\n"
                + "Translate every input item using only its nearby context to keep terminology/register consistent. "
                + "Treat target_duration_sec as an approximate speaking budget, never as permission to drop meaning. "
                + "Priority: meaning > critical facts > natural spoken Vietnamese > context/register > duration fit. "
                + "Keep ids unchanged and return only the schema-conforming JSON.\n"
                + f"INPUT={json.dumps(payload, ensure_ascii=False)}"
            )
            request_identity = self._translation_request_identity(prompt, schema)
            receipt_by_id = receipt_cache.get(request_identity, {})
            for item in batch:
                if not item.vi and item.id in receipt_by_id:
                    item.vi = receipt_by_id[item.id]
                    cached_by_id[item.id] = item.vi
            if all(bool(item.vi and item.vi.strip()) for item in batch):
                if progress_callback is not None:
                    done = min(offset + len(batch), len(segments))
                    progress_callback(
                        done / max(1, len(segments)),
                        f"Đang dịch bằng Codex WebGPT: {done}/{len(segments)} đoạn (sử dụng cache)",
                    )
                continue

            self._last_output_path = None
            result = self._run_json(prompt, schema, "translate")
            translations = result.get("translations", [])
            by_id = {int(item["id"]): str(item["vi"]).strip() for item in translations}
            missing = [item.id for item in batch if item.id not in by_id]
            if missing:
                raise RuntimeError(f"Codex WebGPT thiếu các đoạn có id: {missing}")
            self._persist_translation_receipt(result, request_identity)
            for item in batch:
                item.vi = by_id[item.id]
                cached_by_id[item.id] = item.vi
            try:
                cache_path.write_text(
                    json.dumps({str(k): v for k, v in cached_by_id.items()}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            self.translation_batches += 1
            if progress_callback is not None:
                done = min(offset + len(batch), len(segments))
                progress_callback(done / max(1, len(segments)), f"Đang dịch bằng Codex WebGPT: {done}/{len(segments)} đoạn")
        return segments

    def rewrite_shorter(
        self,
        segment: Segment,
        measured_duration: float,
        glossary: dict[str, str],
    ) -> str:
        ratio = max(0.35, min(0.95, segment.duration / max(measured_duration, 0.1)))
        target_chars = max(8, int(len(segment.vi) * ratio * 0.9))
        schema = {
            "type": "object",
            "properties": {"vi": {"type": "string"}},
            "required": ["vi"],
            "additionalProperties": False,
        }
        prompt = (
            SYSTEM_PROMPT.replace("/no_think\n", "")
            + "\nYou are running as a translation backend. Do not inspect files or call tools.\n"
            + "Rewrite one Vietnamese dubbing line to fit a shorter slot without losing essential meaning.\n"
            + f"Glossary={glossary_prompt_json(glossary)}\n"
            + f"Source English={json.dumps(segment.text, ensure_ascii=False)}\n"
            + f"Current Vietnamese={json.dumps(segment.vi, ensure_ascii=False)}\n"
            + f"Target duration={segment.duration:.2f}s; current audio={measured_duration:.2f}s; "
            + f"aim for about {target_chars} Vietnamese characters."
        )
        result = self._run_json(prompt, schema, "rewrite")
        self.rewrite_calls += 1
        return str(result["vi"]).strip()

    def rewrite_batch(
        self,
        batch_items: list[tuple[Segment, float, float, float, int]],
        glossary: dict[str, str],
    ) -> dict[int, str]:
        if not batch_items:
            return {}
        glossary_text = glossary_prompt_json(glossary)
        schema = {
            "type": "object",
            "properties": {
                "rewrites": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "vi": {"type": "string"},
                        },
                        "required": ["id", "vi"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["rewrites"],
            "additionalProperties": False,
        }
        payload = [
            {
                "id": seg.id,
                "source_en": seg.text,
                "current_vi": seg.vi,
                "target_sec": round(target_dur, 2),
                "measured_sec": round(meas_dur, 2),
                "target_chars": target_chars,
                "mode": "aggressive" if ratio > 1.40 else "mild",
            }
            for seg, target_dur, meas_dur, ratio, target_chars in batch_items
        ]
        prompt = (
            SYSTEM_PROMPT.replace("/no_think\n", "")
            + "\nYou are running as a dubbing timing optimizer.\n"
            + "Rewrite each Vietnamese dubbing line below so it speaks shorter to fit within the target duration, "
            + "without losing essential meaning, facts, numbers, names, negation, modality, or technical terms.\n"
            + "- Mode 'mild' (ratio 1.25x-1.40x): trim filler words and slightly condense phrasing (aim for target_chars).\n"
            + "- Mode 'aggressive' (ratio > 1.40x): heavily condense into direct, natural spoken phrasing while preserving material meaning.\n"
            + f"Glossary={glossary_text}\n"
            + "Return exactly one JSON object conforming to schema: {\"rewrites\": [{\"id\": integer, \"vi\": string}]}.\n"
            + f"INPUT={json.dumps(payload, ensure_ascii=False)}"
        )
        result = self._run_json(prompt, schema, "rewrite_batch")
        rewrites = result.get("rewrites", result.get("translations", []))
        by_id = {int(item["id"]): str(item["vi"]).strip() for item in rewrites if isinstance(item, dict) and "id" in item and "vi" in item}
        self.rewrite_calls += 1
        return by_id

    def stats(self) -> dict[str, Any]:
        return {
            "requested": "webgpt",
            "used": "webgpt",
            "provider": "webgpt",
            "model": self.model,
            "base_url": self.base_url,
            "instance_port": WEBGPT_INSTANCE_PORT,
            "translation_batches": self.translation_batches,
            "rewrite_calls": self.rewrite_calls,
            "retry_budget": self.retry_budget,
            "webgpt_attempts": self.webgpt_attempts,
            "webgpt_retry_attempts": self.webgpt_retry_attempts,
            "webgpt_failures": self.webgpt_failures,
            "webgpt_retries_exhausted": self.webgpt_retries_exhausted,
            "fallback_used": False,
        }


class AuroraTranslator:
    """Stateless Aurora/ChatGPT Web translation adapter for P21."""

    def __init__(
        self,
        config: dict[str, Any],
        work_dir: Path,
        retry_budget: int | None = None,
        *,
        model_override: str | None = None,
        effort_override: str | None = None,
    ):
        self.config = dict(config)
        self.work_dir = work_dir
        self.base_url = _aurora_base_url(config)
        self.model = str(
            model_override
            or config.get("aurora_model")
            or config.get("model_id")
            or config.get("model")
            or ""
        ).strip()
        if not self.model:
            raise ValueError("Aurora cần model_id cụ thể; không dùng model auto cho translation job.")
        self.effort = str(
            effort_override
            or config.get("aurora_effort")
            or config.get("reasoning_effort")
            or config.get("effort")
            or ""
        ).strip()
        self.timeout = float(
            config.get("aurora_timeout_seconds", config.get("timeout_seconds", 900))
        )
        configured_retry_budget = config.get("retry_budget", 0) if retry_budget is None else retry_budget
        self.retry_budget = int(configured_retry_budget)
        if not 0 <= self.retry_budget <= 10:
            raise ValueError("Aurora retry_budget phải nằm trong [0, 10]")
        self.retry_backoff_seconds = max(
            0.0,
            float(
                config.get(
                    "aurora_retry_backoff_seconds",
                    config.get("retry_backoff_seconds", 0.5),
                )
            ),
        )
        self.retry_backoff_max_seconds = max(
            self.retry_backoff_seconds,
            float(
                config.get(
                    "aurora_retry_backoff_max_seconds",
                    config.get("retry_backoff_max_seconds", 8.0),
                )
            ),
        )
        self.translation_batches = 0
        self.rewrite_calls = 0
        self.aurora_attempts = 0
        self.aurora_retry_attempts = 0
        self.aurora_failures = 0
        self.aurora_retries_exhausted = 0
        self.catalog_requests = 0
        self.catalog_revision = ""
        self.catalog_updated_at = ""
        self.model_display_name = str(config.get("model_display_name") or self.model).strip()
        self.supported_efforts: list[str] = []

    @staticmethod
    def _validate_schema(value: Any, schema: dict[str, Any], path: str = "$" ) -> None:
        expected = schema.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                raise ValueError(f"{path} phải là object")
            required = schema.get("required", [])
            for key in required if isinstance(required, list) else []:
                if key not in value:
                    raise ValueError(f"{path} thiếu field bắt buộc {key!r}")
            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                if schema.get("additionalProperties") is False:
                    extra = sorted(set(value) - set(properties))
                    if extra:
                        raise ValueError(f"{path} có field ngoài schema: {extra}")
                for key, child_schema in properties.items():
                    if key in value and isinstance(child_schema, dict):
                        AuroraTranslator._validate_schema(value[key], child_schema, f"{path}.{key}")
            return
        if expected == "array":
            if not isinstance(value, list):
                raise ValueError(f"{path} phải là array")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    AuroraTranslator._validate_schema(item, item_schema, f"{path}[{index}]")
            return
        if expected == "string" and not isinstance(value, str):
            raise ValueError(f"{path} phải là string")
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{path} phải là integer")
        if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"{path} phải là number")
        if expected == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{path} phải là boolean")

    @staticmethod
    def _response_content(payload: Any) -> Any:
        if not isinstance(payload, dict):
            raise ValueError("Aurora response phải là JSON object")
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and "content" in message:
                    return message["content"]
        if "output_text" in payload:
            return payload["output_text"]
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        raise ValueError("Aurora response không có message.content/output_text/result")

    @staticmethod
    def _parse_structured_result(payload: Any, schema: dict[str, Any]) -> dict[str, Any]:
        content = AuroraTranslator._response_content(payload)
        if isinstance(content, dict):
            result = content
        elif isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        text_parts.append(text)
            if not text_parts:
                raise ValueError("Aurora structured content không có text")
            result = _extract_json("".join(text_parts), array=False)
        else:
            result = _extract_json(str(content), array=False)
        if not isinstance(result, dict):
            raise ValueError("Aurora structured result phải là JSON object")
        AuroraTranslator._validate_schema(result, schema)
        return result

    def _retry_delay(self, response: requests.Response | None, attempt_index: int) -> float:
        if response is not None:
            retry_after = str(response.headers.get("Retry-After") or "").strip()
            if retry_after:
                try:
                    return min(self.retry_backoff_max_seconds, max(0.0, float(retry_after)))
                except ValueError:
                    pass
        return min(
            self.retry_backoff_max_seconds,
            self.retry_backoff_seconds * (2 ** attempt_index),
        )

    def _post_json(self, prompt: str, schema: dict[str, Any], *, max_tokens: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.replace("/no_think\n", "")},
                {
                    "role": "user",
                    "content": (
                        prompt
                        + "\nReturn exactly one JSON object and no Markdown or commentary."
                        + f"\nOutput JSON schema: {json.dumps(schema, ensure_ascii=False)}"
                    ),
                },
            ],
            "temperature": float(self.config.get("temperature", 0.15)),
            "max_tokens": int(max_tokens),
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "vi_dubber_translation",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if self.effort:
            payload["reasoning_effort"] = self.effort

        max_attempts = 1 + self.retry_budget
        headers = _aurora_headers(self.config)
        headers["Content-Type"] = "application/json"
        for attempt_index in range(max_attempts):
            self.aurora_attempts += 1
            response: requests.Response | None = None
            error: Exception | None = None
            retryable = False
            try:
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=max(1.0, self.timeout),
                )
                if not response.ok:
                    detail = response.text.strip()[:800]
                    error = RuntimeError(
                        f"Aurora translation trả HTTP {response.status_code}"
                        + (f": {detail}" if detail else "")
                    )
                    retryable = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
                else:
                    try:
                        raw = response.json()
                        return self._parse_structured_result(raw, schema)
                    except (ValueError, TypeError, KeyError) as exc:
                        error = RuntimeError("Aurora trả structured JSON không hợp lệ.")
                        error.__cause__ = exc
                        retryable = True
            except (requests.Timeout, requests.ConnectionError) as exc:
                error = RuntimeError(f"Không kết nối được Aurora tại {self.base_url}: {exc}")
                error.__cause__ = exc
                retryable = True
            except requests.RequestException as exc:
                error = RuntimeError(f"Aurora request thất bại: {exc}")
                error.__cause__ = exc

            self.aurora_failures += 1
            assert error is not None
            if not retryable or attempt_index + 1 >= max_attempts:
                if retryable and attempt_index + 1 >= max_attempts:
                    self.aurora_retries_exhausted += 1
                raise error
            self.aurora_retry_attempts += 1
            delay = self._retry_delay(response, attempt_index)
            if delay > 0:
                time.sleep(delay)
        raise AssertionError("unreachable Aurora retry loop")

    @contextmanager
    def running(self) -> Iterator["AuroraTranslator"]:
        self.catalog_requests += 1
        catalog = aurora_model_catalog(self.config)
        selected = next(
            (item for item in catalog["models"] if item.get("id") == self.model),
            None,
        )
        if selected is None:
            raise RuntimeError(
                f"Model Aurora đã chọn {self.model!r} không còn trong live catalog; "
                "VI Dubber không tự đổi sang model khác."
            )
        self.catalog_revision = str(catalog.get("revision") or "")
        self.catalog_updated_at = str(catalog.get("updated_at") or "")
        self.model_display_name = str(selected.get("display_name") or self.model)
        self.supported_efforts = list(selected.get("supported_efforts") or [])
        if self.effort and self.supported_efforts and self.effort not in self.supported_efforts:
            raise RuntimeError(
                f"Model Aurora {self.model!r} không advertise effort {self.effort!r}; "
                f"supported={self.supported_efforts}."
            )
        yield self

    def translate_segments(
        self,
        segments: list[Segment],
        glossary: dict[str, str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[Segment]:
        batch_size = max(
            1,
            int(
                self.config.get(
                    "aurora_segments_per_batch",
                    self.config.get("segments_per_batch", self.config.get("codex_segments_per_batch", 32)),
                )
            ),
        )
        context_window = max(0, int(self.config.get("context_window", 2)))
        schema = {
            "type": "object",
            "properties": {
                "translations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "vi": {"type": "string"}},
                        "required": ["id", "vi"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["translations"],
            "additionalProperties": False,
        }
        glossary_text = glossary_prompt_json(glossary)
        for offset in range(0, len(segments), batch_size):
            batch = segments[offset : offset + batch_size]
            if all(bool(item.vi and item.vi.strip()) for item in batch):
                continue
            payload = build_translation_payload(
                segments,
                offset=offset,
                count=len(batch),
                context_window=context_window,
            )
            prompt = (
                f"Glossary={glossary_text}\n"
                "Translate every input item using only nearby context for terminology/register consistency. "
                "Treat target_duration_sec as an approximate speaking budget, never as permission to drop meaning. "
                "Priority: meaning > critical facts > natural spoken Vietnamese > context/register > duration fit. "
                "Keep ids unchanged.\n"
                f"INPUT={json.dumps(payload, ensure_ascii=False)}"
            )
            result = self._post_json(prompt, schema, max_tokens=4096)
            translations = result.get("translations", [])
            by_id = {
                int(item["id"]): str(item["vi"]).strip()
                for item in translations
                if isinstance(item, dict)
            }
            missing = [item.id for item in batch if item.id not in by_id or not by_id[item.id]]
            if missing:
                raise RuntimeError(f"Aurora thiếu translation cho segment ids: {missing}")
            for item in batch:
                item.vi = by_id[item.id]
            self.translation_batches += 1
            if progress_callback is not None:
                done = min(offset + len(batch), len(segments))
                progress_callback(
                    done / max(1, len(segments)),
                    f"Đang dịch bằng Aurora: {done}/{len(segments)} đoạn",
                )
        return segments

    def rewrite_shorter(
        self,
        segment: Segment,
        measured_duration: float,
        glossary: dict[str, str],
    ) -> str:
        ratio = max(0.35, min(0.95, segment.duration / max(measured_duration, 0.1)))
        target_chars = max(8, int(len(segment.vi) * ratio * 0.9))
        schema = {
            "type": "object",
            "properties": {"vi": {"type": "string"}},
            "required": ["vi"],
            "additionalProperties": False,
        }
        prompt = (
            "Rewrite one Vietnamese dubbing line to fit a shorter slot without losing essential meaning.\n"
            f"Glossary={glossary_prompt_json(glossary)}\n"
            f"Source English={json.dumps(segment.text, ensure_ascii=False)}\n"
            f"Current Vietnamese={json.dumps(segment.vi, ensure_ascii=False)}\n"
            f"Target duration={segment.duration:.2f}s; current audio={measured_duration:.2f}s; "
            f"aim for about {target_chars} Vietnamese characters."
        )
        result = self._post_json(prompt, schema, max_tokens=768)
        self.rewrite_calls += 1
        return str(result["vi"]).strip()

    def rewrite_batch(
        self,
        batch_items: list[tuple[Segment, float, float, float, int]],
        glossary: dict[str, str],
    ) -> dict[int, str]:
        if not batch_items:
            return {}
        schema = {
            "type": "object",
            "properties": {
                "rewrites": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}, "vi": {"type": "string"}},
                        "required": ["id", "vi"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["rewrites"],
            "additionalProperties": False,
        }
        payload = [
            {
                "id": seg.id,
                "source_en": seg.text,
                "current_vi": seg.vi,
                "target_sec": round(target_dur, 2),
                "measured_sec": round(meas_dur, 2),
                "target_chars": target_chars,
                "mode": "aggressive" if ratio > 1.40 else "mild",
            }
            for seg, target_dur, meas_dur, ratio, target_chars in batch_items
        ]
        prompt = (
            "Rewrite each Vietnamese dubbing line to fit the target duration while preserving essential meaning, "
            "facts, numbers, names, negation, modality, and technical terms.\n"
            f"Glossary={glossary_prompt_json(glossary)}\n"
            f"INPUT={json.dumps(payload, ensure_ascii=False)}"
        )
        result = self._post_json(prompt, schema, max_tokens=2048)
        self.rewrite_calls += 1
        return {
            int(item["id"]): str(item["vi"]).strip()
            for item in result.get("rewrites", [])
            if isinstance(item, dict) and "id" in item and "vi" in item
        }

    def stats(self) -> dict[str, Any]:
        return {
            "requested": "aurora",
            "used": "aurora",
            "provider": "aurora",
            "model": self.model,
            "model_id": self.model,
            "display_name": self.model_display_name,
            "effort": self.effort,
            "catalog_revision": self.catalog_revision,
            "catalog_updated_at": self.catalog_updated_at,
            "translation_batches": self.translation_batches,
            "rewrite_calls": self.rewrite_calls,
            "retry_budget": self.retry_budget,
            "aurora_attempts": self.aurora_attempts,
            "aurora_retry_attempts": self.aurora_retry_attempts,
            "aurora_failures": self.aurora_failures,
            "aurora_retries_exhausted": self.aurora_retries_exhausted,
            "catalog_requests": self.catalog_requests,
            "fallback_used": False,
        }


class HybridTranslator:
    """Use WebGPT first and fall back to the local LLM when WebGPT is unavailable."""

    def __init__(
        self,
        config: dict[str, Any],
        work_dir: Path,
        retry_budget: int | None = None,
    ):
        self.primary = WebGptTranslator(config, work_dir, retry_budget=retry_budget)
        self.fallback = LocalTranslator(config, work_dir)
        self._stack: ExitStack | None = None
        self._active: WebGptTranslator | LocalTranslator | None = None
        self.fallback_used = False
        self.fallback_reason = ""

    def _switch_to_fallback(self, exc: Exception) -> None:
        if self._active is self.fallback:
            return
        if self._stack is None:
            raise RuntimeError("Hybrid translator chưa được khởi tạo trong running().") from exc
        self.fallback_used = True
        self.fallback_reason = str(exc).strip() or exc.__class__.__name__
        self._stack.enter_context(self.fallback.running())
        self._active = self.fallback

    @contextmanager
    def running(self) -> Iterator["HybridTranslator"]:
        with ExitStack() as stack:
            self._stack = stack
            self._active = None
            try:
                try:
                    stack.enter_context(self.primary.running())
                    self._active = self.primary
                except Exception as exc:
                    self._switch_to_fallback(exc)
                yield self
            finally:
                self._active = None
                self._stack = None

    def translate_segments(
        self,
        segments: list[Segment],
        glossary: dict[str, str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[Segment]:
        if self._active is None:
            raise RuntimeError("Hybrid translator chưa chạy.")
        if self._active is self.fallback:
            return self.fallback.translate_segments(segments, glossary, progress_callback)

        last_progress = 0.0

        def primary_progress(value: float, message: str) -> None:
            nonlocal last_progress
            last_progress = max(last_progress, value)
            if progress_callback is not None:
                progress_callback(value, message)

        try:
            return self.primary.translate_segments(segments, glossary, primary_progress)
        except Exception as exc:
            self._switch_to_fallback(exc)
            if progress_callback is not None:
                progress_callback(last_progress, "WebGPT gặp lỗi, đang chuyển sang LLM local")

            def fallback_progress(value: float, message: str) -> None:
                if progress_callback is not None:
                    mapped = last_progress + (1.0 - last_progress) * value
                    progress_callback(mapped, message)

            return self.fallback.translate_segments(segments, glossary, fallback_progress)

    def rewrite_shorter(
        self,
        segment: Segment,
        measured_duration: float,
        glossary: dict[str, str],
    ) -> str:
        if self._active is None:
            raise RuntimeError("Hybrid translator chưa chạy.")
        if self._active is self.fallback:
            return self.fallback.rewrite_shorter(segment, measured_duration, glossary)
        try:
            return self.primary.rewrite_shorter(segment, measured_duration, glossary)
        except Exception as exc:
            self._switch_to_fallback(exc)
            return self.fallback.rewrite_shorter(segment, measured_duration, glossary)

    def rewrite_batch(
        self,
        batch_items: list[tuple[Segment, float, float, float, int]],
        glossary: dict[str, str],
    ) -> dict[int, str]:
        if self._active is None:
            raise RuntimeError("Hybrid translator chưa chạy.")
        if self._active is self.fallback:
            return self.fallback.rewrite_batch(batch_items, glossary)
        try:
            return self.primary.rewrite_batch(batch_items, glossary)
        except Exception as exc:
            self._switch_to_fallback(exc)
            return self.fallback.rewrite_batch(batch_items, glossary)

    def stats(self) -> dict[str, Any]:
        primary = self.primary.stats()
        fallback = self.fallback.stats()
        return {
            "requested": "hybrid",
            "used": "local" if self.fallback_used else "webgpt",
            "model": self.fallback.model_id if self.fallback_used else self.primary.model,
            "translation_batches": int(primary.get("translation_batches") or 0)
            + int(fallback.get("translation_batches") or 0),
            "rewrite_calls": int(primary.get("rewrite_calls") or 0) + int(fallback.get("rewrite_calls") or 0),
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "webgpt_batches": int(primary.get("translation_batches") or 0),
            "local_batches": int(fallback.get("translation_batches") or 0),
            "retry_budget": int(primary.get("retry_budget") or 0),
            "webgpt_attempts": int(primary.get("webgpt_attempts") or 0),
            "webgpt_retry_attempts": int(primary.get("webgpt_retry_attempts") or 0),
            "webgpt_failures": int(primary.get("webgpt_failures") or 0),
            "webgpt_retries_exhausted": int(primary.get("webgpt_retries_exhausted") or 0),
        }


def normalize_translation_provider(value: str | None) -> str:
    provider = str(value or "webgpt").strip().lower()
    if provider in {"aurora", "chatgpt_web_aurora", "chatgpt-web-aurora"}:
        return "aurora"
    if provider in {"webgpt", "chatgpt_web", "chatgpt-web", "codex"}:
        return "webgpt"
    if provider in {"local", "local_only", "local-only"}:
        return "local"
    if provider in {"hybrid", "fallback", "webgpt_fallback", "webgpt-fallback"}:
        return "hybrid"
    raise ValueError(f"Provider dịch không hợp lệ: '{provider}'. Dùng aurora, webgpt, local hoặc hybrid.")


def build_translator(
    config: dict[str, Any],
    work_dir: Path,
    provider_override: str | None = None,
    retry_budget: int | None = None,
    model_override: str | None = None,
    effort_override: str | None = None,
) -> AuroraTranslator | WebGptTranslator | LocalTranslator | HybridTranslator:
    provider = normalize_translation_provider(provider_override or config.get("provider", "webgpt"))
    if provider == "aurora":
        return AuroraTranslator(
            config,
            work_dir,
            retry_budget=retry_budget,
            model_override=model_override,
            effort_override=effort_override,
        )
    if provider == "webgpt":
        return WebGptTranslator(
            config,
            work_dir,
            retry_budget=retry_budget,
            model_override=model_override,
            effort_override=effort_override,
        )
    if provider == "local":
        return LocalTranslator(config, work_dir)
    if provider == "hybrid":
        return HybridTranslator(config, work_dir, retry_budget=retry_budget)
    raise AssertionError(provider)


# Backward compatibility for scripts importing the old name.
CodexTranslator = WebGptTranslator
