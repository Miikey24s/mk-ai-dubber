# M5 local catalog foundation — 2026-09-27

## Status

`LOCAL FOUNDATION / NOT M5 ACCEPTANCE`. This slice implements the offline,
rebuildable metadata boundary only. It does not claim the VI M0 baseline, UI
Library/Review integration, real-media availability coverage, p95 performance,
provider behavior, OAuth, Drive or job12 completion.

## Implemented

- `src/vi_dubber/catalog_store.py` provides a standard-library SQLite catalog
  projection with schema version `1` and transaction-scoped writes.
- Manifest projection rebuild is atomic and preserves user state for surviving
  item IDs; duplicate IDs and invalid metadata fail before mutation.
- Search is bounded, deterministic, casefolded and literal (SQL wildcards are
  escaped). Availability is an explicit state, including safe `unknown`.
- User bookmarks/review state/watch position live in a separate table and are
  written transactionally; deleting a catalog row removes only that user-state
  projection and never source/media files.
- Backup/restore is deterministic metadata-only JSON. It carries a SHA-256
  payload digest, excludes media bytes and absolute paths, and validates the
  complete payload before one restore transaction.
- Availability probing verifies the mapped relative path and exact source hash.
  Relink requires an explicit portable mapping and an exact content fingerprint;
  it never guesses from a filename.

## Validation

```text
uv run pytest -q tests/test_catalog_store.py
6 passed
```

The focused tests cover schema creation/reopen, atomic rebuild and state
retention, literal search/filter, backup round trip and tamper rejection,
availability stale/missing paths, exact-fingerprint relink, and source-file
preservation after catalog deletion.

## Remaining M5 gates

The catalog still needs an owner-approved adapter from real job/manifests,
Review/UI wiring, real licensed media availability/relink runs, cold/warm p95
measurements, and backup/restore rehearsal against the supported application
startup path before M5 can be accepted.

