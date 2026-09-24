# Continuation Checkpoint - 2026-09-24 (Multi-Subagent Acceptance Wave)

Date: 2026-09-24  
Coordinator: Root Coordinator (Antigravity)  
Subagents:
- Subagent 1 (`ced56efa-6c57-4a71-8740-622aed3edd61`): Translation Core Specialist (P21 & P04)
- Subagent 2 (`02a806b6-f68d-4672-99e1-a41820f42d35`): Web UI Specialist (P16)
- Subagent 3 (`9bb27c57-7dfb-4c93-bdc0-4d39be029dc4`): Fault Matrix & Benchmark Specialist (P20 & P17)

---

## 1. Summary of Executed Waves & Evidence

### P21 - Dedicated ChatGPT Web Instance 2: COMPLETE & ACCEPTED
- **Live Connection**: Connected directly to managed WebGPT instance 2 at `http://127.0.0.1:17842/v1`.
- **Dynamic Catalog Discovery**: Successfully discovered advertised models:
  - `chatgpt-web/gpt-5.6-sol` (efforts: `medium`, `high`; default: `high`).
  - `chatgpt-web/gpt-5.6-sol-instant` (efforts: `low`; default: `low`).
- **Live Translation Invocation**:
  - `gpt-5.6-sol` (high): 43.59s live run, output schema valid JSON.
  - `gpt-5.6-sol` (medium): 38.14s live run, output schema valid JSON.
  - `gpt-5.6-sol-instant` (low): 34.99s live run, output schema valid JSON.
- **Fail-Closed Contract**: Requesting unadvertised `low` effort on `gpt-5.6-sol` correctly throws `RuntimeError` before request dispatch.
- **Durable Checkpoint**: `work/checkpoints/P21-live-acceptance.md`.

### P04 - Context-Aware Translation & Quality Contract: COMPLETE & ACCEPTED
- **Glossary Adherence**: 100% adherence to `glossary.yaml` (`FVG` -> `FVG`, `order block` -> `order block`, `sweeps liquidity` -> `quét thanh khoản`, `market structure` -> `cấu trúc thị trường`).
- **Critical Token Preservation**: 100% preservation of named entities (`OpenAI`, `Sam Altman`, `GPT-4`), Vietnamese number/comma formats (`86.400`, `99,8%`), negation (`Không được bán ngay`), modality (`phải chờ`, `có thể hủy`), and directional quantities (`68% thắng`, `32% thua`).
- **Automated Validation**: `tools/p04_quality_receipt.py` -> `passed=true` across all 6 validation sections.
- **Tests**: `tests/test_webgpt_retry.py`, `tests/test_p04_quality_contract.py`, `tests/test_language_quality.py` -> 32 passed.

### P16 - Product Web UI & Selective Rerender: COMPLETE & ACCEPTED
- **Dynamic UI Binding**: `src/vi_dubber/web.py` pins to instance 2, loads live model catalog, and binds dynamic effort choices to the UI dropdown on model selection.
- **Selective Rerender Safety**: Segment edits in the review UI surgically invalidate only downstream audio manifests for that segment (`tts`, `mix_mux`, etc.) while retaining all upstream transcripts and raw audio cache files. The "Render lại đoạn đã sửa" button activates dynamically.
- **Tests**: `tests/test_web_review.py`, `tests/test_review.py` -> 44 passed in 8.95s.
- **Durable Checkpoint**: `work/checkpoints/P16-ui-catalog-acceptance.md`.

### P20 - Production Hardening & Fault Contracts: COMPLETE & ACCEPTED
- **Fault Matrix**: Verified deterministic behavior on 429 rate limit backoff, missing selected model fail-closed, unreachable port rejection, live disk preflight, and atomic replace (`fsync` + temp file replace) safety against process kills.
- **Child Process Kill Recovery**: Verified across 4 stages (`asr`, `translation`, `tts`, `mix_mux`). Uncommitted partial artifacts do not corrupt committed stage manifests. Dead PID jobs transition cleanly from `running` to `paused`, and stale locks in `run.lock` are reclaimed.
- **Tests**: `tests/test_p21_aurora_faults.py`, `tests/test_fault_contracts.py` -> 18 passed.
- **Durable Checkpoint**: `work/checkpoints/P20-fault-matrix-acceptance.md`.

### P17 - Regression Benchmark Suite: PARTIAL (Gates Accepted)
- **Manifest Validation**: `tools/benchmark.py validate-manifest` reports 0 issues.
- **Fixture Coverage**: 10/10 source-ready categories (100%), 7/10 benchmark-ready available.
- **Release Gate**: `compare_fixture_sets` enforces fail-closed until all 10 available fixture categories pass.
- **Tests**: `tests/test_benchmark_manifest.py`, `tests/test_regression_benchmark.py` -> 16 passed.

---

## 2. Regression Suite Full Run

Full pytest suite executed across all 31 test files:
```text
uv run pytest -q
........................................................................ [ 21%]
........................................................................ [ 42%]
........................................................................ [ 64%]
........................................................................ [ 85%]
.................................................                        [100%]
337 passed in 21.09s
```

All 337 tests passed with zero regressions.
