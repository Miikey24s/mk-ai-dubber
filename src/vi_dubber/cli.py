from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import typer
from rich.console import Console
from rich.table import Table

from .jobs import list_job_states, request_control
from .pipeline import load_config, run_pipeline
from .preflight import raise_for_preflight, run_preflight
from .profiles import PROFILE_OVERRIDES
from .runtime import (
    PROJECT_ROOT,
    WORK_DIR,
    command_version,
    configure_runtime,
    ffmpeg_exe,
    find_codex_exe,
    validate_server_bind_host,
)
from .translate import PINNED_WEBGPT_MODEL, normalize_translation_provider, webgpt_route_info
from .webgpt_runtime import (
    DUBBER_WEBGPT_PORT,
    default_core_repo,
    default_runtime_home,
    initialize_runtime,
    login_runtime,
    runtime_status,
    start_runtime,
    stop_runtime,
)


app = typer.Typer(no_args_is_help=True, add_completion=False)
webgpt_runtime_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(webgpt_runtime_app, name="webgpt-runtime")
console = Console()


def _job_dir_from_name(value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = WORK_DIR / value
    candidate = candidate.resolve()
    try:
        candidate.relative_to(WORK_DIR.resolve())
    except ValueError as exc:
        raise typer.BadParameter("Job phải nằm trong thư mục work của VI Dubber") from exc
    if not candidate.is_dir() or not candidate.name.startswith("job-"):
        raise typer.BadParameter(f"Không tìm thấy job hợp lệ: {value}")
    return candidate


@app.command()
def dub(
    input_video: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    config: Path = typer.Option(PROJECT_ROOT / "config.yaml", "--config", exists=True),
    voice_ref: Optional[Path] = typer.Option(None, "--voice-ref", exists=True, dir_okay=False),
    hf_token: Optional[str] = typer.Option(None, "--hf-token", envvar="HUGGINGFACE_TOKEN", hidden=True),
    diarize: Optional[bool] = typer.Option(None, "--diarize/--no-diarize"),
    translator: Optional[str] = typer.Option(None, "--translator", help="webgpt (backend hiện tại)"),
    translation_model: Optional[str] = typer.Option(None, "--translation-model"),
    translation_effort: Optional[str] = typer.Option(None, "--translation-effort"),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="fast | balanced_fast | balanced_best | max_quality",
    ),
    resume: bool = typer.Option(True, "--resume/--fresh"),
) -> None:
    """Lồng tiếng Việt cho một video bằng Dedicated Dubber-WebGPT."""
    if output is None:
        output = input_video.with_name(f"{input_video.stem}_vi.mp4")
    resolved = load_config(config, profile)
    translation_provider = normalize_translation_provider(
        translator or str(resolved["translation"].get("provider", "webgpt"))
    )
    if translation_provider != "webgpt":
        raise typer.BadParameter(
            f"VI Dubber hiện chỉ cho phép translator=webgpt (Dedicated Dubber-WebGPT, port {DUBBER_WEBGPT_PORT})."
        )
    if diarize is None:
        diarization_value = resolved.get("diarization", {}).get("enabled", "auto")
        if isinstance(diarization_value, bool):
            effective_diarize = diarization_value
        elif str(diarization_value).strip().lower() == "auto":
            effective_diarize = bool(hf_token)
        else:
            effective_diarize = str(diarization_value).strip().lower() in {"1", "true", "yes", "on"}
    else:
        effective_diarize = diarize
    raise_for_preflight(
        run_preflight(
            resolved,
            translation_provider=translation_provider,
            input_path=input_video,
            voice_ref=voice_ref,
            diarize=effective_diarize,
            hf_token=hf_token,
        )
    )
    result = run_pipeline(
        input_video,
        output,
        config,
        voice_ref=voice_ref,
        hf_token=hf_token,
        diarize_override=diarize,
        translation_provider=translation_provider,
        translation_model=translation_model,
        translation_effort=translation_effort,
        profile=profile,
        resume=resume,
    )
    console.print(f"\n[bold green]Hoàn tất:[/] {result['output']}")
    console.print(f"Phụ đề SRT tiếng Việt: {result['subtitle']}")
    console.print(f"Dữ liệu trung gian/QA: {result['work_dir']}")


