"""P23 real-media final assembly acceptance harness.

This probe creates a real local media timeline, runs VI Dubber's production
``mux_dubbed_video`` primitive, and records process-tree RSS, workspace growth,
system GPU-memory observations, final container metadata, loudness, and hashes.

It is deliberately scoped to the final-media/FFmpeg side of P23. Passing this
probe does not claim 6h ASR/TTS/translation throughput or whole-pipeline VRAM
acceptance; those remain separate gates.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Sequence

from vi_dubber.longform import MacroChunk
from vi_dubber.longform_state import (
    chunk_stage_fingerprint,
    commit_chunk_stage,
    load_chunk_stage,
)
from vi_dubber.media import (
    ffmpeg_exe,
    ffprobe_exe,
    measure_mix_metrics,
    mux_dubbed_video,
)
from vi_dubber.scheduler import BoundedExecutor


SUPERLONG_MIN_SECONDS = 6 * 3600
DEFAULT_HOURS = 6.01
DEFAULT_MAX_RSS_MIB = 1024.0
DEFAULT_MAX_WORKSPACE_GROWTH_MIB = 1536.0
DEFAULT_MAX_PROCESS_GPU_MIB = 512.0
DEFAULT_CONTROL_CHUNK_SECONDS = 15 * 60.0
DEFAULT_CONTROL_MAX_RSS_DELTA_MIB = 128.0
DEFAULT_CONTROL_MAX_TEMP_DISK_MIB = 16.0
DEFAULT_CONTROL_PAYLOAD_BYTES = 512 * 1024
RESTART_HELPER_EXIT_CODE = 86


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in Path(root).rglob("*") if path.is_file())


def _run_checked(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(args)}\n{result.stderr.strip()}"
        )


def generate_real_media_fixture(root: Path, duration_seconds: float) -> dict[str, Path]:
    """Create compact but real encoded media with a 6h-capable timeline."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ffmpeg = str(ffmpeg_exe())
    duration = f"{duration_seconds:.3f}"
    video = root / "source.mp4"
    background = root / "background.m4a"
    voice = root / "voice.m4a"

    _run_checked(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=1",
            "-t",
            duration,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "45",
            "-g",
            "60",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ]
    )
    for output, frequency, gain in (
        (background, 220, "0.025"),
        (voice, 440, "0.080"),
    ):
        _run_checked(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000",
                "-t",
                duration,
                "-af",
                f"volume={gain}",
                "-c:a",
                "aac",
                "-b:a",
                "32k",
                str(output),
            ]
        )
    return {"video": video, "background": background, "voice": voice}


