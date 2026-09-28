# M5 catalog metadata UI contract — PREP ONLY — 2026-09-28

## Scope

This slice adds a deterministic, metadata-only Playwright contract for the
local React catalog panel. The fixture contains only a temporary SQLite catalog
projection; it creates no media, does not open artifact bytes, and does not
start WebGPT, OAuth, connectors, or Job12.

The browser proof covers:

- responsive layout at `1440`, `768`, and `360` CSS pixels with no horizontal
  overflow;
- Vietnamese labels, keyboard focus containment, Escape close, and focus
  restoration to the Catalog trigger;
- explicit loading, transport error, stale-row, and unsupported/unavailable
  states. Unsupported/unavailable is the local read-denied equivalent: the
  panel must not turn a denied projection into an empty successful result;
- browser reload/restore of the durable catalog row and lineage revision;
- metadata-only fixture boundary, with the intentionally injected `503` as the
  only expected browser console error.

The E3 metadata benchmark remains a separate deterministic offline measurement
over 1,000 catalog items and 100,000 derived segments. Its p95 values are
recorded in `M5-catalog-metadata-ui-e3-receipt-2026-09-28.json`; every timing
has `target_decision=NOT_A_PRODUCT_CLAIM`.

## Validation

```text
uv run pytest -q tests/test_m5_catalog_metadata_ui_contract.py
1 passed

uv run pytest -q tests/test_m5_e3_benchmark.py
2 passed

uv run python scripts/benchmark_m5_e3_metadata.py --repeats 3 --output work/checkpoints/M5-catalog-metadata-ui-e3-receipt-2026-09-28.json
status=PREP_ONLY; metadata-only backup round trip=true; promotion=NO_CLAIM

git diff --check
pass
```

## Gate status

This is **PREP_ONLY** software/browser evidence. It does not close M5
real-media create/reopen/relink/export, production browser deployment, cold OS
page-cache performance, owner visual review, provider/OAuth behavior, or
whole-pipeline/Job12 acceptance.
