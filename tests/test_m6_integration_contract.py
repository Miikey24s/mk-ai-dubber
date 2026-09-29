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
    assert all(errors == [] for errors in receipt["validation"]["receipt_binding_errors"].values())
    assert receipt["claims_excluded"]


def test_project_adapter_preflight_keeps_learn_and_drive_project_owned() -> None:
    contract = benchmark.load_contract()

    preflight = benchmark.build_project_adapter_preflight(contract)

    assert preflight["status"] == "PREP_ONLY"
    assert preflight["backend"] == "project-owned-adapter"
    assert preflight["chatgpt_connector_backend_used"] is False
    assert preflight["external_io_performed"] is False
    assert preflight["validation"]["passed"] is True

    learn = preflight["adapters"]["vi_to_learn"]
    assert learn["status"] == "validated"
    assert learn["operation"] == "authorized_reference_only"
    assert learn["learn_owns_progress"] is True
    assert learn["answer_keys_exposed"] is False
    assert learn["auto_completion_enabled"] is False
    assert learn["external_write_performed"] is False

    drive = preflight["adapters"]["vi_to_drive"]
    assert drive["status"] == "validated"
    assert drive["operation"] == "copy_intent_only"
    assert drive["scope"] == "drive.file"
    assert drive["account_user_selected"] is True
    assert drive["parent_picker_selected"] is True
    assert drive["source_retained_by_app"] is True
    assert drive["remote_identity_recorded"] is False
    assert drive["external_write_performed"] is False


def test_project_adapter_preflight_blocks_unselected_drive_destination() -> None:
    contract = benchmark.load_contract()
    contract["export_request"]["destination"]["account_ref"] = "account-guess"

    preflight = benchmark.build_project_adapter_preflight(contract)

    assert preflight["status"] == "PREP_ONLY"
    assert preflight["validation"]["passed"] is False
    assert preflight["validation"]["drive_request_errors"] == [
        "request.destination.account_ref_user_selected"
    ]
    assert preflight["adapters"]["vi_to_learn"]["status"] == "validated"
    assert preflight["adapters"]["vi_to_drive"]["status"] == "blocked"
    assert preflight["external_io_performed"] is False


@pytest.mark.parametrize(
    ("path", "value", "error", "adapter"),
    [
        (
            ("learn_reference", "artifact"),
            "malformed",
            "reference.artifact",
            "vi_to_learn",
        ),
        (
            ("export_request", "destination"),
            ["malformed"],
            "request.destination",
            "vi_to_drive",
        ),
    ],
)
def test_project_adapter_preflight_fail_closes_malformed_nested_payloads(
    path: tuple[str, str], value: object, error: str, adapter: str
) -> None:
    contract = benchmark.load_contract()
    contract[path[0]][path[1]] = value

    preflight = benchmark.build_project_adapter_preflight(contract)

    errors = (
        preflight["validation"]["learn_reference_errors"]
        if adapter == "vi_to_learn"
        else preflight["validation"]["drive_request_errors"]
    )
    assert error in errors
    assert preflight["validation"]["passed"] is False
    assert preflight["adapters"][adapter]["status"] == "blocked"
    assert preflight["external_io_performed"] is False


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


def test_connection_capability_binds_destination_and_epoch() -> None:
    contract = benchmark.load_contract()
    capability = contract["connection_capability"]
    request = contract["export_request"]

    assert benchmark.validate_connection_capability(capability, request) == []

    stale = copy.deepcopy(request)
    stale["connection"]["epoch"] = 2
    assert "request.capability.epoch_match" in benchmark.validate_connection_capability(
        capability, stale
    )

    wrong_scope = copy.deepcopy(capability)
    wrong_scope["scope"] = "drive.readonly"
    assert "capability.scope" in benchmark.validate_connection_capability(
        wrong_scope, request
    )


