from __future__ import annotations

"""Metadata-only browser contract for the local M5 catalog boundary.

This fixture deliberately contains no media files and does not start a
provider/runtime.  It verifies the catalog panel's local read model and the
states that can be rendered without opening an artifact: loading, error,
stale, and unavailable/unsupported (the local equivalent of a denied read).
"""

import json
import socket
import threading
import time
from pathlib import Path
from typing import Generator

import pytest
import uvicorn
from playwright.sync_api import Page, expect

import vi_dubber.api as api_mod
from vi_dubber.api import create_app
from vi_dubber.catalog_store import CatalogItem, CatalogStore, UserState


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _catalog_payload(*, status: str = "unavailable", query: str = "") -> dict[str, object]:
    return {
        "format": "vi-dubber-catalog-view-v1",
        "schema_version": 1,
        "catalog_status": status,
        "query": query,
        "availability_filter": None,
        "pagination": {"limit": 100, "offset": 0, "returned": 0},
        "items": [],
        "counts": {"returned": 0, "availability": {}},
        "metadata_only": True,
    }


@pytest.fixture
def m5_metadata_ui_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[str, Path], None, None]:
    """Serve the real local app against a catalog-only temporary workspace."""

    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)

    # No source, preview, transcript, or output artifact is created, so the
    # browser proof cannot accidentally exercise media I/O.  Catalog rows are
    # intentionally orphanable here: this is a read-only projection check,
    # while job selection belongs to the separate M3/M5 integration flow.
    job_id = "job-m5-ui-ready"
    catalog = CatalogStore(work / "catalog.sqlite3")
    catalog.rebuild(
        [
            CatalogItem(
                item_id=job_id,
                title="M5 Metadata Ready",
                source_fingerprint="a" * 64,
                revision="m5-ui-r1",
                availability="available",
                segment_count=12,
                source_ref=f"jobs/{job_id}/source/fixture.mp4",
                metadata={"fixture": "m5-metadata-ui"},
            ),
            CatalogItem(
                item_id="job-m5-ui-stale",
                title="M5 Metadata Stale",
                source_fingerprint="b" * 64,
                revision="m5-ui-stale-r2",
                availability="stale",
                segment_count=4,
                source_ref="jobs/job-m5-ui-stale/source/fixture.mp4",
                metadata={"fixture": "m5-metadata-ui"},
            ),
            CatalogItem(
                item_id="job-m5-ui-denied",
                title="M5 Metadata Unknown",
                source_fingerprint="c" * 64,
                revision="m5-ui-unknown-r1",
                availability="unknown",
                segment_count=0,
                source_ref="jobs/job-m5-ui-denied/source/fixture.mp4",
                metadata={"fixture": "m5-metadata-ui"},
            ),
        ]
    )
    catalog.set_user_state(
        UserState(
            item_id=job_id,
            revision="m5-ui-r1",
            review_state="in_review",
            watch_position_seconds=12.5,
        )
    )

    # create_app reads the module-level work root.  Patch only this isolated
    # process; no persistent project state, runtime, provider, or Job12 path is
    # touched.
    monkeypatch.setattr(api_mod, "WORK_DIR", work)
    port = _find_free_port()
    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail(f"M5 metadata UI fixture server did not start at {base_url}")

    try:
        yield base_url, work
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _stub_system(page: Page) -> None:
    page.route(
        "**/api/system",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "gpu_name": "M5 metadata fixture",
                    "gpu_vram_used_bytes": 0,
                    "gpu_vram_total_bytes": 0,
                    "gpu_utilization_pct": 0,
                    "cpu_utilization_pct": 0,
                    "active_jobs_count": 1,
                    "webgpt_connected": False,
                    "webgpt_model": "fixture",
                    "webgpt_port": 17850,
                    "server_uptime_seconds": 0,
                    "websocket_connected": True,
                }
            ),
        ),
    )


def _assert_no_horizontal_overflow(page: Page, width: int) -> None:
    metrics = page.evaluate(
        """() => ({
          innerWidth: window.innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          bodyScrollWidth: document.body.scrollWidth,
        })"""
    )
    assert metrics["innerWidth"] == width
    assert metrics["scrollWidth"] <= width, metrics
    assert metrics["bodyScrollWidth"] <= width, metrics


