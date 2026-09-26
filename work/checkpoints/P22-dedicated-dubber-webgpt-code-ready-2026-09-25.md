# P22 Dedicated Dubber-WebGPT — code-ready checkpoint (25/09/2026)

## Decision

Managed WebGPT instance 2 on `127.0.0.1:17842` remains historical P21 evidence only. The active target is Dedicated Dubber-WebGPT on `127.0.0.1:17850` with its own runtime home/browser login/session and direct provider-only Responses transport.

## Implemented

- Added `vi-dubber webgpt-runtime init|login|start|status|stop`.
- Dedicated runtime home defaults to `D:\ANNAM\TradingWorkspace\.runtime\dubber-webgpt` and stays outside Git.
- Dedicated runtime reuses the current WebGPT core checkout but does not mutate the global Codex/Cockpit route.
- Product translation defaults to `http://127.0.0.1:17850/v1` and `webgpt_transport: direct-responses`.
- Active UI/API/CLI copy and frontend mock data moved from `:17842 / instance 2` to Dedicated Dubber-WebGPT `:17850`.
- Frontend production bundle rebuilt after the migration.
- CLI/direct pipeline path snapshots live model/effort/display/catalog metadata when a WebGPT model selection is explicitly supplied, while preserving local-only early failure tests when no external selection exists.

## Verification

- `17841`: still listening for the existing Codex/Cockpit runtime.
- `17842`: free; intentionally not reused.
- `17850`: free/offline until the dedicated account login is created.
- `uv run vi-dubber webgpt-runtime init`: pass.
- `uv run vi-dubber webgpt-runtime status`: correctly reports dedicated login missing and runtime offline.
- `npm run build`: pass (`tsc && vite build`).
- Focused pipeline foundation regression: `21 passed`.
- Full Python suite: `385 passed, 2 warnings`.

## Remaining live gate

The dedicated browser profile intentionally has no copied cookie/session from the daily Codex runtime. One manual account step is required:

```powershell
uv run vi-dubber webgpt-runtime login
```

After that, continue P22 in this order:

1. `uv run vi-dubber webgpt-runtime start`
2. verify `/healthz` and `/v1/models` on `:17850`
3. run direct Responses P04 fixture and terminology/critical-token gates
4. re-run P16 UI live catalog/status acceptance
5. run P20 restart/disconnect/model-missing/capacity fault gates
6. only then benchmark repeated warm c1/c2; do not mix migration correctness with concurrency promotion

P22 must remain **IN PROGRESS** until those live gates pass.