def test_connection_revoke_fences_pending_intents_and_blocks_new_dispatch() -> None:
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter(contract["connection_capability"])
    request = copy.deepcopy(contract["export_request"])

    pending = adapter.submit(request)
    assert pending["status"] == "pending"
    capability = adapter.revoke_connection("user_revoked_connection")
    assert capability["status"] == "revoked"
    assert capability["revoked"] is True
    fenced = adapter.receipt(request["request_id"])
    assert fenced["status"] == "revoked"
    assert benchmark.apply_event(fenced, "dispatch_started") == fenced

    # Replaying the same idempotency key is a read/dedupe path, not a new send.
    assert adapter.submit(copy.deepcopy(request))["status"] == "revoked"

    blocked = copy.deepcopy(request)
    blocked["request_id"] = "new-request-after-revoke"
    blocked["idempotency_key"] = "new-idempotency-after-revoke"
    with pytest.raises(ValueError, match="connection capability is not active"):
        adapter.submit(blocked)


def test_receipt_binding_rejects_connection_or_epoch_tampering() -> None:
    contract = benchmark.load_contract()
    receipt = copy.deepcopy(contract["receipt_examples"]["unknown"])

    assert benchmark.validate_receipt_capability_binding(
        receipt, contract["export_request"], contract["connection_capability"]
    ) == []

    receipt["connection"]["connection_id"] = "connection-other"
    errors = benchmark.validate_receipt_capability_binding(
        receipt, contract["export_request"], contract["connection_capability"]
    )
    assert "receipt.connection.connection_id_match" in errors
    assert "receipt.capability.connection_id_match" in errors

    receipt = copy.deepcopy(contract["receipt_examples"]["unknown"])
    receipt["connection"]["epoch"] = 2
    errors = benchmark.validate_receipt_capability_binding(
        receipt, contract["export_request"], contract["connection_capability"]
    )
    assert "receipt.connection.epoch_match" in errors
    assert "receipt.capability.epoch_match" in errors


def test_receipt_binding_survives_timeout_reconcile_and_connection_revoke() -> None:
    contract = benchmark.load_contract()
    request = copy.deepcopy(contract["export_request"])
    capability = copy.deepcopy(contract["connection_capability"])
    adapter = benchmark.OfflineExportAdapter(capability)

    adapter.submit(request)
    unknown = adapter.mark_timeout(request["request_id"])
    retry = adapter.reconcile(request["request_id"], {"status": "not_found"})
    adapter.mark_timeout(request["request_id"])
    revoked_capability = adapter.revoke_connection("connection_revoked")
    fenced = adapter.receipt(request["request_id"])

    for receipt in (unknown, retry["receipt"], fenced):
        assert benchmark.validate_receipt_capability_binding(
            receipt, request, capability
        ) == []
    assert revoked_capability["status"] == "revoked"
    assert fenced["status"] == "revoked"
    assert fenced["reconcile_required"] is False


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

    # Revoking a connection cannot rewrite a completed remote copy.  The
    # receipt remains a durable success; deletion is a separate, explicit
    # operation outside this offline contract.
    revoked = adapter.revoke(request["request_id"], "user_revoked_connection")
    assert revoked["status"] == "succeeded"
    assert benchmark.apply_event(revoked, "dispatch_started") == revoked


def test_revoke_fences_unknown_intent_without_rewriting_terminal_success() -> None:
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter()
    request = copy.deepcopy(contract["export_request"])
    adapter.submit(request)
    adapter.mark_timeout(request["request_id"])
    revoked = adapter.revoke(request["request_id"], "user_revoked_connection")
    assert revoked["status"] == "revoked"
    assert adapter.reconcile(request["request_id"], {"status": "not_found"})["action"] == "no_lookup_needed"


@pytest.mark.parametrize("status", ["unknown", "succeeded", "revoked"])
def test_receipt_validator_keeps_request_identity(status: str) -> None:
    contract = benchmark.load_contract()
    receipt = copy.deepcopy(contract["receipt_examples"][status])
    receipt["idempotency_key"] = "wrong-key"

    assert "receipt.idempotency_key_match" in benchmark.validate_export_receipt(
        receipt, contract["export_request"]
    )