@app.command("web")
def web_app(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(7860, "--port"),
    share: bool = typer.Option(False, "--share/--no-share"),
) -> None:
    """Mở giao diện web VI Dubber trên máy (kèm REST API và Gradio)."""
    from .web import launch_app

    try:
        host = validate_server_bind_host(host)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--host") from exc
    launch_app(host=host, port=port, share=share)


@app.command("api")
def api_server(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(7860, "--port"),
) -> None:
    """Khởi chạy máy chủ REST API và Web UI độc lập."""
    import uvicorn
    from .api import create_app

    try:
        host = validate_server_bind_host(host)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--host") from exc
    uvicorn.run(create_app(), host=host, port=port)


@app.command("jobs")
def jobs(limit: int = typer.Option(20, min=1, max=100)) -> None:
    """Liệt kê trạng thái job đã persisted, kể cả sau khi UI/browser đóng."""
    table = Table("Job", "Trạng thái", "Stage", "Tiến độ", "Input", "Profile", "Cập nhật")
    for state in list_job_states(WORK_DIR)[:limit]:
        metadata = state.get("metadata") or {}
        table.add_row(
            Path(str(state["job_dir"])).name,
            str(state.get("status") or ""),
            str(state.get("stage") or ""),
            f"{float(state.get('progress') or 0.0):.0%}",
            str(metadata.get("input_name") or ""),
            str(metadata.get("profile") or ""),
            str(state.get("updated_at") or ""),
        )
    console.print(table)


@app.command("job-control")
def job_control(
    job: str = typer.Argument(..., help="Tên job, ví dụ job-cafbd0adb862d5ae"),
    action: str = typer.Argument(..., help="pause | cancel | run"),
) -> None:
    """Gửi control request cho job; pipeline thực thi tại checkpoint an toàn kế tiếp."""
    normalized = action.strip().lower()
    if normalized not in {"pause", "cancel", "run"}:
        raise typer.BadParameter("action phải là pause, cancel hoặc run")
    job_dir = _job_dir_from_name(job)
    request_control(job_dir, normalized)  # type: ignore[arg-type]
    console.print(f"Đã ghi yêu cầu [bold]{normalized}[/] cho {job_dir.name}.")


@app.command()
def doctor(
    config: Path = typer.Option(PROJECT_ROOT / "config.yaml", "--config", exists=True),
) -> None:
    """Kiểm tra môi trường trước khi chạy tác vụ lồng tiếng dài."""
    configure_runtime()
    table = Table("Hạng mục", "Kết quả")
    table.add_row("Python", sys.version.split()[0])
    table.add_row("FFmpeg", command_version(ffmpeg_exe(), "-version"))

    try:
        import torch

        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            table.add_row("CUDA", f"OK - {gpu} ({vram:.1f} GB), torch {torch.__version__}")
        else:
            table.add_row("CUDA", "KHÔNG KHẢ DỤNG")
    except Exception as exc:
        table.add_row("CUDA", f"LỖI - {exc}")

    for module in ("whisperx", "audio_separator", "vieneu", "torchcodec", "gradio", "yt_dlp"):
        try:
            __import__(module)
            table.add_row(module, "OK")
        except Exception as exc:
            table.add_row(module, f"LỖI - {exc}")
    try:
        resolved = load_config(config)
    except Exception as exc:
        resolved = {}
        table.add_row("Config schema", f"LỖI - {exc}")

    translation_config = dict(resolved.get("translation", {}) or {}) if resolved else {}
    try:
        route = webgpt_route_info(translation_config)
    except Exception as exc:
        route = {"ready": False, "reason": f"probe lỗi: {type(exc).__name__}: {exc}"}
    transport = str(translation_config.get("webgpt_transport") or "codex-exec").strip().lower()
    if transport == "codex-exec" and find_codex_exe() is None and shutil.which("codex") is None:
        table.add_row("Dedicated Dubber-WebGPT", "LỖI - route codex-exec cần lệnh Codex nhưng không tìm thấy")
    elif bool(route.get("ready")):
        table.add_row(
            "Dedicated Dubber-WebGPT",
            f"OK - {route.get('model') or PINNED_WEBGPT_MODEL} · port {route.get('port') or DUBBER_WEBGPT_PORT} · {transport}",
        )
    else:
        table.add_row("Dedicated Dubber-WebGPT", f"CHƯA KẾT NỐI - {route.get('reason') or 'không rõ nguyên nhân'}")
    local_model = PROJECT_ROOT / "models" / "llm" / "Qwen--Qwen3-14B-GGUF" / "Qwen3-14B-Q4_K_M.gguf"
    table.add_row("LLM local", f"OK - {local_model.name}" if local_model.exists() else "CHƯA CÓ MODEL")
    table.add_row(
        "TypeSafe semantic QA",
        "CÓ KEY - shadow mode" if os.getenv("TYPESAFE_API_KEY") else "TÙY CHỌN - thiếu TYPESAFE_API_KEY",
    )
    table.add_row("Token diarization", "đã có" if os.getenv("HUGGINGFACE_TOKEN") else "tùy chọn / chưa có")
    if resolved:
        table.add_row("Profile mặc định", str(resolved["profile"]))
        table.add_row("Translator mặc định", str(resolved.get("translation", {}).get("provider", "webgpt")))
        table.add_row("Retry budget", str(resolved.get("reliability", {}).get("retry_budget", 3)))
        table.add_row("Config schema", "OK")
    table.add_row("Profiles", ", ".join(sorted(PROFILE_OVERRIDES)))
    console.print(table)


