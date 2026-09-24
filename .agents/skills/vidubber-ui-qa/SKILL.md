---
name: vidubber-ui-qa
description: Verify VI Dubber Gradio web UI with scoped Playwright CLI/tests, selective visual evidence, and durable handoffs. Use for UI acceptance, catalog dynamic binding, review editor selective rerender, and browser reload persistence without desktop takeover.
---

# VI Dubber UI QA

Verify the VI Dubber studio web interface using Playwright CLI and automated browser scripts. Scope covers UI acceptance, live model catalog interaction, segment review editing, selective rerender validation, and reload persistence.

## 1. Start from the existing kit

From `D:\ANNAM\TradingWorkspace\projects\vi-dubber`:

```powershell
uv run python tools/ui_qa.py doctor
```

Or from `D:\ANNAM\TradingWorkspace` using the workspace QA kit:

```powershell
node tooling/ui-qa/qa.mjs doctor
```

Doctor is read-only. It verifies:
- Python Playwright runtime and Chromium executable (`chromium_headless_shell`).
- Gradio web app dependencies and port availability.
- No personal profile, no external telemetry.

## 2. Choose the smallest useful check

- **Automated E2E Test Suite**:
  Run deterministic Playwright tests in headless Chromium:
  ```powershell
  uv run pytest -q tests/test_web_e2e_playwright.py
  ```
- **One-Command Full E2E Verification**:
  Runs an ephemeral server, executes the complete user journey, and captures screenshots:
  ```powershell
  uv run python tools/ui_qa.py e2e
  ```
- **Scoped Interactive CLI Session**:
  Use isolated, named sessions for exploratory testing against a running server:
  ```powershell
  # Using Python tool:
  uv run python tools/ui_qa.py cli --session vidubber-p16 --open http://127.0.0.1:7860
  uv run python tools/ui_qa.py cli --session vidubber-p16 --snapshot
  uv run python tools/ui_qa.py cli --session vidubber-p16 --screenshot
  uv run python tools/ui_qa.py cli --session vidubber-p16 --close

  # Or using workspace Node qa.mjs:
  node tooling/ui-qa/qa.mjs cli --session tw-vidubber-p16 --origin http://127.0.0.1:7860 -- open http://127.0.0.1:7860
  node tooling/ui-qa/qa.mjs cli --session tw-vidubber-p16 -- snapshot
  node tooling/ui-qa/qa.mjs cli --session tw-vidubber-p16 -- screenshot
  node tooling/ui-qa/qa.mjs cli --session tw-vidubber-p16 -- close
  ```

## 3. Evidence before acceptance

Never claim UI accepted by merely checking selector existence. Test real behaviors against expected oracles:
1. **Model Catalog & Dynamic Effort**:
   - Model `chatgpt-web/gpt-5.6-sol` exposes efforts `["medium", "high"]`, default `high`.
   - Switching model to `chatgpt-web/gpt-5.6-sol-instant` dynamically updates the effort dropdown DOM to `["low"]`, value `low`.
2. **Review Editor & Selective Invalidation**:
   - Editing a segment text changes its badge to `CẦN RENDER`.
   - The "Render lại đoạn đã sửa" button transitions from disabled/hidden to `interactive=True`.
   - Saving preserves untouched segment cache files and only invalidates downstream audio for the modified segment.
3. **Browser Reload Persistence**:
   - Simulating `page.reload()` retains the active job selection, review state, and audio player bindings.
4. **Visual Evidence**:
   - Capture screenshots at key checkpoints (`work/artifacts/ui-qa/` or `work/p16-ui-accept/`).
   - Review images for layout clipping, font rendering, and modal visibility.

## 4. Boundaries

- **No Personal Profiles**: Always launch isolated browser contexts with clean storage.
- **Strict Loopback Origin**: Only allow `http://127.0.0.1:<port>` or `http://localhost:<port>`. Never send requests to external domains.
- **Bounded Auto-Wait**: Use Playwright web-first locators with sensible timeouts (5–10s max); avoid hardcoded arbitrary sleeps.
- **Deterministic Receipts**: Save structured JSON test receipts and screenshots locally before marking UI tasks complete.
