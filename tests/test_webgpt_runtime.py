from __future__ import annotations

import json
from pathlib import Path

import pytest

from vi_dubber import webgpt_runtime


def test_initialize_runtime_creates_isolated_provider_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = tmp_path / "core"
    home = tmp_path / "runtime-home"
    core.mkdir()
    monkeypatch.setattr(webgpt_runtime, "_core_cli", lambda _core: core / "src" / "cli.ts")
    monkeypatch.setattr(
        webgpt_runtime,
        "_default_config_from_core",
        lambda _core, _home: {
            "version": 3,
            "releaseVersion": "5.0.8",
            "mode": "browser-only",
            "integrationOwner": "standalone",
            "host": "127.0.0.1",
            "port": 17841,
            "browserHost": "managed-chrome",
            "browserInteractionMode": "automatic",
            "storageStatePath": str(home / "browser" / "storage-state.json"),
            "runtimeCommand": ["bun", "cli.ts"],
        },
    )

    config = webgpt_runtime.initialize_runtime(core_repo=core, home=home)

    assert config["mode"] == "browser-only"
    assert config["integrationOwner"] == "cockpit"
    assert config["port"] == webgpt_runtime.DUBBER_WEBGPT_PORT
    assert config["experimentalBiggerContext"] is False
    assert config["experimentalSkillAttachments"] is False
    assert config["autoApproveToolCalls"] is False
    on_disk = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert on_disk == config


def test_initialize_runtime_preserves_existing_config_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = tmp_path / "core"
    home = tmp_path / "runtime-home"
    core.mkdir()
    home.mkdir()
    existing = {"port": webgpt_runtime.DUBBER_WEBGPT_PORT, "solAvailable": False, "custom": "keep"}
    (home / "config.json").write_text(json.dumps(existing), encoding="utf-8")
    monkeypatch.setattr(webgpt_runtime, "_core_cli", lambda _core: core / "src" / "cli.ts")

    config = webgpt_runtime.initialize_runtime(core_repo=core, home=home)

    assert config == existing


def test_start_runtime_requires_dedicated_login_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "runtime-home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps(
            {
                "port": webgpt_runtime.DUBBER_WEBGPT_PORT,
                "storageStatePath": str(home / "browser" / "storage-state.json"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(webgpt_runtime, "runtime_status", lambda **_kwargs: {"ready": False})

    with pytest.raises(RuntimeError, match="webgpt-runtime login"):
        webgpt_runtime.start_runtime(core_repo=tmp_path / "core", home=home)

