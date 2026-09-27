"""Typed, offline VI-to-Learn reference contract.

This module owns only the local hand-off shape. It does not read a connector,
sign a payload, upload media, or mutate the Learn course/progress owner. A
future adapter can serialize LearnReference after its permission and
destination gates have passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit


LEARN_REFERENCE_SCHEMA_VERSION = "workspace-learn-reference-v1"
TRUSTED_FIXTURE_STATE = "trusted_fixture"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class LearnReferenceError(ValueError):
    """Raised when a VI-owned reference is unsafe or incomplete."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LearnReferenceError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if _CONTROL_RE.search(normalized):
        raise LearnReferenceError(f"{field} contains a control character")
    return normalized


def _sha256(value: Any, field: str) -> str:
    normalized = _text(value, field).lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise LearnReferenceError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _utc_timestamp(value: Any, field: str) -> str:
    normalized = _text(value, field)
    if not normalized.endswith("Z"):
        raise LearnReferenceError(f"{field} must be an explicit UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(normalized[:-1] + "+00:00")
    except ValueError as exc:
        raise LearnReferenceError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise LearnReferenceError(f"{field} must be UTC")
    return normalized


def _portable_artifact_id(value: Any) -> str:
    normalized = _text(value, "artifact_id")
    if "\\" in normalized:
        raise LearnReferenceError("artifact_id must use portable POSIX separators")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or ":" in path.parts[0] or ".." in path.parts:
        raise LearnReferenceError("artifact_id must be a safe relative identifier")
    return path.as_posix()


def _safe_resource_uri(value: Any) -> str:
    normalized = _text(value, "resource_uri")
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise LearnReferenceError("resource_uri is not a valid URI") from exc
    if parsed.query or parsed.fragment:
        raise LearnReferenceError("resource_uri must not contain a query or fragment")
    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or ".." in decoded_path.split("/"):
        raise LearnReferenceError("resource_uri path must not traverse")
    if _CONTROL_RE.search(decoded_path):
        raise LearnReferenceError("resource_uri path contains a control character")
    return normalized


def _allowlisted_uri(value: Any, allowlist: tuple[str, ...]) -> str:
    normalized = _safe_resource_uri(value)
    parsed = urlsplit(normalized)
    decoded_path = unquote(parsed.path)
    for prefix in allowlist:
        try:
            allowed = urlsplit(_text(prefix, "resource_allowlist"))
        except LearnReferenceError:
            continue
        allowed_path = unquote(allowed.path)
        if (
            parsed.scheme.casefold() == allowed.scheme.casefold()
            and parsed.netloc.casefold() == allowed.netloc.casefold()
            and decoded_path.startswith(allowed_path)
            and decoded_path != allowed_path.rstrip("/")
        ):
            return normalized
    raise LearnReferenceError("resource_uri is not allowlisted")


@dataclass(frozen=True, slots=True)
class ReferenceIdentity:
    issuer: str
    trust_state: str
    key_id: str

    def __post_init__(self) -> None:
        _text(self.issuer, "identity.issuer")
        if self.trust_state != TRUSTED_FIXTURE_STATE:
            raise LearnReferenceError("identity.trust_state must be trusted_fixture")
        _text(self.key_id, "identity.key_id")

    def to_dict(self) -> dict[str, str]:
        return {
            "issuer": self.issuer,
            "trust_state": self.trust_state,
            "key_id": self.key_id,
        }


@dataclass(frozen=True, slots=True)
class LearnArtifact:
    artifact_id: str
    kind: str
    revision: str
    language: str
    qa_status: str
    source_timestamp_utc: str
    source_fingerprint: str
    artifact_sha256: str
    resource_uri: str
    source_visible: bool

    def __post_init__(self) -> None:
        _portable_artifact_id(self.artifact_id)
        _text(self.kind, "artifact.kind")
        _text(self.revision, "artifact.revision")
        _text(self.language, "artifact.language")
        if self.qa_status != "passed":
            raise LearnReferenceError("artifact.qa_status must be passed")
        _utc_timestamp(self.source_timestamp_utc, "artifact.source_timestamp_utc")
        _sha256(self.source_fingerprint, "artifact.source_fingerprint")
        _sha256(self.artifact_sha256, "artifact.artifact_sha256")
        _safe_resource_uri(self.resource_uri)
        if self.source_visible is not True:
            raise LearnReferenceError("artifact.source_visible must be true")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "revision": self.revision,
            "language": self.language,
            "qa_status": self.qa_status,
            "source_timestamp_utc": self.source_timestamp_utc,
            "source_fingerprint": self.source_fingerprint,
            "artifact_sha256": self.artifact_sha256,
            "resource_uri": self.resource_uri,
            "source_visible": self.source_visible,
        }


