from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
MODELS_DIR = PROJECT_ROOT / "models"
WORK_DIR = PROJECT_ROOT / "work"

_DLL_HANDLES: list[object] = []


def _first(pattern: str) -> Path:
    matches = sorted(TOOLS_DIR.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"Required bundled tool not found: {pattern}")
    return matches[0]


def ffmpeg_exe() -> Path:
    return _first("ffmpeg/**/bin/ffmpeg.exe")


def ffprobe_exe() -> Path:
    return _first("ffmpeg/**/bin/ffprobe.exe")


def llama_server_exe() -> Path:
    path = TOOLS_DIR / "llama" / "llama-server.exe"
    if not path.exists():
        raise FileNotFoundError(f"llama-server.exe not found: {path}")
    return path


def find_codex_exe() -> Path | None:
    found = shutil.which("codex")
    if found:
        return Path(found)
    candidates: list[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.extend(sorted((Path(local_appdata) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"), reverse=True))
    home = Path.home()
    candidates.append(home / ".codex" / "plugins" / ".plugin-appserver" / "codex.exe")
    candidates.append(home / ".codex" / ".sandbox-bin" / "codex.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def configure_runtime() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / "separator").mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / "whisperx").mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / "pyannote").mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / "hf").mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / "torch").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(MODELS_DIR / "hf" / "hub"))
    os.environ.setdefault("TORCH_HOME", str(MODELS_DIR / "torch"))
    os.environ.setdefault("AUDIO_SEPARATOR_MODEL_DIR", str(MODELS_DIR / "separator"))

    ff_bin = ffmpeg_exe().parent
    current_path = os.environ.get("PATH", "")
    if str(ff_bin).lower() not in current_path.lower():
        current_path = f"{ff_bin}{os.pathsep}{current_path}"
        os.environ["PATH"] = current_path

    codex_bin = find_codex_exe()
    if codex_bin is not None:
        codex_dir = str(codex_bin.parent)
        if codex_dir.lower() not in os.environ.get("PATH", "").lower():
            os.environ["PATH"] = f"{codex_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        _DLL_HANDLES.append(os.add_dll_directory(str(ff_bin)))


def command_version(executable: Path, *args: str) -> str:
    result = subprocess.run(
        [str(executable), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0] if text else "unknown"
