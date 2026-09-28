from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Generator

import pytest
import uvicorn
from playwright.sync_api import Page, expect

import vi_dubber.api as api_mod
from vi_dubber.api import create_app
from vi_dubber.artifacts import atomic_write_json
from vi_dubber.catalog_store import CatalogItem, CatalogStore, UserState
from vi_dubber.jobs import update_job_state
from vi_dubber.longform import MacroChunk
from vi_dubber.longform_state import commit_chunk_stage
from vi_dubber.runtime import PROJECT_ROOT, WORK_DIR


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _ffmpeg_path() -> Path:
    candidates = sorted((PROJECT_ROOT / "tools" / "ffmpeg").rglob("ffmpeg.exe"))
    if not candidates:
        pytest.skip("Bundled ffmpeg.exe is required for the M3 product fixture")
    return candidates[0]


def _write_valid_preview(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(_ffmpeg_path()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=640x360:d=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _write_valid_voice_track(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(_ffmpeg_path()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _seed_m3_fixture(work: Path) -> tuple[str, str]:
    target_id = "job-m3-watch-review"
    target = work / target_id
    target.mkdir(parents=True, exist_ok=True)

    segments = [
        {
            "id": 0,
            "start": 1.0,
            "end": 2.0,
            "text": "The first source sentence.",
            "vi": "Câu dịch đầu tiên.",
            "speaker": "SPEAKER_00",
            "review_status": "unreviewed",
        },
        {
            "id": 1,
            "start": 4.0,
            "end": 5.0,
            "text": "The second ready sentence.",
            "vi": "Câu dịch thứ hai vẫn tua được.",
            "speaker": "SPEAKER_00",
            "review_status": "unreviewed",
        },
        {
            "id": 2,
            "start": 11.0,
            "end": 12.0,
            "text": "The blocked chunk stays unavailable.",
            "vi": "Phần bị chặn vẫn chưa thể dùng.",
            "speaker": "SPEAKER_00",
            "review_status": "needs_review",
        },
        {
            "id": 3,
            "start": 21.0,
            "end": 22.0,
            "text": "The current chunk is still processing.",
            "vi": "Phần hiện tại vẫn đang xử lý.",
            "speaker": "SPEAKER_00",
            "review_status": "unreviewed",
        },
    ]
    atomic_write_json(target / "segments_vi.json", segments)

    chunks = []
    for index in range(4):
        start = float(index * 10)
        chunks.append(
            {
                "chunk_id": f"chunk_{index + 1:04d}",
                "index": index,
                "source_start": start,
                "source_end": start + 10.0,
                "context_start": start,
                "context_end": start + 10.0,
                "boundary_reason": "m3_ui_fixture",
            }
        )
    atomic_write_json(target / "chunks" / "plan.json", {"version": 1, "chunks": chunks})

    preview_path = target / "chunks" / "chunk_0001" / "preview" / "PREVIEW_chunk_0001.mp4"
    _write_valid_preview(preview_path)
    _write_valid_voice_track(target / "voice_track.wav")
    preview_meta = target / "chunks" / "chunk_0001" / "preview.json"
    atomic_write_json(
        preview_meta,
        {
            "version": 1,
            "kind": "preview",
            "label": "PREVIEW",
            "final": False,
            "stale": False,
            "chunk_id": "chunk_0001",
            "index": 0,
            "start": 0.0,
            "end": 10.0,
            "duration": 10.0,
            "qa_status": "passed",
            "artifact": "chunks/chunk_0001/preview/PREVIEW_chunk_0001.mp4",
        },
    )
    commit_chunk_stage(
        target,
        MacroChunk(
            chunk_id="chunk_0001",
            index=0,
            source_start=0.0,
            source_end=10.0,
            context_start=0.0,
            context_end=10.0,
            boundary_reason="m3_ui_fixture",
        ),
        "preview",
        inputs={"fixture": "m3-react-ui"},
        artifacts=[preview_path, preview_meta],
        versions={"policy": 1},
    )

    update_job_state(
        target,
        status="paused",
        stage="tts",
        progress=0.62,
        message="M3 representative UI proof fixture",
        metadata={
            "input_name": "M3 Watch Review Fixture",
            "profile": "balanced_fast",
            "translation_model": "product-fixture",
            "source_mode": "Local",
            "longform": {
                "enabled": True,
                "phase": "tts",
                "current_chunk": "chunk_0003",
                "completed_chunks": 2,
                "completed_tts_chunks": 1,
                "total_chunks": 4,
                "preview_total_chunks": 4,
                "preview_ready_chunks": ["chunk_0001"],
                "preview_stale_chunks": [],
                "preview_blocked_chunks": ["chunk_0002"],
                "preview_policy_version": 1,
                "eta_seconds": 145.0,
            },
        },
    )

    # A newer job keeps the target out of the default selection so the browser
    # must exercise the real job archive selector before entering Watch/Review.
    decoy_id = "job-m3-library-entry"
    decoy = work / decoy_id
    decoy.mkdir(parents=True, exist_ok=True)
    _write_valid_preview(decoy / "output.mp4")
    _write_valid_voice_track(decoy / "voice_track.wav")
    time.sleep(0.01)
    update_job_state(
        decoy,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Library entry used to prove job selection",
        metadata={
            "input_name": "M3 Library Entry",
            "profile": "balanced_fast",
            "translation_model": "product-fixture",
            "source_mode": "Local",
        },
    )
    atomic_write_json(decoy / "segments_vi.json", [])

    # M5 catalog rows are seeded independently from the job archive so this
    # browser proof exercises the durable metadata projection and the real
    # CatalogPanel API path. The source fingerprints are fixture identities;
    # no media bytes or provider state are involved in the UI acceptance.
    catalog = CatalogStore(work / "catalog.sqlite3")
    catalog.rebuild(
        [
            CatalogItem(
                item_id=target_id,
                title="M3 Watch Review Fixture",
                source_fingerprint="c" * 64,
                revision="catalog-m3-target-r1",
                availability="available",
                segment_count=len(segments),
                source_ref=f"jobs/{target_id}/source/fixture.mp4",
                metadata={"fixture": "m5-catalog-browser"},
            ),
            CatalogItem(
                item_id=decoy_id,
                title="M3 Library Entry",
                source_fingerprint="d" * 64,
                revision="catalog-m3-decoy-r1",
                availability="missing",
                segment_count=0,
                source_ref=f"jobs/{decoy_id}/source/fixture.mp4",
                metadata={"fixture": "m5-catalog-browser"},
            ),
        ]
    )
    catalog.set_user_state(
        UserState(
            item_id=target_id,
            revision="catalog-m3-target-r1",
            review_state="in_review",
            watch_position_seconds=4.0,
        )
    )
    return target_id, decoy_id


@pytest.fixture
def m3_react_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[str, str, Path], None, None]:
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    target_id, _ = _seed_m3_fixture(work)
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
        pytest.fail(f"M3 React fixture server did not start at {base_url}")

    try:
        yield base_url, target_id, work
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _layout_metrics(page: Page) -> dict[str, int | bool]:
    return page.evaluate(
        """() => ({
          innerWidth: window.innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          innerHeight: window.innerHeight,
          scrollHeight: document.documentElement.scrollHeight,
          bodyOverflowX: document.body.scrollWidth > window.innerWidth,
        })"""
    )


def test_m3_library_watch_review_edit_stale_flow(
    page: Page,
    m3_react_server: tuple[str, str, Path],
) -> None:
    base_url, target_id, work = m3_react_server
    artifact_dir = WORK_DIR / "artifacts" / "m3-vi-ui-proof-r2"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page.on(
        "console",
        lambda msg: console_errors.append(
            f"{msg.text} @ {(msg.location or {}).get('url', '')}"
        )
        if msg.type == "error"
        else None,
    )
    page.add_init_script("localStorage.clear()")
    page.route(
        "**/api/system",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "gpu_name": "M3 isolated fixture",
                    "gpu_vram_used_bytes": 0,
                    "gpu_vram_total_bytes": 0,
                    "gpu_utilization_pct": 0,
                    "cpu_utilization_pct": 0,
                    "active_jobs_count": 0,
                    "webgpt_connected": False,
                    "webgpt_model": "fixture",
                    "webgpt_port": 17850,
                    "server_uptime_seconds": 0,
                    "websocket_connected": True,
                }
            ),
        ),
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(base_url, wait_until="networkidle", timeout=30000)

    shared_control = page.locator("button.ui-button.ui-button--neutral").first
    expect(shared_control).to_be_visible()
    shared_control_style = page.evaluate(
        """(el) => {
          const style = getComputedStyle(el);
          return {
            focus: style.getPropertyValue('--ui-focus').trim(),
            background: style.backgroundColor,
            borderStyle: style.borderStyle,
          };
        }""",
        shared_control.element_handle(),
    )
    assert shared_control_style["focus"] in {"#2563eb", "#60a5fa"}
    assert shared_control_style["background"] != "rgba(0, 0, 0, 0)"
    assert shared_control_style["borderStyle"] == "solid"

    # Library-like archive selector: prove the target is chosen from persisted jobs.
    archive_trigger = page.get_by_text("M3 Library Entry", exact=True).first
    expect(archive_trigger).to_be_visible(timeout=10000)
    archive_trigger.hover()
    target_choice = page.get_by_text("M3 Watch Review Fixture", exact=True).first
    expect(target_choice).to_be_visible(timeout=5000)
    target_choice.click()
    expect(page.get_by_text("M3 Watch Review Fixture", exact=True).first).to_be_visible()

    # Watch: persisted bilingual transcript and all representative preview states.
    expect(page.get_by_text("The first source sentence.", exact=True)).to_be_visible(timeout=10000)
    expect(page.locator("button:has-text('#0001'):has-text('READY')")).to_be_enabled()
    expect(page.locator("button:has-text('#0002'):has-text('QA BLOCK')")).to_be_disabled()
    expect(page.locator("button:has-text('#0003'):has-text('TTS')")).to_be_disabled()
    expect(page.locator("button:has-text('#0004'):has-text('QUEUED')")).to_be_disabled()
    page.locator("button:has-text('#0001'):has-text('READY')").click()
    expect(page.get_by_text("PREVIEW · Part 1/4", exact=False)).to_be_visible(timeout=5000)
    video = page.locator("video").first
    expect(video).to_be_visible()
    page.wait_for_function(
        "el => el.readyState >= 1",
        arg=video.element_handle(),
        timeout=10000,
    )
    download_link = page.get_by_title("Tải preview chunk")
    expect(download_link).to_be_visible()
    assert download_link.get_attribute("href") == (
        f"/api/jobs/{target_id}/previews/chunk_0001?download=true"
    )

    # Native fullscreen -> Import must await fullscreen exit before the dialog
    # takes focus, then keep keyboard focus contained and restore it on close.
    fullscreen_button = page.get_by_title("Fullscreen")
    fullscreen_button.click()
    page.wait_for_function("document.fullscreenElement !== null", timeout=5000)
    import_button = page.locator('button[title="Import Video / YouTube"]').first
    import_button.click()
    page.wait_for_function("document.fullscreenElement === null", timeout=5000)
    dialog = page.locator('[role="dialog"][aria-modal="true"]')
    expect(dialog).to_be_visible()
    assert page.evaluate(
        "(el) => el.contains(document.activeElement)",
        dialog.element_handle(),
    )
    page.keyboard.press("Tab")
    assert page.evaluate(
        "(el) => el.contains(document.activeElement)",
        dialog.element_handle(),
    )
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(
        "(el) => el.contains(document.activeElement)",
        dialog.element_handle(),
    )
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    assert page.evaluate(
        "(el) => document.activeElement === el",
        import_button.element_handle(),
    )

    # Intentional segment navigation must seek even while media is playing.
    page.evaluate("(el) => el.play()", video.element_handle())
    page.wait_for_function(
        "el => !el.paused && el.currentTime > 0.2",
        arg=video.element_handle(),
        timeout=5000,
    )
    page.get_by_text("The second ready sentence.", exact=True).click()
    page.wait_for_function(
        "el => Math.abs(el.currentTime - 4.0) < 0.75 && !el.paused",
        arg=video.element_handle(),
        timeout=5000,
    )
    page.screenshot(path=str(artifact_dir / "01-1440-watch-ready.png"), full_page=True)

    # Review: use the real PATCH endpoint. The product backend must invalidate
    # only the edited chunk and the UI must stop advertising its old preview.
    editor = page.locator("textarea").first
    expect(editor).to_have_value("Câu dịch đầu tiên.")
    editor.fill("Câu dịch M3 đã chỉnh.")
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and f"/api/jobs/{target_id}/segments/0" in response.url
    ) as response_info:
        page.get_by_title("Lưu").first.click()
    response = response_info.value
    assert response.status == 200
    receipt = response.json()["receipt"]
    assert receipt["kind"] == "content_edit"
    assert receipt["longform_chunk_id"] == "chunk_0001"

    expect(page.get_by_text("chunk stale sau edit", exact=False)).to_be_visible(timeout=10000)
    stale_chunk = page.locator("button:has-text('#0001'):has-text('STALE')")
    expect(stale_chunk).to_be_disabled()
    expect(page.get_by_text("PREVIEW · Part 1/4", exact=False)).to_have_count(0)

    state = json.loads((work / target_id / "state.json").read_text(encoding="utf-8"))
    longform = state["metadata"]["longform"]
    assert longform["preview_ready_chunks"] == []
    assert longform["preview_stale_chunks"] == ["chunk_0001"]
    assert longform["preview_blocked_chunks"] == ["chunk_0002"]
    assert not (
        work
        / target_id
        / "chunks"
        / "chunk_0001"
        / "manifests"
        / "preview.json"
    ).exists()
    page.screenshot(path=str(artifact_dir / "02-1440-review-stale.png"), full_page=True)

    # Accessibility/responsive proof: keyboard focus is visible, Vietnamese glyphs
    # survive, and the product page itself does not overflow at M3 target widths.
    editor.focus()
    assert page.evaluate("(el) => document.activeElement === el", editor.element_handle())
    editor.press("Tab")
    assert page.evaluate("document.activeElement !== document.body")

    responsive: dict[str, dict[str, int | bool]] = {}
    for width, height in ((768, 1024), (360, 800)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        expect(editor).to_be_visible()
        expect(editor).to_have_value("Câu dịch M3 đã chỉnh.")
        responsive[str(width)] = _layout_metrics(page)
        assert responsive[str(width)]["scrollWidth"] <= width
        page.screenshot(
            path=str(artifact_dir / f"0{3 if width == 768 else 4}-{width}-review-stale.png"),
            full_page=True,
        )

    # Both themes must preserve the same stale state and interaction contract.
    page.set_viewport_size({"width": 1440, "height": 900})
    page.get_by_label("Chế độ Sáng").click()
    expect(page.locator("html")).not_to_have_class("dark")
    expect(stale_chunk).to_be_disabled()
    page.screenshot(path=str(artifact_dir / "05-1440-light-stale.png"), full_page=True)

    # Media decode/network failures would be console errors in this proof. Ignore
    # React DevTools notices; they are not console.error.
    assert console_errors == [], "\n".join(console_errors)
    (artifact_dir / "receipt.json").write_text(
        json.dumps(
            {
                "scope": "M3 VI representative Library -> Watch -> Review edit/stale product fixture",
                "backend": "FastAPI create_app with isolated persisted WORK_DIR",
                "frontend": "built React/Vite dist from current HEAD",
                "target_job": target_id,
                "states_proved": ["ready", "qa-blocked", "processing", "queued", "stale-after-edit"],
                "responsive": responsive,
                "console_errors": console_errors,
                "screenshots": sorted(p.name for p in artifact_dir.glob("*.png")),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_m5_catalog_panel_search_filter_select_and_reload(
    page: Page,
    m3_react_server: tuple[str, str, Path],
) -> None:
    """Prove the local catalog survives browser reopen and drives job selection."""

    base_url, _target_id, _work = m3_react_server
    console_errors: list[str] = []
    page.on(
        "console",
        lambda msg: console_errors.append(
            f"{msg.text} @ {(msg.location or {}).get('url', '')}"
        )
        if msg.type == "error"
        else None,
    )
    page.add_init_script("localStorage.clear()")
    page.route(
        "**/api/system",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "gpu_name": "M5 catalog fixture",
                    "gpu_vram_used_bytes": 0,
                    "gpu_vram_total_bytes": 0,
                    "gpu_utilization_pct": 0,
                    "cpu_utilization_pct": 0,
                    "active_jobs_count": 0,
                    "webgpt_connected": False,
                    "webgpt_model": "fixture",
                    "webgpt_port": 17850,
                    "server_uptime_seconds": 0,
                    "websocket_connected": True,
                }
            ),
        ),
    )
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(base_url, wait_until="networkidle", timeout=30000)

    open_catalog = page.get_by_role("button", name="Catalog")
    expect(open_catalog).to_be_visible(timeout=10000)
    open_catalog.click()
    dialog = page.get_by_role("dialog", name="Catalog cục bộ")
    expect(dialog).to_be_visible(timeout=5000)
    target_row = dialog.get_by_role("button", name="M3 Watch Review Fixture")
    decoy_row = dialog.get_by_role("button", name="M3 Library Entry")
    search = dialog.get_by_placeholder("Tìm theo tên hoặc nguồn...")
    close_button = dialog.get_by_role("button", name="Đóng")
    expect(search).to_have_attribute("maxlength", "256")
    expect(search).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(close_button).to_be_focused()
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(
        "(el) => el.contains(document.activeElement)",
        dialog.element_handle(),
    )
    search.focus()
    expect(target_row).to_be_visible(timeout=5000)
    expect(decoy_row).to_be_visible(timeout=5000)
    expect(target_row).to_contain_text("Review: in_review")
    expect(target_row).to_contain_text("catalog-m3-target-r1")

    # The availability filter is backed by the API query, not a client-side
    # guess; selecting available removes the missing decoy row.
    dialog.locator("#catalog-availability").select_option("available")
    expect(target_row).to_be_visible(timeout=5000)
    expect(decoy_row).to_have_count(0)

    search.fill("M3 Watch Review Fixture")
    expect(target_row).to_be_visible(timeout=5000)
    target_row.click()
    expect(dialog).to_have_count(0)
    assert page.evaluate(
        "(el) => document.activeElement === el",
        open_catalog.element_handle(),
    )

    # Selection updates the same persisted job state used by Watch/Review.
    expect(page.get_by_role("button", name="M3 Watch Review Fixture").first).to_be_visible()
    page.reload(wait_until="networkidle", timeout=30000)
    page.get_by_role("button", name="Catalog").click()
    dialog = page.get_by_role("dialog", name="Catalog cục bộ")
    expect(dialog).to_be_visible(timeout=5000)
    expect(dialog.get_by_role("button", name="M3 Watch Review Fixture")).to_be_visible(timeout=5000)
    expect(dialog.get_by_text("M3 Library Entry", exact=True)).to_be_visible(timeout=5000)
    expect(dialog.locator("#catalog-availability")).to_have_value("")
    assert page.url == base_url + "/"
    assert console_errors == [], "\n".join(console_errors)
