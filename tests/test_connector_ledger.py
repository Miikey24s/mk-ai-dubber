from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from vi_dubber.connector_ledger import (
    CONNECTOR_LEDGER_SCHEMA_VERSION,
    ConnectorLedger,
    ConnectorLedgerError,
    ConnectorLedgerIntegrityError,
    PREP_ONLY,
)


FIXTURE = Path(__file__).parent / "fixtures" / "m6_integration_contract.json"


def _contract() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _connection(contract: dict) -> dict:
    return copy.deepcopy(contract["connection_capability"])


def _request(contract: dict) -> dict:
    return copy.deepcopy(contract["export_request"])


def test_schema_and_connection_are_durable(tmp_path: Path) -> None:
    contract = _contract()
    db_path = tmp_path / "connector.sqlite3"
    first = ConnectorLedger(db_path)
    first.initialize()
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CONNECTOR_LEDGER_SCHEMA_VERSION

    first.register_connection(_connection(contract))
    first.submit(_request(contract))

    # A new service instance represents a process restart.  The connection,
    # intent, source lineage and receipt are all recovered from SQLite.
    restarted = ConnectorLedger(db_path)
    assert restarted.connection("connection-fixture-0001", 1)["status"] == "active"
    receipt = restarted.receipt("connection-fixture-0001", 1, "export-request-fixture-0001")
    assert receipt["execution_mode"] == PREP_ONLY
    assert receipt["status"] == "pending"
    assert receipt["source"]["revision"] == 3
    assert receipt["source"]["artifact_sha256"] == "b" * 64
    assert [event["event_type"] for event in restarted.events("connection-fixture-0001", 1)] == [
        "connection_registered",
        "intent_created",
    ]


def test_same_intent_dedupes_and_conflicting_reuse_fails(tmp_path: Path) -> None:
    contract = _contract()
    ledger = ConnectorLedger(tmp_path / "connector.sqlite3")
    ledger.register_connection(_connection(contract))
    first = ledger.submit(_request(contract))
    duplicate = _request(contract)
    duplicate["request_id"] = "different-request-id"
    assert ledger.submit(duplicate) == first

    conflict = _request(contract)
    conflict["source"]["revision"] = 4
    with pytest.raises(ConnectorLedgerError, match="idempotency key"):
        ledger.submit(conflict)


def test_unknown_reconcile_retry_and_terminal_failure_are_durable(tmp_path: Path) -> None:
    contract = _contract()
    ledger = ConnectorLedger(tmp_path / "connector.sqlite3")
    ledger.register_connection(_connection(contract))
    ledger.submit(_request(contract))
    assert ledger.mark_timeout("connection-fixture-0001", 1, "export-request-fixture-0001")["status"] == "unknown"

    retry = ledger.reconcile("connection-fixture-0001", 1, "export-request-fixture-0001", {"status": "not_found"})
    assert retry["action"] == "retry_allowed"
    assert retry["receipt"]["attempt"] == 2
    ledger.mark_timeout("connection-fixture-0001", 1, "export-request-fixture-0001")
    ledger.reconcile("connection-fixture-0001", 1, "export-request-fixture-0001", {"status": "not_found"})
    ledger.mark_timeout("connection-fixture-0001", 1, "export-request-fixture-0001")
    exhausted = ledger.reconcile("connection-fixture-0001", 1, "export-request-fixture-0001", {"status": "not_found"})
    assert exhausted["action"] == "retry_exhausted"
    assert exhausted["receipt"]["status"] == "failed"
    assert exhausted["receipt"]["error_code"] == "retry_exhausted"

    # The failed terminal state survives a restart and cannot be reopened.
    restarted = ConnectorLedger(tmp_path / "connector.sqlite3")
    assert restarted.mark_timeout("connection-fixture-0001", 1, "export-request-fixture-0001")["status"] == "failed"
    assert restarted.reconcile("connection-fixture-0001", 1, "export-request-fixture-0001", {"status": "not_found"})["action"] == "no_lookup_needed"


