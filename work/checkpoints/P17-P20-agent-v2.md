# P17/P20 deterministic reliability checkpoint v2

Date: 2026-09-22

Status: deterministic slice PASS; full P17/CP7 and full P20 remain incomplete.

## Scope

Owned source changed:

- `src/vi_dubber/jobs.py`
- `src/vi_dubber/preflight.py`
- `src/vi_dubber/cli.py`
- `tools/benchmark.py`

Owned tests changed:

- `tests/test_jobs.py`
- `tests/test_preflight.py`
- `tests/test_regression_benchmark.py`

`tests/test_fault_contracts.py` was regression-tested but not changed. No edits were made to pipeline, web, config, PLAN, or README.

Open-source gate: `BUILD`. This slice is project-specific control-plane validation/state logic and benchmark policy; no new dependency or copied upstream implementation was needed.

## Behavior changed

- Job lease PID probing now fails closed on `PermissionError`; an inaccessible process is treated as potentially alive instead of stealing its lock.
- Corrupt/invalid `control.json` no longer silently becomes `run`; invalid control requests are rejected before persistence.
- Local translation preflight no longer probes WebGPT at all.
- WebGPT probe exceptions become actionable preflight errors instead of aborting the readiness check.
- Hybrid translation preflight distinguishes fully ready, WebGPT-only degraded, local-fallback degraded, and fully unavailable states.
- Preflight exposes and validates bounded `retry_budget` in `[0, 10]`; TypeSafe readiness includes configured `max_attempts` without exposing secrets.
- Doctor survives WebGPT probe exceptions and reports default translator plus retry budget.
- P17 manifest comparison now separates metric-threshold success from required fixture-class coverage. Full `passed=true` requires all 10 PLAN P17 classes in both before/after available fixture sets, preventing partial fixture coverage from being reported as release acceptance.

## Validation

Pre-edit focused baseline:

`uv run pytest -q tests/test_jobs.py tests/test_preflight.py tests/test_regression_benchmark.py tests/test_fault_contracts.py`

Result: `14 passed in 3.74s`.

Post-edit focused:

`uv run pytest -q tests/test_jobs.py tests/test_preflight.py tests/test_regression_benchmark.py tests/test_fault_contracts.py`

Result: `20 passed in 3.60s`.

Full regression:

`uv run pytest -q`

Result: `169 passed in 11.07s`.

Doctor:

`uv run vi-dubber doctor`

Result: command completed successfully; current runtime still reports CUDA RTX 2070 SUPER, WebGPT route, local Qwen model and config schema OK. New rows report `Translator mặc định = webgpt` and `Retry budget = 3`.

P17 fail-closed coverage smoke:

`uv run python tools/benchmark.py compare-manifests --before-manifest work/benchmarks/fixtures.json --after-manifest work/benchmarks/fixtures.json --before-root . --after-root . --max-rewrite-rate 1 --max-overflow-rate 1 --max-tempo-p95 2`

Result: non-zero as intended. Numeric thresholds passed, but report returned `passed=false` because only 2/10 required fixture categories are currently available. Missing categories: clean talking head, emotional/prosody stress, fast English, music under dialogue, names/numbers/technical terms, noisy speech, overlapping speech, two speakers.

## Current SHA256

- `src/vi_dubber/jobs.py`: `07E799ABA558621BAA5D61CE329A4901235E854ACFC6BD574DFD68AA86CEE5D0`
- `src/vi_dubber/preflight.py`: `354E109A8EDBFC3D87935E2F87E7E8EAD763C8377C81D565E7F3EF5344C808E9`
- `src/vi_dubber/cli.py`: `8D56B9D68D0B7C09D129F13030AA942EA4589BFC5041FF7B80143A96C570BC1B`
- `tools/benchmark.py`: `0E97DC51FA2DD2082EE9F04555D3EB7A8BE2C771C71743F4CE95CB369223D1CE`
- `tests/test_jobs.py`: `7AB14E42349F56C7A5516066177082AE8DF2A55CB569C53EBF138C267ABB1AED`
- `tests/test_preflight.py`: `5053CC3B65E82E1D95C3484011842BA81EC67C39C3CA0D0B102F7347975859F8`
- `tests/test_regression_benchmark.py`: `832D7C1FCABDB689D927CFB7C7E562286DFA4A7DD036C4341215E8F869C980BC`
- `tests/test_fault_contracts.py` unchanged: `B72D694265A5FF6992604A4AD8EEFB10777EFC071396E3C2946418A667619134`

The pre-edit hash command was run, but PowerShell table formatting truncated the displayed hashes; exact pre-edit values are intentionally not reconstructed or guessed.

## Remaining gaps

- P17 still lacks 8 required representative fixture classes plus WER/critical-token metrics, TypeSafe eval/calibration metrics, and human listening A/B evidence.
- This slice validates retry budgets/readiness contracts, not real WebGPT/TypeSafe retry behavior inside their provider modules.
- No deterministic evidence was invented for Internet disconnect/partial WebGPT response/route-down, TypeSafe 429/5xx, process kill/reboot/power loss, CUDA OOM, disk-full, browser reconnect, or real TTS crash-at-segment. Those still require production-path fixtures where deterministic, otherwise manual receipts under PLAN P20.
- Therefore do not mark P17, P20, or CP7 complete from this checkpoint alone.