@dataclass(frozen=True, slots=True)
class LearnSafety:
    answer_keys_exposed: bool = False
    auto_completion_enabled: bool = False

    def __post_init__(self) -> None:
        if self.answer_keys_exposed is not False:
            raise LearnReferenceError("answer keys must remain excluded")
        if self.auto_completion_enabled is not False:
            raise LearnReferenceError("auto-completion must remain disabled")

    def to_dict(self) -> dict[str, bool]:
        return {
            "answer_keys_exposed": self.answer_keys_exposed,
            "auto_completion_enabled": self.auto_completion_enabled,
        }


@dataclass(frozen=True, slots=True)
class LearnReference:
    """A validated VI-owned artifact reference for a future Learn adapter."""

    reference_id: str
    identity: ReferenceIdentity
    artifact: LearnArtifact
    safety: LearnSafety = LearnSafety()
    schema_version: str = LEARN_REFERENCE_SCHEMA_VERSION
    source_system: str = "vi-dubber"
    target_system: str = "learn"

    def __post_init__(self) -> None:
        if self.schema_version != LEARN_REFERENCE_SCHEMA_VERSION:
            raise LearnReferenceError("unsupported Learn reference schema")
        _text(self.reference_id, "reference_id")
        if self.source_system != "vi-dubber" or self.target_system != "learn":
            raise LearnReferenceError("reference system boundary is invalid")
        if not isinstance(self.identity, ReferenceIdentity):
            raise LearnReferenceError("identity must be ReferenceIdentity")
        if not isinstance(self.artifact, LearnArtifact):
            raise LearnReferenceError("artifact must be LearnArtifact")
        if not isinstance(self.safety, LearnSafety):
            raise LearnReferenceError("safety must be LearnSafety")

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        trusted_issuers: tuple[str, ...],
        resource_allowlist: tuple[str, ...],
    ) -> "LearnReference":
        if not isinstance(payload, Mapping):
            raise LearnReferenceError("reference must be an object")
        if payload.get("schema_version") != LEARN_REFERENCE_SCHEMA_VERSION:
            raise LearnReferenceError("unsupported Learn reference schema")
        identity_raw = payload.get("identity")
        if not isinstance(identity_raw, Mapping):
            raise LearnReferenceError("identity must be an object")
        issuer = _text(identity_raw.get("issuer"), "identity.issuer")
        if issuer not in trusted_issuers:
            raise LearnReferenceError("identity issuer is not trusted")
        identity = ReferenceIdentity(
            issuer=issuer,
            trust_state=identity_raw.get("trust_state"),
            key_id=_text(identity_raw.get("key_id"), "identity.key_id"),
        )
        artifact_raw = payload.get("artifact")
        if not isinstance(artifact_raw, Mapping):
            raise LearnReferenceError("artifact must be an object")
        revision = artifact_raw.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, (str, int)):
            raise LearnReferenceError("artifact.revision must be text or a positive integer")
        if isinstance(revision, int) and revision < 1:
            raise LearnReferenceError("artifact.revision must be positive")
        artifact = LearnArtifact(
            artifact_id=_portable_artifact_id(artifact_raw.get("artifact_id")),
            kind=_text(artifact_raw.get("kind"), "artifact.kind"),
            revision=str(revision),
            language=_text(artifact_raw.get("language"), "artifact.language"),
            qa_status=artifact_raw.get("qa_status"),
            source_timestamp_utc=_utc_timestamp(
                artifact_raw.get("source_timestamp_utc"), "artifact.source_timestamp_utc"
            ),
            source_fingerprint=_sha256(
                artifact_raw.get("source_fingerprint"), "artifact.source_fingerprint"
            ),
            artifact_sha256=_sha256(
                artifact_raw.get("artifact_sha256"), "artifact.artifact_sha256"
            ),
            resource_uri=_allowlisted_uri(artifact_raw.get("resource_uri"), resource_allowlist),
            source_visible=artifact_raw.get("source_visible"),
        )
        safety_raw = payload.get("safety")
        if not isinstance(safety_raw, Mapping):
            raise LearnReferenceError("safety must be an object")
        safety = LearnSafety(
            answer_keys_exposed=safety_raw.get("answer_keys_exposed"),
            auto_completion_enabled=safety_raw.get("auto_completion_enabled"),
        )
        return cls(
            reference_id=_text(payload.get("reference_id"), "reference_id"),
            identity=identity,
            artifact=artifact,
            safety=safety,
            source_system=payload.get("source_system"),
            target_system=payload.get("target_system"),
        )

    @classmethod
    def from_catalog_item(
        cls,
        item: Any,
        *,
        reference_id: str,
        artifact_id: str,
        kind: str,
        language: str,
        source_timestamp_utc: str,
        artifact_sha256: str,
        resource_uri: str,
        issuer: str,
        key_id: str,
        trusted_issuers: tuple[str, ...],
        resource_allowlist: tuple[str, ...],
    ) -> "LearnReference":
        """Create a reference from a validated VI catalog item.

        The output artifact hash and provenance timestamp are explicit inputs;
        this prevents the bridge from treating a filename, mtime, or source
        hash as proof that an exported subtitle/video is complete and
        QA-passed.
        """
        required = ("item_id", "source_fingerprint", "revision", "availability")
        if any(not hasattr(item, name) for name in required):
            raise LearnReferenceError("item must expose a validated VI catalog identity")
        if item.availability != "available":
            raise LearnReferenceError("catalog item is not currently available")
        normalized_artifact_id = _portable_artifact_id(artifact_id)
        if not (
            normalized_artifact_id == str(item.item_id)
            or normalized_artifact_id.startswith(f"{item.item_id}/")
        ):
            raise LearnReferenceError("artifact_id must remain within the catalog item lineage")
        identity = ReferenceIdentity(
            issuer=_text(issuer, "identity.issuer"),
            trust_state=TRUSTED_FIXTURE_STATE,
            key_id=_text(key_id, "identity.key_id"),
        )
        artifact = LearnArtifact(
            artifact_id=normalized_artifact_id,
            kind=kind,
            revision=str(item.revision),
            language=language,
            qa_status="passed",
            source_timestamp_utc=source_timestamp_utc,
            source_fingerprint=str(item.source_fingerprint),
            artifact_sha256=artifact_sha256,
            resource_uri=_allowlisted_uri(resource_uri, resource_allowlist),
            source_visible=True,
        )
        reference = cls(reference_id=reference_id, identity=identity, artifact=artifact)
        if identity.issuer not in trusted_issuers:
            raise LearnReferenceError("identity issuer is not trusted")
        return reference

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "reference_id": self.reference_id,
            "source_system": self.source_system,
            "target_system": self.target_system,
            "identity": self.identity.to_dict(),
            "artifact": self.artifact.to_dict(),
            "safety": self.safety.to_dict(),
        }
