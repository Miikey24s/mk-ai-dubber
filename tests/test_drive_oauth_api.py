from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import vi_dubber.api as api_mod
import vi_dubber.drive_oauth as oauth_mod


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path, *, configured: bool) -> TestClient:
    monkeypatch.setattr(api_mod, "WORK_DIR", tmp_path)
    for name in ("VI_DUBBER_DRIVE_CLIENT_ID", "VI_DUBBER_DRIVE_CLIENT_SECRET", "VI_DUBBER_DRIVE_REDIRECT_URI"):
        monkeypatch.delenv(name, raising=False)
    if configured:
        monkeypatch.setenv("VI_DUBBER_DRIVE_CLIENT_ID", "test-client")
        monkeypatch.setenv("VI_DUBBER_DRIVE_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("VI_DUBBER_DRIVE_REDIRECT_URI", "http://127.0.0.1:7860/api/connectors/drive/oauth/callback")
    return TestClient(
        api_mod.create_app(),
        base_url="http://127.0.0.1:7860",
        client=("127.0.0.1", 50000),
    )


def test_drive_oauth_unconfigured_is_read_only_and_does_not_call_google(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, configured=False)
    monkeypatch.setattr(oauth_mod.requests, "post", lambda *args, **kwargs: pytest.fail("unexpected provider call"))
    status = client.get("/api/connectors/drive/oauth/status")
    assert status.status_code == 200
    assert status.json() == {
        "provider": "drive", "configured": False, "connected": False, "scope": None,
        "connection_state": "disconnected", "expired": False, "reconnect_required": False,
        "connection_id": None, "expires_at_unix": None, "storage": "process-memory-only",
        "execution_mode": "PREP_ONLY",
    }
    assert client.post("/api/connectors/drive/oauth/start").status_code == 503


def test_drive_oauth_state_pkce_exact_scope_and_memory_only(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, configured=True)
    starts = client.post("/api/connectors/drive/oauth/start", headers={"origin": "http://127.0.0.1:5173"})
    assert starts.status_code == 200
    auth_url = urlparse(starts.json()["authorization_url"])
    assert auth_url.netloc == "accounts.google.com"
    params = parse_qs(auth_url.query)
    assert params["scope"] == [oauth_mod.DRIVE_FILE_SCOPE]
    assert params["code_challenge_method"] == ["S256"]
    assert params["access_type"] == ["offline"]
    assert params["redirect_uri"] == ["http://127.0.0.1:7860/api/connectors/drive/oauth/callback"]
    assert "test-secret" not in starts.text

    mismatch = client.get("/api/connectors/drive/oauth/callback", params={"state": "wrong", "code": "code"})
    assert mismatch.status_code == 400

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "secret-access-token", "refresh_token": "secret-refresh-token", "token_type": "Bearer", "scope": oauth_mod.DRIVE_FILE_SCOPE, "expires_in": 3600}

    def fake_post(url, *, data, timeout):
        assert url == oauth_mod.TOKEN_URL
        assert data["code"] == "one-time-code"
        assert data["client_secret"] == "test-secret"
        assert data["code_verifier"]
        assert timeout == 10
        return Response()

    monkeypatch.setattr(oauth_mod.requests, "post", fake_post)
    callback = client.get("/api/connectors/drive/oauth/callback", params={"state": params["state"][0], "code": "one-time-code"})
    assert callback.status_code == 200
    assert callback.headers["cache-control"] == "no-store"
    assert callback.headers["referrer-policy"] == "no-referrer"
    assert "secret-access-token" not in callback.text
    assert "secret-refresh-token" not in callback.text
    status = client.get("/api/connectors/drive/oauth/status")
    assert status.json()["connected"] is True
    assert status.json()["connection_state"] == "connected"
    assert status.json()["scope"] == "drive.file"
    assert "secret-access-token" not in status.text
    assert "secret-refresh-token" not in status.text
    assert not list(tmp_path.iterdir())

    replay = client.get("/api/connectors/drive/oauth/callback", params={"state": params["state"][0], "code": "one-time-code"})
    assert replay.status_code == 400
    disconnected = client.post("/api/connectors/drive/oauth/disconnect")
    assert disconnected.json()["connected"] is False
    assert disconnected.json()["connection_state"] == "disconnected"


