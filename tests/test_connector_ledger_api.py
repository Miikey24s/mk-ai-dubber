from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import vi_dubber.api as api_mod
from vi_dubber.api import create_app


FIXTURE = Path(__file__).parent / "fixtures" / "m6_integration_contract.json"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    work.mkdir(parents=True)
    monkeypatch.setattr(api_mod, "WORK_DIR", work)
    return TestClient(create_app())


def _contract() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_connector_api_persists_prep_only_receipt_and_events(client: TestClient) -> None:
    contract = _contract()
    connection = client.post("/api/connectors/connections", json=contract["connection_capability"])
    assert connection.status_code == 200
    assert connection.json()["status"] == "PREP_ONLY"
    assert connection.json()["connection"]["epoch"] == 1

    intent = client.post(
        "/api/connectors/connection-fixture-0001/epochs/1/intents",
        json=contract["export_request"],
    )
    assert intent.status_code == 200
    assert intent.json()["status"] == "PREP_ONLY"
    assert intent.json()["receipt"]["status"] == "pending"

    receipt = client.get(
        "/api/connectors/connection-fixture-0001/epochs/1/intents/export-request-fixture-0001"
    )
    assert receipt.status_code == 200
    assert receipt.json()["receipt"]["source"]["artifact_sha256"] == "b" * 64

    events = client.get("/api/connectors/connection-fixture-0001/epochs/1/events")
    assert events.status_code == 200
    assert [row["event_type"] for row in events.json()["events"]] == [
        "connection_registered",
        "intent_created",
    ]


def test_connector_api_unknown_reconcile_and_revoke_are_local_only(client: TestClient) -> None:
    contract = _contract()
    assert client.post("/api/connectors/connections", json=contract["connection_capability"]).status_code == 200
    assert client.post(
        "/api/connectors/connection-fixture-0001/epochs/1/intents",
        json=contract["export_request"],
    ).status_code == 200

    unknown = client.post(
        "/api/connectors/connection-fixture-0001/epochs/1/intents/export-request-fixture-0001/unknown"
    )
    assert unknown.status_code == 200
    assert unknown.json()["receipt"]["status"] == "unknown"

    manual = client.post(
        "/api/connectors/connection-fixture-0001/epochs/1/intents/export-request-fixture-0001/reconcile",
        json=None,
    )
    assert manual.status_code == 200
    assert manual.json()["action"] == "manual_reconciliation"

    revoked = client.post(
        "/api/connectors/connection-fixture-0001/epochs/1/revoke",
        json={"reason": "user_revoked_connection"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["connection"]["status"] == "revoked"
    receipt = client.get(
        "/api/connectors/connection-fixture-0001/epochs/1/intents/export-request-fixture-0001"
    )
    assert receipt.json()["receipt"]["status"] == "revoked"


def test_connector_api_fences_path_mismatch_and_rejects_secrets(client: TestClient) -> None:
    contract = _contract()
    bad_path = copy.deepcopy(contract["export_request"])
    bad_path["request_id"] = "request-path-mismatch"
    mismatch = client.post(
        "/api/connectors/other-connection/epochs/1/intents",
        json=bad_path,
    )
    assert mismatch.status_code == 409
    assert client.get(
        "/api/connectors/connection-fixture-0001/epochs/1/intents/request-path-mismatch"
    ).status_code == 404

    secret = copy.deepcopy(contract["connection_capability"])
    secret["access_token"] = "must-not-persist"
    response = client.post("/api/connectors/connections", json=secret)
    assert response.status_code == 409
    assert "secret" in response.json()["detail"]

    missing = client.get("/api/connectors/missing/epochs/1")
    assert missing.status_code == 404
