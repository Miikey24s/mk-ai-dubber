from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runtime import MODELS_DIR, WORK_DIR, configure_runtime
from .translate import webgpt_route_info


@dataclass(slots=True)
class PreflightCheck:
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def run_preflight(
    config: dict[str, Any],
    *,
    translation_provider: str,
    input_path: Path | None = None,
    voice_ref: Path | None = None,
    diarize: bool = False,
    hf_token: str | None = None,
) -> list[PreflightCheck]:
    """Return actionable, secret-free readiness checks for one requested job."""
    configure_runtime()
    checks: list[PreflightCheck] = []

    if input_path is not None:
        checks.append(
            PreflightCheck(
                "input",
                "ok" if input_path.is_file() else "error",
                str(input_path) if input_path.is_file() else "Không tìm thấy input video",
            )
        )
    if voice_ref is not None:
        checks.append(
            PreflightCheck(
                "voice_ref",
                "ok" if voice_ref.is_file() else "error",
                str(voice_ref) if voice_ref.is_file() else "Không tìm thấy voice reference",
            )
        )

    min_free_gib = float(config.get("reliability", {}).get("min_free_disk_gb", 5.0))
    work_root = WORK_DIR if WORK_DIR.exists() else WORK_DIR.parent
    try:
        usage = shutil.disk_usage(work_root)
    except OSError as exc:
        checks.append(PreflightCheck("disk", "error", f"Không đọc được dung lượng đĩa: {exc}"))
    else:
        free_gib = usage.free / (1024**3)
        checks.append(
            PreflightCheck(
                "disk",
                "ok" if free_gib >= min_free_gib else "error",
                f"Còn {free_gib:.1f} GiB; yêu cầu tối thiểu {min_free_gib:.1f} GiB",
            )
        )

    reliability = config.get("reliability", {})
    retry_budget_raw = reliability.get("retry_budget", 3)
    try:
        retry_budget = int(retry_budget_raw)
    except (TypeError, ValueError):
        retry_budget = -1
    retry_valid = 0 <= retry_budget <= 10
    checks.append(
        PreflightCheck(
            "retry_policy",
            "ok" if retry_valid else "error",
            (
                f"Bounded retry budget: {retry_budget}"
                if retry_valid
                else f"retry_budget không hợp lệ: {retry_budget_raw!r}; yêu cầu [0, 10]"
            ),
        )
    )

    tts = config.get("tts", {})
    asr = config.get("asr", {})
    requires_cuda = str(tts.get("device", "cpu")).lower() == "cuda" or str(
        asr.get("device", "cuda")
    ).lower() == "cuda"
    try:
        import torch

        cuda_ready = bool(torch.cuda.is_available())
        detail = torch.cuda.get_device_name(0) if cuda_ready else "CUDA không khả dụng"
    except Exception as exc:
        cuda_ready = False
        detail = f"Không kiểm tra được CUDA: {exc}"
    checks.append(
        PreflightCheck(
            "cuda",
            "ok" if cuda_ready else ("error" if requires_cuda else "warning"),
            detail,
        )
    )

    local_model = MODELS_DIR / "llm" / "Qwen--Qwen3-14B-GGUF" / "Qwen3-14B-Q4_K_M.gguf"
    provider = translation_provider.strip().lower()
    route: dict[str, Any] = {}
    route_error = ""
    if provider in {"webgpt", "hybrid"}:
        try:
            route = webgpt_route_info(dict(config.get("translation", {}) or {}))
        except Exception as exc:
            route_error = f"{type(exc).__name__}: {exc}"

    if provider == "webgpt":
        ready = bool(route.get("ready"))
        checks.append(
            PreflightCheck(
                "translation",
                "ok" if ready else "error",
                str(
                    route.get("model")
                    or route.get("reason")
                    or route_error
                    or "WebGPT route chưa sẵn sàng"
                ),
            )
        )
    elif provider == "local":
        checks.append(
            PreflightCheck(
                "translation",
                "ok" if local_model.is_file() else "error",
                local_model.name if local_model.is_file() else "Thiếu Qwen local model",
            )
        )
    elif provider == "hybrid":
        webgpt_ready = bool(route.get("ready"))
        local_ready = local_model.is_file()
        if webgpt_ready and local_ready:
            status = "ok"
            detail = "WebGPT sẵn sàng; local fallback sẵn sàng"
        elif webgpt_ready:
            status = "warning"
            detail = "WebGPT sẵn sàng; thiếu local fallback, lỗi WebGPT sẽ không degrade an toàn"
        elif local_ready:
            status = "warning"
            reason = str(route.get("reason") or route_error or "WebGPT route chưa sẵn sàng")
            detail = f"WebGPT chưa sẵn sàng ({reason}); sẽ chạy local fallback"
        else:
            status = "error"
            reason = str(route.get("reason") or route_error or "WebGPT route chưa sẵn sàng")
            detail = f"WebGPT lỗi ({reason}) và local fallback cũng thiếu"
        checks.append(
            PreflightCheck(
                "translation",
                status,
                detail,
            )
        )
    else:
        checks.append(
            PreflightCheck(
                "translation",
                "error",
                f"Provider dịch không hợp lệ: {translation_provider!r}; dùng webgpt, local hoặc hybrid",
            )
        )

    semantic = config.get("qa", {}).get("semantic", {})
    if bool(semantic.get("enabled", False)):
        has_key = bool(os.getenv("TYPESAFE_API_KEY"))
        strict = str(config.get("profile") or "") == "max_quality"
        attempts = int(semantic.get("max_attempts", 3))
        checks.append(
            PreflightCheck(
                "typesafe",
                "ok" if has_key else ("error" if strict else "warning"),
                (
                    f"API key có trong environment; max_attempts={attempts}"
                    if has_key
                    else f"Thiếu TYPESAFE_API_KEY; semantic QA sẽ degraded; max_attempts={attempts}"
                ),
            )
        )

    if diarize:
        token_ready = bool((hf_token or "").strip() or os.getenv("HUGGINGFACE_TOKEN"))
        checks.append(
            PreflightCheck(
                "diarization",
                "ok" if token_ready else "error",
                "HF token có trong runtime" if token_ready else "Thiếu HUGGINGFACE_TOKEN cho diarization",
            )
        )
    return checks


def raise_for_preflight(checks: list[PreflightCheck]) -> None:
    errors = [check for check in checks if check.status == "error"]
    if not errors:
        return
    details = "; ".join(f"{item.name}: {item.detail}" for item in errors)
    raise RuntimeError(f"Preflight chưa đạt: {details}")
