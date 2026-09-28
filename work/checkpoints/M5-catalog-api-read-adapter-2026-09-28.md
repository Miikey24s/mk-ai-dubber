# M5 catalog API read adapter — PREP ONLY — 2026-09-28

## Scope

Commit `0c5afbd` connects the metadata-only catalog view to the existing local
API read paths without making projection rebuilds implicit. `GET /api/catalog`
exposes the versioned `vi-dubber-catalog-view-v1` shape with literal search,
availability filtering and bounded pagination. `GET /api/jobs` and
`GET /api/jobs/{job_id}` attach an additive `catalog` field when a matching
durable row exists.

The adapter probes `work/catalog.sqlite3` through SQLite read-only mode and
requires the supported schema version before reading. Missing, unsupported or
invalid storage returns an empty, stable view with `catalog_status`; it does
not create/migrate a database, rebuild from job directories, hash media, expose
media URLs/bytes, call providers/OAuth, or touch Job12.

## Validation

```text
uv run pytest -q tests/test_api.py tests/test_catalog_view.py tests/test_catalog_store.py tests/test_catalog_projection.py
45 passed, 1 skipped, 2 warnings

uv run python -m compileall -q src/vi_dubber tests/test_api.py
pass

git diff --check
pass
```

## Gate status

This is an offline read-path adapter and remains **PREP_ONLY**. M5 still needs
an owner-approved startup/rebuild lifecycle, real-media create/reopen/review/
edit/rerender/export evidence, UI/browser acceptance and frozen performance
checks before product acceptance.
