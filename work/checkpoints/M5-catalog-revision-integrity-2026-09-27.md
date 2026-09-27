# M5 catalog revision integrity — PREP ONLY — 2026-09-27

## Status

`PREP ONLY`. This slice hardens the local metadata projection contract. It
does not promote M5, wire a Review/UI flow, touch media bytes, open a provider,
or close the real-media/restart/restore acceptance gates.

## Change

- User review/bookmark state can be persisted only when its `revision` exactly
  matches the catalog item revision.
- A projection rebuild retains state for surviving IDs only when the revision
  is unchanged; state from an old lineage is dropped instead of being shown as
  current.
- Backup restore rejects a digest-valid payload whose user state points at a
  different item revision, before mutating the target database.
- Read/export paths detect a manually corrupted SQLite state row and fail
  closed with `CatalogIntegrityError`.
- Rebuild validates persisted user state before replacing the projection, so a
  corrupt row aborts the transaction instead of causing a partial wipe.

The media/source contract is unchanged: catalog operations still store only
portable metadata and content fingerprints, never media blobs.

## Validation

```text
uv run pytest -q tests/test_catalog_store.py tests/test_catalog_projection.py tests/test_m5_catalog_contract.py tests/test_m5_e3_benchmark.py
20 passed
```

The added cases cover revision change during rebuild, write-time mismatch,
digest-valid restore mismatch with no target mutation, direct SQLite tampering
detected on read/export, and corrupt persisted state rejected before rebuild
mutation. This is local integrity evidence only; M5 still requires the
authorized application startup/restart/restore workflow and real media
availability evidence before acceptance.
