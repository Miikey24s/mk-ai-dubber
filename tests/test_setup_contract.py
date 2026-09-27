from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_setup_uses_the_tracked_uv_lockfile() -> None:
    """A clean setup must not silently resolve a different dependency graph."""

    setup = (PROJECT_ROOT / "setup.ps1").read_text(encoding="utf-8")

    assert "uv sync --dev --locked" in setup
    assert "uv sync --dev\n" not in setup
    assert "uv sync --dev `" not in setup
