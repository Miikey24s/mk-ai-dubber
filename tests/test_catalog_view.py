from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vi_dubber.catalog_store import CatalogIntegrityError, CatalogItem, CatalogStore, UserState
from vi_dubber.catalog_view import (
    CATALOG_VIEW_FORMAT,
    CATALOG_VIEW_SCHEMA_VERSION,
    build_catalog_view,
    catalog_view_from_backup,
)


def _item(item_id: str, *, availability: str = "available", revision: str = "r1") -> CatalogItem:
    return CatalogItem(
        item_id=item_id,
        title=f"Fixture {item_id}",
        source_fingerprint=(item_id.replace("job-", "")[:1] or "a") * 64,
        revision=revision,
        availability=availability,
        segment_count=4,
        source_ref=f"sources/{item_id}/source.mp4",
        metadata={
            "source_name": f"{item_id}.mp4",
            "job_status": "completed",
            "job_stage": "complete",
            "progress": 1.0,
            "source_size_bytes": 12,
            "debug_path": "internal/must-not-leak/source.mp4",
            "provider_credentials": "must-not-leak",
        },
    )


def test_catalog_view_is_versioned_joined_and_allowlisted() -> None:
    item = _item("job-a")
    state = UserState(
        item_id="job-a",
        revision="r1",
        bookmarks=({"segment_index": 2, "position_seconds": 3.5},),
        review_state="in_review",
        watch_position_seconds=4.25,
    )

    view = build_catalog_view(
        [item],
        [state],
        query=" Fixture ",
        availability="available",
        limit=25,
        offset=5,
    )

    assert view["format"] == CATALOG_VIEW_FORMAT
    assert view["schema_version"] == CATALOG_VIEW_SCHEMA_VERSION
    assert view["query"] == "Fixture"
    assert view["pagination"] == {"limit": 25, "offset": 5, "returned": 1}
    assert view["counts"]["availability"]["available"] == 1
    row = view["items"][0]
    assert row["source"] == {"fingerprint": "a" * 64, "ref": "sources/job-a/source.mp4"}
    assert row["lineage"] == {"revision": "r1", "segment_count": 4}
    assert row["review"]["review_state"] == "in_review"
    assert row["review"]["bookmarks"] == [{"segment_index": 2, "position_seconds": 3.5}]
    assert row["job"] == {
        "source_name": "job-a.mp4",
        "job_status": "completed",
        "job_stage": "complete",
        "progress": 1.0,
        "source_size_bytes": 12,
    }
    serialized = json.dumps(view, ensure_ascii=False, sort_keys=True)
    assert "must-not-leak" not in serialized
    assert "provider_credentials" not in serialized
    assert "metadata_only" in view and view["metadata_only"] is True


def test_catalog_store_read_view_applies_literal_search_filter_and_page(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    store.rebuild(
        [
            _item("job-b", availability="missing"),
            _item("job-a", availability="available"),
            _item("job-c", availability="available"),
        ]
    )
    store.set_user_state(UserState("job-a", "r1", (), "reviewed", 8.0))

    view = store.read_view("fixture", availability="available", limit=1, offset=1)
    assert [item["item_id"] for item in view["items"]] == ["job-c"]
    assert view["items"][0]["review"]["review_state"] == ""
    assert view["counts"]["returned"] == 1
    assert view["pagination"]["offset"] == 1


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (UserState("job-a", "r2"), "revision"),
        (UserState("job-missing", "r1"), "unknown item"),
    ],
)
def test_catalog_view_rejects_cross_lineage_or_unknown_state(
    state: UserState,
    message: str,
) -> None:
    with pytest.raises(CatalogIntegrityError, match=message):
        build_catalog_view([_item("job-a")], [state])


def test_catalog_view_from_backup_round_trips_without_mutating_or_exposing_paths(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    store.rebuild([_item("job-a")])
    store.set_user_state(UserState("job-a", "r1", (), "reviewed", 1.0))
    backup = store.export_metadata()

    view = catalog_view_from_backup(json.loads(json.dumps(backup)))
    assert view["items"][0]["item_id"] == "job-a"
    assert view["items"][0]["review"]["review_state"] == "reviewed"
    assert str(tmp_path) not in json.dumps(view)

    malformed = copy.deepcopy(backup)
    malformed["user_state"][0]["revision"] = "r2"
    with pytest.raises(CatalogIntegrityError, match="revision"):
        catalog_view_from_backup(malformed)
