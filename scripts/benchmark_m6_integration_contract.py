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
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "m6_integration_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPORT_STATUSES = {"pending", "succeeded", "failed", "unknown", "revoked", "cancelled"}
TERMINAL_STATUSES = {"succeeded", "revoked", "cancelled"}
FORBIDDEN_SECRET_KEYS = {
    "access_token",
    "client_secret",
    "refresh_token",
    "authorization",
    "password",
    "secret",
}


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


def validate_learn_reference(reference: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if reference.get("schema_version") != "workspace-learn-reference-v1":
        errors.append("reference.schema_version")
    if reference.get("source_system") != "vi-dubber" or reference.get("target_system") != "learn":
        errors.append("reference.system_boundary")

    identity = reference.get("identity")
    trusted = contract.get("trusted_fixture_issuers", [])
    if not isinstance(identity, dict) or identity.get("issuer") not in trusted:
        errors.append("reference.identity.issuer")
    if not isinstance(identity, dict) or identity.get("trust_state") != "trusted_fixture":
        errors.append("reference.identity.trust_state")

    artifact = reference.get("artifact")
    if not isinstance(artifact, dict):
        return errors + ["reference.artifact"]
    for key in ("artifact_id", "kind", "language", "source_timestamp_utc", "resource_uri"):
        if not str(artifact.get(key) or "").strip():
            errors.append(f"reference.artifact.{key}")
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
    if not isinstance(uri, str) or not any(uri.startswith(prefix) for prefix in contract.get("resource_allowlist", [])):
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
    return [f"{prefix}.{field}" for field in fields if not str(payload.get(field) or "").strip()]


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
        if not isinstance(source.get("revision"), int) or source["revision"] < 1:
            errors.append("request.source.revision")
        if not _is_sha256(source.get("artifact_sha256")):
            errors.append("request.source.artifact_sha256")
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
    consent = request.get("consent")
    if not isinstance(consent, dict) or consent.get("user_selected") is not True:
        errors.append("request.consent.user_selected")
    if isinstance(consent, dict) and consent.get("revoked") is True:
        errors.append("request.consent.revoked")
    if _secret_keys(request):
        errors.append("request.secret_fields")
    return errors


def validate_export_receipt(receipt: dict[str, Any], request: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipt.get("schema_version") != "workspace-export-receipt-v1":
        errors.append("receipt.schema_version")
    for field in ("request_id", "idempotency_key", "destination_provider"):
        if not str(receipt.get(field) or "").strip():
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
    if not isinstance(receipt.get("attempt"), int) or receipt["attempt"] < 1:
        errors.append("receipt.attempt")
    if status == "unknown":
        if receipt.get("reconcile_required") is not True:
            errors.append("receipt.unknown.reconcile_required")
        if any(receipt.get(field) is not None for field in ("external_id", "remote_revision", "remote_sha256")):
            errors.append("receipt.unknown.remote_identity")
    elif status == "succeeded":
        if receipt.get("reconcile_required") is not False:
            errors.append("receipt.succeeded.reconcile_required")
        if not str(receipt.get("external_id") or "").strip():
            errors.append("receipt.succeeded.external_id")
        if not str(receipt.get("remote_revision") or "").strip():
            errors.append("receipt.succeeded.remote_revision")
        if not _is_sha256(receipt.get("remote_sha256")):
            errors.append("receipt.succeeded.remote_sha256")
    elif status == "revoked":
        if not str(receipt.get("revocation_reason") or "").strip():
            errors.append("receipt.revoked.reason")
        if receipt.get("reconcile_required") is not False:
            errors.append("receipt.revoked.reconcile_required")
    if _secret_keys(receipt):
        errors.append("receipt.secret_fields")
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


def reconcile_unknown(receipt: dict[str, Any], lookup: dict[str, Any] | None) -> dict[str, Any]:
    """Plan a safe next action; this never sends or deletes anything."""
    if receipt.get("status") != "unknown":
        return {"action": "no_lookup_needed", "status": receipt.get("status")}
    if lookup is None:
        return {"action": "manual_reconciliation", "reason": "lookup_unavailable"}
    remote_status = lookup.get("status")
    if remote_status == "succeeded":
        return {"action": "adopt_existing", "status": "succeeded", "resend": False}
    if remote_status == "not_found":
        return {"action": "retry_allowed", "status": "pending", "resend": True}
    return {"action": "manual_reconciliation", "reason": "ambiguous_lookup"}


def apply_event(state: dict[str, Any], event: str) -> dict[str, Any]:
    """Pure state reducer used to test stale/out-of-order event behavior."""
    current = str(state.get("status"))
    if event == "connection_revoked":
        if current not in {"cancelled"}:
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
    elif next_status == "succeeded":
        next_state["reconcile_required"] = False
    return next_state


class OfflineExportAdapter:
    """In-memory adapter skeleton for contract tests; it never performs I/O.

    A production connector can replace this boundary with a durable intent and
    receipt store.  Keeping this object side-effect free makes permission,
    dedupe, unknown-outcome, and revoke behavior testable before OAuth or a
    cloud destination is introduced.
    """

    def __init__(self) -> None:
        self._intents: dict[str, dict[str, Any]] = {}
        self._keys: dict[str, str] = {}

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        errors = validate_export_request(request)
        if errors:
            raise ValueError("invalid export request: " + ", ".join(errors))
        key = str(request["idempotency_key"])
        fingerprint = request_intent_fingerprint(request)
        previous_fingerprint = self._keys.get(key)
        if previous_fingerprint is not None and previous_fingerprint != fingerprint:
            raise ValueError("idempotency key conflicts with an existing export intent")
        if previous_fingerprint is not None:
            return deepcopy(self._intents[key]["receipt"])

        receipt = {
            "schema_version": "workspace-export-receipt-v1",
            "request_id": request["request_id"],
            "idempotency_key": key,
            "destination_provider": request["destination"]["provider"],
            "status": "pending",
            "attempt": 1,
            "reconcile_required": False,
            "external_id": None,
            "remote_revision": None,
            "remote_sha256": None,
        }
        self._keys[key] = fingerprint
        self._intents[key] = {"request": deepcopy(request), "receipt": receipt}
        return deepcopy(receipt)

    def _entry(self, request_id: str) -> dict[str, Any]:
        for entry in self._intents.values():
            if entry["request"]["request_id"] == request_id:
                return entry
        raise KeyError(f"unknown request_id: {request_id}")

    def mark_unknown(self, request_id: str) -> dict[str, Any]:
        entry = self._entry(request_id)
        entry["receipt"] = apply_event(entry["receipt"], "outcome_unknown")
        return deepcopy(entry["receipt"])

    def reconcile(self, request_id: str, lookup: dict[str, Any] | None) -> dict[str, Any]:
        entry = self._entry(request_id)
        receipt = entry["receipt"]
        plan = reconcile_unknown(receipt, lookup)
        if plan["action"] == "adopt_existing":
            if not isinstance(lookup, dict) or not str(lookup.get("external_id") or "").strip():
                return {**plan, "action": "manual_reconciliation", "reason": "missing_external_id"}
            if not _is_sha256(lookup.get("remote_sha256")):
                return {**plan, "action": "manual_reconciliation", "reason": "missing_remote_sha256"}
            entry["receipt"] = {
                **receipt,
                "status": "succeeded",
                "reconcile_required": False,
                "external_id": lookup["external_id"],
                "remote_revision": str(lookup.get("remote_revision") or "unknown"),
                "remote_sha256": lookup["remote_sha256"],
            }
        elif plan["action"] == "retry_allowed":
            entry["receipt"] = {**receipt, "status": "pending", "reconcile_required": False}
        return {**plan, "receipt": deepcopy(entry["receipt"])}

    def revoke(self, request_id: str, reason: str) -> dict[str, Any]:
        if not str(reason).strip():
            raise ValueError("revocation reason is required")
        entry = self._entry(request_id)
        entry["receipt"] = {
            **entry["receipt"],
            "status": "revoked",
            "reconcile_required": False,
            "revocation_reason": reason,
        }
        return deepcopy(entry["receipt"])

    def cancel(self, request_id: str) -> dict[str, Any]:
        entry = self._entry(request_id)
        entry["receipt"] = apply_event(entry["receipt"], "cancel_requested")
        return deepcopy(entry["receipt"])


def run_probe(contract: dict[str, Any]) -> dict[str, Any]:
    reference = contract["learn_reference"]
    request = contract["export_request"]
    receipts = contract["receipt_examples"]
    reference_errors = validate_learn_reference(reference, contract)
    request_errors = validate_export_request(request)
    receipt_errors = {
        name: validate_export_receipt(receipt, request) for name, receipt in receipts.items()
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
    adapter_revoked = adapter.revoke(request["request_id"], "user_revoked_connection")
    return {
        "status": "PREP_ONLY",
        "contract": str(CONTRACT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "validation": {
            "learn_reference_errors": reference_errors,
            "export_request_errors": request_errors,
            "receipt_errors": receipt_errors,
            "passed": not reference_errors and not request_errors and not any(receipt_errors.values()),
        },
        "idempotency": dedupe,
        "reconciliation": recovery,
        "stale_event_state": state,
        "offline_adapter": {
            "initial": adapter_initial,
            "duplicate_same_intent": adapter_duplicate,
            "unknown": adapter_unknown,
            "reconciled": adapter_reconciled,
            "revoked": adapter_revoked,
        },
        "claims_excluded": [
            "actual VI-to-Learn or Drive connector behavior",
            "OAuth, account consent, token storage, network, or cloud permissions",
            "cryptographic signature verification beyond the trusted fixture marker",
            "media bytes upload or remote deletion",
            "exactly-once delivery; unknown outcomes require lookup/manual reconciliation",
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