def probe_media(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(ffprobe_exe()),
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _media_duration(probe: dict[str, Any]) -> float:
    return float((probe.get("format") or {}).get("duration") or 0.0)


def _stream_codecs(probe: dict[str, Any]) -> dict[str, list[str]]:
    codecs: dict[str, list[str]] = {"video": [], "audio": []}
    for stream in probe.get("streams") or []:
        kind = str(stream.get("codec_type") or "")
        if kind in codecs:
            codecs[kind].append(str(stream.get("codec_name") or ""))
    return codecs


def build_media_timeline(
    duration_seconds: float,
    *,
    chunk_seconds: float = DEFAULT_CONTROL_CHUNK_SECONDS,
) -> list[MacroChunk]:
    """Build deterministic instrumented work units over the real media duration."""
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    count = max(1, int(math.ceil(duration_seconds / chunk_seconds)))
    chunks: list[MacroChunk] = []
    for index in range(count):
        start = index * chunk_seconds
        end = min(duration_seconds, (index + 1) * chunk_seconds)
        chunks.append(
            MacroChunk(
                chunk_id=f"chunk_{index + 1:04d}",
                index=index,
                source_start=float(start),
                source_end=float(end),
                context_start=max(0.0, float(start) - 2.0),
                context_end=min(float(duration_seconds), float(end) + 2.0),
                boundary_reason="instrumented-real-media",
            )
        )
    return chunks


def _validate_timeline(chunks: Sequence[MacroChunk], duration_seconds: float) -> bool:
    if not chunks or chunks[0].index != 0 or abs(chunks[0].source_start) > 1e-9:
        return False
    if abs(chunks[-1].source_end - duration_seconds) > 1e-6:
        return False
    return all(
        left.index + 1 == right.index
        and abs(left.source_end - right.source_start) <= 1e-9
        for left, right in zip(chunks, chunks[1:])
    )


def deterministic_timeline_digest(
    chunks: Sequence[MacroChunk],
    completion_order: Sequence[int],
) -> str:
    by_index = {chunk.index: chunk for chunk in chunks}
    if len(completion_order) != len(by_index) or set(completion_order) != set(by_index):
        raise ValueError("completion_order must contain every chunk index exactly once")
    timeline = sorted(
        (by_index[index] for index in completion_order),
        key=lambda chunk: (chunk.source_start, chunk.source_end, chunk.index),
    )
    duration_seconds = timeline[-1].source_end
    if not _validate_timeline(timeline, duration_seconds):
        raise ValueError("chunk timeline is not contiguous")
    payload = [
        {
            "chunk_id": chunk.chunk_id,
            "index": chunk.index,
            "start": chunk.source_start,
            "end": chunk.source_end,
        }
        for chunk in timeline
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _gpu_memory_snapshot() -> dict[str, float] | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    result = subprocess.run(
        [
            executable,
            "--query-gpu=memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    first = result.stdout.strip().splitlines()[0]
    used, total = [float(part.strip()) for part in first.split(",", 1)]
    return {"used_mib": used, "total_mib": total}


class _PdhFmtCounterValueUnion(ctypes.Union):
    _fields_ = [
        ("longValue", wintypes.LONG),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
        ("AnsiStringValue", ctypes.c_char_p),
        ("WideStringValue", wintypes.LPWSTR),
    ]


class _PdhFmtCounterValue(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _PdhFmtCounterValueUnion)]


class _PdhFmtCounterValueItemW(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _PdhFmtCounterValue)]


class _WindowsGpuProcessMemorySampler:
    """Sample Windows per-process dedicated GPU memory through PDH."""

    PDH_FMT_DOUBLE = 0x00000200
    PDH_MORE_DATA = 0x800007D2
    COUNTER_PATH = r"\GPU Process Memory(*)\Dedicated Usage"
    INSTANCE_PID = re.compile(r"^pid_(\d+)_", re.IGNORECASE)

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh", use_last_error=True)
        self._query = wintypes.HANDLE()
        self._counter = wintypes.HANDLE()

        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhOpenQueryW.restype = wintypes.DWORD
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.DWORD
        self._pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCollectQueryData.restype = wintypes.DWORD
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.DWORD
        self._pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCloseQuery.restype = wintypes.DWORD

        status = self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query))
        if status != 0:
            raise OSError(f"PdhOpenQueryW failed with status 0x{status:08x}")
        status = self._pdh.PdhAddEnglishCounterW(
            self._query,
            self.COUNTER_PATH,
            0,
            ctypes.byref(self._counter),
        )
        if status != 0:
            self.close()
            raise OSError(f"PdhAddEnglishCounterW failed with status 0x{status:08x}")

    def sample(self) -> dict[int, int]:
        status = self._pdh.PdhCollectQueryData(self._query)
        if status != 0:
            raise OSError(f"PdhCollectQueryData failed with status 0x{status:08x}")

        buffer_size = wintypes.DWORD(0)
        item_count = wintypes.DWORD(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            self._counter,
            self.PDH_FMT_DOUBLE,
            ctypes.byref(buffer_size),
            ctypes.byref(item_count),
            None,
        )
        if status not in (0, self.PDH_MORE_DATA):
            raise OSError(f"PdhGetFormattedCounterArrayW(size) failed with status 0x{status:08x}")
        if buffer_size.value == 0 or item_count.value == 0:
            return {}

        buffer = ctypes.create_string_buffer(buffer_size.value)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            self._counter,
            self.PDH_FMT_DOUBLE,
            ctypes.byref(buffer_size),
            ctypes.byref(item_count),
            ctypes.cast(buffer, ctypes.c_void_p),
        )
        if status != 0:
            raise OSError(f"PdhGetFormattedCounterArrayW(data) failed with status 0x{status:08x}")

        items = ctypes.cast(buffer, ctypes.POINTER(_PdhFmtCounterValueItemW))
        by_pid: dict[int, int] = {}
        for index in range(item_count.value):
            item = items[index]
            if item.FmtValue.CStatus != 0 or not item.szName:
                continue
            match = self.INSTANCE_PID.match(item.szName)
            if match is None:
                continue
            pid = int(match.group(1))
            value = max(0, int(round(float(item.FmtValue.doubleValue))))
            by_pid[pid] = by_pid.get(pid, 0) + value
        return by_pid

    def close(self) -> None:
        if self._query:
            self._pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()


