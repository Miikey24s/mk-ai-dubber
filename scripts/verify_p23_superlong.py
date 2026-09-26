"""Deterministic/synthetic P23 super-long acceptance harness.

The harness exercises real bounded-scheduler and per-chunk manifest primitives with
a generated 6h+ timeline. It intentionally does not claim real-media throughput,
VRAM behavior, WebGPT reliability, or FFmpeg bitwise parity for a six-hour render.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
import threading
import time
from typing import Iterable, Sequence

from vi_dubber.longform import MacroChunk
from vi_dubber.longform_state import (
    chunk_stage_fingerprint,
    commit_chunk_stage,
    load_chunk_stage,
)
from vi_dubber.scheduler import BoundedExecutor


@dataclass(frozen=True)
class SyntheticChunk:
    index: int
    start: float
    end: float
    state: str = "pending"


def build_synthetic_timeline(
    hours: float = 6.25,
    chunk_seconds: int = 900,
) -> list[SyntheticChunk]:
    if hours < 6:
        raise ValueError("P23 super-long fixture must cover at least six hours")
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    total = int(hours * 3600)
    return [
        SyntheticChunk(i, float(i * chunk_seconds), float(min(total, (i + 1) * chunk_seconds)))
        for i in range((total + chunk_seconds - 1) // chunk_seconds)
    ]


def validate_order(chunks: Iterable[SyntheticChunk]) -> bool:
    items = list(chunks)
    if not items or items[0].index != 0 or items[0].start != 0.0:
        return False
    return all(
        a.index + 1 == b.index and abs(a.end - b.start) < 1e-9
        for a, b in zip(items, items[1:])
    )


def deterministic_assembly_digest(
    chunks: Sequence[SyntheticChunk],
    completion_order: Sequence[int],
) -> str:
    """Return a stable synthetic assembly digest independent of completion order."""
    by_index = {chunk.index: chunk for chunk in chunks}
    if set(completion_order) != set(by_index) or len(completion_order) != len(by_index):
        raise ValueError("completion_order must contain every chunk index exactly once")
    completed = [by_index[index] for index in completion_order]
    timeline = sorted(completed, key=lambda item: (item.start, item.end, item.index))
    if not validate_order(timeline):
        raise ValueError("synthetic timeline is not contiguous and ordered")
    payload = [
        {"index": item.index, "start": item.start, "end": item.end}
        for item in timeline
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def simulate_fault_resume(chunks: list[SyntheticChunk], failed_index: int) -> dict[str, object]:
    if failed_index < 0 or failed_index >= len(chunks):
        raise ValueError("failed_index must identify a fixture chunk")
    completed = {c.index for c in chunks if c.index < failed_index}
    resumed = [c.index for c in chunks if c.index >= failed_index]
    return {
        "completed_preserved": completed,
        "resume_from": min(resumed) if resumed else None,
        "reused_after_fault": not any(i >= failed_index for i in completed),
    }


def _as_macro_chunk(chunk: SyntheticChunk) -> MacroChunk:
    return MacroChunk(
        chunk_id=f"chunk_{chunk.index + 1:04d}",
        index=chunk.index,
        source_start=chunk.start,
        source_end=chunk.end,
        context_start=max(0.0, chunk.start - 2.0),
        context_end=chunk.end + 2.0,
        boundary_reason="synthetic",
    )


def _manifest_lookup_args(chunk: SyntheticChunk) -> tuple[MacroChunk, str, str]:
    macro = _as_macro_chunk(chunk)
    fingerprint = chunk_stage_fingerprint(
        macro,
        "asr",
        inputs={"fixture": "p23-superlong", "index": chunk.index},
        config={"mode": "synthetic"},
        versions={"probe": 1},
    )
    return macro, "asr", fingerprint


def run_manifest_fault_resume_probe(
    root: Path,
    chunks: Sequence[SyntheticChunk],
    failed_index: int,
) -> dict[str, object]:
    """Crash at one chunk, then resume through real P23 manifest validation."""
    if failed_index < 0 or failed_index >= len(chunks):
        raise ValueError("failed_index must identify a fixture chunk")
    job_dir = Path(root) / "manifest-resume"
    artifact_hashes_before: dict[int, str] = {}

    def artifact_for(chunk: SyntheticChunk) -> Path:
        return job_dir / "chunks" / f"chunk_{chunk.index + 1:04d}" / "synthetic.bin"

    # First process lifetime: commit only chunks strictly before the fault.
    for chunk in chunks[:failed_index]:
        macro, _stage, _fingerprint = _manifest_lookup_args(chunk)
        artifact = artifact_for(chunk)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(f"committed:{chunk.index}".encode("ascii"))
        commit_chunk_stage(
            job_dir,
            macro,
            "asr",
            inputs={"fixture": "p23-superlong", "index": chunk.index},
            artifacts=[artifact],
            config={"mode": "synthetic"},
            versions={"probe": 1},
        )
        artifact_hashes_before[chunk.index] = hashlib.sha256(artifact.read_bytes()).hexdigest()

    # Simulate dying after the failed chunk artifact write but before manifest commit.
    failed_chunk = chunks[failed_index]
    failed_macro, _stage, failed_fingerprint = _manifest_lookup_args(failed_chunk)
    failed_artifact = artifact_for(failed_chunk)
    failed_artifact.parent.mkdir(parents=True, exist_ok=True)
    failed_artifact.write_bytes(b"orphaned-before-manifest-commit")
    failed_reusable_before_resume = (
        load_chunk_stage(job_dir, failed_macro, "asr", failed_fingerprint) is not None
    )

    reused: list[int] = []
    recomputed: list[int] = []
    for chunk in chunks:
        macro, stage, fingerprint = _manifest_lookup_args(chunk)
        artifact = artifact_for(chunk)
        if load_chunk_stage(job_dir, macro, stage, fingerprint) is not None:
            reused.append(chunk.index)
            continue
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(f"resumed:{chunk.index}".encode("ascii"))
        commit_chunk_stage(
            job_dir,
            macro,
            stage,
            inputs={"fixture": "p23-superlong", "index": chunk.index},
            artifacts=[artifact],
            config={"mode": "synthetic"},
            versions={"probe": 1},
        )
        recomputed.append(chunk.index)

    preserved_hashes = {
        index: hashlib.sha256(artifact_for(chunks[index]).read_bytes()).hexdigest()
        for index in range(failed_index)
    }
    all_committed = all(
        load_chunk_stage(job_dir, *_manifest_lookup_args(chunk)) is not None
        for chunk in chunks
    )
    return {
        "failed_index": failed_index,
        "failed_reusable_before_resume": failed_reusable_before_resume,
        "reused_indices": reused,
        "recomputed_indices": recomputed,
        "completed_sibling_hashes_preserved": preserved_hashes == artifact_hashes_before,
        "all_chunks_committed_after_resume": all_committed,
    }


def _process_rss_bytes() -> int:
    if os.name == "nt":
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process_handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
            process_handle,
            ctypes.byref(counters),
            counters.cb,
        ):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return int(counters.WorkingSetSize)

    import resource

    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if os.uname().sysname == "Darwin" else value * 1024


def run_bounded_resource_probe(
    root: Path,
    chunks: Sequence[SyntheticChunk],
    *,
    max_workers: int = 1,
    max_pending: int = 2,
    payload_bytes: int = 1024 * 1024,
    downstream_delay_seconds: float = 0.004,
) -> dict[str, object]:
    """Exercise real backpressure while recording process RSS and temp-disk peaks."""
    if max_workers != 1:
        raise ValueError("current deterministic probe expects one downstream worker")
    spool = Path(root) / "bounded-spool"
    spool.mkdir(parents=True, exist_ok=True)
    release_first = threading.Event()
    first_started = threading.Event()
    producer_finished = threading.Event()
    lock = threading.Lock()
    peak_rss = _process_rss_bytes()
    rss_before = peak_rss
    peak_temp_disk = 0
    completion_order: list[int] = []
    futures = []

    def consume(chunk: SyntheticChunk) -> int:
        nonlocal peak_rss, peak_temp_disk
        if chunk.index == 0:
            first_started.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("resource probe did not release first downstream task")
        payload = bytearray(payload_bytes)
        payload[0:8] = int(chunk.index).to_bytes(8, "little", signed=False)
        with lock:
            peak_rss = max(peak_rss, _process_rss_bytes())
        temp_path = spool / f"chunk-{chunk.index:04d}.part"
        temp_path.write_bytes(payload)
        with lock:
            temp_bytes = sum(path.stat().st_size for path in spool.glob("*.part"))
            peak_temp_disk = max(peak_temp_disk, temp_bytes)
        time.sleep(downstream_delay_seconds)
        digest = hashlib.sha256(payload).hexdigest()
        temp_path.unlink(missing_ok=True)
        (spool / f"chunk-{chunk.index:04d}.json").write_text(
            json.dumps({"index": chunk.index, "sha256": digest}, sort_keys=True),
            encoding="utf-8",
        )
        with lock:
            completion_order.append(chunk.index)
            peak_rss = max(peak_rss, _process_rss_bytes())
        return chunk.index

    with BoundedExecutor(
        "p23-superlong-downstream",
        max_workers=max_workers,
        max_pending=max_pending,
    ) as queue:
        def produce() -> None:
            try:
                for chunk in chunks:
                    futures.append(queue.submit(consume, chunk))
            finally:
                producer_finished.set()

        producer = threading.Thread(target=produce, name="p23-superlong-producer", daemon=True)
        producer.start()
        if not first_started.wait(timeout=2):
            raise TimeoutError("downstream worker did not start")

        deadline = time.monotonic() + 2.0
        saturated = queue.snapshot()
        while saturated.queued < max_pending and time.monotonic() < deadline:
            time.sleep(0.002)
            saturated = queue.snapshot()
        producer_was_blocked = saturated.queued == max_pending and not producer_finished.is_set()
        release_first.set()
        producer.join(timeout=10)
        if producer.is_alive():
            raise TimeoutError("producer did not drain after downstream release")
        for future in futures:
            future.result(timeout=5)

    receipt_bytes = sum(path.stat().st_size for path in spool.glob("*.json"))
    return {
        "queue": {
            "max_workers": max_workers,
            "max_pending": max_pending,
            "saturated_running": saturated.running,
            "saturated_queued": saturated.queued,
            "producer_was_blocked": producer_was_blocked,
        },
        "rss": {
            "before_bytes": rss_before,
            "peak_bytes": peak_rss,
            "delta_bytes": max(0, peak_rss - rss_before),
        },
        "disk": {
            "payload_bytes_per_chunk": payload_bytes,
            "peak_temp_bytes": peak_temp_disk,
            "retained_receipt_bytes": receipt_bytes,
            "temp_files_after": len(list(spool.glob("*.part"))),
        },
        "completion_order": completion_order,
        "completed": len(completion_order),
    }


def build_receipt(root: Path) -> dict[str, object]:
    chunks = build_synthetic_timeline()
    failed_index = len(chunks) // 2
    forward = [chunk.index for chunk in chunks]
    reverse = list(reversed(forward))
    interleaved = forward[::2] + forward[1::2]
    digest = deterministic_assembly_digest(chunks, forward)
    ordering_digests = {
        "forward": digest,
        "reverse": deterministic_assembly_digest(chunks, reverse),
        "interleaved": deterministic_assembly_digest(chunks, interleaved),
    }
    resource_probe = run_bounded_resource_probe(root, chunks)
    manifest_probe = run_manifest_fault_resume_probe(root, chunks, failed_index)
    return {
        "schema_version": 1,
        "scope": "synthetic-local-p23-superlong",
        "claims_excluded": [
            "real 6h media throughput",
            "VRAM/OOM acceptance",
            "live WebGPT pressure/restart acceptance",
            "FFmpeg bitwise final-media parity",
        ],
        "timeline": {
            "hours": chunks[-1].end / 3600,
            "chunks": len(chunks),
            "ordered": validate_order(chunks),
        },
        "backpressure_resource_probe": resource_probe,
        "manifest_fault_resume_probe": manifest_probe,
        "ordering": {
            "digests": ordering_digests,
            "completion_order_independent": len(set(ordering_digests.values())) == 1,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="vi-dubber-p23-superlong-") as temp:
        receipt = build_receipt(Path(temp))
    encoded = json.dumps(receipt, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
