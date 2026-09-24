from __future__ import annotations

import importlib
import json
import math
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal


RunKind = Literal["cold", "warm", "resume"]

STANDARD_COUNTERS = (
    "source_segments",
    "speech_turns",
    "translation_calls",
    "webgpt_batches",
    "rewrite_calls",
    "typesafe_requests",
    "typesafe_tokens",
    "tts_inferences",
    "tts_batches",
    "cache_hits",
    "cache_misses",
    "qa_repairs",
)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def cuda_memory_snapshot(*, enabled: bool = True) -> dict[str, Any]:
    """Return process-local PyTorch CUDA allocator metrics.

    These values are not whole-GPU VRAM usage and can exclude subprocesses or
    non-PyTorch allocators.
    """
    if not enabled:
        return {"available": False, "reason": "disabled"}

    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        return {"available": False, "reason": f"torch_unavailable:{type(exc).__name__}"}

    try:
        if not torch.cuda.is_available():
            return {"available": False, "reason": "cuda_unavailable"}
        device_index = int(torch.cuda.current_device())
        device_name = str(torch.cuda.get_device_name(device_index))
        return {
            "available": True,
            "scope": "torch_allocator_process",
            "device_index": device_index,
            "device_name": device_name,
            "allocated_bytes": int(torch.cuda.memory_allocated(device_index)),
            "reserved_bytes": int(torch.cuda.memory_reserved(device_index)),
            "peak_allocated_bytes": int(torch.cuda.max_memory_allocated(device_index)),
            "peak_reserved_bytes": int(torch.cuda.max_memory_reserved(device_index)),
        }
    except Exception as exc:
        return {"available": False, "reason": f"cuda_probe_failed:{type(exc).__name__}"}


class MetricsRecorder:
    """Thread-safe wall-time and counter recorder for benchmark/control-plane metrics."""

    schema_version = 1

    def __init__(
        self,
        *,
        run_kind: RunKind = "cold",
        cache: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        enable_cuda: bool = False,
        clock: Callable[[], float] = time.perf_counter,
        started_at: float | None = None,
        failure_path: Path | None = None,
    ) -> None:
        if run_kind not in {"cold", "warm", "resume"}:
            raise ValueError(f"Unsupported run_kind: {run_kind}")
        self._run_kind = run_kind
        self._cache = _json_safe(dict(cache or {}))
        self._metadata = _json_safe(dict(metadata or {}))
        self._enable_cuda = enable_cuda
        self._clock = clock
        self._failure_path = failure_path
        self._lock = threading.Lock()
        self._started = clock() if started_at is None else float(started_at)
        self._finished_elapsed: float | None = None
        self._final_resources: dict[str, Any] | None = None
        self._active_stages = 0
        self._stages: dict[str, dict[str, Any]] = {}
        # Unknown is different from zero. Counters appear only after the
        # integration explicitly records them.
        self._counters: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Measure one stage invocation and preserve timing even if it raises."""
        if not name or not name.strip():
            raise ValueError("Stage name must not be empty")
        with self._lock:
            if self._finished_elapsed is not None:
                raise RuntimeError("MetricsRecorder is already finished")
            self._active_stages += 1

        started = self._clock()
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = max(0.0, self._clock() - started)
            with self._lock:
                item = self._stages.setdefault(
                    name,
                    {"wall_seconds": 0.0, "calls": 0, "failed_calls": 0},
                )
                item["wall_seconds"] += elapsed
                item["calls"] += 1
                if failed:
                    item["failed_calls"] += 1
                self._active_stages -= 1
            if failed and self._failure_path is not None:
                try:
                    self.write_snapshot(self._failure_path, status="failed")
                except Exception:
                    # Metrics persistence must never replace the pipeline's
                    # original exception.
                    pass

    def increment(self, name: str, amount: int = 1) -> None:
        if not name or not name.strip():
            raise ValueError("Counter name must not be empty")
        if not isinstance(amount, int):
            raise TypeError("Counter amount must be an int")
        with self._lock:
            if self._finished_elapsed is not None:
                raise RuntimeError("MetricsRecorder is already finished")
            self._counters[name] = self._counters.get(name, 0) + amount

    def set_counter(self, name: str, value: int) -> None:
        if not name or not name.strip():
            raise ValueError("Counter name must not be empty")
        if not isinstance(value, int):
            raise TypeError("Counter value must be an int")
        with self._lock:
            if self._finished_elapsed is not None:
                raise RuntimeError("MetricsRecorder is already finished")
            self._counters[name] = value

    def finish(self) -> None:
        with self._lock:
            if self._finished_elapsed is None:
                if self._active_stages:
                    raise RuntimeError("Cannot finish metrics while a stage is active")
                self._finished_elapsed = max(0.0, self._clock() - self._started)
                self._final_resources = {
                    "torch_cuda_allocator": cuda_memory_snapshot(enabled=self._enable_cuda)
                }

    def snapshot(self, *, status: str | None = None) -> dict[str, Any]:
        with self._lock:
            elapsed = self._finished_elapsed
            if elapsed is None:
                elapsed = max(0.0, self._clock() - self._started)
            stages = {
                name: {
                    "wall_seconds": float(item["wall_seconds"]),
                    "calls": int(item["calls"]),
                    "failed_calls": int(item["failed_calls"]),
                }
                for name, item in sorted(self._stages.items())
            }
            counters = {name: int(value) for name, value in sorted(self._counters.items())}
            resources = self._final_resources

        return {
            "schema_version": self.schema_version,
            "status": status or ("complete" if self._finished_elapsed is not None else "running"),
            "run": {
                "kind": self._run_kind,
                "cache": self._cache,
                "metadata": self._metadata,
            },
            "total_wall_seconds": float(elapsed),
            "stages": stages,
            "counter_schema": list(STANDARD_COUNTERS),
            "counters": counters,
            "resources": resources
            if resources is not None
            else {"torch_cuda_allocator": cuda_memory_snapshot(enabled=self._enable_cuda)},
        }

    def write_snapshot(self, path: Path, *, status: str | None = None) -> None:
        payload = self.snapshot(status=status)
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
