"""Durable local catalog projection for the VI Dubber M5 boundary.

The catalog is deliberately a projection over existing job/manifests.  It owns
metadata, search and user review state; it never owns or deletes media bytes.
All writes are transactionally committed to SQLite and metadata backups are
portable JSON snapshots rather than copies of a live database or blob store.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .artifacts import atomic_write_json, canonical_json, fingerprint_file


CATALOG_SCHEMA_VERSION = 1
BACKUP_FORMAT = "vi-dubber-m5-catalog-backup-v1"
AVAILABILITY_STATES = frozenset({"available", "missing", "stale", "failed", "pending", "unknown"})
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\|/(?!/))")
_MAX_SEARCH_LIMIT = 5_000


class CatalogError(ValueError):
    """Raised for invalid catalog data or an unsafe catalog operation."""


class CatalogIntegrityError(CatalogError):
    """Raised when a backup or relink fingerprint does not verify."""


@dataclass(frozen=True, slots=True)
class CatalogItem:
    item_id: str
    title: str
    source_fingerprint: str
    revision: str
    availability: str
    segment_count: int
    source_ref: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UserState:
    item_id: str
    revision: str
    bookmarks: tuple[Mapping[str, Any], ...] = ()
    review_state: str = ""
    watch_position_seconds: float = 0.0


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{name} must be a non-empty string")
    return value.strip()


def _validate_item_id(value: Any) -> str:
    item_id = _require_text(value, "item_id")
    if any(separator in item_id for separator in ("/", "\\")) or item_id in {".", ".."}:
        raise CatalogError("item_id must be a portable identifier")
    return item_id


def _validate_fingerprint(value: Any, name: str = "source_fingerprint") -> str:
    fingerprint = _require_text(value, name).lower()
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise CatalogError(f"{name} must be a lowercase SHA-256 hex digest")
    return fingerprint


def _validate_source_ref(value: Any) -> str:
    source_ref = _require_text(value, "source_ref")
    if "\\" in source_ref:
        raise CatalogError("source_ref must use portable POSIX separators")
    path = PurePosixPath(source_ref)
    if path.is_absolute() or not path.parts or ".." in path.parts or any(":" in part for part in path.parts):
        raise CatalogError("source_ref must be a safe relative POSIX path")
    return path.as_posix()


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CatalogError("metadata must be an object")
    try:
        encoded = canonical_json(dict(value))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise CatalogError("metadata must be JSON-compatible and finite") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - guarded by input check
        raise CatalogError("metadata must be an object")
    if _contains_absolute_path(decoded):
        raise CatalogError("metadata must not contain absolute filesystem paths")
    return decoded


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_ABSOLUTE_PATH_RE.search(value))
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(key) or _contains_absolute_path(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_path(item) for item in value)
    return False


def _validate_item(item: CatalogItem) -> CatalogItem:
    if not isinstance(item, CatalogItem):
        raise CatalogError("catalog entries must be CatalogItem values")
    availability = _require_text(item.availability, "availability").lower()
    if availability not in AVAILABILITY_STATES:
        raise CatalogError(f"unsupported availability state: {availability}")
    if isinstance(item.segment_count, bool) or not isinstance(item.segment_count, int) or item.segment_count < 0:
        raise CatalogError("segment_count must be a non-negative integer")
    return CatalogItem(
        item_id=_validate_item_id(item.item_id),
        title=_require_text(item.title, "title"),
        source_fingerprint=_validate_fingerprint(item.source_fingerprint),
        revision=_require_text(item.revision, "revision"),
        availability=availability,
        segment_count=item.segment_count,
        source_ref=_validate_source_ref(item.source_ref),
        metadata=_validate_metadata(item.metadata),
    )


def _validate_bookmarks(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise CatalogError("bookmarks must be a list")
    result: list[dict[str, Any]] = []
    for bookmark in value:
        if not isinstance(bookmark, Mapping):
            raise CatalogError("each bookmark must be an object")
        index = bookmark.get("segment_index")
        position = bookmark.get("position_seconds")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise CatalogError("bookmark segment_index must be a non-negative integer")
        if isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(float(position)) or float(position) < 0:
            raise CatalogError("bookmark position_seconds must be finite and non-negative")
        # Preserve only the stable user-facing bookmark shape; arbitrary keys
        # must not become an accidental persistence channel for secrets/blobs.
        result.append({"segment_index": index, "position_seconds": float(position)})
    return tuple(result)


def _validate_user_state(state: UserState) -> UserState:
    if not isinstance(state, UserState):
        raise CatalogError("user state must be a UserState value")
    position = state.watch_position_seconds
    if isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(float(position)) or float(position) < 0:
        raise CatalogError("watch_position_seconds must be finite and non-negative")
    return UserState(
        item_id=_validate_item_id(state.item_id),
        revision=_require_text(state.revision, "revision"),
        bookmarks=_validate_bookmarks(state.bookmarks),
        review_state=str(state.review_state or "").strip(),
        watch_position_seconds=float(position),
    )


def _item_from_row(row: sqlite3.Row) -> CatalogItem:
    try:
        metadata = json.loads(row["metadata_json"])
        return _validate_item(
            CatalogItem(
                item_id=row["item_id"],
                title=row["title"],
                source_fingerprint=row["source_fingerprint"],
                revision=row["revision"],
                availability=row["availability"],
                segment_count=row["segment_count"],
                source_ref=row["source_ref"],
                metadata=metadata,
            )
        )
    except (CatalogError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CatalogIntegrityError("catalog row failed validation") from exc


def _state_from_row(row: sqlite3.Row) -> UserState:
    try:
        bookmarks = json.loads(row["bookmarks_json"])
        if not isinstance(bookmarks, list):
            raise CatalogIntegrityError("user state bookmarks row is not a list")
        return _validate_user_state(
            UserState(
                item_id=row["item_id"],
                revision=row["revision"],
                bookmarks=tuple(bookmarks),
                review_state=row["review_state"],
                watch_position_seconds=float(row["watch_position_seconds"]),
            )
        )
    except (CatalogError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CatalogIntegrityError("user state row failed validation") from exc


class CatalogStore:
    """SQLite-backed catalog projection with metadata-only backup/restore."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        """Create or migrate the local schema in one transaction."""
        with self._connect() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > CATALOG_SCHEMA_VERSION:
                raise CatalogError(
                    f"catalog schema {current} is newer than supported {CATALOG_SCHEMA_VERSION}"
                )
            if current == 0:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS catalog_items (
                        item_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        title_casefold TEXT NOT NULL,
                        source_fingerprint TEXT NOT NULL,
                        revision TEXT NOT NULL,
                        availability TEXT NOT NULL,
                        segment_count INTEGER NOT NULL CHECK (segment_count >= 0),
                        source_ref TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_catalog_title_casefold
                        ON catalog_items(title_casefold);
                    CREATE INDEX IF NOT EXISTS idx_catalog_availability
                        ON catalog_items(availability);
                    CREATE TABLE IF NOT EXISTS user_state (
                        item_id TEXT PRIMARY KEY,
                        revision TEXT NOT NULL,
                        bookmarks_json TEXT NOT NULL,
                        review_state TEXT NOT NULL,
                        watch_position_seconds REAL NOT NULL CHECK (watch_position_seconds >= 0),
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(item_id) REFERENCES catalog_items(item_id) ON DELETE RESTRICT
                    );
                    PRAGMA user_version = 1;
                    """
                )

    def _ensure_initialized(self) -> None:
        if not self.db_path.exists():
            self.initialize()
        else:
            with self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != CATALOG_SCHEMA_VERSION:
                self.initialize()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _item_params(item: CatalogItem, now: str) -> tuple[Any, ...]:
        return (
            item.item_id,
            item.title,
            item.title.casefold(),
            item.source_fingerprint,
            item.revision,
            item.availability,
            item.segment_count,
            item.source_ref,
            canonical_json(item.metadata),
            now,
        )

    def upsert_item(self, item: CatalogItem) -> None:
        item = _validate_item(item)
        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO catalog_items (
                    item_id, title, title_casefold, source_fingerprint, revision,
                    availability, segment_count, source_ref, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    title = excluded.title,
                    title_casefold = excluded.title_casefold,
                    source_fingerprint = excluded.source_fingerprint,
                    revision = excluded.revision,
                    availability = excluded.availability,
                    segment_count = excluded.segment_count,
                    source_ref = excluded.source_ref,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                self._item_params(item, self._now()),
            )
            connection.commit()

    def rebuild(self, items: Iterable[CatalogItem]) -> int:
        """Atomically replace the projection; state survives only its revision."""
        materialized = [_validate_item(item) for item in items]
        seen: set[str] = set()
        for item in materialized:
            if item.item_id in seen:
                raise CatalogError(f"duplicate catalog item: {item.item_id}")
            seen.add(item.item_id)
        self._ensure_initialized()
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Foreign-key protection intentionally prevents deleting a catalog
            # row while user state still references it.  Snapshot the user
            # state inside this same transaction, replace the projection, then
            # restore state for IDs that still exist.
            # Validate the existing state before deleting anything.  A direct
            # SQLite edit or interrupted external restore must not be carried
            # into the rebuilt projection, nor should it cause a partial wipe.
            preserved_states = [
                _state_from_row(row)
                for row in connection.execute("SELECT * FROM user_state ORDER BY item_id")
            ]
            connection.execute("DELETE FROM user_state")
            connection.execute("DELETE FROM catalog_items")
            connection.executemany(
                """
                INSERT INTO catalog_items (
                    item_id, title, title_casefold, source_fingerprint, revision,
                    availability, segment_count, source_ref, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._item_params(item, now) for item in materialized],
            )
            valid_ids = {item.item_id for item in materialized}
            revisions = {item.item_id: item.revision for item in materialized}
            connection.executemany(
                """
                INSERT INTO user_state (
                    item_id, revision, bookmarks_json, review_state,
                    watch_position_seconds, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        state.item_id,
                        state.revision,
                        canonical_json(list(state.bookmarks)),
                        state.review_state,
                        state.watch_position_seconds,
                        now,
                    )
                    for state in preserved_states
                    # Review/bookmark state is scoped to the exact catalog
                    # revision.  A rebuild may discover a newer job lineage;
                    # carrying state across that boundary would make an old
                    # segment index or review decision look current.
                    if state.item_id in valid_ids and state.revision == revisions[state.item_id]
                ],
            )
            connection.commit()
        return len(materialized)

    def get_item(self, item_id: str) -> CatalogItem | None:
        item_id = _validate_item_id(item_id)
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM catalog_items WHERE item_id = ?", (item_id,)).fetchone()
        return _item_from_row(row) if row is not None else None

    def search(
        self,
        query: str = "",
        *,
        availability: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CatalogItem]:
        if not isinstance(query, str):
            raise CatalogError("query must be a string")
        if availability is not None and availability not in AVAILABILITY_STATES:
            raise CatalogError(f"unsupported availability state: {availability}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= _MAX_SEARCH_LIMIT:
            raise CatalogError(f"limit must be between 1 and {_MAX_SEARCH_LIMIT}")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise CatalogError("offset must be a non-negative integer")
        normalized = query.casefold().strip()
        # Escape wildcards so search text is literal rather than a SQL pattern.
        escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses = ["title_casefold LIKE ? ESCAPE '\\'"]
        params: list[Any] = [f"%{escaped}%"]
        if availability is not None:
            clauses.append("availability = ?")
            params.append(availability)
        params.extend((limit, offset))
        self._ensure_initialized()
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM catalog_items WHERE {' AND '.join(clauses)} ORDER BY item_id LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [_item_from_row(row) for row in rows]

    def delete_item(self, item_id: str) -> None:
        """Remove only the catalog projection; source/media is never touched."""
        item_id = _validate_item_id(item_id)
        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # User state is a separate, intentionally disposable projection;
            # removing a catalog item removes its bookmark/review row but never
            # reaches through to source or rendition files.
            connection.execute("DELETE FROM user_state WHERE item_id = ?", (item_id,))
            connection.execute("DELETE FROM catalog_items WHERE item_id = ?", (item_id,))
            connection.commit()

    def set_user_state(self, state: UserState) -> None:
        state = _validate_user_state(state)
        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM catalog_items WHERE item_id = ?",
                (state.item_id,),
            ).fetchone()
            if row is None:
                raise CatalogError(f"cannot persist state for unknown item: {state.item_id}")
            if row["revision"] != state.revision:
                raise CatalogError(
                    f"user state revision {state.revision!r} does not match catalog revision {row['revision']!r}"
                )
            connection.execute(
                """
                INSERT INTO user_state (
                    item_id, revision, bookmarks_json, review_state,
                    watch_position_seconds, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    revision = excluded.revision,
                    bookmarks_json = excluded.bookmarks_json,
                    review_state = excluded.review_state,
                    watch_position_seconds = excluded.watch_position_seconds,
                    updated_at = excluded.updated_at
                """,
                (
                    state.item_id,
                    state.revision,
                    canonical_json(list(state.bookmarks)),
                    state.review_state,
                    state.watch_position_seconds,
                    self._now(),
                ),
            )
            connection.commit()

    def get_user_state(self, item_id: str) -> UserState | None:
        item_id = _validate_item_id(item_id)
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_state.*, catalog_items.revision AS catalog_revision
                FROM user_state
                LEFT JOIN catalog_items ON catalog_items.item_id = user_state.item_id
                WHERE user_state.item_id = ?
                """,
                (item_id,),
            ).fetchone()
        if row is None:
            return None
        if row["catalog_revision"] is None:
            raise CatalogIntegrityError("user state references an unknown catalog item")
        state = _state_from_row(row)
        if state.revision != row["catalog_revision"]:
            raise CatalogIntegrityError("user state revision does not match catalog item revision")
        return state

    def export_metadata(self) -> dict[str, Any]:
        """Return a deterministic, blob-free snapshot of catalog and user state."""
        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("BEGIN")
            items = [_item_from_row(row) for row in connection.execute("SELECT * FROM catalog_items ORDER BY item_id")]
            states = []
            for row in connection.execute(
                """
                SELECT user_state.*, catalog_items.revision AS catalog_revision
                FROM user_state
                LEFT JOIN catalog_items ON catalog_items.item_id = user_state.item_id
                ORDER BY user_state.item_id
                """
            ):
                if row["catalog_revision"] is None:
                    raise CatalogIntegrityError("user state references an unknown catalog item")
                state = _state_from_row(row)
                if state.revision != row["catalog_revision"]:
                    raise CatalogIntegrityError("user state revision does not match catalog item revision")
                states.append(state)
            connection.commit()
        core = {
            "format": BACKUP_FORMAT,
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog": [self._item_to_backup(item) for item in items],
            "user_state": [self._state_to_backup(state) for state in states],
        }
        return {**core, "payload_digest": _digest_payload(core)}

    def backup_to(self, path: Path) -> dict[str, Any]:
        payload = self.export_metadata()
        atomic_write_json(Path(path), payload)
        return payload

    def restore_metadata(self, payload: Mapping[str, Any], *, replace: bool = True) -> int:
        """Restore a verified metadata snapshot without touching media files."""
        items, states, core = _parse_backup(payload)
        self._ensure_initialized()
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if replace:
                connection.execute("DELETE FROM user_state")
                connection.execute("DELETE FROM catalog_items")
            connection.executemany(
                """
                INSERT INTO catalog_items (
                    item_id, title, title_casefold, source_fingerprint, revision,
                    availability, segment_count, source_ref, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    title = excluded.title,
                    title_casefold = excluded.title_casefold,
                    source_fingerprint = excluded.source_fingerprint,
                    revision = excluded.revision,
                    availability = excluded.availability,
                    segment_count = excluded.segment_count,
                    source_ref = excluded.source_ref,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                [self._item_params(item, now) for item in items],
            )
            connection.executemany(
                """
                INSERT INTO user_state (
                    item_id, revision, bookmarks_json, review_state,
                    watch_position_seconds, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    revision = excluded.revision,
                    bookmarks_json = excluded.bookmarks_json,
                    review_state = excluded.review_state,
                    watch_position_seconds = excluded.watch_position_seconds,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        state.item_id,
                        state.revision,
                        canonical_json(list(state.bookmarks)),
                        state.review_state,
                        state.watch_position_seconds,
                        now,
                    )
                    for state in states
                ],
            )
            connection.commit()
        # Keep the parsed core in scope to make it explicit that the digest was
        # validated before any mutation.  The returned count is intentionally
        # just the catalog cardinality for callers rebuilding a projection.
        _ = core
        return len(items)

    def probe_availability(self, item_id: str, root: Path) -> str:
        """Verify the mapped source path and persist an explicit availability state."""
        item = self.get_item(item_id)
        if item is None:
            raise CatalogError(f"unknown catalog item: {item_id}")
        try:
            candidate = (Path(root).resolve() / Path(*PurePosixPath(item.source_ref).parts)).resolve(strict=False)
            candidate.relative_to(Path(root).resolve())
        except (OSError, ValueError):
            state = "failed"
        else:
            try:
                if not candidate.is_file():
                    state = "missing"
                elif fingerprint_file(candidate)["sha256"] != item.source_fingerprint:
                    state = "stale"
                else:
                    state = "available"
            except OSError:
                state = "failed"
        self.upsert_item(
            CatalogItem(
                item_id=item.item_id,
                title=item.title,
                source_fingerprint=item.source_fingerprint,
                revision=item.revision,
                availability=state,
                segment_count=item.segment_count,
                source_ref=item.source_ref,
                metadata=item.metadata,
            )
        )
        return state

    def relink(self, item_id: str, candidate: Path, *, source_ref: str) -> CatalogItem:
        """Relink only with an explicit portable mapping and exact content hash."""
        item = self.get_item(item_id)
        if item is None:
            raise CatalogError(f"unknown catalog item: {item_id}")
        mapped_ref = _validate_source_ref(source_ref)
        candidate = Path(candidate)
        try:
            actual = fingerprint_file(candidate)["sha256"]
        except OSError as exc:
            raise CatalogIntegrityError("relink candidate is not readable") from exc
        if actual != item.source_fingerprint:
            raise CatalogIntegrityError("relink candidate fingerprint does not match catalog source")
        updated = CatalogItem(
            item_id=item.item_id,
            title=item.title,
            source_fingerprint=item.source_fingerprint,
            revision=item.revision,
            availability="available",
            segment_count=item.segment_count,
            source_ref=mapped_ref,
            metadata=item.metadata,
        )
        self.upsert_item(updated)
        return updated

    @staticmethod
    def _item_to_backup(item: CatalogItem) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "title": item.title,
            "source_fingerprint": item.source_fingerprint,
            "revision": item.revision,
            "availability": item.availability,
            "segment_count": item.segment_count,
            "source_ref": item.source_ref,
            "metadata": dict(item.metadata),
        }

    @staticmethod
    def _state_to_backup(state: UserState) -> dict[str, Any]:
        return {
            "item_id": state.item_id,
            "revision": state.revision,
            "bookmarks": list(state.bookmarks),
            "review_state": state.review_state,
            "watch_position_seconds": state.watch_position_seconds,
        }


def _digest_payload(payload: Mapping[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_backup(payload: Mapping[str, Any]) -> tuple[list[CatalogItem], list[UserState], dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise CatalogIntegrityError("catalog backup must be an object")
    if set(payload) != {"format", "schema_version", "catalog", "user_state", "payload_digest"}:
        raise CatalogIntegrityError("catalog backup contains unsupported fields")
    if payload.get("format") != BACKUP_FORMAT or payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise CatalogIntegrityError("unsupported catalog backup format or schema")
    catalog_raw = payload.get("catalog")
    state_raw = payload.get("user_state")
    if not isinstance(catalog_raw, list) or not isinstance(state_raw, list):
        raise CatalogIntegrityError("catalog backup lists are invalid")
    items: list[CatalogItem] = []
    seen: set[str] = set()
    item_fields = {"item_id", "title", "source_fingerprint", "revision", "availability", "segment_count", "source_ref", "metadata"}
    for raw in catalog_raw:
        if not isinstance(raw, Mapping):
            raise CatalogIntegrityError("catalog backup item is invalid")
        if set(raw) != item_fields:
            raise CatalogIntegrityError("catalog backup item contains unsupported fields")
        try:
            item = _validate_item(CatalogItem(**dict(raw)))
        except (CatalogError, TypeError) as exc:
            raise CatalogIntegrityError("catalog backup item failed validation") from exc
        if item.item_id in seen:
            raise CatalogIntegrityError(f"duplicate catalog backup item: {item.item_id}")
        seen.add(item.item_id)
        items.append(item)
    states: list[UserState] = []
    item_revisions = {item.item_id: item.revision for item in items}
    state_fields = {"item_id", "revision", "bookmarks", "review_state", "watch_position_seconds"}
    state_ids: set[str] = set()
    for raw in state_raw:
        if not isinstance(raw, Mapping):
            raise CatalogIntegrityError("user state backup is invalid")
        if set(raw) != state_fields:
            raise CatalogIntegrityError("user state backup contains unsupported fields")
        try:
            state = _validate_user_state(UserState(**dict(raw)))
        except (CatalogError, TypeError) as exc:
            raise CatalogIntegrityError("user state backup failed validation") from exc
        if state.item_id not in seen:
            raise CatalogIntegrityError(f"user state references unknown item: {state.item_id}")
        if state.revision != item_revisions[state.item_id]:
            raise CatalogIntegrityError("user state revision does not match catalog item revision")
        if state.item_id in state_ids:
            raise CatalogIntegrityError(f"duplicate user state backup item: {state.item_id}")
        state_ids.add(state.item_id)
        states.append(state)
    core = {
        "format": BACKUP_FORMAT,
        "schema_version": CATALOG_SCHEMA_VERSION,
        "catalog": [CatalogStore._item_to_backup(item) for item in items],
        "user_state": [CatalogStore._state_to_backup(state) for state in states],
    }
    digest = payload.get("payload_digest")
    if not isinstance(digest, str) or digest != _digest_payload(core):
        raise CatalogIntegrityError("catalog backup payload digest mismatch")
    return items, states, core

