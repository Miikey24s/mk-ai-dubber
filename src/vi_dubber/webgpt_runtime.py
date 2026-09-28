from __future__ import annotations

import json
import hashlib
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from .runtime import PROJECT_ROOT


DUBBER_WEBGPT_PORT = 17850
DUBBER_WEBGPT_BASE_URL = f"http://127.0.0.1:{DUBBER_WEBGPT_PORT}/v1"
START_LOCK_STALE_SECONDS = 5 * 60


def default_core_repo() -> Path:
    configured = os.getenv("VI_DUBBER_WEBGPT_CORE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (PROJECT_ROOT.parents[2] / "AI" / "codex-chatgpt-web-cockpit").resolve()


def default_runtime_home() -> Path:
    configured = os.getenv("VI_DUBBER_WEBGPT_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (PROJECT_ROOT.parents[1] / ".runtime" / "dubber-webgpt").resolve()


def _bun_executable() -> str:
    bun = shutil.which("bun")
    if not bun:
        raise RuntimeError("Không tìm thấy Bun trên PATH; Dedicated Dubber-WebGPT cần Bun để chạy core WebGPT.")
    return str(Path(bun).resolve())


def _core_cli(core_repo: Path) -> Path:
    cli = core_repo / "src" / "cli.ts"
    package = core_repo / "package.json"
    if not cli.is_file() or not package.is_file():
        raise RuntimeError(f"Không tìm thấy codex-chatgpt-web core hợp lệ tại {core_repo}")
    return cli


def _runtime_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CODEX_CHATGPT_WEB_HOME"] = str(home)
    return env


def _login_marker_path(storage_state: Path) -> Path:
    """Return the marker written only after the core verifies a live ChatGPT composer."""
    return Path(f"{storage_state}.verified.json")


def _verified_login_state(storage_state: Path) -> bool:
    if not storage_state.is_file():
        return False
    marker_path = _login_marker_path(storage_state)
    if not marker_path.is_file():
        return False
    # The managed browser refreshes cookies and local storage after normal
    # turns, so the storage-state mtime is not a login change signal. The
    # marker is bootstrap evidence; live provider health still verifies the
    # actual ChatGPT surface before accepting work.
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(marker, dict)
        and marker.get("version") == 1
        and marker.get("authenticated") is True
        and isinstance(marker.get("verifiedAt"), str)
        and bool(marker["verifiedAt"].strip())
    )


def _config_fingerprint(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _catalog_model_ids(payload: Any) -> list[str]:
    """Return a stable, validated model-id list from the runtime catalog.

    The health endpoint can stay reachable while a proxy or provider returns a
    malformed/empty catalog. Treat that response as unavailable instead of
    advertising readiness (or accidentally exposing individual characters of a
    string as model ids) to the translation UI.
    """
    if not isinstance(payload, dict):
        raise RuntimeError("runtime model catalog phải là JSON object")
    raw_models = payload.get("data", payload.get("models", []))
    if not isinstance(raw_models, list):
        raise RuntimeError("runtime model catalog không có danh sách model hợp lệ")

    model_ids: list[str] = []
    seen: set[str] = set()
    for item in raw_models:
        if isinstance(item, dict):
            candidate = item.get("id") or item.get("slug")
        elif isinstance(item, str):
            candidate = item
        else:
            continue
        if not isinstance(candidate, str):
            continue
        model_id = candidate.strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            model_ids.append(model_id)
    if not model_ids:
        raise RuntimeError("runtime model catalog đang trống")
    return model_ids


def _source_revision(core_repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=core_repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "unknown"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ProcessLookupError):
        return False
    return True


def _acquire_start_lock(path: Path) -> None:
    payload = json.dumps({"pid": os.getpid(), "createdAt": time.time()})
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, payload.encode("ascii"))
            finally:
                os.close(fd)
            return
        except FileExistsError as exc:
            stale = False
            try:
                existing = json.loads(path.read_text(encoding="ascii"))
                owner_pid = int(existing.get("pid") or 0) if isinstance(existing, dict) else 0
                created_at = float(existing.get("createdAt") or 0) if isinstance(existing, dict) else 0
                stale = (created_at > 0 and time.time() - created_at > START_LOCK_STALE_SECONDS) or not _process_is_alive(owner_pid)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                try:
                    stale = time.time() - path.stat().st_mtime > START_LOCK_STALE_SECONDS
                except OSError:
                    stale = False
            if stale and attempt == 0:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            raise RuntimeError("Dedicated Dubber-WebGPT đang có một phiên start khác; không tạo process thứ hai.") from exc


def _default_config_from_core(core_repo: Path, home: Path) -> dict[str, Any]:
    cli = _core_cli(core_repo)
    config_module = (cli.parent / "config.ts").resolve().as_uri()
    code = (
        f'import {{ defaultConfig }} from {json.dumps(config_module)}; '
        'console.log(JSON.stringify(defaultConfig("browser-only")));'
    )
    result = subprocess.run(
        [_bun_executable(), "-e", code],
        cwd=core_repo,
        env=_runtime_env(home),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Không tạo được config Dedicated Dubber-WebGPT từ core hiện tại: {detail}")
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Core WebGPT trả default config không hợp lệ.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Core WebGPT trả default config không phải JSON object.")
    return payload


def initialize_runtime(
    *,
    core_repo: Path | None = None,
    home: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    core_repo = (core_repo or default_core_repo()).resolve()
    home = (home or default_runtime_home()).resolve()
    _core_cli(core_repo)
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / "config.json"

    if config_path.exists() and not force:
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Config Dedicated Dubber-WebGPT hiện có bị lỗi: {config_path}") from exc
        if not isinstance(existing, dict):
            raise RuntimeError(f"Config Dedicated Dubber-WebGPT không hợp lệ: {config_path}")
        if int(existing.get("port") or 0) != DUBBER_WEBGPT_PORT:
            raise RuntimeError(
                f"Runtime home đã có config port {existing.get('port')}; "
                f"dùng --force nếu muốn reset sang {DUBBER_WEBGPT_PORT}."
            )
        return existing

    config = _default_config_from_core(core_repo, home)
    config.update(
        {
            "mode": "browser-only",
            # The current bridge uses this flag to enter provider-only mode. The dedicated
            # runtime does not register itself as a global Codex route or Cockpit provider.
            "integrationOwner": "cockpit",
            "host": "127.0.0.1",
            "port": DUBBER_WEBGPT_PORT,
            "browserHost": "managed-chrome",
            "browserInteractionMode": "automatic",
            "headed": True,
            "experimentalBiggerContext": False,
            "experimentalSkillAttachments": False,
            "autoApproveToolCalls": False,
        }
    )
    temporary = config_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, config_path)
    return config


def _load_runtime_config(home: Path) -> dict[str, Any]:
    path = home / "config.json"
    if not path.is_file():
        raise RuntimeError("Dedicated Dubber-WebGPT chưa init. Chạy `vi-dubber webgpt-runtime init` trước.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Không đọc được runtime config: {path}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Runtime config không hợp lệ: {path}")
    return data


def login_runtime(*, core_repo: Path | None = None, home: Path | None = None) -> int:
    core_repo = (core_repo or default_core_repo()).resolve()
    home = (home or default_runtime_home()).resolve()
    _load_runtime_config(home)
    cli = _core_cli(core_repo)
    result = subprocess.run(
        [_bun_executable(), str(cli), "--home", str(home), "login"],
        cwd=core_repo,
        env=_runtime_env(home),
        check=False,
    )
    return int(result.returncode)


def runtime_status(*, home: Path | None = None, timeout: float = 1.5) -> dict[str, Any]:
    home = (home or default_runtime_home()).resolve()
    result: dict[str, Any] = {
        "ready": False,
        "home": str(home),
        "base_url": DUBBER_WEBGPT_BASE_URL,
        "port": DUBBER_WEBGPT_PORT,
        "login_state": False,
        "models": [],
    }
    try:
        config = _load_runtime_config(home)
    except RuntimeError as exc:
        result["reason"] = str(exc)
        return result

    storage_state = Path(str(config.get("storageStatePath") or ""))
    marker_path = _login_marker_path(storage_state) if str(storage_state) else Path("")
    result["login_state"] = _verified_login_state(storage_state)
    result["storage_state"] = str(storage_state)
    result["login_marker"] = str(marker_path)
    receipt_path = home / "provider.launch.json"
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(receipt, dict):
                result["launch_receipt"] = receipt
        except (OSError, json.JSONDecodeError):
            result["launch_receipt"] = None
    try:
        health = requests.get(f"http://127.0.0.1:{DUBBER_WEBGPT_PORT}/healthz", timeout=timeout)
        health.raise_for_status()
        health_payload = health.json()
        if not isinstance(health_payload, dict):
            raise RuntimeError("healthz không trả JSON object")
        result["health"] = health_payload
        if int(health_payload.get("port") or 0) != DUBBER_WEBGPT_PORT:
            raise RuntimeError(f"listener port không khớp {DUBBER_WEBGPT_PORT}")
        if not bool(health_payload.get("accepting_turns", False)):
            raise RuntimeError("runtime đang không nhận turn mới")

        catalog_response = requests.get(f"{DUBBER_WEBGPT_BASE_URL}/models", timeout=timeout)
        catalog_response.raise_for_status()
        result["models"] = _catalog_model_ids(catalog_response.json())
        result["ready"] = True
        result["reason"] = ""
    except Exception as exc:
        result["reason"] = str(exc)
    return result


def start_runtime(
    *,
    core_repo: Path | None = None,
    home: Path | None = None,
    wait_seconds: float = 15.0,
) -> dict[str, Any]:
    core_repo = (core_repo or default_core_repo()).resolve()
    home = (home or default_runtime_home()).resolve()
    current = runtime_status(home=home)
    if current.get("ready"):
        return current

    config = _load_runtime_config(home)
    storage_state = Path(str(config.get("storageStatePath") or ""))
    if not _verified_login_state(storage_state):
        raise RuntimeError(
            "Dedicated Dubber-WebGPT chưa có ChatGPT login đã được core xác minh. "
            "Chạy `vi-dubber webgpt-runtime login` và đăng nhập một lần trước khi start."
        )

    cli = _core_cli(core_repo)
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    lock_path = home / "provider.start.lock"
    _acquire_start_lock(lock_path)
    try:
        stdout_file = (logs / "provider.stdout.log").open("a", encoding="utf-8")
        stderr_file = (logs / "provider.stderr.log").open("a", encoding="utf-8")
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        process = subprocess.Popen(
            [_bun_executable(), str(cli), "--home", str(home), "serve"],
            cwd=core_repo,
            env=_runtime_env(home),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=creationflags,
            close_fds=True,
        )
        stdout_file.close()
        stderr_file.close()
        (home / "provider.pid").write_text(str(process.pid), encoding="ascii")
        _write_json_atomic(home / "provider.launch.json", {
            "version": 1,
            "pid": process.pid,
            "startedAt": time.time(),
            "coreRepo": str(core_repo),
            "sourceRevision": _source_revision(core_repo),
            "configSha256": _config_fingerprint(config),
        })

        deadline = time.monotonic() + max(1.0, wait_seconds)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            status = runtime_status(home=home, timeout=1.0)
            if status.get("ready"):
                return status
            time.sleep(0.25)

        detail = ""
        stderr_path = logs / "provider.stderr.log"
        if stderr_path.is_file():
            try:
                detail = stderr_path.read_text(encoding="utf-8", errors="replace")[-1600:].strip()
            except OSError:
                pass
        raise RuntimeError(
            "Dedicated Dubber-WebGPT không lên healthy sau khi start."
            + (f" Log cuối: {detail}" if detail else "")
        )
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def stop_runtime(*, home: Path | None = None, force: bool = False) -> dict[str, Any]:
    home = (home or default_runtime_home()).resolve()
    status = runtime_status(home=home)
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    active_http = int(health.get("active_http_turns") or 0)
    active_browser = int(health.get("active_browser_turns") or 0)
    if (active_http or active_browser) and not force:
        raise RuntimeError(
            f"Runtime còn {active_http} HTTP turn và {active_browser} browser turn; "
            "không stop giữa job. Dùng --force chỉ khi chủ động hủy."
        )

    pid_path = home / "provider.pid"
    receipt_path = home / "provider.launch.json"
    receipt: dict[str, Any] = {}
    if receipt_path.is_file():
        try:
            loaded_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(loaded_receipt, dict): receipt = loaded_receipt
        except (OSError, json.JSONDecodeError):
            receipt = {}
    pid = int(health.get("pid") or 0)
    if not pid and pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            pid = 0
    if pid > 0:
        receipt_pid = int(receipt.get("pid") or 0)
        if receipt_pid and pid != receipt_pid:
            raise RuntimeError(
                "Health endpoint không thuộc process Dedicated Dubber-WebGPT đang được receipt quản lý; "
                "không stop để tránh hạ nhầm process."
            )
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass
    try:
        receipt_path.unlink()
    except FileNotFoundError:
        pass
    return runtime_status(home=home, timeout=0.3)

