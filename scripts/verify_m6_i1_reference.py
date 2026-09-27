"""Generate a deterministic offline receipt for the VI-to-Learn I1 contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vi_dubber.catalog_store import CatalogItem
from vi_dubber.learn_reference import LearnReference, LearnReferenceError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "m6_integration_contract.json"


def load_contract() -> dict[str, Any]:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "PREP_ONLY":
        raise ValueError("I1 contract fixture must remain PREP_ONLY")
    return payload


def _parse_reference(contract: dict[str, Any]) -> LearnReference:
    return LearnReference.from_dict(
        contract["learn_reference"],
        trusted_issuers=tuple(contract["trusted_fixture_issuers"]),
        resource_allowlist=tuple(contract["resource_allowlist"]),
    )


def _catalog_bridge(contract: dict[str, Any]) -> LearnReference:
    item = CatalogItem(
        item_id="job-fixture-0001",
        title="fixture",
        source_fingerprint="a" * 64,
        revision="lineage-r3",
        availability="available",
        segment_count=2,
        source_ref="fixture.mp4",
    )
    return LearnReference.from_catalog_item(
        item,
        reference_id="learn-ref-catalog-fixture",
        artifact_id="job-fixture-0001/subtitle-r3",
        kind="subtitle",
        language="vi",
        source_timestamp_utc="2026-09-27T10:00:00Z",
        artifact_sha256="b" * 64,
        resource_uri="learn://authorized/job-fixture-0001/subtitle-r3.srt",
        issuer=contract["trusted_fixture_issuers"][0],
        key_id="fixture-key-1",
        trusted_issuers=tuple(contract["trusted_fixture_issuers"]),
        resource_allowlist=tuple(contract["resource_allowlist"]),
    )


def _rejected_cases(contract: dict[str, Any]) -> dict[str, bool]:
    reference = contract["learn_reference"]
    cases: dict[str, dict[str, Any]] = {
        "schema_version": {**reference, "schema_version": "workspace-learn-reference-v0"},
        "qa_not_passed": {
            **reference,
            "artifact": {**reference["artifact"], "qa_status": "pending"},
        },
        "authority_prefix_bypass": {
            **reference,
            "artifact": {
                **reference["artifact"],
                "resource_uri": "learn://authorized-evil/file.srt",
            },
        },
        "decoded_path_traversal": {
            **reference,
            "artifact": {
                **reference["artifact"],
                "resource_uri": "learn://authorized/job/%2e%2e/secret.srt",
            },
        },
        "answer_key_exposure": {
            **reference,
            "safety": {**reference["safety"], "answer_keys_exposed": True},
        },
    }
    results: dict[str, bool] = {}
    for name, payload in cases.items():
        try:
            LearnReference.from_dict(
                payload,
                trusted_issuers=tuple(contract["trusted_fixture_issuers"]),
                resource_allowlist=tuple(contract["resource_allowlist"]),
            )
        except LearnReferenceError:
            results[name] = True
        else:
            results[name] = False
    return results


def run_probe(contract: dict[str, Any]) -> dict[str, Any]:
    typed = _parse_reference(contract)
    round_trip = LearnReference.from_dict(
        typed.to_dict(),
        trusted_issuers=tuple(contract["trusted_fixture_issuers"]),
        resource_allowlist=tuple(contract["resource_allowlist"]),
    )
    bridged = _catalog_bridge(contract)
    rejected = _rejected_cases(contract)
    return {
        "status": "PREP_ONLY",
        "contract": str(CONTRACT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "schema_version": typed.schema_version,
        "typed_reference": typed.to_dict(),
        "catalog_bridge": bridged.to_dict(),
        "validation": {
            "typed_parse": True,
            "round_trip": round_trip.to_dict() == typed.to_dict(),
            "catalog_lineage": (
                bridged.artifact.artifact_id.startswith("job-fixture-0001/")
                and bridged.artifact.source_fingerprint == "a" * 64
                and bridged.artifact.revision == "lineage-r3"
            ),
            "rejected_cases": rejected,
            "passed": (
                round_trip.to_dict() == typed.to_dict()
                and bridged.artifact.source_fingerprint == "a" * 64
                and all(rejected.values())
            ),
        },
        "claims_excluded": [
            "cryptographic signature verification beyond the trusted fixture marker",
            "actual Learn/Drive connector behavior, OAuth, account or network access",
            "media upload, deletion, remote revision, or exactly-once delivery",
            "course progress writes, answer-key exposure, or automatic completion",
            "human QA of naturalness or content correctness",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = run_probe(load_contract())
    serialized = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if receipt["validation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