def test_success_reconciliation_requires_remote_lineage(tmp_path: Path) -> None:
    contract = _contract()
    ledger = ConnectorLedger(tmp_path / "connector.sqlite3")
    ledger.register_connection(_connection(contract))
    ledger.submit(_request(contract))
    ledger.mark_unknown("connection-fixture-0001", 1, "export-request-fixture-0001")
    with pytest.raises(ConnectorLedgerError, match="remote_revision"):
        ledger.reconcile(
            "connection-fixture-0001",
            1,
            "export-request-fixture-0001",
            {"status": "succeeded", "external_id": "remote-1", "remote_sha256": "c" * 64},
        )
    adopted = ledger.reconcile(
        "connection-fixture-0001",
        1,
        "export-request-fixture-0001",
        {"status": "succeeded", "external_id": "remote-1", "remote_revision": "r2", "remote_sha256": "c" * 64},
    )
    assert adopted["action"] == "adopt_existing"
    assert adopted["receipt"]["status"] == "succeeded"
    assert adopted["receipt"]["remote_revision"] == "r2"


def test_revocation_fences_pending_and_cannot_rewrite_success(tmp_path: Path) -> None:
    contract = _contract()
    ledger = ConnectorLedger(tmp_path / "connector.sqlite3")
    ledger.register_connection(_connection(contract))
    ledger.submit(_request(contract))
    capability = ledger.revoke_connection("connection-fixture-0001", 1, "user_revoked_connection")
    assert capability["status"] == "revoked"
    assert ledger.receipt("connection-fixture-0001", 1, "export-request-fixture-0001")["status"] == "revoked"
    with pytest.raises(ConnectorLedgerError, match="not active"):
        ledger.submit(_request(contract))

    # A completed remote copy remains a historical success when its
    # connection is revoked; revocation only fences non-terminal work.
    contract2 = _contract()
    contract2["connection_capability"]["epoch"] = 2
    contract2["export_request"]["connection"]["epoch"] = 2
    contract2["export_request"]["destination"]["parent_ref"] = "picker:folder-2"
    contract2["export_request"]["request_id"] = "export-request-fixture-0002"
    contract2["export_request"]["idempotency_key"] = "idem-fixture-0002"
    ledger.register_connection(contract2["connection_capability"])
    ledger.submit(contract2["export_request"])
    ledger.mark_unknown("connection-fixture-0001", 2, "export-request-fixture-0002")
    ledger.reconcile("connection-fixture-0001", 2, "export-request-fixture-0002", {"status": "succeeded", "external_id": "remote-2", "remote_revision": "r1", "remote_sha256": "d" * 64})
    ledger.revoke_connection("connection-fixture-0001", 2, "user_revoked_connection")
    assert ledger.receipt("connection-fixture-0001", 2, "export-request-fixture-0002")["status"] == "succeeded"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["connection_capability"].update({"access_token": "secret"}),
        lambda value: value["export_request"]["source"].update({"refresh_token": "secret"}),
        lambda value: value["export_request"]["source"].update({"revision": True}),
        lambda value: value["export_request"]["source"].update({"artifact_sha256": "not-a-hash"}),
    ],
)
def test_unsafe_or_malformed_payloads_fail_closed(tmp_path: Path, mutator) -> None:
    contract = _contract()
    mutator(contract)
    ledger = ConnectorLedger(tmp_path / "connector.sqlite3")
    if "access_token" in contract["connection_capability"]:
        with pytest.raises(ConnectorLedgerError, match="secret"):
            ledger.register_connection(contract["connection_capability"])
    else:
        ledger.register_connection(_connection(_contract()))
        with pytest.raises(ConnectorLedgerError):
            ledger.submit(contract["export_request"])


def test_tampered_persisted_receipt_fails_closed(tmp_path: Path) -> None:
    contract = _contract()
    db_path = tmp_path / "connector.sqlite3"
    ledger = ConnectorLedger(db_path)
    ledger.register_connection(_connection(contract))
    ledger.submit(_request(contract))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE connector_receipts SET receipt_json = ? WHERE request_id = ?",
            (json.dumps({"status": "succeeded"}), "export-request-fixture-0001"),
        )
        connection.commit()
    with pytest.raises(ConnectorLedgerIntegrityError):
        ConnectorLedger(db_path).receipt("connection-fixture-0001", 1, "export-request-fixture-0001")