@pytest.mark.parametrize("artifact_id", ["../other/file.srt", "C:/secret.srt", "job\\secret.srt", "job/\x1fsecret.srt"])
def test_i1_artifact_lineage_is_relative_and_owner_bound(artifact_id: str) -> None:
    contract = benchmark.load_contract()
    reference = copy.deepcopy(contract["learn_reference"])
    reference["artifact"]["artifact_id"] = artifact_id
    assert "reference.artifact.artifact_id_safe" in benchmark.validate_learn_reference(reference, contract)

    request = copy.deepcopy(contract["export_request"])
    request["source"]["artifact_id"] = artifact_id
    errors = benchmark.validate_export_request(request)
    assert "request.source.artifact_id_safe" in errors


def test_i1_double_encoded_uri_and_non_boundary_allowlist_fail_closed() -> None:
    contract = benchmark.load_contract()
    reference = copy.deepcopy(contract["learn_reference"])
    reference["artifact"]["resource_uri"] = "learn://authorized/job/%252e%252e/secret.srt"
    assert "reference.artifact.resource_allowlist" in benchmark.validate_learn_reference(reference, contract)

    scoped = copy.deepcopy(contract)
    scoped["resource_allowlist"] = ["learn://authorized/job/"]
    reference["artifact"]["resource_uri"] = "learn://authorized/jobmalicious/file.srt"
    assert "reference.artifact.resource_allowlist" in benchmark.validate_learn_reference(reference, scoped)


@pytest.mark.parametrize(
    ("path", "value", "error"),
    [
        (("request_id",), True, "request.request_id"),
        (("idempotency_key",), 42, "request.idempotency_key"),
        (("source", "revision"), True, "request.source.revision"),
        (("source", "data_class"), "private-secret", "request.source.data_class"),
    ],
)
def test_i2_request_contract_rejects_coercion_and_unbounded_data_class(
    path: tuple[str, ...], value: object, error: str
) -> None:
    contract = benchmark.load_contract()
    request = copy.deepcopy(contract["export_request"])
    target = request
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert error in benchmark.validate_export_request(request)


def test_i2_capability_requires_selected_account_and_consistent_revocation_state() -> None:
    contract = benchmark.load_contract()
    capability = copy.deepcopy(contract["connection_capability"])
    request = copy.deepcopy(contract["export_request"])
    capability["account_ref"] = "account-guess"
    request["destination"]["account_ref"] = "account-guess"
    assert "capability.account_ref_user_selected" in benchmark.validate_connection_capability(capability, request)

    capability = copy.deepcopy(contract["connection_capability"])
    capability["status"] = "revoked"
    capability["revoked"] = False
    assert "capability.revoked_state" in benchmark.validate_connection_capability(capability, request)


def test_receipt_lineage_rejects_source_or_intent_fingerprint_tampering() -> None:
    contract = benchmark.load_contract()
    request = copy.deepcopy(contract["export_request"])
    receipt = copy.deepcopy(contract["receipt_examples"]["unknown"])
    receipt["source"]["revision"] = 99
    assert "receipt.source_lineage_match" in benchmark.validate_export_receipt(receipt, request)
    receipt = copy.deepcopy(contract["receipt_examples"]["unknown"])
    receipt["intent_fingerprint"] = "0" * 64
    assert "receipt.intent_fingerprint_match" in benchmark.validate_export_receipt(receipt, request)


@pytest.mark.parametrize("status", ["pending", "cancelled"])
def test_receipt_pending_and_cancelled_states_cannot_claim_remote_identity(status: str) -> None:
    contract = benchmark.load_contract()
    receipt = copy.deepcopy(contract["receipt_examples"]["unknown"])
    receipt["status"] = status
    receipt["reconcile_required"] = False
    receipt["external_id"] = "remote-file"
    errors = benchmark.validate_export_receipt(receipt, contract["export_request"])
    assert f"receipt.{status}.remote_identity" in errors


