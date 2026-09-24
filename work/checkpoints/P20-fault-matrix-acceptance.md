# P20 & P17 Acceptance Checkpoint: Fault Matrix, Crash Recovery & Benchmark Verification

**Date:** 2026-09-24  
**Author:** Subagent 3 (Fault Matrix & Benchmark Specialist for P20 & P17)  
**Workspace:** `D:\ANNAM\TradingWorkspace\projects\vi-dubber`  
**Status:** **P20 ACCEPTED** (Core Fault Contracts, Crash Recovery & Atomic Write Safety) | **P17 PARTIALLY COMPLETE** (Source Coverage 10/10, Benchmark-Ready 7/10, Gate Fails Closed)

---

## 1. Executive Summary

This checkpoint certifies the operational hardening, crash recovery, and benchmark regression integrity of VI Dubber under milestones **P20** (Fault Contracts, Crash Recovery, Atomic Safety) and **P17** (Regression Benchmarks).

- **P20 Fault Contracts & Recovery:** **ACCEPTED**. Full automated verification of disk preflight checks, child process kill across 4 distinct pipeline stage boundaries (`asr`, `translation`, `tts`, `mix_mux`), duplicate run prevention, stale/corrupt lease recovery, atomic JSON file replacement with fsync barrier, and Aurora/WebGPT/TypeSafe provider fault contracts.
- **P17 Regression Benchmarks:** **PARTIAL (7/10 Available, 10/10 Source-Ready)**. The fixture manifest (`work/benchmarks/fixtures.json`) passes zero-issue integrity validation (`tools/benchmark.py validate-manifest`). 7 out of 10 required categories are fully benchmarked with committed artifact receipts; 3 synthetic stress categories (`two-speakers`, `overlapping-speech`, `emotional-prosody-stress`) have verified source provenance and behavior probes in `source-ready` status. Release gating correctly fails closed until all 10 categories are benchmarked.

---

## 2. Test Suite Execution Receipt

Command executed:
```bash
uv run pytest -q tests/test_p21_aurora_faults.py tests/test_fault_contracts.py tests/test_benchmark_manifest.py tests/test_regression_benchmark.py
```

### Result:
```text
..................................                                       [100%]
34 passed in 9.63s
```
*(All 34 test cases passed synchronously without error or retry).*

### Itemized Test Breakdown:

| Test Suite | Tests Passed | Focus Area |
|---|:---:|---|
| `tests/test_p21_aurora_faults.py` | 8 | Provider fault contracts: HTTP 401/403/503 catalog fails closed, model absence fail-closed, bounded 429 backoff, disconnect commit-isolation, stage cache fingerprinting, Qwen/WebGPT fallback provenance. |
| `tests/test_fault_contracts.py` | 10 | Core fault contracts: corrupted artifact invalidation, duplicate lease guard, child process termination across 4 stages (`asr`, `translation`, `tts`, `mix_mux`), power-loss atomic write safety, live disk preflight, deterministic profile snapshots, multi-update job persistence. |
| `tests/test_benchmark_manifest.py` | 9 | Fixture manifest verification: rate/percentile calculations, degraded handling for optional fields, missing artifact rejection, real fixture path & SHA-256 validation, synthetic stress receipt verification, baseline metric matching. |
| `tests/test_regression_benchmark.py` | 7 | Benchmark evaluation: directional regression tracking, fail-closed gate on missing metrics, segment QA micro-WER & critical tokens, semantic QA extraction, fixture comparison aggregation, corrupt artifact rejection, release gate failure on missing categories. |
| **Total** | **34** | **100% Pass** |

---

## 3. Deep-Dive Verification: P20 Fault Contracts & Safety

