# M5 process startup/restart smoke — PREP ONLY — 2026-09-28

## Scope

This isolated offline rehearsal starts **three fresh Python processes** against
a temporary workspace containing two synthetic job directories and one
malformed directory:

1. `boot` projects the synthetic jobs into a new SQLite catalog and persists a
   review/bookmark state;
2. `restart` imports the real `vi_dubber.api` entrypoint, calls only the local
   deterministic `/api/health` route, then rebuilds the catalog in a new
   interpreter and verifies that the unchanged revision retains review state;
3. `lineage_change` changes one synthetic manifest fingerprint in the isolated
   workspace, starts another fresh interpreter, and verifies that the old
   review state is invalidated instead of crossing the new revision.

The child processes patch their runtime paths to the temporary workspace before
the API import. They do not use the repository `work/` directory, do not read
or write Job12, do not open media, and do not call WebGPT, TypeSafe, OAuth,
Drive, or any other provider. No application server listener is opened.

## Validation

```text
uv run pytest -q tests/test_m5_process_startup.py
1 passed

uv run python scripts/verify_m5_process_startup.py \
  --output work/checkpoints/M5-process-startup-receipt-2026-09-28.json
status=PREP_ONLY
fresh_python_processes=3; boot_indexed=2; boot_skipped=job-invalid
restart_indexed=2; app_entrypoint_imported=true; health_status=200
restart_state_retained_same_revision=true; lineage_change_invalidated_state=true

uv run python -m compileall -q scripts/verify_m5_process_startup.py
pass

git diff --check
pass
```

Machine receipt: `M5-process-startup-receipt-2026-09-28.json`.

## Explicit exclusions

This evidence remains **PREP_ONLY**. It does not close M0 or M5 product/UI
acceptance and does not prove real authorized media/playback, browser behavior,
network/listener readiness, cold/warm latency SLOs, provider/OAuth/Drive
behavior, whole-pipeline quality, or Job12 completion.
