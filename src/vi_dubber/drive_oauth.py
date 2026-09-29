"""Local, process-owned Google Drive OAuth session for the VI product."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

import requests


DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
CALLBACK_PATH = "/api/connectors/drive/oauth/callback"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
STATE_TTL_SECONDS = 600
MAX_PENDING_STATES = 16


class DriveOAuthError(Exception):
    pass


@dataclass(frozen=True)
class DriveOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_environment(cls) -> DriveOAuthConfig | None:
        client_id = os.getenv("VI_DUBBER_DRIVE_CLIENT_ID", "").strip()
        client_secret = os.getenv("VI_DUBBER_DRIVE_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("VI_DUBBER_DRIVE_REDIRECT_URI", "").strip()
        if not all((client_id, client_secret, redirect_uri)):
            return None
        parsed = urlparse(redirect_uri)
        try:
            port = parsed.port
        except ValueError as exc:
            raise DriveOAuthError("Drive redirect URI has an invalid port") from exc
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or port is None
            or parsed.path != CALLBACK_PATH
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise DriveOAuthError("Drive redirect URI must be the exact local callback URL")
        return cls(client_id, client_secret, redirect_uri)


class DriveOAuthSession:
    def __init__(self, config: DriveOAuthConfig | None):
        self._config = config
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, float, int]] = {}
        self._epoch = 0
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._connection_id: str | None = None

    def status(self) -> dict[str, object]:
        with self._lock:
            connected = self._token is not None and self._expires_at > time.time() + 30
            expired = self._token is not None and not connected
            return {
                "provider": "drive",
                "configured": self._config is not None,
                "connected": connected,
                "connection_state": "connected" if connected else "reconnect_required" if expired else "disconnected",
                "expired": expired,
                "reconnect_required": expired,
                "scope": "drive.file" if connected else None,
                "connection_id": self._connection_id if connected else None,
                "expires_at_unix": int(self._expires_at) if connected else None,
                "storage": "process-memory-only",
                "execution_mode": "PREP_ONLY",
            }

    def start(self) -> str:
        if self._config is None:
            raise DriveOAuthError("Drive OAuth is not configured")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        with self._lock:
            now = time.time()
            self._pending = {key: value for key, value in self._pending.items() if value[1] > now}
            if len(self._pending) >= MAX_PENDING_STATES:
                self._pending.pop(next(iter(self._pending)))
            self._pending[state] = (verifier, now + STATE_TTL_SECONDS, self._epoch)
        query = urlencode({
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": DRIVE_FILE_SCOPE,
            "access_type": "offline",
            "prompt": "select_account",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return f"{AUTHORIZE_URL}?{query}"

    def complete(self, state: str, code: str | None, error: str | None) -> None:
        if self._config is None:
            raise DriveOAuthError("Drive OAuth is not configured")
        if not state or len(state) > 256:
            raise DriveOAuthError("Invalid OAuth state")
        with self._lock:
            pending = self._pending.pop(state, None)
            if pending is None or pending[1] < time.time():
                raise DriveOAuthError("OAuth state expired or mismatched")
        if error:
            raise DriveOAuthError("Google authorization was cancelled or denied")
        if not code or len(code) > 4096:
            raise DriveOAuthError("Google did not return an authorization code")
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "redirect_uri": self._config.redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": pending[0],
                },
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DriveOAuthError("Google token exchange failed") from exc
        if not isinstance(payload, dict):
            raise DriveOAuthError("Google token response is invalid")
        token = payload.get("access_token")
        token_type = payload.get("token_type")
        scopes = payload.get("scope")
        expires_in = payload.get("expires_in")
        refresh_token = payload.get("refresh_token")
        if (
            not isinstance(token, str)
            or not token
            or token_type != "Bearer"
            or not isinstance(scopes, str)
            or set(scopes.split()) != {DRIVE_FILE_SCOPE}
            or not isinstance(expires_in, int)
            or not 60 <= expires_in <= 86400
            or (refresh_token is not None and (not isinstance(refresh_token, str) or not refresh_token))
        ):
            raise DriveOAuthError("Google token response did not grant the exact Drive file scope")
        with self._lock:
            if self._epoch != pending[2]:
                raise DriveOAuthError("OAuth session changed during token exchange")
            self._token = token
            self._refresh_token = refresh_token
            self._expires_at = time.time() + expires_in
            self._connection_id = secrets.token_urlsafe(18)

    def disconnect(self) -> None:
        with self._lock:
            self._epoch += 1
            self._pending.clear()
            self._token = None
            self._refresh_token = None
            self._expires_at = 0.0
            self._connection_id = None

    def access_token(self) -> str | None:
        with self._lock:
            if self._token is None or self._expires_at <= time.time() + 30:
                return None
            return self._token
