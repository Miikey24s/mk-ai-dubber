# M3 Watch/Review functional proof — 26/09/2026

Status: **FUNCTIONAL PROOF PASS / M3 NOT ACCEPTED**.

This checkpoint covers the representative VI Dubber `Library-like selector -> Watch -> Review -> edit -> stale preview` product fixture only. It does not close the VI/shared visual-lock gate, does not satisfy the manual Figma Make M2 gate, and does not claim the future Library persistence product scope is implemented.

## Source and test

- Repository: `D:\ANNAM\TradingWorkspace\projects\vi-dubber`
- Source HEAD at verification: `65a68a8` (`fix: reconcile P23 short correctness oracle`)
- Test: `tests/test_m3_react_ui_playwright.py`
- Command: `uv run pytest -q tests/test_m3_react_ui_playwright.py`
- Result: `1 passed in 18.73s`
- Backend: real `FastAPI create_app()` against an isolated persisted `WORK_DIR` fixture.
- Frontend: current built React/Vite product UI, not a screenshot-only mock.

## Behavior proved

- Persisted job selection reaches the intended Watch/Review fixture.
- Preview states remain distinct: READY is playable, QA BLOCK/TTS/QUEUED are not advertised as ready.
- Editing a segment goes through the real PATCH endpoint and invalidates only the dependent chunk preview.
- The edited preview becomes STALE and is no longer playable as current output; persisted state records the stale chunk.
- Vietnamese text survives edit/reload state used by the fixture.
- Keyboard focus remains usable in the exercised review path.
- 768 px and 360 px layouts have no document-level horizontal overflow in the exercised state.
- Dark and light themes preserve the same stale/disabled semantics.
- Browser console error list is empty after valid fixture media/audio were supplied.

## Evidence

Runtime evidence is under `work/artifacts/m3-vi-ui-proof-r2/` on this machine.

- `receipt.json` SHA-256: `309200c22aacd72f1c0fc4bd0713b864cb064ab30fdbd46a1b964ae7b1ffaf69`
- `01-1440-watch-ready.png`: `6e6926eb0f5787715084d87cf895be664eac9bcb1f13e1cfda8db5c5d3c7d461`
- `02-1440-review-stale.png`: `9ef20fcbf937f918402c441808a7e9ad16d87970962b8e2d320d38d22e7d060e`
- `03-768-review-stale.png`: `a39e7e09dc0d98d5eacb12ae0c4654d4673b00e94188a0ac872fb7d3daaa66fd`
- `04-360-review-stale.png`: `8cf21004c107c2ba4a59e2818458f033c6d5dab200683d919ee7ece67be4d71f`
- `05-1440-light-stale.png`: `c379ef334f37e73d37844694b2a23b0e112fc193e26189d00adc2f9d7139f14e`

## Remaining gates

- M2 remains `BLOCKED-MAKE` until the owner performs the prepared Make run and returns an accessible artifact/export.
- VI/shared visual lock still requires the existing owner-approval authority; this proof does not self-approve the visual direction.
- Full M3 acceptance still requires the plan rubric/hard-gate review against the integrated Make/selected-diff state when that branch is available.
