"""Offline PREP_ONLY contract probe for the future M6 integration boundary.

The module deliberately has no connector, OAuth, network, media, provider, or
production-runtime dependency.  It validates the data boundary that a future
adapter must implement: a VI-owned artifact reference, a user-scoped export
intent, and durable receipts whose retry/revoke/reconcile semantics are safe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "m6_integration_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPORT_STATUSES = {"pending", "succeeded", "failed", "unknown", "revoked", "cancelled"}
TERMINAL_STATUSES = {"succeeded", "failed", "revoked", "cancelled"}
CAPABILITY_STATUSES = {"active", "revoked", "expired"}
DEFAULT_RETRY_POLICY = {
    "timeout_seconds": 30,
    "max_attempts": 3,
    "retryable_outcomes": ("timeout", "transient_failure"),
}
FORBIDDEN_SECRET_KEYS = {
    "access_token",
    "client_secret",
    "refresh_token",
    "authorization",
    "password",
    "secret",
}
DATA_CLASSES = {"subtitle", "video", "audio", "report", "metadata"}
OWNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _strict_text(value: Any) -> bool:
    """Require a real, control-free text value; bool/int coercion is unsafe."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    )


def _safe_artifact_id(value: Any) -> bool:
    """Accept only a relative POSIX lineage key, never a host path or traversal."""
    if not _strict_text(value) or "\\" in value:
        return False
    path = PurePosixPath(value.strip())
    return bool(path.parts) and not path.is_absolute() and all(
        part not in {"", ".", ".."} and ":" not in part for part in path.parts
    )


def _safe_owner_id(value: Any) -> bool:
    return isinstance(value, str) and OWNER_ID_RE.fullmatch(value.strip()) is not None


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "PREP_ONLY":
        raise ValueError("M6 integration contract must remain PREP_ONLY")
    return payload


def canonical_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _secret_keys(payload: Any) -> list[str]:
    return sorted(
        key
        for key, _ in _walk_keys(payload)
        if key.casefold() in FORBIDDEN_SECRET_KEYS
    )