def test_drive_oauth_parallel_tabs_have_independent_one_time_states(monkeypatch) -> None:
    session = oauth_mod.DriveOAuthSession(oauth_mod.DriveOAuthConfig(
        "id", "secret", "http://127.0.0.1:7860/api/connectors/drive/oauth/callback",
    ))
    first = parse_qs(urlparse(session.start()).query)["state"][0]
    second = parse_qs(urlparse(session.start()).query)["state"][0]
    assert first != second
    assert session.status()["connected"] is False

    class Response:
        def __init__(self, code: str):
            self.code = code

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "access_token": f"secret-{self.code}", "refresh_token": f"refresh-{self.code}",
                "token_type": "Bearer", "scope": oauth_mod.DRIVE_FILE_SCOPE, "expires_in": 3600,
            }

    monkeypatch.setattr(oauth_mod.requests, "post", lambda url, *, data, timeout: Response(data["code"]))
    with pytest.raises(oauth_mod.DriveOAuthError):
        session.complete("unknown", "ignored", None)
    session.complete(second, "second", None)
    with pytest.raises(oauth_mod.DriveOAuthError):
        session.complete(second, "replay", None)
    session.complete(first, "first", None)
    assert session.access_token() == "secret-first"
    with pytest.raises(oauth_mod.DriveOAuthError):
        session.complete(first, "replay", None)
    session.disconnect()
    assert session._refresh_token is None
    assert session.access_token() is None


def test_drive_oauth_expiry_requires_reconnect_without_refresh_call(monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(oauth_mod.time, "time", lambda: clock[0])
    session = oauth_mod.DriveOAuthSession(oauth_mod.DriveOAuthConfig(
        "id", "secret", "http://127.0.0.1:7860/api/connectors/drive/oauth/callback",
    ))
    state = parse_qs(urlparse(session.start()).query)["state"][0]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "access_token": "secret-access", "refresh_token": "secret-refresh",
                "token_type": "Bearer", "scope": oauth_mod.DRIVE_FILE_SCOPE, "expires_in": 60,
            }

    calls = []
    monkeypatch.setattr(oauth_mod.requests, "post", lambda *args, **kwargs: (calls.append(1), Response())[1])
    session.complete(state, "code", None)
    assert session.status()["connection_state"] == "connected"
    assert session._refresh_token == "secret-refresh"
    assert session.access_token() == "secret-access"
    clock[0] += 31
    status = session.status()
    assert status["connected"] is False
    assert status["connection_state"] == "reconnect_required"
    assert status["expired"] is True
    assert status["reconnect_required"] is True
    assert status["scope"] is None
    assert session.access_token() is None
    assert len(calls) == 1
    assert "secret-refresh" not in str(status)


def test_drive_oauth_rejects_nonlocal_and_cross_origin(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path, configured=True)
    assert client.post("/api/connectors/drive/oauth/start", headers={"origin": "https://example.com"}).status_code == 403
    assert client.post("/api/connectors/drive/oauth/start", headers={"host": "example.com"}).status_code == 403
    remote = TestClient(client.app, base_url="http://127.0.0.1:7860", client=("192.0.2.1", 50000))
    assert remote.post("/api/connectors/drive/oauth/start").status_code == 403


def test_drive_oauth_rejects_invalid_redirect_and_extra_scope(monkeypatch, tmp_path) -> None:
    _client(monkeypatch, tmp_path, configured=True)
    monkeypatch.setenv("VI_DUBBER_DRIVE_REDIRECT_URI", "https://example.com/api/connectors/drive/oauth/callback")
    with pytest.raises(oauth_mod.DriveOAuthError):
        api_mod.create_app()

    session = oauth_mod.DriveOAuthSession(oauth_mod.DriveOAuthConfig(
        "id", "secret", "http://127.0.0.1:7860/api/connectors/drive/oauth/callback",
    ))
    state = parse_qs(urlparse(session.start()).query)["state"][0]

    class TooBroad:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "access_token": "secret", "token_type": "Bearer",
                "scope": f"{oauth_mod.DRIVE_FILE_SCOPE} https://www.googleapis.com/auth/drive",
                "expires_in": 3600,
            }

    monkeypatch.setattr(oauth_mod.requests, "post", lambda *args, **kwargs: TooBroad())
    with pytest.raises(oauth_mod.DriveOAuthError):
        session.complete(state, "code", None)
    assert session.status()["connected"] is False
