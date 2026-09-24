from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
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


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
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
