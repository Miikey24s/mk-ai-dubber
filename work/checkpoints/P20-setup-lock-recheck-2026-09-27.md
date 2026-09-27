# P20 setup lockfile recheck — 2026-09-27

Status: **PASS — scoped setup reproducibility hardening only**.

This receipt records the setup change in `9d9d3eb` and does not promote or
reopen any broader P20/P23 or VI baseline gate.

## Change

- `setup.ps1` now runs `uv sync --dev --locked`.
- The tracked `uv.lock` is therefore the dependency graph for a clean setup;
  a stale lock fails loudly instead of silently resolving a new graph.
- `tests/test_setup_contract.py` protects this contract and does not start a
  service or mutate the runtime environment.

## Validation

Focused static contract:

```text
uv run --with pytest python -m pytest -q tests/test_setup_contract.py
1 passed
```

Disposable offline setup smoke:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\p20_fresh_install_smoke.ps1
PASS
```

The smoke copied the setup surface to a temporary directory, installed the
locked graph offline (169 packages), exercised portable FFmpeg, compileall,
doctor and the CLI entry point, rejected an intentionally incorrect local
model checksum without leaving a target or partial file, and accepted the
explicit local-model fixture path. The existing project `.venv` was unchanged
and the temporary sandbox was removed.

## Scope limits

- Dedicated Dubber-WebGPT was intentionally unavailable during this offline
  smoke; no provider request was made.
- No PostgreSQL, broker, OAuth, long-media job, or production web service was
  started.
- The default setup still intentionally omits the large local Qwen model;
  `-ProvisionLocalModel` remains explicit and opt-in.

