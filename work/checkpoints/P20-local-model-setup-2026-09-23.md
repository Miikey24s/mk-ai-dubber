# P20 local-model setup receipt - 2026-09-23

Task: `P20-LOCAL-SETUP`

## Intended behavior

- `setup.ps1` remains WebGPT-first and does not download the approximately 9 GB Qwen GGUF by default.
- `setup.ps1 -ProvisionLocalModel` reads `translation.repo` and `translation.filename` from `config.yaml`, provisions that exact file under `models/llm/<repo-with-slash-replaced-by-double-dash>/`, and leaves system configuration unchanged.
- The network path uses `huggingface_hub`, validates the completed file, and compares its SHA-256 with the upstream LFS ETag when the server exposes a SHA-256 ETag.
- `-LocalModelSource <path>` provides an offline/cached-file path. It copies through a `.partial` file and atomically replaces the target only after minimum-size and optional `-LocalModelSha256` validation pass.
- Supplying source/checksum parameters without the explicit opt-in switch fails early.

## Disposable validation

`tools/p20_fresh_install_smoke.ps1` now exercises both policies in a disposable `%TEMP%` project copy:

1. run the real default setup offline and assert that no Qwen target appears;
2. create a local 1,000,001-byte fake GGUF fixture;
3. prove a deliberately wrong SHA-256 is rejected without leaving a target or `.partial` file;
4. run the real setup again with `-ProvisionLocalModel -LocalModelSource ... -LocalModelSha256 ...`;
5. assert the configured target exists and has the fixture SHA-256;
6. verify the main workspace `.venv` remains unchanged.

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\p20_fresh_install_smoke.ps1
```

Result: `PASS`.

- Default model download: skipped.
- Wrong SHA-256: rejected without a target or `.partial` file.
- Offline local fixture: provisioned to the configured Qwen target with matching SHA-256.
- Doctor before opt-in: `LLM local: CHƯA CÓ MODEL`.
- Doctor after opt-in: `LLM local: OK - Qwen3-14B-Q4_K_M.gguf`.
- Main workspace `.venv`: unchanged.

Focused regression:

```powershell
uv run pytest -q tests/test_preflight.py
```

Result: `6 passed in 3.48s`. Both changed PowerShell scripts also parse successfully, cleanup left no `vi-dubber-p20-fresh-*` sandbox or `vi-dubber-provision-*.py` helper in `%TEMP%`, and a missing `-LocalModelSource` was rejected without leaking the temporary provision helper.

## Network-only validation remaining

The real Hugging Face path was deliberately not run because it downloads about 9 GB. One manual acceptance run still remains when network, disk budget, and download consent are available:

```powershell
.\setup.ps1 -ProvisionLocalModel
```

That run should finish with doctor reporting `LLM local: OK - Qwen3-14B-Q4_K_M.gguf` and the provisioner reporting `validation: upstream LFS SHA-256` for the currently configured Qwen repository.
