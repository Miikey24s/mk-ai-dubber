# M5 E3 metadata benchmark prep — PREP ONLY — 2026-09-27

## Status

`PREP ONLY`. This slice adds a reusable, deterministic E3 benchmark and
integrity receipt for the existing local catalog boundary. It does not promote
any performance target, open provider/OAuth/Drive/job12 work, or access real
media.

## What is exercised

- `tests/fixtures/m5_catalog_contract.json` remains the single deterministic
  fixture: 1,000 catalog items and 100,000 derived segments.
- `scripts/benchmark_m5_e3_metadata.py` projects that fixture into the existing
  `CatalogStore` in a temporary SQLite database, then measures the contract's
  list/search cases and one metadata view.
- Every query is checked against the fixture's expected IDs/counts before its
  timing is accepted. The harness verifies rebuild cardinality, metadata-only
  backup round-trip, and temporary-storage cleanup.
- Cold means a fresh `CatalogStore` and SQLite connection for each sample; the
  OS page cache is explicitly not flushed. Warm means the same store after one
  discarded warm-up. Each operation reports min/p50/p95/max in milliseconds.

The receipt always contains `status=PREP_ONLY`, `promotion.decision=NO_CLAIM`,
and the timing targets copied from the master plan (`list/search p95 <=500 ms`,
initial metadata view p95 <=2,000 ms). A measured value below a target is an
observation on this machine, not M5 acceptance.

## Validation

```text
uv run pytest -q tests/test_m5_e3_benchmark.py
uv run python scripts/benchmark_m5_e3_metadata.py --repeats 11 --output <receipt>
```

The benchmark is local-only and deletes its temporary SQLite files after the
run. It does not open source media, hash media bytes, call a provider, use
OAuth/Drive/network, or execute the deferred job12 path.

## Remaining gate

M5 still requires real authorized media workflow/restart/restore evidence and a
frozen machine/version protocol before E3 can be promoted. Browser/DOM memory,
multi-process contention, OS-cache-cold behavior, licensed media availability,
and connector/provider behavior remain outside this receipt.
