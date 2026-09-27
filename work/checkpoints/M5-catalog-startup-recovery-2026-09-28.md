# M5 catalog startup/restart/restore rehearsal — PREP ONLY — 2026-09-28

## Scope

This offline slice rehearses the local metadata boundary that M5 will use for
long-lived Jobs/Review state. It creates two tiny synthetic job/manifests and
one malformed job in a temporary directory, then exercises:

1. initial manifest projection and invalid-job isolation;
2. a second `CatalogStore` instance simulating application restart;
3. retention of review/bookmark state for an unchanged lineage;
4. metadata-only backup to JSON and restore into a fresh SQLite database; and
5. invalidation of review state after a manifest lineage changes.

The temporary source bytes are only fixtures. The rehearsal does not start the
web/API server, call WebGPT/TypeSafe/OAuth, touch a real job, or access Job12.

## Windows reliability fix

`CatalogStore` now owns its SQLite handle lifetime through an explicit
`_connection()` context manager. Python's `sqlite3.Connection` context manager
commits/rolls back but does not close the handle; on Windows that could leave a
database locked after restart/restore until garbage collection. The new helper
preserves transaction behavior and closes the handle on both success and
failure, which the temporary-directory cleanup in this rehearsal verifies.

## Validation

```text
uv run pytest -q tests/test_m5_catalog_recovery.py tests/test_catalog_projection.py tests/test_catalog_store.py tests/test_m5_catalog_contract.py tests/test_m5_e3_benchmark.py
25 passed

uv run python scripts/verify_m5_catalog_recovery.py
status=PREP_ONLY
initial_indexed=2; initial_skipped=job-invalid
restart_indexed=2; restart_state_retained_same_revision=true
backup_restore_count=2; backup_round_trip_equal=true
changed_lineage_invalidated_state=true

uv run python -m compileall -q src/vi_dubber scripts/verify_m5_catalog_recovery.py
pass

git diff --check
pass
```

Machine receipt: `M5-catalog-startup-recovery-receipt-2026-09-28.json`.

## Explicit exclusions

This evidence remains `PREP_ONLY`. It does not close M0, M5 product/UI
acceptance, real authorized media availability/relink, application server
startup wiring, cold/warm p95, provider behavior, OAuth/Drive, Job12, or
whole-pipeline media quality.
