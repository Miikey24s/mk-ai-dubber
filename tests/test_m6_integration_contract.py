from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_m6_integration_contract.py"
SPEC = importlib.util.spec_from_file_location("benchmark_m6_integration_contract", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_m6_fixture_is_prep_only_and_all_boundaries_validate() -> None:
    contract = benchmark.load_contract()
    receipt = benchmark.run_probe(contract)

    assert contract["status"] == "PREP_ONLY"
    assert receipt["status"] == "PREP_ONLY"
    assert receipt["validation"]["passed"] is True
    assert receipt["validation"]["learn_reference_errors"] == []
    assert receipt["validation"]["export_request_errors"] == []
    assert all(errors == [] for errors in receipt["validation"]["receipt_errors"].values())
    assert receipt["claims_excluded"]


def test_reference_rejects_unallowlisted_resource_and_answer_key_exposure() -> None:
    contract = benchmark.load_contract()
    reference = copy.deepcopy(contract["learn_reference"])
    reference["artifact"]["resource_uri"] = "https://attacker.invalid/file.srt"
    reference["safety"]["answer_keys_exposed"] = True

    errors = benchmark.validate_learn_reference(reference, contract)

    assert "reference.artifact.resource_allowlist" in errors
    assert "reference.safety.answer_keys_exposed" in errors


@pytest.mark.parametrize(
    "resource_uri",
    [
        "learn://authorized-evil/file.srt",
        "learn://authorized/job/../secret.srt",
        "learn://authorized/job/%2e%2e/secret.srt",
        "learn://authorized/job/\x1fsecret.srt",
        "learn://authorized/job/file.srt?redirect=https://attacker.invalid",
        "https://authorized/job/file.srt",
    ],
)
def test_reference_uri_boundary_rejects_lookalike_authority_and_unsafe_paths(
    resource_uri: str,
) -> None:
    contract = benchmark.load_contract()
    reference = copy.deepcopy(contract["learn_reference"])
    reference["artifact"]["resource_uri"] = resource_uri

    assert "reference.artifact.resource_allowlist" in benchmark.validate_learn_reference(
        reference, contract
    )


def test_reference_requires_explicit_utc_provenance_and_key_id() -> None:
    contract = benchmark.load_contract()
    reference = copy.deepcopy(contract["learn_reference"])
    reference["artifact"]["source_timestamp_utc"] = "2026-09-27T17:00:00+07:00"
    reference["identity"].pop("key_id")

    errors = benchmark.validate_learn_reference(reference, contract)

    assert "reference.artifact.source_timestamp_utc_format" in errors
    assert "reference.identity.key_id" in errors


def test_probe_receipt_records_uri_and_provenance_hardening() -> None:
    receipt = benchmark.run_probe(benchmark.load_contract())

    assert receipt["hardening"]["status"] == "passed"
    assert all(receipt["hardening"]["rejected_cases"].values())


def test_dedupe_accepts_same_intent_and_rejects_conflicting_reuse() -> None:
    contract = benchmark.load_contract()
    first = copy.deepcopy(contract["export_request"])
    duplicate = copy.deepcopy(first)
    duplicate["request_id"] = "retry-with-new-request-id"
    conflict = copy.deepcopy(first)
    conflict["request_id"] = "conflicting-retry"
    conflict["source"]["revision"] = 4

    outcomes = benchmark.dedupe_requests([first, duplicate, conflict])

    assert [item["action"] for item in outcomes] == ["send_once", "dedupe", "reject_conflict"]


def test_unknown_outcome_requires_lookup_before_retry() -> None:
    contract = benchmark.load_contract()
    unknown = contract["receipt_examples"]["unknown"]

    assert benchmark.reconcile_unknown(unknown, None)["action"] == "manual_reconciliation"
    assert benchmark.reconcile_unknown(unknown, {"status": "succeeded"}) == {
        "action": "adopt_existing",
        "status": "succeeded",
        "resend": False,
    }
    assert benchmark.reconcile_unknown(unknown, {"status": "not_found"}) == {
        "action": "retry_allowed",
        "status": "pending",
        "resend": True,
    }


def test_revoke_and_stale_events_cannot_reopen_terminal_export() -> None:
    state = {"status": "pending", "reconcile_required": False}
    state = benchmark.apply_event(state, "outcome_unknown")
    state = benchmark.apply_event(state, "connection_revoked")
    assert state["status"] == "revoked"
    assert state["reconcile_required"] is False

    stale = benchmark.apply_event(state, "dispatch_started")
    assert stale == state


def test_offline_adapter_is_idempotent_and_never_performs_connector_io() -> None:
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter()
    request = copy.deepcopy(contract["export_request"])
    duplicate = copy.deepcopy(request)
    duplicate["request_id"] = "second-request-id"

    first = adapter.submit(request)
    second = adapter.submit(duplicate)
    assert first == second
    assert first["status"] == "pending"
    assert benchmark.validate_export_receipt(first, request) == []

    conflict = copy.deepcopy(request)
    conflict["source"]["revision"] = 99
    with pytest.raises(ValueError, match="idempotency key conflicts"):
        adapter.submit(conflict)


def test_offline_adapter_reconciles_unknown_then_revoke_blocks_reopen() -> None:
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter()
    request = copy.deepcopy(contract["export_request"])
    adapter.submit(request)
    unknown = adapter.mark_unknown(request["request_id"])
    assert unknown["status"] == "unknown"

    adopted = adapter.reconcile(
        request["request_id"],
        {
            "status": "succeeded",
            "external_id": "remote-1",
            "remote_revision": "r2",
            "remote_sha256": "d" * 64,
        },
    )
    assert adopted["action"] == "adopt_existing"
    assert adopted["receipt"]["status"] == "succeeded"
    assert adopted["receipt"]["external_id"] == "remote-1"

    revoked = adapter.revoke(request["request_id"], "user_revoked_connection")
    assert revoked["status"] == "revoked"
    assert benchmark.apply_event(revoked, "dispatch_started") == revoked


@pytest.mark.parametrize("status", ["unknown", "succeeded", "revoked"])
def test_receipt_validator_keeps_request_identity(status: str) -> None:
    contract = benchmark.load_contract()
    receipt = copy.deepcopy(contract["receipt_examples"][status])
    receipt["idempotency_key"] = "wrong-key"

    assert "receipt.idempotency_key_match" in benchmark.validate_export_receipt(
        receipt, contract["export_request"]
    )
