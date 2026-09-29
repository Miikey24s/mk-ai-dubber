"""Durable, local connector intent/receipt ledger for VI Dubber.

This module is the project-owned boundary for the future Learn/Drive adapters.
It deliberately stops before OAuth, network calls, media reads, or uploads.  A
connection capability and every export intent are persisted in SQLite so a
restart cannot lose the idempotency fence, source lineage, or an ambiguous
outcome.  A later transport adapter can use the ledger after its own account
and destination gates have passed.

The ledger is intentionally small and provider-specific only where the M6
contract is specific (Drive ``drive.file`` copy intents).  It stores metadata
and receipts, never tokens or media bytes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .runtime import WORK_DIR


CONNECTOR_LEDGER_SCHEMA_VERSION = 1
CONNECTOR_LEDGER_FORMAT = "vi-dubber-connector-ledger-v1"
CONNECTOR_LEDGER_DB_NAME = "connector_ledger.sqlite3"
PREP_ONLY = "PREP_ONLY"

EXPORT_STATUSES = frozenset({"pending", "unknown", "succeeded", "failed", "revoked", "cancelled"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "revoked", "cancelled"})
CONNECTION_STATUSES = frozenset({"active", "revoked", "expired"})
DATA_CLASSES = frozenset({"subtitle", "video", "audio", "report", "metadata"})
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "client_secret",
        "refresh_token",
        "authorization",
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OWNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_JSON_BYTES = 2 * 1024 * 1024


class ConnectorLedgerError(ValueError):
    """Raised for invalid connector ledger input or an unsafe transition."""


class ConnectorLedgerIntegrityError(ConnectorLedgerError):
    """Raised when persisted rows no longer match their typed contract."""


def _strict_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConnectorLedgerError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if _CONTROL_RE.search(normalized):
        raise ConnectorLedgerError(f"{field} contains a control character")
    return normalized


def _sha256(value: Any, field: str) -> str:
    normalized = _strict_text(value, field).lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ConnectorLedgerError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _revision(value: Any, field: str) -> int | str:
    if isinstance(value, bool):
        raise ConnectorLedgerError(f"{field} must be a positive integer or text")
    if isinstance(value, int):
        if value < 1:
            raise ConnectorLedgerError(f"{field} must be positive")
        return value
    return _strict_text(value, field)


def _owner_id(value: Any, field: str) -> str:
    normalized = _strict_text(value, field)
    if _OWNER_ID_RE.fullmatch(normalized) is None:
        raise ConnectorLedgerError(f"{field} must be a stable owner identifier")
    return normalized


def _utc(value: Any, field: str) -> str:
    normalized = _strict_text(value, field)
    if not normalized.endswith("Z"):
        raise ConnectorLedgerError(f"{field} must be an explicit UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(normalized[:-1] + "+00:00")
    except ValueError as exc:
        raise ConnectorLedgerError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ConnectorLedgerError(f"{field} must be UTC")
    return normalized


def _safe_artifact_id(value: Any, field: str = "artifact_id") -> str:
    normalized = _strict_text(value, field)
    if "\\" in normalized:
        raise ConnectorLedgerError(f"{field} must use portable POSIX separators")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        raise ConnectorLedgerError(f"{field} must be a safe relative identifier")
    return path.as_posix()


def _secret_keys(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text.casefold() in _SECRET_KEYS:
                found.append(key_text)
            found.extend(_secret_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_secret_keys(child))
    return found


def _canonical_json(value: Any, field: str = "payload") -> str:
    if _secret_keys(value):
        raise ConnectorLedgerError(f"{field} contains secret fields")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ConnectorLedgerError(f"{field} must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ConnectorLedgerError(f"{field} exceeds the size limit")
    return encoded


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def request_intent_fingerprint(request: Mapping[str, Any]) -> str:
    """Fingerprint the requested copy without transport/request identifiers."""

    material = copy.deepcopy(dict(request))
    material.pop("request_id", None)
    material.pop("idempotency_key", None)
    return _digest(material)


def _selected(value: Any, marker: str, field: str) -> str:
    normalized = _strict_text(value, field)
    if not normalized.startswith(marker) or not normalized[len(marker) :].strip():
        raise ConnectorLedgerError(f"{field} must be explicitly selected by the user")
    return normalized


def _validate_connection(capability: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(capability, Mapping):
        raise ConnectorLedgerError("connection capability must be an object")
    raw = dict(capability)
    _canonical_json(raw, "connection capability")
    if raw.get("schema_version") != "workspace-connection-capability-v1":
        raise ConnectorLedgerError("unsupported connection capability schema")
    connection_id = _strict_text(raw.get("connection_id"), "connection_id")
    provider = _strict_text(raw.get("provider"), "provider").casefold()
    account_ref = _strict_text(raw.get("account_ref"), "account_ref")
    scope = _strict_text(raw.get("scope"), "scope")
    epoch = raw.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ConnectorLedgerError("epoch must be a positive integer")
    status = raw.get("status")
    if status not in CONNECTION_STATUSES:
        raise ConnectorLedgerError("unsupported connection status")
    if raw.get("user_selected") is not True:
        raise ConnectorLedgerError("connection user_selected must be true")
    revoked = raw.get("revoked")
    if status == "active" and revoked is not False:
        raise ConnectorLedgerError("active connection must not be revoked")
    if status in {"revoked", "expired"} and revoked is not True:
        raise ConnectorLedgerError("revoked or expired connection must be marked revoked")
    if provider == "drive":
        _selected(account_ref, "user-selected:", "account_ref")
        if scope != "drive.file":
            raise ConnectorLedgerError("Drive connector scope must be drive.file")
    return {
        "schema_version": "workspace-connection-capability-v1",
        "connection_id": connection_id,
        "provider": provider,
        "account_ref": account_ref,
        "scope": scope,
        "epoch": epoch,
        "status": status,
        "user_selected": True,
        "revoked": revoked,
        **({"revocation_reason": _strict_text(raw["revocation_reason"], "revocation_reason")} if "revocation_reason" in raw else {}),
    }


def _validate_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ConnectorLedgerError("export request must be an object")
    raw = copy.deepcopy(dict(request))
    _canonical_json(raw, "export request")
    if raw.get("schema_version") != "workspace-export-request-v1":
        raise ConnectorLedgerError("unsupported export request schema")
    request_id = _strict_text(raw.get("request_id"), "request_id")
    idempotency_key = _strict_text(raw.get("idempotency_key"), "idempotency_key")
    source = raw.get("source")
    if not isinstance(source, Mapping):
        raise ConnectorLedgerError("source must be an object")
    source = dict(source)
    if source.get("system") != "vi-dubber":
        raise ConnectorLedgerError("source.system must be vi-dubber")
    source["artifact_id"] = _safe_artifact_id(source.get("artifact_id"), "source.artifact_id")
    source["revision"] = _revision(source.get("revision"), "source.revision")
    source["artifact_sha256"] = _sha256(source.get("artifact_sha256"), "source.artifact_sha256")
    if source.get("data_class") not in DATA_CLASSES:
        raise ConnectorLedgerError("source.data_class is unsupported")
    source["owner_id"] = _owner_id(source.get("owner_id"), "source.owner_id")
    source["project_id"] = _owner_id(source.get("project_id"), "source.project_id")
    source["source_timestamp_utc"] = _utc(source.get("source_timestamp_utc"), "source.source_timestamp_utc")
    raw["source"] = source
    destination = raw.get("destination")
    if not isinstance(destination, Mapping):
        raise ConnectorLedgerError("destination must be an object")
    destination = dict(destination)
    if destination.get("provider") != "drive":
        raise ConnectorLedgerError("destination.provider must be drive")
    destination["account_ref"] = _selected(destination.get("account_ref"), "user-selected:", "destination.account_ref")
    destination["parent_ref"] = _selected(destination.get("parent_ref"), "picker:", "destination.parent_ref")
    if destination.get("scope") != "drive.file":
        raise ConnectorLedgerError("destination.scope must be drive.file")
    if destination.get("mode") != "copy":
        raise ConnectorLedgerError("destination.mode must be copy")
    raw["destination"] = destination
    connection = raw.get("connection")
    if not isinstance(connection, Mapping):
        raise ConnectorLedgerError("connection must be an object")
    connection = dict(connection)
    connection_id = _strict_text(connection.get("connection_id"), "connection.connection_id")
    epoch = connection.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ConnectorLedgerError("connection.epoch must be a positive integer")
    raw["connection"] = {"connection_id": connection_id, "epoch": epoch}
    retry = raw.get("retry_policy")
    if not isinstance(retry, Mapping):
        raise ConnectorLedgerError("retry_policy must be an object")
    retry = dict(retry)
    timeout = retry.get("timeout_seconds")
    attempts = retry.get("max_attempts")
    outcomes = retry.get("retryable_outcomes")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(float(timeout)) or not 0 < float(timeout) <= 300:
        raise ConnectorLedgerError("retry_policy.timeout_seconds is invalid")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 5:
        raise ConnectorLedgerError("retry_policy.max_attempts is invalid")
    if not isinstance(outcomes, list) or not outcomes or any(item not in {"timeout", "transient_failure"} for item in outcomes):
        raise ConnectorLedgerError("retry_policy.retryable_outcomes is invalid")
    raw["retry_policy"] = retry
    consent = raw.get("consent")
    if not isinstance(consent, Mapping) or consent.get("user_selected") is not True or consent.get("revoked") is not False:
        raise ConnectorLedgerError("consent must confirm a non-revoked user selection")
    return raw


def _validate_receipt_payload(receipt: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a persisted receipt against its immutable request lineage."""

    if not isinstance(receipt, Mapping):
        raise ConnectorLedgerIntegrityError("receipt must be an object")
    raw = copy.deepcopy(dict(receipt))
    _canonical_json(raw, "export receipt")
    if raw.get("schema_version") != "workspace-export-receipt-v1":
        raise ConnectorLedgerIntegrityError("unsupported export receipt schema")
    if raw.get("execution_mode") != PREP_ONLY:
        raise ConnectorLedgerIntegrityError("receipt execution mode must remain PREP_ONLY")
    for field in ("request_id", "idempotency_key", "destination_provider"):
        _strict_text(raw.get(field), f"receipt.{field}")
    if raw["request_id"] != request["request_id"]:
        raise ConnectorLedgerIntegrityError("receipt request_id does not match its intent")
    if raw["idempotency_key"] != request["idempotency_key"]:
        raise ConnectorLedgerIntegrityError("receipt idempotency_key does not match its intent")
    if raw["destination_provider"] != request["destination"]["provider"]:
        raise ConnectorLedgerIntegrityError("receipt destination provider does not match its intent")
    if raw.get("connection") != request.get("connection"):
        raise ConnectorLedgerIntegrityError("receipt connection binding does not match its intent")
    if raw.get("source") != request.get("source"):
        raise ConnectorLedgerIntegrityError("receipt source lineage does not match its intent")
    if raw.get("intent_fingerprint") != request_intent_fingerprint(request):
        raise ConnectorLedgerIntegrityError("receipt intent fingerprint does not match its intent")
    state = raw.get("status")
    if state not in EXPORT_STATUSES:
        raise ConnectorLedgerIntegrityError("receipt status is invalid")
    attempt = raw.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ConnectorLedgerIntegrityError("receipt attempt is invalid")
    if not isinstance(raw.get("reconcile_required"), bool):
        raise ConnectorLedgerIntegrityError("receipt reconcile_required is invalid")
    remote_fields = ("external_id", "remote_revision", "remote_sha256")
    if state != "succeeded" and any(raw.get(field) is not None for field in remote_fields):
        raise ConnectorLedgerIntegrityError(f"receipt {state} cannot contain remote identity")
    if state == "unknown" and raw.get("reconcile_required") is not True:
        raise ConnectorLedgerIntegrityError("unknown receipt requires reconciliation")
    if state in {"pending", "succeeded", "failed", "revoked", "cancelled"} and raw.get("reconcile_required") is not False:
        raise ConnectorLedgerIntegrityError(f"receipt {state} has an invalid reconcile flag")
    if state == "succeeded":
        _strict_text(raw.get("external_id"), "receipt.external_id")
        _strict_text(raw.get("remote_revision"), "receipt.remote_revision")
        _sha256(raw.get("remote_sha256"), "receipt.remote_sha256")
    if state == "revoked":
        _strict_text(raw.get("revocation_reason"), "receipt.revocation_reason")
    if state == "failed":
        _strict_text(raw.get("error_code"), "receipt.error_code")
        if raw.get("retryable") is not False:
            raise ConnectorLedgerIntegrityError("failed receipt must be non-retryable")
    return raw


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ConnectorLedger:
    """SQLite-backed, PREP_ONLY connection and export ledger.

    Methods only validate and persist local state.  They never invoke a
    connector SDK, read an artifact, open a browser, or perform network I/O.
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else WORK_DIR / CONNECTOR_LEDGER_DB_NAME

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        except BaseException:
            connection.rollback()
            connection.close()
            raise
        else:
            if connection.in_transaction:
                connection.commit()
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > CONNECTOR_LEDGER_SCHEMA_VERSION:
                raise ConnectorLedgerError(f"ledger schema {version} is newer than supported {CONNECTOR_LEDGER_SCHEMA_VERSION}")
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS connector_connections (
                        connection_id TEXT NOT NULL,
                        epoch INTEGER NOT NULL CHECK (epoch >= 1),
                        provider TEXT NOT NULL,
                        account_ref TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('active','revoked','expired')),
                        user_selected INTEGER NOT NULL CHECK (user_selected = 1),
                        revoked INTEGER NOT NULL CHECK (revoked IN (0,1)),
                        capability_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (connection_id, epoch)
                    );
                    CREATE INDEX IF NOT EXISTS idx_connector_connections_status
                        ON connector_connections(status);
                    CREATE TABLE IF NOT EXISTS connector_intents (
                        connection_id TEXT NOT NULL,
                        epoch INTEGER NOT NULL CHECK (epoch >= 1),
                        idempotency_key TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        request_fingerprint TEXT NOT NULL,
                        source_artifact_id TEXT NOT NULL,
                        source_revision TEXT NOT NULL,
                        source_sha256 TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (connection_id, epoch, idempotency_key),
                        UNIQUE (connection_id, epoch, request_id),
                        FOREIGN KEY (connection_id, epoch)
                            REFERENCES connector_connections(connection_id, epoch)
                            ON DELETE RESTRICT
                    );
                    CREATE TABLE IF NOT EXISTS connector_receipts (
                        connection_id TEXT NOT NULL,
                        epoch INTEGER NOT NULL CHECK (epoch >= 1),
                        request_id TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('pending','unknown','succeeded','failed','revoked','cancelled')),
                        attempt INTEGER NOT NULL CHECK (attempt >= 1),
                        reconcile_required INTEGER NOT NULL CHECK (reconcile_required IN (0,1)),
                        source_revision TEXT NOT NULL,
                        source_sha256 TEXT NOT NULL,
                        receipt_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (connection_id, epoch, request_id),
                        UNIQUE (connection_id, epoch, idempotency_key),
                        FOREIGN KEY (connection_id, epoch, idempotency_key)
                            REFERENCES connector_intents(connection_id, epoch, idempotency_key)
                            ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS connector_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        connection_id TEXT NOT NULL,
                        epoch INTEGER NOT NULL CHECK (epoch >= 1),
                        request_id TEXT,
                        event_type TEXT NOT NULL,
                        from_status TEXT,
                        to_status TEXT,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (connection_id, epoch)
                            REFERENCES connector_connections(connection_id, epoch)
                            ON DELETE RESTRICT
                    );
                    CREATE INDEX IF NOT EXISTS idx_connector_events_lookup
                        ON connector_events(connection_id, epoch, event_id);
                    PRAGMA user_version = 1;
                    """
                )

    def _ensure_initialized(self) -> None:
        if not self.db_path.exists():
            self.initialize()
        else:
            with self._connection() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != CONNECTOR_LEDGER_SCHEMA_VERSION:
                self.initialize()

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        *,
        connection_id: str,
        epoch: int,
        request_id: str | None,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO connector_events (
                connection_id, epoch, request_id, event_type, from_status,
                to_status, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                connection_id,
                epoch,
                request_id,
                _strict_text(event_type, "event_type"),
                from_status,
                to_status,
                _canonical_json(dict(payload or {}), "event payload"),
                _now(),
            ),
        )

    def register_connection(self, capability: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _validate_connection(capability)
        connection_id = normalized["connection_id"]
        epoch = normalized["epoch"]
        now = _now()
        encoded = _canonical_json(normalized, "connection capability")
        self._ensure_initialized()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM connector_connections WHERE connection_id = ? AND epoch = ?",
                (connection_id, epoch),
            ).fetchone()
            if row is not None:
                if row["capability_json"] != encoded:
                    raise ConnectorLedgerError("connection epoch already exists with a conflicting capability")
                return normalized
            connection.execute(
                """
                INSERT INTO connector_connections (
                    connection_id, epoch, provider, account_ref, scope, status,
                    user_selected, revoked, capability_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    epoch,
                    normalized["provider"],
                    normalized["account_ref"],
                    normalized["scope"],
                    normalized["status"],
                    1,
                    1 if normalized["revoked"] else 0,
                    encoded,
                    now,
                    now,
                ),
            )
            self._record_event(
                connection,
                connection_id=connection_id,
                epoch=epoch,
                request_id=None,
                event_type="connection_registered",
                from_status=None,
                to_status=normalized["status"],
            )
        return copy.deepcopy(normalized)

    def connection(self, connection_id: str, epoch: int) -> dict[str, Any]:
        connection_id = _strict_text(connection_id, "connection_id")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ConnectorLedgerError("epoch must be a positive integer")
        self._ensure_initialized()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT capability_json FROM connector_connections WHERE connection_id = ? AND epoch = ?",
                (connection_id, epoch),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown connection epoch: {connection_id}/{epoch}")
        try:
            payload = json.loads(row["capability_json"])
            return _validate_connection(payload)
        except (ConnectorLedgerError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConnectorLedgerIntegrityError("persisted connection capability is invalid") from exc

    def submit(self, request: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _validate_request(request)
        binding = normalized["connection"]
        connection_id = binding["connection_id"]
        epoch = binding["epoch"]
        key = normalized["idempotency_key"]
        fingerprint = request_intent_fingerprint(normalized)
        self._ensure_initialized()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cap = connection.execute(
                "SELECT * FROM connector_connections WHERE connection_id = ? AND epoch = ?",
                (connection_id, epoch),
            ).fetchone()
            if cap is None:
                raise ConnectorLedgerError("connection epoch is not registered")
            if cap["status"] != "active" or cap["revoked"]:
                raise ConnectorLedgerError("connection capability is not active")
            if cap["provider"] != normalized["destination"]["provider"] or cap["account_ref"] != normalized["destination"]["account_ref"] or cap["scope"] != normalized["destination"]["scope"]:
                raise ConnectorLedgerError("request destination does not match connection capability")
            existing = connection.execute(
                "SELECT request_id, request_fingerprint FROM connector_intents WHERE connection_id = ? AND epoch = ? AND idempotency_key = ?",
                (connection_id, epoch, key),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise ConnectorLedgerError("idempotency key conflicts with an existing export intent")
                # A retry may carry a fresh transport request_id.  The
                # idempotency row remains authoritative and returns the
                # original receipt without creating a second dispatch.
                return self._receipt_locked(connection, connection_id, epoch, existing["request_id"])
            existing_request = connection.execute(
                "SELECT request_fingerprint FROM connector_intents WHERE connection_id = ? AND epoch = ? AND request_id = ?",
                (connection_id, epoch, normalized["request_id"]),
            ).fetchone()
            if existing_request is not None:
                raise ConnectorLedgerError("request_id conflicts with an existing export intent")
            now = _now()
            receipt = {
                "schema_version": "workspace-export-receipt-v1",
                "execution_mode": PREP_ONLY,
                "request_id": normalized["request_id"],
                "idempotency_key": key,
                "destination_provider": normalized["destination"]["provider"],
                "connection": copy.deepcopy(binding),
                "status": "pending",
                "attempt": 1,
                "reconcile_required": False,
                "external_id": None,
                "remote_revision": None,
                "remote_sha256": None,
                "source": copy.deepcopy(normalized["source"]),
                "intent_fingerprint": fingerprint,
            }
            encoded_request = _canonical_json(normalized, "export request")
            encoded_receipt = _canonical_json(receipt, "export receipt")
            connection.execute(
                """
                INSERT INTO connector_intents (
                    connection_id, epoch, idempotency_key, request_id,
                    request_fingerprint, source_artifact_id, source_revision,
                    source_sha256, request_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    epoch,
                    key,
                    normalized["request_id"],
                    fingerprint,
                    normalized["source"]["artifact_id"],
                    str(normalized["source"]["revision"]),
                    normalized["source"]["artifact_sha256"],
                    encoded_request,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO connector_receipts (
                    connection_id, epoch, request_id, idempotency_key,
                    status, attempt, reconcile_required, source_revision,
                    source_sha256, receipt_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    epoch,
                    normalized["request_id"],
                    key,
                    "pending",
                    1,
                    0,
                    str(normalized["source"]["revision"]),
                    normalized["source"]["artifact_sha256"],
                    encoded_receipt,
                    now,
                    now,
                ),
            )
            self._record_event(
                connection,
                connection_id=connection_id,
                epoch=epoch,
                request_id=normalized["request_id"],
                event_type="intent_created",
                from_status=None,
                to_status="pending",
                payload={"idempotency_key": key, "source_revision": normalized["source"]["revision"]},
            )
            return copy.deepcopy(receipt)

    def _receipt_locked(self, connection: sqlite3.Connection, connection_id: str, epoch: int, request_id: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT i.request_json, r.receipt_json
            FROM connector_receipts r
            JOIN connector_intents i
              ON i.connection_id = r.connection_id AND i.epoch = r.epoch
             AND i.request_id = r.request_id AND i.idempotency_key = r.idempotency_key
            WHERE r.connection_id = ? AND r.epoch = ? AND r.request_id = ?
            """,
            (connection_id, epoch, request_id),
        ).fetchone()
        if row is None:
            raise ConnectorLedgerIntegrityError("intent index points to a missing receipt")
        try:
            request = _validate_request(json.loads(row["request_json"]))
            receipt = _validate_receipt_payload(json.loads(row["receipt_json"]), request)
            if receipt["connection"] != {"connection_id": connection_id, "epoch": epoch}:
                raise ConnectorLedgerIntegrityError("receipt binding mismatch")
            return copy.deepcopy(receipt)
        except (ConnectorLedgerError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, ConnectorLedgerIntegrityError):
                raise
            raise ConnectorLedgerIntegrityError("persisted connector receipt is invalid") from exc

    def receipt(self, connection_id: str, epoch: int, request_id: str) -> dict[str, Any]:
        connection_id = _strict_text(connection_id, "connection_id")
        request_id = _strict_text(request_id, "request_id")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ConnectorLedgerError("epoch must be a positive integer")
        self._ensure_initialized()
        with self._connection() as connection:
            try:
                return self._receipt_locked(connection, connection_id, epoch, request_id)
            except ConnectorLedgerIntegrityError:
                raise

    def _load_request_and_receipt_locked(self, connection: sqlite3.Connection, connection_id: str, epoch: int, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        row = connection.execute(
            """
            SELECT i.request_json, r.receipt_json
            FROM connector_intents i
            JOIN connector_receipts r
              ON r.connection_id = i.connection_id AND r.epoch = i.epoch
             AND r.request_id = i.request_id AND r.idempotency_key = i.idempotency_key
            WHERE i.connection_id = ? AND i.epoch = ? AND i.request_id = ?
            """,
            (connection_id, epoch, request_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown request_id: {request_id}")
        try:
            request = _validate_request(json.loads(row["request_json"]))
            receipt = _validate_receipt_payload(json.loads(row["receipt_json"]), request)
            return request, receipt
        except (ConnectorLedgerError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConnectorLedgerIntegrityError("persisted connector intent is invalid") from exc

    @staticmethod
    def _next_status(current: str, event_type: str) -> str:
        if current in TERMINAL_STATUSES:
            return current
        if event_type in {"connection_revoked", "cancel_requested"}:
            return "revoked" if event_type == "connection_revoked" else "cancelled"
        transitions = {
            "dispatch_started": "pending",
            "dispatch_timeout": "unknown",
            "outcome_unknown": "unknown",
            "lookup_not_found": "pending",
            "lookup_succeeded": "succeeded",
            "dispatch_failed": "failed",
        }
        try:
            return transitions[event_type]
        except KeyError as exc:
            raise ConnectorLedgerError(f"unsupported ledger event: {event_type}") from exc

    def _write_receipt_locked(
        self,
        connection: sqlite3.Connection,
        *,
        connection_id: str,
        epoch: int,
        request: Mapping[str, Any],
        receipt: dict[str, Any],
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt = _validate_receipt_payload(receipt, request)
        old_status = receipt.get("status")
        new_status = receipt.get("status")
        encoded = _canonical_json(receipt, "export receipt")
        connection.execute(
            """
            UPDATE connector_receipts
            SET status = ?, attempt = ?, reconcile_required = ?, source_revision = ?,
                source_sha256 = ?, receipt_json = ?, updated_at = ?
            WHERE connection_id = ? AND epoch = ? AND request_id = ?
            """,
            (
                new_status,
                receipt.get("attempt"),
                1 if receipt.get("reconcile_required") else 0,
                str(request["source"]["revision"]),
                request["source"]["artifact_sha256"],
                encoded,
                _now(),
                connection_id,
                epoch,
                request["request_id"],
            ),
        )
        connection.execute(
            "UPDATE connector_intents SET updated_at = ? WHERE connection_id = ? AND epoch = ? AND request_id = ?",
            (_now(), connection_id, epoch, request["request_id"]),
        )
        if old_status != new_status or event_type not in {"dispatch_started"}:
            self._record_event(
                connection,
                connection_id=connection_id,
                epoch=epoch,
                request_id=request["request_id"],
                event_type=event_type,
                from_status=old_status,
                to_status=new_status,
                payload=payload,
            )
        return copy.deepcopy(receipt)

    def _transition(self, connection_id: str, epoch: int, request_id: str, event_type: str, *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_initialized()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request, receipt = self._load_request_and_receipt_locked(connection, connection_id, epoch, request_id)
            current = receipt["status"]
            next_status = self._next_status(current, event_type)
            if next_status == current:
                return copy.deepcopy(receipt)
            receipt["status"] = next_status
            receipt["reconcile_required"] = next_status == "unknown"
            if next_status == "revoked":
                receipt["revocation_reason"] = _strict_text((extra or {}).get("reason"), "revocation_reason")
            return self._write_receipt_locked(connection, connection_id=connection_id, epoch=epoch, request=request, receipt=receipt, event_type=event_type, payload=extra)

    def mark_unknown(self, connection_id: str, epoch: int, request_id: str) -> dict[str, Any]:
        return self._transition(connection_id, epoch, request_id, "outcome_unknown")

    def mark_timeout(self, connection_id: str, epoch: int, request_id: str) -> dict[str, Any]:
        return self._transition(connection_id, epoch, request_id, "dispatch_timeout")

    def cancel(self, connection_id: str, epoch: int, request_id: str) -> dict[str, Any]:
        return self._transition(connection_id, epoch, request_id, "cancel_requested")

    def reconcile(self, connection_id: str, epoch: int, request_id: str, lookup: Mapping[str, Any] | None) -> dict[str, Any]:
        self._ensure_initialized()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request, receipt = self._load_request_and_receipt_locked(connection, connection_id, epoch, request_id)
            if receipt["status"] != "unknown":
                return {"action": "no_lookup_needed", "status": receipt["status"], "receipt": copy.deepcopy(receipt)}
            if lookup is None or not isinstance(lookup, Mapping):
                return {"action": "manual_reconciliation", "reason": "lookup_unavailable", "receipt": copy.deepcopy(receipt)}
            lookup = dict(lookup)
            _canonical_json(lookup, "reconciliation lookup")
            remote_status = lookup.get("status")
            if remote_status == "succeeded":
                external_id = _strict_text(lookup.get("external_id"), "external_id")
                remote_revision = _strict_text(lookup.get("remote_revision"), "remote_revision")
                remote_sha256 = _sha256(lookup.get("remote_sha256"), "remote_sha256")
                receipt.update(
                    status="succeeded",
                    reconcile_required=False,
                    external_id=external_id,
                    remote_revision=remote_revision,
                    remote_sha256=remote_sha256,
                )
                updated = self._write_receipt_locked(connection, connection_id=connection_id, epoch=epoch, request=request, receipt=receipt, event_type="lookup_succeeded", payload={"external_id": external_id, "remote_revision": remote_revision})
                return {"action": "adopt_existing", "status": "succeeded", "resend": False, "receipt": updated}
            if remote_status == "not_found":
                policy = request["retry_policy"]
                if receipt["attempt"] >= policy["max_attempts"]:
                    receipt.update(status="failed", reconcile_required=False, error_code="retry_exhausted", retryable=False)
                    updated = self._write_receipt_locked(connection, connection_id=connection_id, epoch=epoch, request=request, receipt=receipt, event_type="dispatch_failed", payload={"reason": "retry_exhausted"})
                    return {"action": "retry_exhausted", "status": "failed", "resend": False, "error_code": "retry_exhausted", "receipt": updated}
                receipt.update(status="pending", attempt=receipt["attempt"] + 1, reconcile_required=False)
                updated = self._write_receipt_locked(connection, connection_id=connection_id, epoch=epoch, request=request, receipt=receipt, event_type="lookup_not_found", payload={"retry": True})
                return {"action": "retry_allowed", "status": "pending", "resend": True, "receipt": updated}
            return {"action": "manual_reconciliation", "reason": "ambiguous_lookup", "receipt": copy.deepcopy(receipt)}

    def revoke_connection(self, connection_id: str, epoch: int, reason: str) -> dict[str, Any]:
        connection_id = _strict_text(connection_id, "connection_id")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ConnectorLedgerError("epoch must be a positive integer")
        reason = _strict_text(reason, "revocation_reason")
        self._ensure_initialized()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT capability_json, status FROM connector_connections WHERE connection_id = ? AND epoch = ?",
                (connection_id, epoch),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown connection epoch: {connection_id}/{epoch}")
            capability = _validate_connection(json.loads(row["capability_json"]))
            if capability["status"] != "revoked":
                capability.update(status="revoked", revoked=True, revocation_reason=reason)
                connection.execute(
                    "UPDATE connector_connections SET status = 'revoked', revoked = 1, capability_json = ?, updated_at = ? WHERE connection_id = ? AND epoch = ?",
                    (_canonical_json(capability, "connection capability"), _now(), connection_id, epoch),
                )
                self._record_event(connection, connection_id=connection_id, epoch=epoch, request_id=None, event_type="connection_revoked", from_status=row["status"], to_status="revoked", payload={"reason": reason})
            rows = connection.execute(
                "SELECT request_id FROM connector_receipts WHERE connection_id = ? AND epoch = ? AND status NOT IN ('succeeded','failed','revoked','cancelled')",
                (connection_id, epoch),
            ).fetchall()
            for item in rows:
                request, receipt = self._load_request_and_receipt_locked(connection, connection_id, epoch, item["request_id"])
                receipt.update(status="revoked", reconcile_required=False, revocation_reason=reason)
                self._write_receipt_locked(connection, connection_id=connection_id, epoch=epoch, request=request, receipt=receipt, event_type="connection_revoked", payload={"reason": reason})
            return copy.deepcopy(capability)

    def events(self, connection_id: str, epoch: int, *, request_id: str | None = None) -> list[dict[str, Any]]:
        connection_id = _strict_text(connection_id, "connection_id")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ConnectorLedgerError("epoch must be a positive integer")
        self._ensure_initialized()
        with self._connection() as connection:
            if request_id is None:
                rows = connection.execute("SELECT * FROM connector_events WHERE connection_id = ? AND epoch = ? ORDER BY event_id", (connection_id, epoch)).fetchall()
            else:
                request_id = _strict_text(request_id, "request_id")
                rows = connection.execute("SELECT * FROM connector_events WHERE connection_id = ? AND epoch = ? AND request_id = ? ORDER BY event_id", (connection_id, epoch, request_id)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                _canonical_json(payload, "event payload")
            except (ConnectorLedgerError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ConnectorLedgerIntegrityError("persisted connector event is invalid") from exc
            result.append({
                "event_id": row["event_id"],
                "connection_id": row["connection_id"],
                "epoch": row["epoch"],
                "request_id": row["request_id"],
                "event_type": row["event_type"],
                "from_status": row["from_status"],
                "to_status": row["to_status"],
                "payload": payload,
                "created_at": row["created_at"],
            })
        return result


def default_connector_ledger() -> ConnectorLedger:
    """Return the project-local ledger without performing initialization."""

    return ConnectorLedger(WORK_DIR / CONNECTOR_LEDGER_DB_NAME)