def _is_utc_timestamp(value: Any) -> bool:
    """Accept only an explicit, parseable UTC timestamp for source provenance."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _is_allowlisted_resource_uri(value: Any, contract: dict[str, Any]) -> bool:
    """Validate the URI boundary without allowing look-alike authorities or traversal."""
    if not isinstance(value, str) or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    decoded_path = parsed.path
    for _ in range(3):
        decoded = unquote(decoded_path)
        if decoded == decoded_path:
            break
        decoded_path = decoded
    if (
        parsed.query
        or parsed.fragment
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded_path)
    ):
        return False
    allowlisted = contract.get("resource_allowlist", [])
    if not isinstance(allowlisted, list):
        return False
    for prefix in allowlisted:
        if not isinstance(prefix, str):
            continue
        try:
            allowed = urlsplit(prefix)
        except ValueError:
            continue
        allowed_path = unquote(allowed.path)
        if (
            bool(allowed_path)
            and allowed_path.endswith("/")
            and not allowed.query
            and not allowed.fragment
            and parsed.scheme.casefold() == allowed.scheme.casefold()
            and parsed.netloc.casefold() == allowed.netloc.casefold()
            and decoded_path.startswith(allowed_path)
            and decoded_path != allowed_path.rstrip("/")
            and "\\" not in decoded_path
            and ".." not in decoded_path.split("/")
        ):
            return True
    return False


def validate_learn_reference(reference: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if reference.get("schema_version") != "workspace-learn-reference-v1":
        errors.append("reference.schema_version")
    if not _strict_text(reference.get("reference_id")):
        errors.append("reference.reference_id")
    if reference.get("source_system") != "vi-dubber" or reference.get("target_system") != "learn":
        errors.append("reference.system_boundary")

    identity = reference.get("identity")
    trusted = contract.get("trusted_fixture_issuers", [])
    if not isinstance(identity, dict) or identity.get("issuer") not in trusted:
        errors.append("reference.identity.issuer")
    if not isinstance(identity, dict) or identity.get("trust_state") != "trusted_fixture":
        errors.append("reference.identity.trust_state")
    if not isinstance(identity, dict) or not str(identity.get("key_id") or "").strip():
        errors.append("reference.identity.key_id")

    artifact = reference.get("artifact")
    if not isinstance(artifact, dict):
        return errors + ["reference.artifact"]
    for key in ("artifact_id", "kind", "language", "source_timestamp_utc", "resource_uri"):
        if not _strict_text(artifact.get(key)):
            errors.append(f"reference.artifact.{key}")
    if not _safe_artifact_id(artifact.get("artifact_id")):
        errors.append("reference.artifact.artifact_id_safe")
    for key in ("owner_id", "project_id"):
        if not _safe_owner_id(artifact.get(key)):
            errors.append(f"reference.artifact.{key}")
    if not _is_utc_timestamp(artifact.get("source_timestamp_utc")):
        errors.append("reference.artifact.source_timestamp_utc_format")
    if not isinstance(artifact.get("revision"), int) or artifact["revision"] < 1:
        errors.append("reference.artifact.revision")
    if artifact.get("qa_status") != "passed":
        errors.append("reference.artifact.qa_status")
    for key in ("source_fingerprint", "artifact_sha256"):
        if not _is_sha256(artifact.get(key)):
            errors.append(f"reference.artifact.{key}")
    if artifact.get("source_visible") is not True:
        errors.append("reference.artifact.source_visible")
    uri = artifact.get("resource_uri")
    if not _is_allowlisted_resource_uri(uri, contract):
        errors.append("reference.artifact.resource_allowlist")

    safety = reference.get("safety")
    if not isinstance(safety, dict) or safety.get("answer_keys_exposed") is not False:
        errors.append("reference.safety.answer_keys_exposed")
    if not isinstance(safety, dict) or safety.get("auto_completion_enabled") is not False:
        errors.append("reference.safety.auto_completion_enabled")
    if _secret_keys(reference):
        errors.append("reference.secret_fields")
    return errors


def _required_text(payload: dict[str, Any], fields: tuple[str, ...], prefix: str) -> list[str]:
    return [f"{prefix}.{field}" for field in fields if not _strict_text(payload.get(field))]


def _has_selected_ref(value: Any, marker: str) -> bool:
    if not isinstance(value, str) or not value.startswith(marker):
        return False
    suffix = value[len(marker) :].strip()
    return bool(suffix) and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in suffix)


def validate_export_request(request: dict[str, Any]) -> list[str]:
    errors = _required_text(request, ("request_id", "idempotency_key"), "request")
    if request.get("schema_version") != "workspace-export-request-v1":
        errors.append("request.schema_version")
    source = request.get("source")
    if not isinstance(source, dict):
        errors.append("request.source")
    else:
        errors += _required_text(source, ("system", "artifact_id", "data_class"), "request.source")
        if source.get("system") != "vi-dubber":
            errors.append("request.source.system")
        if not _safe_artifact_id(source.get("artifact_id")):
            errors.append("request.source.artifact_id_safe")
        if source.get("data_class") not in DATA_CLASSES:
            errors.append("request.source.data_class")
        for key in ("owner_id", "project_id"):
            if not _safe_owner_id(source.get(key)):
                errors.append(f"request.source.{key}")
        if not isinstance(source.get("revision"), int) or isinstance(source.get("revision"), bool) or source["revision"] < 1:
            errors.append("request.source.revision")
        if not _is_sha256(source.get("artifact_sha256")):
            errors.append("request.source.artifact_sha256")
        if not _is_utc_timestamp(source.get("source_timestamp_utc")):
            errors.append("request.source.source_timestamp_utc")
    destination = request.get("destination")
    if not isinstance(destination, dict):
        errors.append("request.destination")
    else:
        if destination.get("provider") != "drive":
            errors.append("request.destination.provider")
        errors += _required_text(destination, ("account_ref", "parent_ref", "scope", "mode"), "request.destination")
        if destination.get("scope") != "drive.file":
            errors.append("request.destination.scope")
        if destination.get("mode") != "copy":
            errors.append("request.destination.mode")
        if not _has_selected_ref(destination.get("account_ref"), "user-selected:"):
            errors.append("request.destination.account_ref_user_selected")
        if not _has_selected_ref(destination.get("parent_ref"), "picker:"):
            errors.append("request.destination.parent_ref_picker_selected")
    retry_policy = request.get("retry_policy")
    if not isinstance(retry_policy, dict):
        errors.append("request.retry_policy")
    else:
        timeout_seconds = retry_policy.get("timeout_seconds")
        max_attempts = retry_policy.get("max_attempts")
        retryable_outcomes = retry_policy.get("retryable_outcomes")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not (0 < timeout_seconds <= 300):
            errors.append("request.retry_policy.timeout_seconds")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not (1 <= max_attempts <= 5):
            errors.append("request.retry_policy.max_attempts")
        if (
            not isinstance(retryable_outcomes, list)
            or not retryable_outcomes
            or any(outcome not in {"timeout", "transient_failure"} for outcome in retryable_outcomes)
        ):
            errors.append("request.retry_policy.retryable_outcomes")
    consent = request.get("consent")
    if not isinstance(consent, dict) or consent.get("user_selected") is not True:
        errors.append("request.consent.user_selected")
    if not isinstance(consent, dict) or consent.get("revoked") is not False:
        errors.append("request.consent.revoked")
    connection = request.get("connection")
    if not isinstance(connection, dict):
        errors.append("request.connection")
    else:
        if not _strict_text(connection.get("connection_id")):
            errors.append("request.connection.connection_id")
        if not isinstance(connection.get("epoch"), int) or isinstance(connection.get("epoch"), bool) or connection["epoch"] < 1:
            errors.append("request.connection.epoch")
    if _secret_keys(request):
        errors.append("request.secret_fields")
    return errors


def validate_connection_capability(
    capability: dict[str, Any], request: dict[str, Any] | None = None
) -> list[str]:
    """Validate the local capability binding without contacting a provider.

    ``epoch`` is intentionally explicit: a connector must bind each intent to
    the currently active capability epoch, so a later connector can fence stale
    permission state after revocation.
    """
    errors: list[str] = []
    if not isinstance(capability, dict):
        return ["capability"]
    if capability.get("schema_version") != "workspace-connection-capability-v1":
        errors.append("capability.schema_version")
    for field in ("connection_id", "provider", "account_ref", "scope"):
        if not _strict_text(capability.get(field)):
            errors.append(f"capability.{field}")
    if capability.get("provider") != "drive":
        errors.append("capability.provider")
    if capability.get("scope") != "drive.file":
        errors.append("capability.scope")
    epoch = capability.get("epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        errors.append("capability.epoch")
    if capability.get("status") not in CAPABILITY_STATUSES:
        errors.append("capability.status")
    if capability.get("user_selected") is not True:
        errors.append("capability.user_selected")
    if capability.get("status") == "active" and capability.get("revoked") is not False:
        errors.append("capability.revoked")
    if capability.get("status") in {"revoked", "expired"} and capability.get("revoked") is not True:
        errors.append("capability.revoked_state")
    if not _has_selected_ref(capability.get("account_ref"), "user-selected:"):
        errors.append("capability.account_ref_user_selected")
    if _secret_keys(capability):
        errors.append("capability.secret_fields")

    if isinstance(request, dict):
        destination = request.get("destination")
        binding = request.get("connection")
        if not isinstance(destination, dict):
            errors.append("request.destination")
        else:
            if destination.get("provider") != capability.get("provider"):
                errors.append("request.capability.provider_match")
            if destination.get("account_ref") != capability.get("account_ref"):
                errors.append("request.capability.account_match")
            if destination.get("scope") != capability.get("scope"):
                errors.append("request.capability.scope_match")
        if not isinstance(binding, dict):
            errors.append("request.connection")
        else:
            if binding.get("connection_id") != capability.get("connection_id"):
                errors.append("request.capability.connection_id_match")
            if binding.get("epoch") != capability.get("epoch"):
                errors.append("request.capability.epoch_match")
    return errors


def validate_export_receipt(receipt: dict[str, Any], request: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipt.get("schema_version") != "workspace-export-receipt-v1":
        errors.append("receipt.schema_version")
    for field in ("request_id", "idempotency_key", "destination_provider"):
        if not _strict_text(receipt.get(field)):
            errors.append(f"receipt.{field}")
    if receipt.get("request_id") != request.get("request_id"):
        errors.append("receipt.request_id_match")
    if receipt.get("idempotency_key") != request.get("idempotency_key"):
        errors.append("receipt.idempotency_key_match")
    if receipt.get("destination_provider") != request.get("destination", {}).get("provider"):
        errors.append("receipt.destination_provider")
    status = receipt.get("status")
    if status not in EXPORT_STATUSES:
        errors.append("receipt.status")
    if not isinstance(receipt.get("attempt"), int) or isinstance(receipt.get("attempt"), bool) or receipt["attempt"] < 1:
        errors.append("receipt.attempt")
    if receipt.get("source") != request.get("source"):
        errors.append("receipt.source_lineage_match")
    expected_fingerprint = request_intent_fingerprint(request)
    if receipt.get("intent_fingerprint") != expected_fingerprint:
        errors.append("receipt.intent_fingerprint_match")
    connection = receipt.get("connection")
    if not isinstance(connection, dict):
        errors.append("receipt.connection")
    elif connection != request.get("connection"):
        errors.append("receipt.connection.request_match")
    remote_fields = ("external_id", "remote_revision", "remote_sha256")
    if status in {"pending", "unknown", "failed", "revoked", "cancelled"} and any(
        receipt.get(field) is not None for field in remote_fields
    ):
        errors.append(f"receipt.{status}.remote_identity")
    if status == "unknown":
        if receipt.get("reconcile_required") is not True:
            errors.append("receipt.unknown.reconcile_required")
    elif status == "succeeded":
        if receipt.get("reconcile_required") is not False:
            errors.append("receipt.succeeded.reconcile_required")
        if not _strict_text(receipt.get("external_id")):
            errors.append("receipt.succeeded.external_id")
        if not _strict_text(receipt.get("remote_revision")):
            errors.append("receipt.succeeded.remote_revision")
        if not _is_sha256(receipt.get("remote_sha256")):
            errors.append("receipt.succeeded.remote_sha256")
    elif status == "revoked":
        if not _strict_text(receipt.get("revocation_reason")):
            errors.append("receipt.revoked.reason")
        if receipt.get("reconcile_required") is not False:
            errors.append("receipt.revoked.reconcile_required")
    elif status == "failed":
        if receipt.get("reconcile_required") is not False:
            errors.append("receipt.failed.reconcile_required")
        if not _strict_text(receipt.get("error_code")):
            errors.append("receipt.failed.error_code")
        if receipt.get("retryable") is not False:
            errors.append("receipt.failed.retryable")
    elif status == "pending" and receipt.get("reconcile_required") is not False:
        errors.append("receipt.pending.reconcile_required")
    elif status == "cancelled" and receipt.get("reconcile_required") is not False:
        errors.append("receipt.cancelled.reconcile_required")
    retry_policy = request.get("retry_policy")
    max_attempts = retry_policy.get("max_attempts") if isinstance(retry_policy, dict) else DEFAULT_RETRY_POLICY["max_attempts"]
    if isinstance(max_attempts, int) and receipt.get("attempt", 0) > max_attempts:
        errors.append("receipt.attempt_exceeds_retry_policy")
    if _secret_keys(receipt):
        errors.append("receipt.secret_fields")
    return errors


def validate_receipt_capability_binding(
    receipt: dict[str, Any],
    request: dict[str, Any],
    capability: dict[str, Any],
) -> list[str]:
    """Check receipt lineage against the request and local capability epoch.

    A receipt remains a local audit record after a capability is revoked. It
    therefore records the connection binding used for that intent instead of
    inheriting whatever capability happens to be current when it is read. The
    check is relational and side-effect free: it does not imply that a
    provider accepted the export.
    """
    errors: list[str] = []
    binding = receipt.get("connection")
    request_binding = request.get("connection")
    if not isinstance(binding, dict):
        return ["receipt.connection"]
    if not isinstance(request_binding, dict):
        errors.append("request.connection")
    else:
        if binding.get("connection_id") != request_binding.get("connection_id"):
            errors.append("receipt.connection.connection_id_match")
        if binding.get("epoch") != request_binding.get("epoch"):
            errors.append("receipt.connection.epoch_match")

    if not isinstance(capability, dict):
        errors.append("capability")
    else:
        if binding.get("connection_id") != capability.get("connection_id"):
            errors.append("receipt.capability.connection_id_match")
        if binding.get("epoch") != capability.get("epoch"):
            errors.append("receipt.capability.epoch_match")
    return errors


def request_intent_fingerprint(request: dict[str, Any]) -> str:
    intent = deepcopy(request)
    intent.pop("request_id", None)
    intent.pop("idempotency_key", None)
    return canonical_digest(intent)


def dedupe_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    outcomes: list[dict[str, Any]] = []
    for request in requests:
        errors = validate_export_request(request)
        if errors:
            raise ValueError("invalid export request: " + ", ".join(errors))
        key = str(request.get("idempotency_key") or "")
        fingerprint = request_intent_fingerprint(request)
        previous = seen.get(key)
        if previous is None:
            seen[key] = fingerprint
            outcomes.append({"request_id": request.get("request_id"), "action": "send_once"})
        elif previous == fingerprint:
            outcomes.append({"request_id": request.get("request_id"), "action": "dedupe"})
        else:
            outcomes.append({"request_id": request.get("request_id"), "action": "reject_conflict"})
    return outcomes


def reconcile_unknown(
    receipt: dict[str, Any],
    lookup: dict[str, Any] | None,
    retry_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan a safe next action; this never sends or deletes anything."""
    if receipt.get("status") != "unknown":
        return {"action": "no_lookup_needed", "status": receipt.get("status")}
    if lookup is None:
        return {"action": "manual_reconciliation", "reason": "lookup_unavailable"}
    remote_status = lookup.get("status")
    if remote_status == "succeeded":
        return {"action": "adopt_existing", "status": "succeeded", "resend": False}
    if remote_status == "not_found":
        policy = retry_policy or DEFAULT_RETRY_POLICY
        max_attempts = policy.get("max_attempts", DEFAULT_RETRY_POLICY["max_attempts"])
        try:
            attempt = int(receipt.get("attempt", 1))
        except (TypeError, ValueError):
            return {"action": "manual_reconciliation", "reason": "invalid_attempt"}
        if not isinstance(max_attempts, int) or max_attempts < 1:
            return {"action": "manual_reconciliation", "reason": "invalid_retry_policy"}
        if attempt >= max_attempts:
            return {
                "action": "retry_exhausted",
                "status": "failed",
                "resend": False,
                "error_code": "retry_exhausted",
            }
        return {"action": "retry_allowed", "status": "pending", "resend": True}
    return {"action": "manual_reconciliation", "reason": "ambiguous_lookup"}


