# M5 catalog/search prep — PREP ONLY — 2026-09-27

## Status

`PREP ONLY`. This checkpoint prepares a deterministic offline contract for the
future M5 VI local catalog/search and metadata backup/restore work. It does not
open M5 production implementation, change the VI runtime, change the UI, or
claim the VI baseline/M5 gate is accepted.

## Reused constraints

- Master plan §7 requires a rebuildable manifest projection/index, separate user
  bookmarks/review state, metadata-only backup/restore, no source/media blob copy,
  no filename guessing on relink, and explicit availability states.
- The existing VI artifact layer already uses relative paths, content hashes,
  atomic JSON writes, and fail-closed manifest validation (`src/vi_dubber/artifacts.py`;
  `tests/test_artifacts.py`). This prep does not duplicate or modify that runtime.
- Existing P23/job manifests remain the source evidence for long-form processing;
  this fixture is only a future catalog consumer contract.

## Fixture and probe

- Contract: `tests/fixtures/m5_catalog_contract.json`
- Offline probe: `scripts/benchmark_m5_catalog_contract.py`
- Contract test: `tests/test_m5_catalog_contract.py`
- Dataset shape: 1,000 deterministic catalog items and 100,000 derived segments.
- Search cases: casefold title lookup, availability filter, and title prefix.
- Backup contract: metadata/manifest identity/revision/availability/user state
  included; media bytes/provider credentials/absolute paths excluded; canonical
  digest must survive a JSON round trip.
- Targets copied from master §7: local list/search p95 ≤500 ms and initial
  metadata view p95 ≤2 s. These are future measurement targets only; no timing
  result here is product acceptance.

## Explicit exclusions and next owner action

This prep does not implement SQLite schema/migrations, transaction boundaries,
catalog rebuild, relink, Review UI, media availability probing, real-media
benchmarks, OAuth/Drive, provider calls, or blob backup. After VI M0 baseline is
accepted, the M5 owner can map this contract onto the existing artifact/job
manifests, choose the persistence boundary, add migration/restore fixtures, and
run cold/warm measurements with a frozen machine/version protocol.