### 3.1 Live Disk Preflight
- **Mechanism:** Implemented in `src/vi_dubber/preflight.py` (`run_preflight`, `raise_for_preflight`). Evaluates `reliability.min_free_disk_gb` against OS disk usage via `shutil.disk_usage`.
- **Contract:** If free disk space is less than configured threshold, preflight immediately emits an `error` status check (`PreflightCheck("disk", "error", "Còn ... GiB; yêu cầu tối thiểu ... GiB")`).
- **Fail-Clean Guarantee:** Verified by `test_live_disk_preflight_fails_clean_without_filling_the_drive`. Calling `raise_for_preflight()` throws a descriptive `RuntimeError` before any model download, audio extraction, or directory initialization begins. No temporary files or residual disk bloat are created.

### 3.2 Child Process Kill, Lease Protection & Crash Recovery
- **Mechanism:** Implemented in `src/vi_dubber/jobs.py` (`claim_job`, `job_lease_is_live`, `_pid_is_alive`, `reconcile_job_state`).
- **Exclusive Lease Guard:** Process creates `run.lock` with `O_CREAT | O_EXCL | O_WRONLY`, storing `{version: 1, pid, claimed_at}`. If another active process holds the lock (`os.kill(pid, 0)` succeeds), `JobAlreadyRunning` is raised to prohibit concurrent duplicate processing. Corrupt or unparseable lock files are safely unlinked and reclaimed.
- **Stage-Boundary Termination Verification:** Verified by `test_terminated_process_preserves_committed_checkpoint_and_reclaims_stale_lease` parametrized across four distinct pipeline stages:
  1. `asr` (previous stage `separation`, committed `stems/vocals.wav`, partial `segments_raw.partial.json`, progress 0.20)
  2. `translation` (previous stage `asr`, committed `segments_raw.json`, partial `segments_translated.partial.json`, progress 0.34)
  3. `tts` (previous stage `translation`, committed `segments_translated.json`, partial `tts/00001_raw.partial.wav`, progress 0.60)
  4. `mix_mux` (previous stage `timing_assembly`, committed `voice_vi.wav`, partial `dubbed.partial.mp4`, progress 0.84)
- **Recovery Behavior:**
  - Upon SIGTERM / process kill mid-stage, the child process ceases execution immediately.
  - Previous completed stage manifest (`manifests/{previous_stage}.json`) remains valid and uncorrupted (`load_stage_manifest() is not None`).
  - Current stage manifest (`manifests/{stage}.json`) was never committed (`load_stage_manifest() is None`).
  - Calling `reconcile_job_state()` inspects `state.json`, detects that PID is no longer alive, updates job status from `running` to `paused`, preserves exact progress float, and records `metadata.recovered_from_stale_running = True`.
  - Next `claim_job()` recognizes the dead PID in `run.lock`, reclaims the lock cleanly, and allows pipeline resumption directly from the last committed stage manifest without rerunning upstream stages.

### 3.3 Atomic Replacement Safety
- **Mechanism:** Implemented in `src/vi_dubber/artifacts.py` (`atomic_write_json`).
- **Protocol:** Writes serialized JSON to a hidden temporary sibling file (`dir=path.parent`, prefix `.{path.name}.`, suffix `.tmp`), flushes application buffers (`handle.flush()`), enforces disk persistence (`os.fsync(handle.fileno())`), and replaces the target file via `os.replace`.
- **Termination at Fsync Barrier:** Verified by `test_terminated_process_during_atomic_write_preserves_previous_committed_json`. A child process executing `atomic_write_json` was paused and killed at the `os.fsync` hook immediately before `os.replace`.
- **Result:** The original target file retained its previous committed valid JSON without byte corruption or partial writes. Uncommitted temporary files were safely cleaned up.

---

## 4. Benchmark Harness & Fixture Registry Audit (P17)

### 4.1 Manifest Validation
Command executed:
```bash
uv run python tools/benchmark.py validate-manifest
```
Output:
```json
{
  "issues": [],
  "manifest": "work\\benchmarks\\fixtures.json",
  "valid": true
}
```
Validation confirms that all source files, verification receipts, generator scripts, and result artifacts referenced in `work/benchmarks/fixtures.json` exist on disk and strictly match their recorded SHA-256 hashes.

