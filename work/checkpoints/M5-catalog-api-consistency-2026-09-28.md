# M5 catalog API consistency audit — PREP ONLY — 2026-09-28

## Scope

`verify_m5_catalog_api_consistency.py` exercises the local metadata boundary
with a temporary SQLite catalog and synthetic job state. It checks that
`/api/catalog`, `/api/jobs` and `/api/jobs/{job_id}` expose the same version,
lineage revision, availability/review state and allowlisted metadata. It also
checks that a missing catalog remains an explicit empty/unavailable state and
that a future SQLite schema is not migrated by a GET.

The audit is read-only from the API perspective: it does not rebuild from a
work directory, hash/open media, start a provider/runtime, use OAuth or
connectors, or touch Job12. SQLite probe handles are closed explicitly so the
Windows temporary workspace can be removed after the check.

## Validation

```text
uv run python scripts/verify_m5_catalog_api_consistency.py
status=PREP_ONLY; schema_and_format_stable=true;
jobs_projection_consistent=true; details_projection_consistent=true

uv run pytest -q tests/test_m5_catalog_api_consistency.py tests/test_api.py tests/test_catalog_view.py tests/test_catalog_store.py tests/test_catalog_projection.py
46 passed, 1 skipped, 2 warnings

uv run python -m compileall -q src/vi_dubber scripts/verify_m5_catalog_api_consistency.py tests/test_m5_catalog_api_consistency.py
pass

git diff --check
pass
```

Machine receipt: `M5-catalog-api-consistency-receipt-2026-09-28.json`.

## Gate status

This is an offline API contract audit and remains **PREP_ONLY**. It does not
close M5 product/UI acceptance, real-media startup/rebuild/relink, browser
DOM/performance checks or external provider behavior.
