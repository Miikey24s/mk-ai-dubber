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


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("account_ref", "account-guess", "request.destination.account_ref_user_selected"),
        ("account_ref", "user-selected:", "request.destination.account_ref_user_selected"),
        ("parent_ref", "folder-guess", "request.destination.parent_ref_picker_selected"),
        ("parent_ref", "picker:", "request.destination.parent_ref_picker_selected"),
    ],
)
def test_export_authorization_is_fail_closed_to_user_selected_destination(
    field: str, value: str, error: str
) -> None:
    contract = benchmark.load_contract()
    request = copy.deepcopy(contract["export_request"])
    request["destination"][field] = value

    assert error in benchmark.validate_export_request(request)


def test_timeout_is_ambiguous_and_bounded_retry_exhaustion_is_terminal() -> None:
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter()
    request = copy.deepcopy(contract["export_request"])
    adapter.submit(request)

    timed_out = adapter.mark_timeout(request["request_id"])
    assert timed_out["status"] == "unknown"
    assert timed_out["reconcile_required"] is True

    first_retry = adapter.reconcile(request["request_id"], {"status": "not_found"})
    assert first_retry["action"] == "retry_allowed"
    assert first_retry["receipt"]["attempt"] == 2

    adapter.mark_timeout(request["request_id"])
    second_retry = adapter.reconcile(request["request_id"], {"status": "not_found"})
    assert second_retry["action"] == "retry_allowed"
    assert second_retry["receipt"]["attempt"] == 3

    adapter.mark_timeout(request["request_id"])
    exhausted = adapter.reconcile(request["request_id"], {"status": "not_found"})
    assert exhausted["action"] == "retry_exhausted"
    assert exhausted["status"] == "failed"
    assert exhausted["resend"] is False
    assert exhausted["error_code"] == "retry_exhausted"
    assert exhausted["receipt"]["status"] == "failed"
    assert exhausted["receipt"]["attempt"] == 3
    assert exhausted["receipt"]["reconcile_required"] is False
    assert exhausted["receipt"]["retryable"] is False
    assert benchmark.validate_export_receipt(exhausted["receipt"], request) == []


def test_cancelled_timeout_cannot_be_reopened_by_reconcile_or_dispatch() -> None:
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter()
    request = copy.deepcopy(contract["export_request"])
    adapter.submit(request)
    cancelled = adapter.cancel(request["request_id"])
    assert cancelled["status"] == "cancelled"

    assert adapter.mark_timeout(request["request_id"])["status"] == "cancelled"
    assert adapter.revoke(request["request_id"], "connection_revoked")["status"] == "cancelled"
    assert (
        adapter.reconcile(request["request_id"], {"status": "not_found"})["action"]
        == "no_lookup_needed"
    )


def test_failed_retry_exhaustion_is_terminal_against_stale_dispatch() -> None:
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter()
    request = copy.deepcopy(contract["export_request"])
    adapter.submit(request)
    for _ in range(3):
        adapter.mark_timeout(request["request_id"])
        result = adapter.reconcile(request["request_id"], {"status": "not_found"})
    assert result["receipt"]["status"] == "failed"
    assert benchmark.apply_event(result["receipt"], "dispatch_started") == result["receipt"]


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
