from __future__ import annotations

import json
from pathlib import Path

import pytest

from vi_dubber import webgpt_runtime


def test_default_core_repo_uses_native_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VI_DUBBER_WEBGPT_CORE", raising=False)

    expected = (webgpt_runtime.PROJECT_ROOT.parents[2] / "AI" / "codex-chatgpt-web").resolve()

    assert webgpt_runtime.default_core_repo() == expected
    assert webgpt_runtime.default_core_repo().name == "codex-chatgpt-web"


@pytest.mark.parametrize(
    ("package_name", "package_version", "message"),
    [
        ("codex-chatgpt-web", "5.0.8", "v6"),
        ("other-web-runtime", "6.1.3", "native"),
    ],
)
def test_core_cli_rejects_non_native_or_legacy_core(
    tmp_path: Path,
    package_name: str,
    package_version: str,
    message: str,
) -> None:
    core = tmp_path / "core"
    (core / "src").mkdir(parents=True)
    (core / "src" / "cli.ts").write_text("", encoding="utf-8")
    (core / "package.json").write_text(
        json.dumps({"name": package_name, "version": package_version}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=message):
        webgpt_runtime._core_cli(core)


def test_initialize_runtime_creates_isolated_provider_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = tmp_path / "core"
    home = tmp_path / "runtime-home"
    core.mkdir()
    monkeypatch.setattr(webgpt_runtime, "_core_cli", lambda _core: core / "src" / "cli.ts")
    monkeypatch.setattr(webgpt_runtime, "_bun_executable", lambda: "C:/tools/bun.exe")
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
    assert config["integrationOwner"] == "standalone"
    assert config["coreKind"] == "native-chatgpt-web"
    assert config["port"] == webgpt_runtime.DUBBER_WEBGPT_PORT
    assert config["experimentalBiggerContext"] is False
    assert config["experimentalSkillAttachments"] is False
    assert config["autoApproveToolCalls"] is False
    assert config["runtimeCommand"] == ["C:/tools/bun.exe", str(core / "src" / "cli.ts")]
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
    monkeypatch.setattr(webgpt_runtime, "_bun_executable", lambda: "C:/tools/bun.exe")

    config = webgpt_runtime.initialize_runtime(core_repo=core, home=home)

    assert config == existing


def test_initialize_runtime_migrates_legacy_cockpit_owner_without_touching_login_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = tmp_path / "core"
    home = tmp_path / "runtime-home"
    core.mkdir()
    home.mkdir()
    existing = {
        "port": webgpt_runtime.DUBBER_WEBGPT_PORT,
        "integrationOwner": "cockpit",
        "storageStatePath": str(home / "browser" / "storage-state.json"),
        "controlToken": "keep-me",
    }
    (home / "config.json").write_text(json.dumps(existing), encoding="utf-8")
    monkeypatch.setattr(webgpt_runtime, "_core_cli", lambda _core: core / "src" / "cli.ts")
    monkeypatch.setattr(webgpt_runtime, "_bun_executable", lambda: "C:/tools/bun.exe")

    config = webgpt_runtime.initialize_runtime(core_repo=core, home=home)

    assert config["integrationOwner"] == "standalone"
    assert config["coreKind"] == "native-chatgpt-web"
    assert config["runtimeCommand"] == ["C:/tools/bun.exe", str(core / "src" / "cli.ts")]
    assert config["storageStatePath"] == existing["storageStatePath"]
    assert config["controlToken"] == existing["controlToken"]
    assert "cockpit" not in (home / "config.json").read_text(encoding="utf-8")


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


def test_verified_login_state_requires_core_marker(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "browser" / "storage-state.json"
    storage.parent.mkdir()
    storage.write_text("{}", encoding="utf-8")

    assert webgpt_runtime._verified_login_state(storage) is False

    marker = Path(f"{storage}.verified.json")
    marker.write_text(
        json.dumps({"version": 1, "authenticated": True, "verifiedAt": "2026-09-28T00:00:00Z"}),
        encoding="utf-8",
    )
    assert webgpt_runtime._verified_login_state(storage) is True

    marker.write_text(json.dumps({"version": 1, "authenticated": False}), encoding="utf-8")
    assert webgpt_runtime._verified_login_state(storage) is False


def test_verified_login_state_accepts_normal_storage_refresh_after_login(
    tmp_path: Path,
) -> None:
    storage = tmp_path / "browser" / "storage-state.json"
    storage.parent.mkdir()
    storage.write_text("{}", encoding="utf-8")
    marker = Path(f"{storage}.verified.json")
    marker.write_text(
        json.dumps({"version": 1, "authenticated": True, "verifiedAt": "2026-09-28T00:00:00Z"}),
        encoding="utf-8",
    )

    # The managed browser may rewrite cookies/local storage after a successful
    # turn. A fresh mtime alone must not force the user through login again.
    storage.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    assert webgpt_runtime._verified_login_state(storage) is True


def test_catalog_model_ids_accepts_openai_shapes_and_deduplicates() -> None:
    assert webgpt_runtime._catalog_model_ids(
        {
            "data": [
                {"id": " chatgpt-web/gpt-5.6-sol "},
                {"slug": "chatgpt-web/gpt-5.6-sol"},
                "chatgpt-web/gpt-5.6-sol-instant",
                {"id": ""},
                42,
            ]
        }
    ) == ["chatgpt-web/gpt-5.6-sol", "chatgpt-web/gpt-5.6-sol-instant"]


@pytest.mark.parametrize("payload", [None, [], {"data": {}}, {"data": []}, {"models": [None, 7]}])
def test_catalog_model_ids_rejects_malformed_or_empty_catalog(payload: object) -> None:
    with pytest.raises(RuntimeError, match="catalog"):
        webgpt_runtime._catalog_model_ids(payload)