def _open_gpu_process_memory_sampler() -> tuple[_WindowsGpuProcessMemorySampler | None, str, str | None]:
    if os.name != "nt":
        return None, "unavailable", "process GPU attribution is only implemented for Windows PDH"
    try:
        return _WindowsGpuProcessMemorySampler(), "windows-pdh-dedicated-usage", None
    except (OSError, AttributeError) as exc:
        return None, "windows-pdh-dedicated-usage", str(exc)


def _windows_process_parent_map() -> dict[int, int]:
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return {}
    parents: dict[int, int] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return {}
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def _windows_rss_for_pid(pid: int) -> int:
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
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

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return 0
    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0
        return int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def _process_tree_pids(root_pid: int) -> set[int]:
    if os.name == "nt":
        parents = _windows_process_parent_map()
        descendants = {int(root_pid)}
        changed = True
        while changed:
            changed = False
            for pid, parent in parents.items():
                if parent in descendants and pid not in descendants:
                    descendants.add(pid)
                    changed = True
        return descendants

    proc = Path("/proc")
    if proc.is_dir():
        descendants = {int(root_pid)}
        changed = True
        while changed:
            changed = False
            for child_stat in proc.glob("[0-9]*/stat"):
                try:
                    raw = child_stat.read_text(encoding="utf-8")
                    close = raw.rfind(")")
                    fields = raw[close + 2 :].split()
                    parent = int(fields[1])
                    pid = int(child_stat.parent.name)
                except (OSError, ValueError, IndexError):
                    continue
                if parent in descendants and pid not in descendants:
                    descendants.add(pid)
                    changed = True
        return descendants
    return {int(root_pid)}


def _process_tree_rss_bytes(root_pid: int) -> int:
    descendants = _process_tree_pids(root_pid)
    if os.name == "nt":
        return sum(_windows_rss_for_pid(pid) for pid in descendants)

    proc = Path("/proc")
    if proc.is_dir():
        page = os.sysconf("SC_PAGE_SIZE")
        total = 0
        for pid in descendants:
            try:
                statm = (proc / str(pid) / "statm").read_text(encoding="utf-8").split()
                total += int(statm[1]) * page
            except (OSError, ValueError, IndexError):
                pass
        return total
    return 0


def _mux_helper(args: argparse.Namespace) -> int:
    mux_dubbed_video(
        Path(args.video),
        Path(args.background),
        Path(args.voice),
        Path(args.output),
        background_gain_db=float(args.background_gain_db),
        voice_gain_db=float(args.voice_gain_db),
        final_lufs=float(args.final_lufs),
        true_peak_db=float(args.true_peak_db),
        duck_background=bool(args.duck_background),
    )
    return 0


