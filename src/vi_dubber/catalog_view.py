"""Stable, metadata-only read model for the local M5 catalog boundary.

The SQLite catalog remains the persistence authority.  This module only joins
catalog rows with review state into a small versioned shape that a future
Jobs/Review API can consume.  It deliberately contains no media URLs, file
bytes, provider state, or mutation operation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .catalog_store import (
    AVAILABILITY_STATES,
    CatalogError,
    CatalogIntegrityError,
    CatalogItem,
    UserState,
    _validate_item,
    _validate_user_state,
)


CATALOG_VIEW_FORMAT = "vi-dubber-catalog-view-v1"
CATALOG_VIEW_SCHEMA_VERSION = 1


def _safe_job_metadata(item: CatalogItem) -> dict[str, Any]:
    """Expose only the bounded, UI-relevant projection metadata.

    Arbitrary job metadata may grow as the pipeline evolves.  Keeping this
    allow-list small prevents a future provider/debug field from accidentally
    becoming part of the UI contract or leaking a path/credential.
    """

    metadata = item.metadata
    result: dict[str, Any] = {}
    source_name = metadata.get("source_name")
    if isinstance(source_name, str) and source_name.strip():
        result["source_name"] = source_name.strip()
    for key in ("job_status", "job_stage"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    progress = metadata.get("progress")
    if isinstance(progress, (int, float)) and not isinstance(progress, bool) and math.isfinite(float(progress)):
        result["progress"] = min(1.0, max(0.0, float(progress)))
    size_bytes = metadata.get("source_size_bytes")
    if isinstance(size_bytes, int) and not isinstance(size_bytes, bool) and size_bytes >= 0:
        result["source_size_bytes"] = size_bytes
    return result


def _state_for_item(item: CatalogItem, state: UserState | None) -> dict[str, Any]:
    if state is None:
        return {
            "revision": item.revision,
            "review_state": "",
            "watch_position_seconds": 0.0,
            "bookmarks": [],
        }
    if state.item_id != item.item_id:
        raise CatalogIntegrityError("catalog view user state item_id does not match catalog item")
    if state.revision != item.revision:
        raise CatalogIntegrityError("catalog view user state revision does not match catalog item")
    validated = _validate_user_state(state)
    return {
        "revision": validated.revision,
        "review_state": validated.review_state,
        "watch_position_seconds": validated.watch_position_seconds,
        "bookmarks": [dict(bookmark) for bookmark in validated.bookmarks],
    }


def build_catalog_view(
    items: Iterable[CatalogItem],
    states: Iterable[UserState] = (),
    *,
    query: str = "",
    availability: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Build a deterministic, joined read model for Jobs/Review consumers.

    The function validates identity and revision boundaries before returning a
    payload.  It does not write to SQLite or inspect source/media files.
    ``limit``/``offset`` describe the already selected page; callers that need
    SQL-backed pagination should select rows first and pass the same values.
    """

    if not isinstance(query, str):
        raise CatalogError("catalog view query must be a string")
    if availability is not None and availability not in AVAILABILITY_STATES:
        raise CatalogError(f"unsupported availability state: {availability}")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise CatalogError("catalog view limit must be a positive integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise CatalogError("catalog view offset must be a non-negative integer")

    materialized = [_validate_item(item) for item in items]
    materialized.sort(key=lambda item: item.item_id)
    item_ids: set[str] = set()
    for item in materialized:
        if item.item_id in item_ids:
            raise CatalogIntegrityError(f"duplicate catalog view item: {item.item_id}")
        item_ids.add(item.item_id)

    state_by_id: dict[str, UserState] = {}
    for raw_state in states:
        state = _validate_user_state(raw_state)
        if state.item_id in state_by_id:
            raise CatalogIntegrityError(f"duplicate catalog view user state: {state.item_id}")
        if state.item_id not in item_ids:
            raise CatalogIntegrityError(f"catalog view user state references unknown item: {state.item_id}")
        state_by_id[state.item_id] = state

    view_items: list[dict[str, Any]] = []
    availability_counts = {name: 0 for name in sorted(AVAILABILITY_STATES)}
    for item in materialized:
        availability_counts[item.availability] += 1
        view_items.append(
            {
                "item_id": item.item_id,
                "title": item.title,
                "availability": item.availability,
                "source": {
                    "fingerprint": item.source_fingerprint,
                    "ref": item.source_ref,
                },
                "lineage": {
                    "revision": item.revision,
                    "segment_count": item.segment_count,
                },
                "job": _safe_job_metadata(item),
                "review": _state_for_item(item, state_by_id.get(item.item_id)),
            }
        )

    return {
        "format": CATALOG_VIEW_FORMAT,
        "schema_version": CATALOG_VIEW_SCHEMA_VERSION,
        "query": query.strip(),
        "availability_filter": availability,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "returned": len(view_items),
        },
        "items": view_items,
        "counts": {
            "returned": len(view_items),
            "availability": availability_counts,
        },
        "metadata_only": True,
    }


def catalog_view_from_backup(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the same read model from a verified catalog backup payload.

    ``CatalogStore.restore_metadata`` remains the mutation boundary; this
    helper is for offline preview/inspection and therefore only accepts the
    already validated backup shape produced by ``export_metadata``.
    """

    if not isinstance(payload, Mapping):
        raise CatalogError("catalog view backup must be an object")
    catalog = payload.get("catalog")
    user_state = payload.get("user_state")
    if not isinstance(catalog, list) or not isinstance(user_state, list):
        raise CatalogError("catalog view backup lists are invalid")
    if any(not isinstance(raw, Mapping) for raw in (*catalog, *user_state)):
        raise CatalogIntegrityError("catalog view backup row is malformed")
    try:
        items = [CatalogItem(**dict(raw)) for raw in catalog]
        states = [UserState(**dict(raw)) for raw in user_state]
    except (TypeError, ValueError) as exc:
        raise CatalogIntegrityError("catalog view backup row is malformed") from exc
    return build_catalog_view(items, states)

