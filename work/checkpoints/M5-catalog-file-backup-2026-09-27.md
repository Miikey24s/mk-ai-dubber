# M5 catalog file backup/restore — PREP ONLY — 2026-09-27

## Status

`PREP ONLY`. This slice closes a local file-boundary gap around the existing
metadata-only `CatalogStore.backup_to()` API. It does not promote M5, connect
Drive/OAuth/provider services, copy media, or claim the real application
restart/restore gate.

## Implemented boundary

- `CatalogStore.restore_from(path)` is the file counterpart to `backup_to()`.
- The reader consumes at most `MAX_BACKUP_BYTES + 1` bytes (`32 MiB + 1`) and
  rejects an oversized file before parsing the whole payload.
- UTF-8/decode/JSON errors become `CatalogIntegrityError`; no database
  mutation occurs before `restore_metadata()` validates the complete digest,
  schema, paths, revisions and user-state references.
- Successful restore still uses the existing transaction and never reads or
  copies source/media blobs.

## Validation

```text
uv run pytest -q tests/test_catalog_store.py tests/test_catalog_projection.py tests/test_m5_catalog_contract.py tests/test_m5_e3_benchmark.py
24 passed

uv run python -m compileall -q src/vi_dubber
pass

git diff --check
pass
```

The new tests cover backup-to-file/restore-from-file round-trip, malformed
JSON preserving the target catalog, and the bounded-size rejection path. The
receipt is `M5-catalog-file-backup-receipt-2026-09-27.json`.

M5 still requires authorized startup/restart/restore evidence, real media
availability/relink runs, UI wiring and frozen cold/warm measurements before
acceptance.
