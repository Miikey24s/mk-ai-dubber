from __future__ import annotations

import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Literal

from .artifacts import atomic_write_json


JobStatus = Literal[
    "queued",
    "running",
    "paused",
    "failed",
    "cancelled",
    "completed",
]

CONTROL_FILE = "control.json"
STATE_FILE = "state.json"
LOCK_FILE = "run.lock"
EVENTS_FILE = "events.jsonl"
EVENT_JOURNAL_VERSION = 1

_EVENT_JOURNAL_LOCK = RLock()
_SENSITIVE_EVENT_KEY = re.compile(
    r"(?:pass(?:word|phrase)?|secret|token|api[_-]?key|authorization|cookie|"
    r"storage[_-]?state|access[_-]?token|refresh[_-]?token|credential)",
    re.IGNORECASE,
)
_ABSOLUTE_PATH = re.compile(r"(?:^[A-Za-z]:[\\/]|^\\\\|^/)")


class PipelineControl(Exception):
    status: JobStatus


class PipelineCancelled(PipelineControl):
    status = "cancelled"


class PipelinePaused(PipelineControl):
    status = "paused"


class JobAlreadyRunning(RuntimeError):
    pass


class InvalidJobControl(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _redact_event_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    """Return a JSON-safe, path/secret-redacted value for the local event journal."""
    if depth > 8:
        return "[redacted-depth]"
    if key and _SENSITIVE_EVENT_KEY.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_event_value(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_event_value(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return "[redacted-path]" if _ABSOLUTE_PATH.search(value) else value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_event_value(str(value), depth=depth + 1)


def _event_lines(path: Path, *, repair_partial: bool = False) -> list[bytes]:
    """Read complete JSONL lines, optionally dropping an incomplete final line."""
    try:
        raw = path.read_bytes()
    except (OSError, FileNotFoundError):
        return []
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        last_newline = raw.rfind(b"\n")
        if repair_partial:
            try:
                with path.open("r+b") as handle:
                    handle.truncate(max(0, last_newline + 1))
            except OSError:
                # The journal is observability only; a read-only or concurrently
                # replaced file must never prevent the state path from progressing.
                pass
        raw = raw[: last_newline + 1]
    return [line for line in raw.splitlines() if line.strip()]


def _valid_event_records(path: Path, *, repair_partial: bool = False) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in _event_lines(path, repair_partial=repair_partial):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("version") != EVENT_JOURNAL_VERSION:
            continue
        seq = value.get("seq")
        event = value.get("event")
        at = value.get("at")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            continue
        if not isinstance(event, str) or not event.strip() or not isinstance(at, str):
            continue
        records.append(value)
    return records


def load_job_events(job_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load valid job events while ignoring corrupt or partial trailing records."""
    if limit is not None and limit <= 0:
        return []
    with _EVENT_JOURNAL_LOCK:
        records = _valid_event_records(job_dir / EVENTS_FILE)
    if limit is not None:
        return records[-limit:]
    return records


def append_job_event(
    job_dir: Path,
    event: str,
    *,
    stage: str | None = None,
    progress: float | None = None,
    attempt: int | None = None,
    payload: Any = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Append one redacted, monotonic event without making it state authority."""
    if not isinstance(event, str) or not event.strip():
        raise ValueError("event must be a non-empty string")
    if attempt is not None and (isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0):
        raise ValueError("attempt must be a non-negative integer or None")
    normalized_progress: float | None = None
    if progress is not None:
        normalized_progress = max(0.0, min(1.0, float(progress)))
    timestamp = at or _now()
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("at must be a non-empty string")
    path = job_dir / EVENTS_FILE
    with _EVENT_JOURNAL_LOCK:
        job_dir.mkdir(parents=True, exist_ok=True)
        previous = _valid_event_records(path, repair_partial=True)
        next_seq = max((int(item["seq"]) for item in previous), default=0) + 1
        safe_event = _redact_event_value(event.strip())
        safe_stage = _redact_event_value(stage) if stage is not None else None
        record: dict[str, Any] = {
            "version": EVENT_JOURNAL_VERSION,
            "seq": next_seq,
            "at": timestamp,
            "event": safe_event,
            "stage": safe_stage,
            "progress": normalized_progress,
            "attempt": attempt,
            "payload": _redact_event_value(payload),
        }
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode(
            "utf-8"
        )
        try:
            with path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            # Preserve the caller's state authority if a best-effort journal is
            # temporarily unavailable. The state update itself is already durable.
            raise
    return record


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        # os.kill(pid, 0) is not a harmless existence probe on Windows: CPython
        # routes non-console signals through TerminateProcess, so merely listing
        # jobs can kill the process that owns a live job lease.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        error_invalid_parameter = 87

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_exit_code_process = kernel32.GetExitCodeProcess
        get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code_process.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == error_invalid_parameter:
                return False
            # Fail closed: access denied or an unfamiliar query failure does not
            # prove that the process died, so keep the lease live.
            return True
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code_process(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        # Fail closed: lack of permission does not prove that the process died.
        return True
    except (OSError, ProcessLookupError):
        return False
    return True


def _lease_pid(lock_path: Path) -> int:
    payload = load_json(lock_path)
    try:
        return int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return 0


def job_lease_is_live(job_dir: Path) -> bool:
    """Return whether the persisted job currently has a live process lease."""
    lock_path = job_dir / LOCK_FILE
    if not lock_path.is_file():
        return False
    return _pid_is_alive(_lease_pid(lock_path))


@contextmanager
def claim_job(job_dir: Path) -> Iterator[None]:
    """Hold an exclusive process-level lease for one content-addressed job."""
    job_dir.mkdir(parents=True, exist_ok=True)
    lock_path = job_dir / LOCK_FILE
    payload = {"version": 1, "pid": os.getpid(), "claimed_at": _now()}

    owned = False
    for attempt in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing_pid = _lease_pid(lock_path)
            if _pid_is_alive(existing_pid):
                raise JobAlreadyRunning(
                    f"Job đang được process PID {existing_pid} xử lý; không tạo duplicate run"
                )
            if attempt == 0:
                lock_path.unlink(missing_ok=True)
                continue
            raise JobAlreadyRunning("Không thể thu hồi stale job lock")
        else:
            try:
                os.write(fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            owned = True
            break

    if not owned:
        raise JobAlreadyRunning("Không thể claim job")
    try:
        yield
    finally:
        current = load_json(lock_path)
        if int(current.get("pid") or 0) == os.getpid():
            lock_path.unlink(missing_ok=True)


def load_job_state(job_dir: Path) -> dict[str, Any]:
    return load_json(job_dir / STATE_FILE)


def reconcile_job_state(job_dir: Path) -> dict[str, Any]:
    """Recover a persisted running state when its process lease is no longer live."""
    state = load_job_state(job_dir)
    if not state or state.get("status") != "running" or job_lease_is_live(job_dir):
        return state
    return update_job_state(
        job_dir,
        status="paused",
        message="Lần chạy trước đã dừng ngoài ý muốn; có thể bấm Tiếp tục để resume.",
        metadata={
            "recovered_from_stale_running": True,
            "recovery_reason": "missing_or_stale_run_lease",
        },
    )


def update_job_state(
    job_dir: Path,
    *,
    status: JobStatus | None = None,
    stage: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = job_dir / STATE_FILE
    previous = load_json(path)
    created_at = str(previous.get("created_at") or _now())
    next_status = str(status or previous.get("status") or "queued")
    payload: dict[str, Any] = {
        "version": 1,
        "status": next_status,
        "created_at": created_at,
        "updated_at": _now(),
        "stage": stage if stage is not None else previous.get("stage"),
        "progress": (
            max(0.0, min(1.0, float(progress)))
            if progress is not None
            else float(previous.get("progress") or 0.0)
        ),
        "message": message if message is not None else previous.get("message"),
        "metadata": {**dict(previous.get("metadata") or {}), **dict(metadata or {})},
    }
    if error is not None:
        payload["error"] = error
    elif "error" in previous and next_status in {"failed", "paused", "cancelled"}:
        payload["error"] = previous["error"]
    if result is not None:
        payload["result"] = result
    elif "result" in previous:
        payload["result"] = previous["result"]
    atomic_write_json(path, payload)
    # state.json remains the source of truth; the journal is a durable,
    # redacted observability trail and must never make a state transition fail.
    try:
        raw_attempt = payload["metadata"].get("attempt")
        event_attempt = raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) else None
        append_job_event(
            job_dir,
            "state.updated",
            stage=payload.get("stage"),
            progress=payload.get("progress"),
            attempt=event_attempt,
            payload={
                "status": payload.get("status"),
                "message": payload.get("message"),
                "has_error": "error" in payload,
                "has_result": "result" in payload,
            },
        )
    except (OSError, TypeError, ValueError):
        pass
    return payload


def request_control(job_dir: Path, action: Literal["cancel", "pause", "run"]) -> None:
    if action not in {"cancel", "pause", "run"}:
        raise ValueError(f"Job control action không hợp lệ: {action!r}")
    atomic_write_json(
        job_dir / CONTROL_FILE,
        {
            "version": 1,
            "action": action,
            "updated_at": _now(),
        },
    )


def clear_control(job_dir: Path) -> None:
    (job_dir / CONTROL_FILE).unlink(missing_ok=True)


def check_control(job_dir: Path) -> None:
    path = job_dir / CONTROL_FILE
    if not path.exists():
        return
    payload = load_json(path)
    action = payload.get("action")
    if action not in {"cancel", "pause", "run"}:
        raise InvalidJobControl("Job control file bị hỏng hoặc có action không hợp lệ")
    if action == "cancel":
        raise PipelineCancelled("Job đã được yêu cầu hủy")
    if action == "pause":
        raise PipelinePaused("Job đã được yêu cầu tạm dừng")


def list_job_states(work_dir: Path) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for job_dir in work_dir.glob("job-*"):
        state = reconcile_job_state(job_dir)
        if not state:
            continue
        state = {**state, "job_dir": str(job_dir)}
        states.append(state)
    return sorted(states, key=lambda item: str(item.get("updated_at") or ""), reverse=True)
