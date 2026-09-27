# M5 catalog read-model and projection safety — PREP ONLY — 2026-09-28

## Scope

This offline slice adds the versioned `vi-dubber-catalog-view-v1` read model
for the future Jobs/Review consumer and hardens catalog projection recovery.
It uses only synthetic metadata in temporary SQLite files. It does not start
the app, open media, call WebGPT/TypeSafe, use OAuth/Drive, or touch Job12.

## Behavior

- `CatalogStore.read_view()` returns a bounded, deterministic page that joins
  catalog identity, availability, lineage revision/segment count and the
  separate review/watch/bookmark state.
- `catalog_view.py` keeps the contract metadata-only and allowlists job fields;
  arbitrary debug/provider metadata is not copied into the UI payload.
- The view rejects duplicate/unknown user state and any state whose revision
  differs from the catalog item. A backup-to-view path uses the same contract.
- `rebuild_from_work_dir()` now preserves the existing catalog and review state
  when the work root is missing, empty, unreadable, or contains only invalid
  `job-*` entries. It reports the reason (`work_dir_unavailable`,
  `empty_work_dir`, or `all_jobs_invalid`) instead of deleting the projection.
  When valid and invalid jobs are mixed, already-known invalid siblings remain
  in the projection with `availability=unknown` and a `projection_warning`, so
  their review state is not lost. A valid non-empty projection still replaces
  rows atomically for jobs it can verify.

This prevents a transient mount/permission/corruption condition during startup
from being interpreted as an intentional empty library and deleting bookmarks
or review progress.

## Validation

```text
uv run pytest -q tests/test_catalog_store.py tests/test_catalog_projection.py tests/test_catalog_view.py tests/test_m5_catalog_view_contract.py tests/test_m5_catalog_recovery.py tests/test_m5_process_startup.py tests/test_m5_catalog_contract.py tests/test_m5_e3_benchmark.py
35 passed in 4.36s

uv run python scripts/verify_m5_catalog_view_contract.py --output work/checkpoints/M5-catalog-view-contract-receipt-2026-09-28.json
status=PREP_ONLY; stable_schema=true; joined_review_state=true;
cross_lineage_state_rejected=true; invalid_projection_preserved=true;
mixed_invalid_projection_preserved=true; unavailable_projection_preserved=true

uv run python -m compileall -q src/vi_dubber scripts/verify_m5_catalog_view_contract.py tests/test_catalog_projection.py tests/test_catalog_view.py tests/test_m5_catalog_view_contract.py
pass

git diff --check
pass
```

Ruff was not available in the bundled environment (`Failed to spawn: ruff`);
the focused pytest, compileall and diff checks above passed.

Machine receipt: `M5-catalog-view-contract-receipt-2026-09-28.json`.

## Explicit exclusions and next gate

This remains **PREP_ONLY**. It does not close VI M0 or M5 product acceptance,
and it does not wire `CatalogStore` into the application server/frontend. The
remaining M5 work is owner-approved real-media create/reopen/review/edit/
rerender/export, app startup/restart persistence, real missing/moved/relink,
licensed-media and UI/browser acceptance, plus frozen E3 p95/DOM/memory checks.
