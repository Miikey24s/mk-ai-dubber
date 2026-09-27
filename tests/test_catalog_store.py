from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from vi_dubber.catalog_store import (
    BACKUP_FORMAT,
    CATALOG_SCHEMA_VERSION,
    CatalogError,
    CatalogIntegrityError,
    CatalogItem,
    CatalogStore,
    UserState,
    _digest_payload,
)


def _item(item_id: str = "job-0001", *, title: str = "Fixture video 0001", source_fingerprint: str | None = None) -> CatalogItem:
    return CatalogItem(
        item_id=item_id,
        title=title,
        source_fingerprint=source_fingerprint or ("a" * 64),
        revision="r1",
        availability="unknown",
        segment_count=3,
        source_ref=f"sources/{item_id}/source.mp4",
        metadata={"language": "en", "tags": ["fixture"]},
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_schema_is_created_and_reopened_with_explicit_version(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    store = CatalogStore(db_path)
    store.initialize()
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
    # A new store instance must reuse the same schema without losing rows.
    store.upsert_item(_item())
    assert CatalogStore(db_path).get_item("job-0001") == _item()


def test_rebuild_is_atomic_and_retains_state_for_surviving_items(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    store.rebuild([_item(), _item("job-0002", title="Second")])
    store.set_user_state(
        UserState(
            item_id="job-0001",
            revision="r1",
            bookmarks=({"segment_index": 2, "position_seconds": 12.5},),
            review_state="in_review",
            watch_position_seconds=22.0,
        )
    )

    store.rebuild([_item(), _item("job-0003", title="Third")])
    assert store.get_item("job-0002") is None
    assert store.get_item("job-0003") is not None
    assert store.get_user_state("job-0001") == UserState(
        item_id="job-0001",
        revision="r1",
        bookmarks=({"segment_index": 2, "position_seconds": 12.5},),
        review_state="in_review",
        watch_position_seconds=22.0,
    )

    with pytest.raises(CatalogError, match="duplicate"):
        store.rebuild([_item(), _item()])
    assert store.get_item("job-0001") is not None
    assert store.get_item("job-0003") is not None


def test_rebuild_drops_user_state_when_surviving_item_revision_changes(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    store.rebuild([_item()])
    store.set_user_state(UserState("job-0001", "r1", (), "reviewed", 18.0))

    revised = CatalogItem(
        item_id="job-0001",
        title="Fixture video 0001",
        source_fingerprint="a" * 64,
        revision="r2",
        availability="available",
        segment_count=4,
        source_ref="sources/job-0001/source.mp4",
    )
    store.rebuild([revised])
    assert store.get_item("job-0001") == revised
    assert store.get_user_state("job-0001") is None


def test_user_state_revision_must_match_catalog_item(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    store.rebuild([_item()])
    with pytest.raises(CatalogError, match="revision"):
        store.set_user_state(UserState("job-0001", "r2", (), "reviewed", 0.0))
    assert store.get_user_state("job-0001") is None


def test_search_uses_literal_casefold_and_availability_filter(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    store.rebuild(
        [
            _item("job-a", title="Café Demo [100%]"),
            CatalogItem(
                item_id="job-b",
                title="Café Demo 2",
                source_fingerprint="b" * 64,
                revision="r2",
                availability="missing",
                segment_count=0,
                source_ref="sources/job-b/source.mp4",
            ),
        ]
    )
    assert [item.item_id for item in store.search("CAFÉ DEMO")] == ["job-a", "job-b"]
    assert [item.item_id for item in store.search("100%")] == ["job-a"]
    assert [item.item_id for item in store.search("", availability="missing")] == ["job-b"]


def test_metadata_backup_round_trip_verifies_digest_and_excludes_blobs(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture media bytes")
    item = _item(source_fingerprint=_sha256(source))
    first = CatalogStore(tmp_path / "first.sqlite3")
    first.rebuild([item])
    first.set_user_state(UserState("job-0001", "r1", ( {"segment_index": 1, "position_seconds": 1.25}, ), "draft", 8.0))

    payload = first.export_metadata()
    assert payload["format"] == BACKUP_FORMAT
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "fixture media bytes" not in serialized
    assert str(source) not in serialized

    second = CatalogStore(tmp_path / "second.sqlite3")
    assert second.restore_metadata(json.loads(json.dumps(payload))) == 1
    assert second.export_metadata() == payload

    tampered = json.loads(json.dumps(payload))
    tampered["catalog"][0]["title"] = "tampered"
    with pytest.raises(CatalogIntegrityError, match="digest"):
        CatalogStore(tmp_path / "tampered.sqlite3").restore_metadata(tampered)
    assert not (tmp_path / "tampered.sqlite3").exists() or CatalogStore(tmp_path / "tampered.sqlite3").search() == []

    extra_field = json.loads(json.dumps(payload))
    extra_field["api_key"] = "must-not-be-persisted"
    with pytest.raises(CatalogIntegrityError, match="unsupported fields"):
        CatalogStore(tmp_path / "extra.sqlite3").restore_metadata(extra_field)


def test_restore_rejects_digest_valid_revision_mismatch_before_mutation(tmp_path: Path) -> None:
    source = CatalogStore(tmp_path / "source.sqlite3")
    source.rebuild([_item()])
    source.set_user_state(UserState("job-0001", "r1", (), "reviewed", 1.0))
    payload = source.export_metadata()
    payload["user_state"][0]["revision"] = "r2"
    core = {key: payload[key] for key in ("format", "schema_version", "catalog", "user_state")}
    payload["payload_digest"] = _digest_payload(core)

    target = CatalogStore(tmp_path / "target.sqlite3")
    target.rebuild([_item("existing", title="Keep this")])
    with pytest.raises(CatalogIntegrityError, match="revision"):
        target.restore_metadata(payload)
    assert target.get_item("existing") is not None
    assert target.get_item("job-0001") is None


def test_export_fails_closed_if_sqlite_user_state_is_tampered(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    store = CatalogStore(db_path)
    store.rebuild([_item()])
    store.set_user_state(UserState("job-0001", "r1", (), "reviewed", 1.0))
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE user_state SET revision = 'r2' WHERE item_id = 'job-0001'")
        connection.commit()
    with pytest.raises(CatalogIntegrityError, match="revision"):
        CatalogStore(db_path).export_metadata()
    with pytest.raises(CatalogIntegrityError, match="revision"):
        CatalogStore(db_path).get_user_state("job-0001")


def test_probe_and_relink_require_exact_fingerprint_and_explicit_mapping(tmp_path: Path) -> None:
    root = tmp_path / "media-root"
    source_dir = root / "sources" / "job-0001"
    source_dir.mkdir(parents=True)
    original = source_dir / "source.mp4"
    original.write_bytes(b"original")
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    item = _item(source_fingerprint=_sha256(original))
    store.rebuild([item])

    assert store.probe_availability("job-0001", root) == "available"
    original.write_bytes(b"changed")
    assert store.probe_availability("job-0001", root) == "stale"

    replacement = tmp_path / "renamed-input.bin"
    replacement.write_bytes(b"original")
    with pytest.raises(CatalogIntegrityError, match="not readable"):
        store.relink("job-0001", tmp_path / "missing.bin", source_ref="sources/job-0001/source.mp4")
    wrong = tmp_path / "wrong-input.bin"
    wrong.write_bytes(b"different")
    with pytest.raises(CatalogIntegrityError, match="fingerprint"):
        store.relink("job-0001", wrong, source_ref="sources/job-0001/source.mp4")
    updated = store.relink("job-0001", replacement, source_ref="relinked/job-0001/source.mp4")
    assert updated.availability == "available"
    assert updated.source_ref == "relinked/job-0001/source.mp4"
    assert store.get_item("job-0001") == updated


def test_delete_removes_projection_and_user_state_but_not_source(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"keep me")
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    store.rebuild([_item(source_fingerprint=_sha256(source))])
    store.set_user_state(UserState("job-0001", "r1", (), "done", 0.0))
    store.delete_item("job-0001")
    assert store.get_item("job-0001") is None
    assert store.get_user_state("job-0001") is None
    assert source.read_bytes() == b"keep me"


def test_catalog_metadata_rejects_absolute_paths_before_persistence(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    unsafe = _item()
    unsafe = CatalogItem(
        item_id=unsafe.item_id,
        title=unsafe.title,
        source_fingerprint=unsafe.source_fingerprint,
        revision=unsafe.revision,
        availability=unsafe.availability,
        segment_count=unsafe.segment_count,
        source_ref=unsafe.source_ref,
        metadata={"debug_path": r"C:\Users\secret\source.mp4"},
    )
    with pytest.raises(CatalogError, match="absolute filesystem paths"):
        store.upsert_item(unsafe)
    assert store.search() == []