def run_monitored_mux(
    root: Path,
    *,
    video: Path,
    background: Path,
    voice: Path,
    output: Path,
    background_gain_db: float = -8.0,
    voice_gain_db: float = 0.0,
    final_lufs: float = -14.0,
    true_peak_db: float = -1.5,
    duck_background: bool = False,
    poll_seconds: float = 0.25,
) -> dict[str, Any]:
    root = Path(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"stale-incomplete-output")
    stale_hash = _sha256(output)
    baseline_bytes = _tree_bytes(root)
    gpu_before = _gpu_memory_snapshot()
    peak_gpu_used_mib = gpu_before["used_mib"] if gpu_before is not None else None
    gpu_process_sampler, gpu_process_source, gpu_process_error = _open_gpu_process_memory_sampler()
    gpu_process_available = gpu_process_sampler is not None
    observed_tree_pids: set[int] = set()
    peak_process_tree_gpu_bytes = 0
    peak_rss = 0
    peak_workspace = baseline_bytes

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_mux-helper",
        "--video",
        str(video),
        "--background",
        str(background),
        "--voice",
        str(voice),
        "--output",
        str(output),
        "--background-gain-db",
        str(background_gain_db),
        "--voice-gain-db",
        str(voice_gain_db),
        "--final-lufs",
        str(final_lufs),
        "--true-peak-db",
        str(true_peak_db),
    ]
    if duck_background:
        command.append("--duck-background")

    started = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    next_slow_sample = 0.0
    try:
        while process.poll() is None:
            current_tree_pids = _process_tree_pids(process.pid)
            observed_tree_pids.update(current_tree_pids)
            peak_rss = max(peak_rss, _process_tree_rss_bytes(process.pid))
            now = time.monotonic()
            if now >= next_slow_sample:
                peak_workspace = max(peak_workspace, _tree_bytes(root))
                gpu = _gpu_memory_snapshot()
                if gpu is not None:
                    peak_gpu_used_mib = max(peak_gpu_used_mib or 0.0, gpu["used_mib"])
                if gpu_process_sampler is not None:
                    try:
                        process_gpu = gpu_process_sampler.sample()
                        current_process_tree_gpu_bytes = sum(
                            int(process_gpu.get(pid, 0)) for pid in current_tree_pids
                        )
                        peak_process_tree_gpu_bytes = max(
                            peak_process_tree_gpu_bytes,
                            current_process_tree_gpu_bytes,
                        )
                    except OSError as exc:
                        gpu_process_available = False
                        gpu_process_error = str(exc)
                        gpu_process_sampler.close()
                        gpu_process_sampler = None
                next_slow_sample = now + 1.0
            time.sleep(max(0.05, poll_seconds))
        stdout, stderr = process.communicate()
    finally:
        if gpu_process_sampler is not None:
            gpu_process_sampler.close()
    wall_seconds = time.perf_counter() - started
    peak_rss = max(peak_rss, _process_tree_rss_bytes(process.pid))
    peak_workspace = max(peak_workspace, _tree_bytes(root))
    gpu_after = _gpu_memory_snapshot()
    if gpu_after is not None:
        peak_gpu_used_mib = max(peak_gpu_used_mib or 0.0, gpu_after["used_mib"])
    if process.returncode != 0:
        raise RuntimeError(
            f"monitored mux helper failed ({process.returncode})\nstdout={stdout.strip()}\nstderr={stderr.strip()}"
        )

    final_hash = _sha256(output)
    return {
        "wall_seconds": wall_seconds,
        "peak_process_tree_rss_bytes": peak_rss,
        "workspace_baseline_bytes": baseline_bytes,
        "peak_workspace_bytes": peak_workspace,
        "workspace_growth_bytes": max(0, peak_workspace - baseline_bytes),
        "gpu_before": gpu_before,
        "gpu_after": gpu_after,
        "peak_system_gpu_used_mib": peak_gpu_used_mib,
        "system_gpu_delta_mib": (
            max(0.0, float(peak_gpu_used_mib) - float(gpu_before["used_mib"]))
            if gpu_before is not None and peak_gpu_used_mib is not None
            else None
        ),
        "process_gpu_attribution": {
            "source": gpu_process_source,
            "available": gpu_process_available,
            "error": gpu_process_error,
            "observed_process_tree_pids": sorted(observed_tree_pids),
            "peak_dedicated_gpu_bytes": peak_process_tree_gpu_bytes if gpu_process_available else None,
            "peak_dedicated_gpu_mib": (
                peak_process_tree_gpu_bytes / (1024 * 1024)
                if gpu_process_available
                else None
            ),
        },
        "stale_output_was_overwritten": final_hash != stale_hash and output.stat().st_size > 64,
        "output_sha256": final_hash,
        "output_bytes": output.stat().st_size,
    }


