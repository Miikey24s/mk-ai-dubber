# M5 catalog browser acceptance — 2026-09-28

Status: **PREP_ONLY**.

The local React/Vite UI was exercised through the real FastAPI app and an isolated temporary work directory. A Chromium Playwright proof opened the Catalog panel, read the durable `/api/catalog` projection, filtered `available` rows through the backend query, selected the persisted job, reloaded the browser, reopened the panel, and verified the same revision/review metadata remained visible. The dialog also traps keyboard focus and restores focus to the Catalog trigger after close.

Validation:

- focused catalog browser flow: `1 passed`;
- existing M3 React browser regression: `2 passed`;
- M5 API/startup/recovery/catalog regression: `49 passed, 1 skipped`;
- browser console errors: none.

The fixture is metadata-only. It does not call providers, OAuth, external connectors, real media, or Job12. This proves the local UI/read-model interaction and browser reload behavior; it does not close real-media relink, external connector, production browser deployment, or whole-M5 acceptance gates.
