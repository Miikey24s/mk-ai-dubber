from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vi_dubber.catalog_store import CatalogItem
from vi_dubber.learn_reference import LearnReference, LearnReferenceError


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "m6_integration_contract.json"
)


def _contract() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _reference() -> LearnReference:
    contract = _contract()
    return LearnReference.from_dict(
        contract["learn_reference"],
        trusted_issuers=tuple(contract["trusted_fixture_issuers"]),
        resource_allowlist=tuple(contract["resource_allowlist"]),
    )


def test_reference_is_typed_and_round_trips_without_safety_downgrade() -> None:
    reference = _reference()
    payload = reference.to_dict()

    assert reference.artifact.revision == "3"
    assert payload["artifact"]["artifact_sha256"] == "b" * 64
    assert payload["safety"] == {
        "answer_keys_exposed": False,
        "auto_completion_enabled": False,
    }
    assert LearnReference.from_dict(
        payload,
        trusted_issuers=("vi-dubber-local",),
        resource_allowlist=("learn://authorized/",),
    ).to_dict() == payload


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "workspace-learn-reference-v0"),
        (("identity", "key_id"), ""),
        (("artifact", "qa_status"), "pending"),
        (("artifact", "source_timestamp_utc"), "2026-09-27T17:00:00+07:00"),
        (("artifact", "resource_uri"), "learn://authorized-evil/file.srt"),
        (("artifact", "resource_uri"), "learn://authorized/job/../secret.srt"),
        (("artifact", "resource_uri"), "learn://authorized/job/file.srt?x=1"),
        (("safety", "answer_keys_exposed"), True),
        (("safety", "auto_completion_enabled"), True),
    ],
)
def test_reference_rejects_invalid_identity_version_qa_or_uri(
    path: tuple[str, ...], value: object
) -> None:
    contract = _contract()
    payload = copy.deepcopy(contract["learn_reference"])
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(LearnReferenceError):
        LearnReference.from_dict(
            payload,
            trusted_issuers=tuple(contract["trusted_fixture_issuers"]),
            resource_allowlist=tuple(contract["resource_allowlist"]),
        )


def test_catalog_bridge_keeps_lineage_and_requires_explicit_output_provenance() -> None:
    item = CatalogItem(
        item_id="job-fixture-0001",
        title="fixture",
        source_fingerprint="a" * 64,
        revision="lineage-r3",
        availability="available",
        segment_count=2,
        source_ref="fixture.mp4",
    )
    reference = LearnReference.from_catalog_item(
        item,
        reference_id="learn-ref-catalog-0001",
        artifact_id="job-fixture-0001/subtitle-r3",
        kind="subtitle",
        language="vi",
        source_timestamp_utc="2026-09-27T10:00:00Z",
        artifact_sha256="b" * 64,
        resource_uri="learn://authorized/job-fixture-0001/subtitle-r3.srt",
        issuer="vi-dubber-local",
        key_id="fixture-key-1",
        trusted_issuers=("vi-dubber-local",),
        resource_allowlist=("learn://authorized/",),
    )

    assert reference.artifact.source_fingerprint == item.source_fingerprint
    assert reference.artifact.revision == item.revision
    assert reference.artifact.artifact_sha256 != item.source_fingerprint


@pytest.mark.parametrize(
    ("availability", "artifact_id"),
    [
        ("stale", "job-fixture-0001/subtitle-r3"),
        ("available", "job-other/subtitle-r3"),
    ],
)
def test_catalog_bridge_fails_closed_on_stale_or_cross_lineage_item(
    availability: str, artifact_id: str
) -> None:
    item = CatalogItem(
        item_id="job-fixture-0001",
        title="fixture",
        source_fingerprint="a" * 64,
        revision="lineage-r3",
        availability=availability,
        segment_count=2,
        source_ref="fixture.mp4",
    )

    with pytest.raises(LearnReferenceError):
        LearnReference.from_catalog_item(
            item,
            reference_id="learn-ref-invalid",
            artifact_id=artifact_id,
            kind="subtitle",
            language="vi",
            source_timestamp_utc="2026-09-27T10:00:00Z",
            artifact_sha256="b" * 64,
            resource_uri="learn://authorized/job-fixture-0001/subtitle-r3.srt",
            issuer="vi-dubber-local",
            key_id="fixture-key-1",
            trusted_issuers=("vi-dubber-local",),
            resource_allowlist=("learn://authorized/",),
        )
