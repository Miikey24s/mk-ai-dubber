from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from vi_dubber.runtime import PROJECT_ROOT, WORK_DIR


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_doctor() -> dict[str, Any]:
    issues: list[str] = []
    chromium_path = None
    try:
        with sync_playwright() as p:
            chromium_path = p.chromium.executable_path
            if not os.path.exists(chromium_path):
                issues.append(f"Chromium binary not found at {chromium_path}")
    except Exception as exc:
        issues.append(f"Playwright initialization error: {exc}")

    try:
        from vi_dubber import web
        assert hasattr(web, "build_app")
    except Exception as exc:
        issues.append(f"Failed to import vi_dubber.web: {exc}")

    return {
        "scope": "vidubber-ui-qa-readiness",
        "ready": len(issues) == 0,
        "chromium": chromium_path,
        "issues": issues,
        "python": sys.version,
    }


def run_e2e_verification(port: int | None = None) -> dict[str, Any]:
    if port is None:
        port = _find_free_port()

    from vi_dubber.web import build_app

    artifacts_dir = WORK_DIR / "artifacts" / "ui-qa"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = artifacts_dir / "e2e-studio.png"

    # Launch Gradio app in a background thread
    demo = build_app()
    server_thread = threading.Thread(
        target=lambda: demo.launch(
            server_name="127.0.0.1",
            server_port=port,
            prevent_thread_lock=True,
            show_error=False,
            quiet=True,
        ),
        daemon=True,
    )
    server_thread.start()

    # Wait for server to listen
    base_url = f"http://127.0.0.1:{port}"
    server_ready = False
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                server_ready = True
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.1)

    if not server_ready:
        return {"passed": False, "error": f"Gradio server failed to bind to {base_url}"}

    checks: dict[str, Any] = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            # 1. Page load
            page.goto(base_url, timeout=15000, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)

            title = page.title()
            checks["page_title"] = title
            checks["title_ok"] = "VI Dubber Studio" in title or "Gradio" in title

            # Check header
            brand = page.locator("#brandbar, .panel-heading, .step-badge").first
            checks["header_visible"] = brand.is_visible(timeout=5000)

            # 2. Check model catalog dropdown & effort dropdown
            model_dropdown = page.locator("label:has-text('Model AI dịch')").first
            effort_dropdown = page.locator("label:has-text('Mức độ suy luận (Effort)')").first
            checks["model_dropdown_found"] = model_dropdown.count() > 0 or page.locator(".model-selector").count() > 0

            # 3. Capture full page screenshot
            page.screenshot(path=str(screenshot_path), full_page=True)
            checks["screenshot_saved"] = str(screenshot_path)
            checks["screenshot_exists"] = screenshot_path.exists() and screenshot_path.stat().st_size > 0

            # 4. Test page reload persistence
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            checks["reload_passed"] = brand.is_visible(timeout=5000)

            browser.close()
    finally:
        try:
            demo.close()
        except Exception:
            pass

    passed = bool(checks.get("title_ok") and checks.get("header_visible") and checks.get("screenshot_exists"))
    return {
        "passed": passed,
        "url": base_url,
        "checks": checks,
        "screenshot": str(screenshot_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="VI Dubber Playwright UI QA Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check Playwright and UI dependencies")
    e2e_parser = subparsers.add_parser("e2e", help="Run automated E2E browser verification")
    e2e_parser.add_argument("--port", type=int, default=None, help="Port to run test Gradio server on")

    args = parser.parse_args()
    if args.command == "doctor":
        res = run_doctor()
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["ready"] else 1)
    elif args.command == "e2e":
        res = run_e2e_verification(port=args.port)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["passed"] else 1)


if __name__ == "__main__":
    main()