def build_receipt(
    root: Path,
    *,
    duration_seconds: float,
    max_rss_mib: float = DEFAULT_MAX_RSS_MIB,
    max_workspace_growth_mib: float = DEFAULT_MAX_WORKSPACE_GROWTH_MIB,
    max_process_gpu_mib: float = DEFAULT_MAX_PROCESS_GPU_MIB,
) -> dict[str, Any]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fixture_dir = root / "fixture"
    output = root / "dubbed.mp4"
    fixture = generate_real_media_fixture(fixture_dir, duration_seconds)
    source_probe = probe_media(fixture["video"])
    source_duration = _media_duration(source_probe)

    mux = run_monitored_mux(root, output=output, **fixture)
    output_probe = probe_media(output)
    output_duration = _media_duration(output_probe)
    codecs = _stream_codecs(output_probe)
    mix_metrics = measure_mix_metrics(output, target_lufs=-14.0, target_true_peak_db=-1.5)

    duration_delta = abs(output_duration - source_duration)
    final_media_gate = bool(
        source_duration >= duration_seconds - 1.0
        and duration_delta <= 2.0
        and len(codecs["video"]) == 1
        and len(codecs["audio"]) == 1
        and codecs["audio"] == ["aac"]
        and bool(mix_metrics["passes_loudness"])
        and bool(mix_metrics["passes_true_peak"])
        and bool(mux["stale_output_was_overwritten"])
    )
    rss_limit = int(max_rss_mib * 1024 * 1024)
    disk_limit = int(max_workspace_growth_mib * 1024 * 1024)
    rss_gate = int(mux["peak_process_tree_rss_bytes"]) <= rss_limit
    disk_gate = int(mux["workspace_growth_bytes"]) <= disk_limit
    process_gpu = mux["process_gpu_attribution"]
    process_gpu_peak_mib = process_gpu.get("peak_dedicated_gpu_mib")
    final_media_vram_gate = bool(
        process_gpu.get("available")
        and process_gpu_peak_mib is not None
        and float(process_gpu_peak_mib) <= max_process_gpu_mib
    )
    representative_superlong = source_duration >= SUPERLONG_MIN_SECONDS
    gate_passed = bool(
        representative_superlong
        and final_media_gate
        and rss_gate
        and disk_gate
        and final_media_vram_gate
    )
    return {
        "schema_version": 2,
        "scope": "p23-real-media-final-assembly",
        "status": "passed" if gate_passed else "failed",
        "representative_superlong": representative_superlong,
        "claims_excluded": [
            "6h ASR/separation/TTS throughput",
            "whole-pipeline CUDA VRAM/OOM acceptance",
            "live WebGPT pressure/restart acceptance",
            "whole-job end-to-end speedup",
        ],
        "thresholds": {
            "max_process_tree_rss_mib": max_rss_mib,
            "max_workspace_growth_mib": max_workspace_growth_mib,
            "max_process_tree_dedicated_gpu_mib": max_process_gpu_mib,
            "duration_delta_seconds": 2.0,
        },
        "fixture": {
            "requested_duration_seconds": duration_seconds,
            "source_duration_seconds": source_duration,
            "source_probe": source_probe,
            "files": {
                name: {
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for name, path in fixture.items()
            },
        },
        "mux": mux,
        "output": {
            "duration_seconds": output_duration,
            "duration_delta_seconds": duration_delta,
            "probe": output_probe,
            "stream_codecs": codecs,
            "mix_metrics": mix_metrics,
        },
        "gates": {
            "final_media_valid": final_media_gate,
            "bounded_process_tree_rss": rss_gate,
            "bounded_workspace_growth": disk_gate,
            "final_media_process_gpu_pressure": final_media_vram_gate,
            "p23_real_media_final_assembly": gate_passed,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-media", action="store_true")
    parser.add_argument("--max-rss-mib", type=float, default=DEFAULT_MAX_RSS_MIB)
    parser.add_argument(
        "--max-workspace-growth-mib",
        type=float,
        default=DEFAULT_MAX_WORKSPACE_GROWTH_MIB,
    )
    parser.add_argument(
        "--max-process-gpu-mib",
        type=float,
        default=DEFAULT_MAX_PROCESS_GPU_MIB,
    )
    parser.add_argument("--_mux-helper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--video", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--background", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--voice", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--background-gain-db", type=float, default=-8.0, help=argparse.SUPPRESS)
    parser.add_argument("--voice-gain-db", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--final-lufs", type=float, default=-14.0, help=argparse.SUPPRESS)
    parser.add_argument("--true-peak-db", type=float, default=-1.5, help=argparse.SUPPRESS)
    parser.add_argument("--duck-background", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args._mux_helper:
        return _mux_helper(args)
    if args.hours <= 0:
        raise ValueError("hours must be positive")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="vi-dubber-p23-real-media-")
        work_dir = Path(temporary.name)
    else:
        work_dir = args.work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    try:
        receipt = build_receipt(
            work_dir,
            duration_seconds=args.hours * 3600.0,
            max_rss_mib=args.max_rss_mib,
            max_workspace_growth_mib=args.max_workspace_growth_mib,
            max_process_gpu_mib=args.max_process_gpu_mib,
        )
        encoded = json.dumps(receipt, indent=2, sort_keys=True)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0 if receipt["status"] == "passed" else 1
    finally:
        if temporary is not None:
            if args.keep_media:
                raise ValueError("--keep-media requires --work-dir")
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
