# M5 catalog query/cache hardening — 28/09/2026

Status: **SOFTWARE-VERIFIED / PREP_ONLY**.

This slice hardens the metadata-only catalog read boundary while Job12 remains
untouched. Catalog search now rejects oversized input (more than 256 Unicode
characters) and NUL bytes before SQLite receives the query. `GET /api/catalog`
also emits a deterministic, private ETag and supports `If-None-Match` with a
304 response, so browser revalidation does not transfer an unchanged page.

## Scope

- changed `src/vi_dubber/catalog_store.py`, `src/vi_dubber/api.py`;
- added direct-store and FastAPI boundary tests;
- no media opened or hashed;
- no provider, TypeSafe, WebGPT, OAuth, Drive, or network call;
- no Job12 file, lock, process, or state touched;
- no schema migration and no user-state mutation.

## Verification

```text
uv run pytest -q tests/test_catalog_store.py tests/test_api.py -k "search_rejects_unbounded or catalog_etag or catalog_unavailable or jobs_and_details_attach"
4 passed, 32 deselected, 2 warnings

uv run pytest -q
590 passed, 1 skipped, 2 warnings
```

The ETag is derived from the exact response payload, is quoted as an HTTP
entity tag, and is marked `Cache-Control: private, no-cache`; a matching
validator returns `304` with an empty body. This does not claim M5 real-media,
restart/relink, performance, or owner acceptance.
