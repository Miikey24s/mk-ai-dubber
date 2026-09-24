from importlib import metadata

import pytest

import vi_dubber.versions as versions


def test_distribution_version_uses_metadata_without_importing_package(monkeypatch) -> None:
    monkeypatch.setattr(versions.metadata, "version", lambda name: f"{name}-1.2.3")

    assert versions.distribution_version("whisperx") == "whisperx-1.2.3"


def test_distribution_version_returns_unknown_when_not_installed(monkeypatch) -> None:
    def missing(_name: str) -> str:
        raise metadata.PackageNotFoundError("missing")

    monkeypatch.setattr(versions.metadata, "version", missing)

    assert versions.distribution_version("not-installed") == "unknown"


def test_runtime_versions_maps_known_distribution_names(monkeypatch) -> None:
    seen: list[str] = []

    def fake_version(name: str) -> str:
        seen.append(name)
        return "9.9.9"

    monkeypatch.setattr(versions.metadata, "version", fake_version)

    assert versions.runtime_versions("whisperx", "audio_separator", "vieneu", "torch") == {
        "whisperx": "9.9.9",
        "audio_separator": "9.9.9",
        "vieneu": "9.9.9",
        "torch": "9.9.9",
    }
    assert seen == ["whisperx", "audio-separator", "vieneu", "torch"]


def test_runtime_versions_rejects_unknown_key() -> None:
    with pytest.raises(KeyError, match="Unknown runtime distribution key"):
        versions.runtime_versions("mystery")
