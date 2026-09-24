from __future__ import annotations

from importlib import metadata


RUNTIME_DISTRIBUTIONS = {
    "audio_separator": "audio-separator",
    "torch": "torch",
    "vieneu": "vieneu",
    "whisperx": "whisperx",
}


def distribution_version(distribution: str) -> str:
    """Return an installed distribution version without importing the package."""
    try:
        return str(metadata.version(distribution))
    except metadata.PackageNotFoundError:
        return "unknown"


def runtime_versions(*keys: str) -> dict[str, str]:
    """Resolve stable package identities for stage cache fingerprints."""
    result: dict[str, str] = {}
    for key in keys:
        distribution = RUNTIME_DISTRIBUTIONS.get(key)
        if distribution is None:
            raise KeyError(f"Unknown runtime distribution key: {key}")
        result[key] = distribution_version(distribution)
    return result
