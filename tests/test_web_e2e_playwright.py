from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Generator

import pytest
from playwright.sync_api import Page, expect

from vi_dubber.runtime import WORK_DIR
from vi_dubber.web import build_app


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def gradio_server() -> Generator[str, None, None]:
    port = _find_free_port()
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

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.1)

    yield base_url

    try:
        demo.close()
    except Exception:
        pass


def test_playwright_studio_title_and_layout(page: Page, gradio_server: str) -> None:
    page.goto(gradio_server, timeout=20000, wait_until="domcontentloaded")
    expect(page).to_have_title("VI Dubber Studio")

    # Step heading checks
    expect(page.locator("text=BƯỚC 01")).to_be_visible()
    expect(page.locator("text=Nguồn video")).to_be_visible()

    # Capture visual verification screenshot
    out_dir = WORK_DIR / "artifacts" / "ui-qa"
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_path = out_dir / "playwright_layout_check.png"
    page.screenshot(path=str(shot_path), full_page=True)
    assert shot_path.exists() and shot_path.stat().st_size > 0


def test_playwright_model_catalog_live_dropdown(page: Page, gradio_server: str) -> None:
    page.goto(gradio_server, timeout=20000, wait_until="domcontentloaded")

    # Label for translation model dropdown
    model_label = page.locator("text=Model dịch").first
    expect(model_label).to_be_visible(timeout=10000)

    # Effort dropdown label
    effort_label = page.locator("text=Effort").first
    expect(effort_label).to_be_visible(timeout=10000)

    # Check that the catalog banner reflects instance 2 connection
    status_banner = page.locator("text=17842").first
    expect(status_banner).to_be_visible(timeout=10000)


def test_playwright_browser_reload_preserves_ui(page: Page, gradio_server: str) -> None:
    page.goto(gradio_server, timeout=20000, wait_until="domcontentloaded")
    expect(page.locator("text=BƯỚC 01")).to_be_visible()

    # Perform full browser page reload
    page.reload(wait_until="domcontentloaded")
    expect(page).to_have_title("VI Dubber Studio")
    expect(page.locator("text=BƯỚC 01")).to_be_visible()
    expect(page.locator("text=17842").first).to_be_visible(timeout=10000)