@webgpt_runtime_app.command("init")
def webgpt_runtime_init(
    force: bool = typer.Option(False, "--force", help="Reset config runtime nhưng không xóa browser login state."),
) -> None:
    """Tạo home/config riêng cho Dedicated Dubber-WebGPT mà không đổi global Codex/Cockpit route."""
    try:
        config = initialize_runtime(force=force)
    except RuntimeError as exc:
        console.print(f"[red]Không init được Dedicated Dubber-WebGPT:[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Đã chuẩn bị Dedicated Dubber-WebGPT[/] tại {default_runtime_home()}")
    console.print(f"Core: {default_core_repo()}")
    console.print(f"Port: {config.get('port', DUBBER_WEBGPT_PORT)}")
    console.print("Bước account còn lại: `uv run vi-dubber webgpt-runtime login`.")


@webgpt_runtime_app.command("login")
def webgpt_runtime_login() -> None:
    """Mở Chrome profile riêng và lưu ChatGPT login cho Dubber-WebGPT."""
    try:
        code = login_runtime()
    except RuntimeError as exc:
        console.print(f"[red]Không mở được login Dedicated Dubber-WebGPT:[/] {exc}")
        raise typer.Exit(code=1) from exc
    if code != 0:
        raise typer.Exit(code=code)


@webgpt_runtime_app.command("start")
def webgpt_runtime_start() -> None:
    """Khởi chạy Dedicated Dubber-WebGPT ở nền trên port riêng."""
    try:
        status = start_runtime()
    except RuntimeError as exc:
        console.print(f"[red]Không start được Dedicated Dubber-WebGPT:[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"[green]Dedicated Dubber-WebGPT online[/] · port {status.get('port')} · "
        f"models={', '.join(status.get('models') or []) or 'catalog trống'}"
    )


@webgpt_runtime_app.command("status")
def webgpt_runtime_status() -> None:
    """Kiểm tra health, login state và live model catalog của runtime riêng."""
    status = runtime_status()
    table = Table("Hạng mục", "Kết quả")
    table.add_row("Home", str(status.get("home") or default_runtime_home()))
    table.add_row("Port", str(status.get("port") or DUBBER_WEBGPT_PORT))
    table.add_row("Login riêng", "OK" if status.get("login_state") else "CHƯA CÓ")
    table.add_row("Runtime", "ONLINE" if status.get("ready") else "OFFLINE")
    table.add_row("Models", ", ".join(status.get("models") or []) or "-")
    if status.get("reason"):
        table.add_row("Chi tiết", str(status["reason"]))
    console.print(table)


@webgpt_runtime_app.command("stop")
def webgpt_runtime_stop(
    force: bool = typer.Option(False, "--force", help="Cho phép dừng dù runtime còn active turn."),
) -> None:
    """Dừng runtime riêng; mặc định từ chối nếu đang có turn chạy."""
    try:
        status = stop_runtime(force=force)
    except RuntimeError as exc:
        console.print(f"[red]Không stop được Dedicated Dubber-WebGPT:[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print("Đã dừng Dedicated Dubber-WebGPT." if not status.get("ready") else "Runtime vẫn đang online.")


if __name__ == "__main__":
    app()