def test_reconcile_success_requires_remote_revision_and_checksum() -> None:
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter()
    request = copy.deepcopy(contract["export_request"])
    adapter.submit(request)
    adapter.mark_timeout(request["request_id"])
    missing_revision = adapter.reconcile(
        request["request_id"], {"status": "succeeded", "external_id": "remote", "remote_sha256": "d" * 64}
    )
    assert missing_revision["action"] == "manual_reconciliation"
    assert missing_revision["reason"] == "missing_remote_revision"


def test_duplicate_dispatch_is_single_send_across_snapshot_restore() -> None:
    contract = benchmark.load_contract()
    request = copy.deepcopy(contract["export_request"])
    adapter = benchmark.OfflineExportAdapter()
    first = adapter.submit(request)
    assert adapter.dispatch_count == 1
    duplicate = copy.deepcopy(request)
    duplicate["request_id"] = "same-intent-new-request-id"
    assert adapter.submit(duplicate) == first
    assert adapter.dispatch_count == 1

    restored = benchmark.OfflineExportAdapter.restore(adapter.snapshot())
    assert restored.submit(duplicate) == first
    assert restored.dispatch_count == 1


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("attempt", True, "invalid_attempt"),
        ("attempt", 0, "invalid_attempt"),
        ("attempt", "1", "invalid_attempt"),
    ],
)
def test_unknown_reconcile_rejects_coercible_or_non_positive_attempts(
    field: str, value: object, reason: str
) -> None:
    """An ambiguous receipt must never gain resend authority via coercion."""
    contract = benchmark.load_contract()
    receipt = copy.deepcopy(contract["receipt_examples"]["unknown"])
    receipt[field] = value

    assert benchmark.reconcile_unknown(
        receipt, {"status": "not_found"}
    ) == {"action": "manual_reconciliation", "reason": reason}


def test_unknown_reconcile_rejects_boolean_retry_limit() -> None:
    contract = benchmark.load_contract()
    receipt = copy.deepcopy(contract["receipt_examples"]["unknown"])

    assert benchmark.reconcile_unknown(
        receipt,
        {"status": "not_found"},
        {"max_attempts": True},
    ) == {"action": "manual_reconciliation", "reason": "invalid_retry_policy"}


@pytest.mark.parametrize(
    "mutator",
    [
        lambda snapshot: snapshot.update({"dispatch_count": -1}),
        lambda snapshot: snapshot["keys"].clear(),
        lambda snapshot: snapshot["intents"][next(iter(snapshot["intents"]))]["receipt"].update(
            {"attempt": True}
        ),
    ],
)
def test_snapshot_restore_rejects_malformed_state_before_resend(mutator) -> None:
    """Crash recovery must fail closed instead of replaying a corrupt intent."""
    contract = benchmark.load_contract()
    adapter = benchmark.OfflineExportAdapter()
    adapter.submit(copy.deepcopy(contract["export_request"]))
    snapshot = adapter.snapshot()
    mutator(snapshot)

    with pytest.raises(ValueError, match="snapshot"):
        benchmark.OfflineExportAdapter.restore(snapshot)


def test_terminal_state_is_monotonic_under_out_of_order_events() -> None:
    succeeded = {"status": "succeeded", "reconcile_required": False}
    for event in ("dispatch_started", "dispatch_timeout", "connection_revoked", "cancel_requested"):
        assert benchmark.apply_event(succeeded, event) == succeeded
    failed = {"status": "failed", "reconcile_required": False}
    assert benchmark.apply_event(failed, "connection_revoked") == failed


def test_dedupe_rejects_invalid_missing_idempotency_key_before_collapsing_requests() -> None:
    contract = benchmark.load_contract()
    request = copy.deepcopy(contract["export_request"])
    request.pop("idempotency_key")
    with pytest.raises(ValueError, match="invalid export request"):
        benchmark.dedupe_requests([request])
