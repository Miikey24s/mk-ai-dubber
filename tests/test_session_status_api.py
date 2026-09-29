from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import vi_dubber.api as api_mod
from vi_dubber.api import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(api_mod, "WORK_DIR", work)
    return TestClient(create_app())


def test_project_session_status_is_explicit_local_demo_and_read_only(client: TestClient) -> None:
    response = client.get("/api/session/status")

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "project-session-v1"
    assert data["auth_mode"] == "local-trusted-demo"
    assert data["production_auth"] is False
    assert data["credentials_present"] is False
    assert data["workspace"] == {"id": "vi-dubber-local"}
    assert data["identity"] == {"marker": "local-owner", "source": "local-process"}
    session = data["session"]
    assert session["status"] == "signed_in"
    assert session["workspace_id"] == "vi-dubber-local"
    assert session["identity_id"] == "local-owner"
    assert session["session_id"].startswith("local-demo-session:")
    assert not any(
        marker in key.lower()
        for key in session
        for marker in ("token", "secret", "password", "cookie")
    )