def test_m5_catalog_metadata_states_responsive_focus_and_restore(
    page: Page,
    m5_metadata_ui_server: tuple[str, Path],
) -> None:
    base_url, _work = m5_metadata_ui_server
    console_errors: list[str] = []
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
    )
    _stub_system(page)
    page.add_init_script("localStorage.clear()")

    for width, height in ((1440, 900), (768, 1024), (360, 800)):
        page.set_viewport_size({"width": width, "height": height})
        page.goto(base_url, wait_until="networkidle", timeout=30000)
        trigger = page.get_by_role("button", name="Catalog")
        expect(trigger).to_be_visible(timeout=10000)
        trigger.click()
        dialog = page.get_by_role("dialog", name="Catalog cục bộ")
        expect(dialog).to_be_visible(timeout=5000)
        expect(dialog.get_by_placeholder("Tìm theo tên hoặc nguồn...")).to_be_focused()
        expect(dialog.get_by_text("3 mục metadata", exact=True)).to_be_visible(timeout=5000)
        expect(dialog.get_by_text("1 mục cần relink", exact=True)).to_be_visible(timeout=5000)
        expect(dialog.get_by_text("Đã sẵn sàng khôi phục sau restart", exact=True)).to_be_visible(timeout=5000)
        _assert_no_horizontal_overflow(page, width)

        # Keyboard contract: focus remains inside the dialog and Escape
        # restores it to the invoking Catalog control.
        page.keyboard.press("Tab")
        assert page.evaluate("(el) => el.contains(document.activeElement)", dialog.element_handle())
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("(el) => el.contains(document.activeElement)", dialog.element_handle())
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        expect(trigger).to_be_focused()

    # Reopen at desktop width for the state matrix and persistence checks.
    page.set_viewport_size({"width": 1440, "height": 900})
    page.get_by_role("button", name="Catalog").click()
    dialog = page.get_by_role("dialog", name="Catalog cục bộ")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("M5 Metadata Ready", exact=True)).to_be_visible(timeout=5000)
    expect(dialog.get_by_text("m5-ui-r1", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="M5 Metadata Ready")).to_contain_text("in_review")

    # A stale item remains explicit and searchable; it is never turned into a
    # playable/downloadable artifact by this metadata-only panel.
    dialog.get_by_placeholder("Tìm theo tên hoặc nguồn...").fill("Stale")
    expect(dialog.get_by_text("M5 Metadata Stale", exact=True)).to_be_visible(timeout=5000)
    expect(dialog.get_by_role("button", name="M5 Metadata Stale Cũ")).to_be_visible()

    # Reload is the restart/restore boundary: the durable catalog projection is
    # reconstructed from SQLite read data, while no browser-only cache is used.
    page.reload(wait_until="networkidle", timeout=30000)
    page.get_by_role("button", name="Catalog").click()
    dialog = page.get_by_role("dialog", name="Catalog cục bộ")
    expect(dialog.get_by_text("M5 Metadata Ready", exact=True)).to_be_visible(timeout=5000)
    expect(dialog.get_by_text("m5-ui-r1", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="M5 Metadata Ready")).to_contain_text("in_review")

    # Unsupported/unavailable is the local denied-read rendering.  It must be
    # explicit instead of presenting an empty successful catalog.
    page.route(
        "**/api/catalog?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_catalog_payload(status="unsupported", query="denied")),
        ),
    )
    dialog.get_by_placeholder("Tìm theo tên hoặc nguồn...").fill("denied")
    expect(dialog.get_by_text("Catalog chưa sẵn sàng", exact=False)).to_be_visible(timeout=5000)
    unavailable_retry = dialog.get_by_role("button", name="Thử lại")
    expect(unavailable_retry).to_be_visible()
    unavailable_retry.click()
    expect(dialog.get_by_text("Catalog chưa sẵn sàng", exact=False)).to_be_visible(timeout=5000)

    # A transport error is distinct from unavailable/unsupported and has its
    # own actionable error state.
    page.unroute("**/api/catalog?*")
    page.route("**/api/catalog?*", lambda route: route.fulfill(status=503, body="fixture error"))
    dialog.get_by_placeholder("Tìm theo tên hoặc nguồn...").fill("error")
    expect(dialog.get_by_text("Không tải được catalog", exact=False)).to_be_visible(timeout=5000)
    error_retry = dialog.get_by_role("button", name="Thử lại")
    expect(error_retry).to_be_visible()
    error_retry.click()
    expect(dialog.get_by_text("Không tải được catalog", exact=False)).to_be_visible(timeout=5000)

    # Loading is observable while a local metadata request is held.  Leaving
    # the route pending is deliberate: it keeps this proof deterministic and
    # never waits on a provider/runtime.
    page.unroute("**/api/catalog?*")
    held_route: dict[str, object] = {}

    def hold_catalog_request(route: object) -> None:
        held_route["route"] = route

    page.route("**/api/catalog?*", hold_catalog_request)
    dialog.get_by_placeholder("Tìm theo tên hoặc nguồn...").fill("loading")
    expect(dialog.get_by_text("Đang tải catalog...", exact=False)).to_be_visible(timeout=2000)
    route = held_route.get("route")
    assert route is not None
    route.fulfill(  # type: ignore[attr-defined]
        status=200,
        content_type="application/json",
        body=json.dumps(_catalog_payload(status="unavailable", query="loading")),
    )
    # The only console error is the intentionally injected 503 transport
    # failure used to prove the error state; real fixture traffic stays clean.
    unexpected_console = [message for message in console_errors if "503" not in message]
    assert unexpected_console == [], "\n".join(unexpected_console)