def apply_event(state: dict[str, Any], event: str) -> dict[str, Any]:
    """Pure state reducer used to test stale/out-of-order event behavior."""
    current = str(state.get("status"))
    if event == "connection_revoked":
        if current not in TERMINAL_STATUSES:
            state = {**state, "status": "revoked", "reconcile_required": False}
        return state
    if event == "cancel_requested":
        if current not in TERMINAL_STATUSES:
            state = {**state, "status": "cancelled", "reconcile_required": False}
        return state
    if current in TERMINAL_STATUSES:
        return state
    transitions = {
        "dispatch_started": "pending",
        # A timeout is an ambiguous remote outcome, so it must reconcile before resend.
        "dispatch_timeout": "unknown",
        "outcome_unknown": "unknown",
        "lookup_not_found": "pending",
        "lookup_succeeded": "succeeded",
        "dispatch_failed": "failed",
    }
    next_status = transitions.get(event)
    if next_status is None:
        raise ValueError(f"unsupported event: {event}")
    next_state = {**state, "status": next_status}
    if next_status == "unknown":
        next_state["reconcile_required"] = True
    elif next_status in {"pending", "succeeded", "failed"}:
        next_state["reconcile_required"] = False
    return next_state


class OfflineExportAdapter:
    """In-memory adapter skeleton for contract tests; it never performs I/O.

    A production connector can replace this boundary with a durable intent and
    receipt store.  Keeping this object side-effect free makes permission,
    dedupe, unknown-outcome, and revoke behavior testable before OAuth or a
    cloud destination is introduced.
    """

    def __init__(self, capability: dict[str, Any] | None = None) -> None:
        self._intents: dict[str, dict[str, Any]] = {}
        self._keys: dict[str, str] = {}
        self._capability = deepcopy(capability) if capability is not None else None
        self._dispatch_count = 0

    @property
    def dispatch_count(self) -> int:
        """Number of new dispatches; exact retries must not increment it."""
        return self._dispatch_count

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        errors = validate_export_request(request)
        if errors:
            raise ValueError("invalid export request: " + ", ".join(errors))
        key = str(request["idempotency_key"])
        fingerprint = request_intent_fingerprint(request)
        previous_fingerprint = self._keys.get(key)
        if previous_fingerprint is not None and previous_fingerprint != fingerprint:
            raise ValueError("idempotency key conflicts with an existing export intent")
        # A repeat of an already-recorded intent is a read/dedupe path.  It must
        # remain safe to inspect after revoke without opening a new dispatch.
        if previous_fingerprint is not None:
            return deepcopy(self._intents[key]["receipt"])
        if self._capability is not None:
            capability_errors = validate_connection_capability(self._capability, request)
            if capability_errors:
                raise ValueError("invalid connection capability: " + ", ".join(capability_errors))
            if self._capability.get("status") != "active" or self._capability.get("revoked") is True:
                raise ValueError("connection capability is not active")

        receipt = {
            "schema_version": "workspace-export-receipt-v1",
            "request_id": request["request_id"],
            "idempotency_key": key,
            "destination_provider": request["destination"]["provider"],
            "connection": deepcopy(request["connection"]),
            "source": deepcopy(request["source"]),
            "intent_fingerprint": fingerprint,
            "status": "pending",
            "attempt": 1,
            "reconcile_required": False,
            "external_id": None,
            "remote_revision": None,
            "remote_sha256": None,
        }
        self._keys[key] = fingerprint
        self._intents[key] = {"request": deepcopy(request), "receipt": receipt}
        self._dispatch_count += 1
        return deepcopy(receipt)

    def snapshot(self) -> dict[str, Any]:
        """Serializable local intent/receipt snapshot for crash/restart tests."""
        return {
            "intents": deepcopy(self._intents),
            "keys": deepcopy(self._keys),
            "capability": deepcopy(self._capability),
            "dispatch_count": self._dispatch_count,
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "OfflineExportAdapter":
        restored = cls(snapshot.get("capability"))
        restored._intents = deepcopy(snapshot.get("intents", {}))
        restored._keys = deepcopy(snapshot.get("keys", {}))
        restored._dispatch_count = int(snapshot.get("dispatch_count", 0))
        return restored

    def _entry(self, request_id: str) -> dict[str, Any]:
        for entry in self._intents.values():
            if entry["request"]["request_id"] == request_id:
                return entry
        raise KeyError(f"unknown request_id: {request_id}")

    def receipt(self, request_id: str) -> dict[str, Any]:
        """Read a local receipt without exposing mutable adapter state."""
        return deepcopy(self._entry(request_id)["receipt"])

    def mark_unknown(self, request_id: str) -> dict[str, Any]:
        entry = self._entry(request_id)
        entry["receipt"] = apply_event(entry["receipt"], "outcome_unknown")
        return deepcopy(entry["receipt"])

    def mark_timeout(self, request_id: str) -> dict[str, Any]:
        """Record a bounded request timeout without assuming the remote outcome."""
        entry = self._entry(request_id)
        entry["receipt"] = apply_event(entry["receipt"], "dispatch_timeout")
        return deepcopy(entry["receipt"])

    def reconcile(self, request_id: str, lookup: dict[str, Any] | None) -> dict[str, Any]:
        entry = self._entry(request_id)
        receipt = entry["receipt"]
        plan = reconcile_unknown(receipt, lookup, entry["request"].get("retry_policy"))
        if plan["action"] == "adopt_existing":
            if not isinstance(lookup, dict) or not str(lookup.get("external_id") or "").strip():
                return {**plan, "action": "manual_reconciliation", "reason": "missing_external_id"}
            if not _strict_text(lookup.get("remote_revision")):
                return {**plan, "action": "manual_reconciliation", "reason": "missing_remote_revision"}
            if not _is_sha256(lookup.get("remote_sha256")):
                return {**plan, "action": "manual_reconciliation", "reason": "missing_remote_sha256"}
            entry["receipt"] = {
                **receipt,
                "status": "succeeded",
                "reconcile_required": False,
                "external_id": lookup["external_id"],
                "remote_revision": lookup["remote_revision"],
                "remote_sha256": lookup["remote_sha256"],
            }
        elif plan["action"] == "retry_allowed":
            entry["receipt"] = {
                **receipt,
                "status": "pending",
                "attempt": int(receipt.get("attempt", 1)) + 1,
                "reconcile_required": False,
            }
        elif plan["action"] == "retry_exhausted":
            entry["receipt"] = {
                **receipt,
                "status": "failed",
                "reconcile_required": False,
                "error_code": "retry_exhausted",
                "retryable": False,
            }
        return {**plan, "receipt": deepcopy(entry["receipt"])}

    def revoke(self, request_id: str, reason: str) -> dict[str, Any]:
        if not str(reason).strip():
            raise ValueError("revocation reason is required")
        entry = self._entry(request_id)
        if entry["receipt"].get("status") in TERMINAL_STATUSES:
            return deepcopy(entry["receipt"])
        entry["receipt"] = {
            **entry["receipt"],
            "status": "revoked",
            "reconcile_required": False,
            "revocation_reason": reason,
        }
        return deepcopy(entry["receipt"])

    def revoke_connection(self, reason: str) -> dict[str, Any]:
        """Revoke a connection epoch and fence non-terminal local intents."""
        if self._capability is None:
            raise ValueError("connection capability is required")
        if not str(reason).strip():
            raise ValueError("revocation reason is required")
        self._capability = {
            **self._capability,
            "status": "revoked",
            "revoked": True,
            "revocation_reason": reason,
        }
        for entry in self._intents.values():
            receipt = entry["receipt"]
            if receipt.get("status") not in TERMINAL_STATUSES:
                entry["receipt"] = {
                    **receipt,
                    "status": "revoked",
                    "reconcile_required": False,
                    "revocation_reason": reason,
                }
        return deepcopy(self._capability)

    def cancel(self, request_id: str) -> dict[str, Any]:
        entry = self._entry(request_id)
        entry["receipt"] = apply_event(entry["receipt"], "cancel_requested")
        return deepcopy(entry["receipt"])


def run_probe(contract: dict[str, Any]) -> dict[str, Any]:
    reference = contract["learn_reference"]
    request = contract["export_request"]
    capability = contract["connection_capability"]
    receipts = contract["receipt_examples"]
    reference_errors = validate_learn_reference(reference, contract)
    request_errors = validate_export_request(request)
    capability_errors = validate_connection_capability(capability, request)
    receipt_errors = {
        name: validate_export_receipt(receipt, request) for name, receipt in receipts.items()
    }
    receipt_binding_errors = {
        name: validate_receipt_capability_binding(receipt, request, capability)
        for name, receipt in receipts.items()
    }
    hardening_cases = {
        "uri_authority_prefix_bypass": validate_learn_reference(
            {**reference, "artifact": {**reference["artifact"], "resource_uri": "learn://authorized-evil/file.srt"}},
            contract,
        ),
        "uri_path_traversal": validate_learn_reference(
            {**reference, "artifact": {**reference["artifact"], "resource_uri": "learn://authorized/job/../secret.srt"}},
            contract,
        ),
        "non_utc_timestamp": validate_learn_reference(
            {**reference, "artifact": {**reference["artifact"], "source_timestamp_utc": "2026-09-27T17:00:00+07:00"}},
            contract,
        ),
        "missing_trusted_key_id": validate_learn_reference(
            {**reference, "identity": {**reference["identity"], "key_id": ""}},
            contract,
        ),
        "unselected_account": validate_export_request(
            {**request, "destination": {**request["destination"], "account_ref": "account-guess"}}
        ),
        "unselected_parent": validate_export_request(
            {**request, "destination": {**request["destination"], "parent_ref": "folder-guess"}}
        ),
        "capability_epoch_mismatch": validate_connection_capability(
            capability,
            {**request, "connection": {**request["connection"], "epoch": 2}},
        ),
        "capability_scope_mismatch": validate_connection_capability(
            {**capability, "scope": "drive.readonly"}, request
        ),
    }
    duplicate_request = deepcopy(request)
    duplicate_request["request_id"] = "export-request-fixture-duplicate"
    conflict_request = deepcopy(request)
    conflict_request["request_id"] = "export-request-fixture-conflict"
    conflict_request["source"] = {**conflict_request["source"], "revision": 4}
    dedupe = dedupe_requests([request, duplicate_request, conflict_request])
    unknown = receipts["unknown"]
    recovery = {
        "lookup_unavailable": reconcile_unknown(unknown, None),
        "lookup_succeeded": reconcile_unknown(unknown, {"status": "succeeded"}),
        "lookup_not_found": reconcile_unknown(unknown, {"status": "not_found"}),
    }
    state = {"status": "pending", "reconcile_required": False}
    for event in ("outcome_unknown", "lookup_succeeded", "dispatch_started"):
        state = apply_event(state, event)
    adapter = OfflineExportAdapter()
    adapter_initial = adapter.submit(request)
    adapter_duplicate = adapter.submit(duplicate_request)
    adapter_unknown = adapter.mark_unknown(request["request_id"])
    adapter_reconciled = adapter.reconcile(
        request["request_id"],
        {
            "status": "succeeded",
            "external_id": "drive-file-fixture-0001",
            "remote_revision": "remote-r7",
            "remote_sha256": "c" * 64,
        },
    )
    # Exercise revoke on a still-ambiguous intent; a completed remote copy is
    # intentionally left as succeeded and is covered by the terminal-state
    # regression tests.
    revoke_adapter = OfflineExportAdapter()
    revoke_adapter.submit(request)
    revoke_adapter.mark_unknown(request["request_id"])
    adapter_revoked = revoke_adapter.revoke(request["request_id"], "user_revoked_connection")
    capability_adapter = OfflineExportAdapter(capability)
    capability_initial = capability_adapter.submit(request)
    capability_revoked = capability_adapter.revoke_connection("connection_revoked")
    capability_fenced_receipt = capability_adapter.receipt(request["request_id"])
    blocked_request = deepcopy(request)
    blocked_request["request_id"] = "export-request-fixture-new-after-revoke"
    blocked_request["idempotency_key"] = "idem-fixture-new-after-revoke"
    capability_submit_blocked = False
    try:
        capability_adapter.submit(blocked_request)
    except ValueError as error:
        capability_submit_blocked = str(error) == "connection capability is not active"
    retry_adapter = OfflineExportAdapter()
    retry_adapter.submit(request)
    timeout_receipts = [retry_adapter.mark_timeout(request["request_id"])]
    retry_receipts = []
    for _ in range(2):
        retry_receipts.append(
            retry_adapter.reconcile(request["request_id"], {"status": "not_found"})
        )
        timeout_receipts.append(retry_adapter.mark_timeout(request["request_id"]))
    exhausted = retry_adapter.reconcile(request["request_id"], {"status": "not_found"})
    cancel_adapter = OfflineExportAdapter()
    cancel_adapter.submit(request)
    cancelled = cancel_adapter.cancel(request["request_id"])
    cancelled_after_timeout = cancel_adapter.mark_timeout(request["request_id"])
    return {
        "status": "PREP_ONLY",
        "contract": str(CONTRACT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "validation": {
            "learn_reference_errors": reference_errors,
            "export_request_errors": request_errors,
            "capability_errors": capability_errors,
            "receipt_errors": receipt_errors,
            "receipt_binding_errors": receipt_binding_errors,
            "passed": not reference_errors
            and not request_errors
            and not capability_errors
            and not any(receipt_errors.values())
            and not any(receipt_binding_errors.values()),
        },
        "hardening": {
            "status": "passed" if all(hardening_cases.values()) else "failed",
            "rejected_cases": hardening_cases,
            "policy": [
                "resource URI must match exact scheme and authority from the allowlist",
                "resource URI path rejects decoded traversal, query/fragment mutation, and control characters",
                "artifact source timestamp must be explicit UTC with Z suffix",
                "trusted identity must include a non-empty key_id",
                "export destination account and parent must carry explicit user-selection markers",
                "export request must bind to the active connection capability id and epoch",
                "connection revoke fences pending intents and blocks later dispatch",
                "export timeout becomes unknown and bounded retry exhaustion becomes terminal failure",
            ],
        },
        "idempotency": dedupe,
        "reconciliation": recovery,
        "retry_policy": request["retry_policy"],
        "timeout_recovery": {
            "timeouts": timeout_receipts,
            "retries": retry_receipts,
            "exhausted": exhausted,
            "cancelled": cancelled,
            "cancelled_after_timeout": cancelled_after_timeout,
        },
        "stale_event_state": state,
        "offline_adapter": {
            "initial": adapter_initial,
            "duplicate_same_intent": adapter_duplicate,
            "unknown": adapter_unknown,
            "reconciled": adapter_reconciled,
            "revoked": adapter_revoked,
        },
        "connection_capability": {
            "validation_errors": capability_errors,
            "initial_receipt": capability_initial,
            "revoked": capability_revoked,
            "fenced_receipt": capability_fenced_receipt,
            "new_dispatch_blocked": capability_submit_blocked,
        },
        "claims_excluded": [
            "actual VI-to-Learn or Drive connector behavior",
            "OAuth, account consent, token storage, network, or cloud permissions",
            "cryptographic signature verification beyond the trusted fixture marker",
            "media bytes upload or remote deletion",
            "exactly-once delivery; unknown outcomes require lookup/manual reconciliation",
            "remote delivery, retry timing, and timeout behavior remain unverified until a connector is authorized",
            "production schema/API implementation",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON receipt path")
    args = parser.parse_args()
    receipt = run_probe(load_contract())
    serialized = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if receipt["validation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