### 4.2 Fixture Registry Status Matrix

| Fixture ID | Category / Class | Role | Duration | RTF | Segments | Status | Notes |
|---|---|---|---:|---:|---:|:---:|---|
| `baseline-short-pwkw-20260916` | `short-fragmented-subtitle-style-speech` | short-real-baseline | 226.1s | 3.42 | 53 | **Available** | Rewrite: 45.3%, Overflow: 20.8%, Tempo p95: 1.25 |
| `baseline-long-trading-strategies-20260916` | `long-monologue` | long-real-baseline | 3238.7s | 1.75 | 734 | **Available** | Rewrite: 71.3%, Overflow: 27.1%, Tempo p95: 1.25 |
| `golden-clean-single-speaker-talking-head` | `clean-single-speaker-talking-head` | clean-talking-head-real-regression | 60.0s | 9.73 | 13 | **Available** | Cold-after WER: 7.07%, Rewrite: 0%, Overflow: 0% |
| `golden-fast-english-speech` | `fast-english-speech` | fast-english-real-regression | 83.3s | 9.00 | 20 | **Available** | WER: 2.84%, Critical Token Acc: 100%, Global Sim: 0.994 |
| `golden-music-under-dialogue` | `music-under-dialogue` | synthetic-source-regression | 8.8s | 19.17 | 2 | **Available** | WER: 0.0%, 220Hz+330Hz bed, Global Sim: 1.000 |
| `golden-noisy-speech` | `noisy-speech` | synthetic-source-regression | 8.8s | 41.64 | 2 | **Available** | WER: 0.0%, Pink noise amp 0.09, Global Sim: 1.000 |
| `golden-names-numbers-technical-terms` | `names-numbers-technical-terms` | technical-terms-real-regression | 90.0s | 8.51 | 20 | **Available** | WER: 33.2%, Critical Token Acc: 96.3%, Segment QA detects numeric defect |
| `golden-two-speakers` | `two-speakers` | synthetic-source-regression | 23.8s | — | — | **Source-Ready** | David + Zira, 4 alternating turns, verified probe |
| `golden-overlapping-speech` | `overlapping-speech` | synthetic-source-regression | 8.8s | — | — | **Source-Ready** | David + Zira, 5.27s designed overlap, verified probe |
| `golden-emotional-prosody-stress` | `emotional-prosody-stress` | synthetic-source-regression | 16.6s | — | — | **Source-Ready** | Zira rate/vol sequence (-4/0/+4, 92/80/100), verified probe |

### 4.3 Coverage Summary
- **Total Registered Fixtures:** 10
- **Available Fixtures (Benchmark-Ready):** 7 / 10 (70%)
- **Source-Ready Fixtures:** 10 / 10 (100% source coverage)
- **Release Gate Status:** `complete: false`, `source_complete: true`.
- **Fail-Closed Protection:** `compare_fixture_sets` prevents release sign-off when benchmarked category coverage is incomplete, correctly gating release until dubbing runs for `two-speakers`, `overlapping-speech`, and `emotional-prosody-stress` are fully processed and archived.

---

## 5. Acceptance Decision & Next Steps

1. **P20 Operational & Fault Matrix:** **ACCEPTED**.
   - Live preflight bounds disk space without allocation leaks.
   - Child process termination across all four primary pipeline stages maintains crash-consistent state manifests and enables seamless resume.
   - Atomic replacement guarantees file integrity across crash boundaries.
   - Provider fault handling (Aurora, WebGPT, TypeSafe) fails closed and preserves provenance.

2. **P17 Regression Benchmarks:** **OPEN GAPS REMAINING**:
   - Complete dubbing pipeline runs on the remaining 3 synthetic source-ready fixtures (`two-speakers`, `overlapping-speech`, `emotional-prosody-stress`) to promote them to `available` (10/10).
   - Incorporate real-human conversational and noise clips before claiming general human acoustic coverage (synthetic fixtures validate pipeline determinism and model stress, not natural speech nuances).
