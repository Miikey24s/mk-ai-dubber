from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    name: str
    max_workers: int
    max_pending: int
    running: int
    queued: int


class BoundedExecutor(Generic[T]):
    """Thread executor with hard backpressure instead of an unbounded work queue."""

    def __init__(self, name: str, *, max_workers: int, max_pending: int) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if max_pending < 0:
            raise ValueError("max_pending must be non-negative")
        self.name = str(name)
        self.max_workers = int(max_workers)
        self.max_pending = int(max_pending)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=f"vi-dubber-{self.name}",
        )
        self._slots = threading.BoundedSemaphore(self.max_workers + self.max_pending)
        self._lock = threading.Lock()
        self._running = 0
        self._submitted = 0
        self._closed = False

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"scheduler queue {self.name!r} is closed")
        self._slots.acquire()
        with self._lock:
            if self._closed:
                self._slots.release()
                raise RuntimeError(f"scheduler queue {self.name!r} is closed")
            self._submitted += 1

        def invoke() -> T:
            with self._lock:
                self._running += 1
            try:
                return fn(*args, **kwargs)
            finally:
                with self._lock:
                    self._running -= 1

        try:
            future = self._executor.submit(invoke)
        except BaseException:
            with self._lock:
                self._submitted -= 1
            self._slots.release()
            raise

        def release_slot(_future: Future[T]) -> None:
            with self._lock:
                self._submitted -= 1
            self._slots.release()

        future.add_done_callback(release_slot)
        return future

    def snapshot(self) -> QueueSnapshot:
        with self._lock:
            running = self._running
            queued = max(0, self._submitted - running)
        return QueueSnapshot(
            name=self.name,
            max_workers=self.max_workers,
            max_pending=self.max_pending,
            running=running,
            queued=queued,
        )

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def __enter__(self) -> "BoundedExecutor[T]":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown(wait=True, cancel_futures=exc is not None)


class ResourceScheduler:
    """Small shared scheduler for work that uses different bottleneck resources."""

    def __init__(
        self,
        *,
        webgpt_workers: int = 2,
        cpu_workers: int = 2,
        qa_workers: int = 1,
        max_pending_per_queue: int = 2,
    ) -> None:
        self._queues = {
            "webgpt": BoundedExecutor(
                "webgpt",
                max_workers=webgpt_workers,
                max_pending=max_pending_per_queue,
            ),
            "cpu": BoundedExecutor(
                "cpu",
                max_workers=cpu_workers,
                max_pending=max_pending_per_queue,
            ),
            "qa": BoundedExecutor(
                "qa",
                max_workers=qa_workers,
                max_pending=max_pending_per_queue,
            ),
        }

    def submit(self, resource: str, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
        try:
            queue = self._queues[resource]
        except KeyError as exc:
            raise ValueError(f"unsupported scheduler resource: {resource!r}") from exc
        return queue.submit(fn, *args, **kwargs)

    def snapshot(self) -> dict[str, dict[str, int | str]]:
        return {
            name: {
                "name": item.name,
                "max_workers": item.max_workers,
                "max_pending": item.max_pending,
                "running": item.running,
                "queued": item.queued,
            }
            for name, queue in self._queues.items()
            for item in [queue.snapshot()]
        }

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        for queue in self._queues.values():
            queue.shutdown(wait=wait, cancel_futures=cancel_futures)

    def __enter__(self) -> "ResourceScheduler":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown(wait=True, cancel_futures=exc is not None)
