# P21 fault-contract test slice

Date: 2026-09-23
Owner: p21_faults
Scope: tests/checkpoint only; no production source changed.

Added `tests/test_p21_aurora_faults.py` covering:

- auth/catalog 401/403/503 fails before generation;
- selected model unavailable fails closed without model substitution;
- HTTP 429 retries are bounded and retain the selected model;
- provider failure/restart does not commit partial `Segment.vi`, then a fresh adapter can recover;
- translation fingerprint changes with model, effort, and catalog revision while a frozen selection remains stable;
- Qwen fallback and legacy WebGPT rollback keep explicit provider/model/fallback provenance.

The HTTP fixture is local and deterministic. It exposes generic health/model endpoints and an OpenAI-compatible structured response, with no network/account dependency.

Integration note: the test helper currently expects `vi_dubber.translate.AuroraTranslator(config, work_dir, retry_budget=...)`, with `running()`, `translate_segments(...)`, and `stats()`, matching the existing translator shape. If the production worker intentionally chooses a different public class name, align only `_aurora_translator_cls()` / `_new_aurora()` rather than weakening the behavioral assertions.

Validation before Aurora production merge:

```text
uv run pytest -q tests/test_p21_aurora_faults.py
6 failed, 2 passed
```

The six failures are all the same expected TDD-red condition: `AuroraTranslator` is not yet present in `vi_dubber.translate`. The two implementation-independent contracts (selection fingerprint invariants and Qwen/legacy provenance) pass.
