import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from vi_dubber.scheduler import BoundedExecutor, ResourceScheduler


def test_bounded_executor_exposes_running_and_queued_depth() -> None:
    release = threading.Event()
    started = threading.Event()

    def blocking(value: int) -> int:
        started.set()
        release.wait(timeout=2)
        return value

    with BoundedExecutor("test", max_workers=1, max_pending=1) as queue:
        first = queue.submit(blocking, 1)
        assert started.wait(timeout=1)
        second = queue.submit(blocking, 2)
        snapshot = queue.snapshot()
        assert snapshot.running == 1
        assert snapshot.queued == 1
        release.set()
        assert first.result(timeout=1) == 1
        assert second.result(timeout=1) == 2


def test_backpressure_blocks_submitter_until_a_slot_is_free() -> None:
    release = threading.Event()
    submit_finished = threading.Event()

    def blocking() -> str:
        release.wait(timeout=2)
        return "done"

    with BoundedExecutor("test", max_workers=1, max_pending=0) as queue:
        first = queue.submit(blocking)

        def submit_second():
            future = queue.submit(lambda: "second")
            submit_finished.set()
            return future.result(timeout=1)

        with ThreadPoolExecutor(max_workers=1) as helper:
            pending = helper.submit(submit_second)
            assert not submit_finished.wait(timeout=0.05)
            release.set()
            assert first.result(timeout=1) == "done"
            assert submit_finished.wait(timeout=1)
            assert pending.result(timeout=1) == "second"


def test_resource_scheduler_keeps_resource_limits_separate() -> None:
    with ResourceScheduler(
        webgpt_workers=2,
        cpu_workers=1,
        qa_workers=1,
        max_pending_per_queue=3,
    ) as scheduler:
        snapshot = scheduler.snapshot()
        assert snapshot["webgpt"]["max_workers"] == 2
        assert snapshot["cpu"]["max_workers"] == 1
        assert snapshot["qa"]["max_workers"] == 1
        assert scheduler.submit("cpu", lambda: 7).result(timeout=1) == 7


def test_resource_scheduler_rejects_unknown_resource() -> None:
    with ResourceScheduler() as scheduler:
        with pytest.raises(ValueError, match="unsupported scheduler resource"):
            scheduler.submit("gpu", lambda: None)
