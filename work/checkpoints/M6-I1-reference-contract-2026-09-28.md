# M6 I1 VI to Learn reference contract - 2026-09-28

Status: PREP_ONLY. This is a local typed contract and deterministic fixture
probe. It does not open Learn/Drive, OAuth, a network connection, or a
course-progress write.

## What changed

- src/vi_dubber/learn_reference.py adds immutable ReferenceIdentity,
  LearnArtifact, LearnSafety, and LearnReference values.
- LearnReference.from_dict() enforces schema/version, trusted fixture issuer,
  key identity, explicit UTC provenance, source and artifact SHA-256 values,
  passed QA, allowlisted resource URI, source visibility, and the
  no-answer-key/no-auto-completion safety boundary.
- LearnReference.from_catalog_item() bridges a validated VI catalog item
  without creating a second media source of truth. The catalog item supplies
  source fingerprint and lineage revision; the output artifact hash and
  provenance timestamp remain explicit inputs, so a filename, mtime, or source
  hash cannot masquerade as a completed QA artifact.
- scripts/verify_m6_i1_reference.py emits the machine receipt and proves typed
  round-trip, catalog-lineage preservation, and fail-closed negative cases.

## Evidence

Commands:

    uv run python scripts/verify_m6_i1_reference.py --output work/checkpoints/M6-I1-reference-contract-receipt-2026-09-28.json
    uv run pytest -q tests/test_learn_reference.py
    uv run python -m compileall -q src/vi_dubber/learn_reference.py tests/test_learn_reference.py scripts/verify_m6_i1_reference.py

Results:

- deterministic receipt has validation.passed=true;
- 13 focused tests passed;
- negative cases reject schema drift, non-passed QA, authority-prefix bypass,
  decoded traversal, and answer-key exposure;
- receipt and reference round-trip preserve the typed safety boundary.

This remains software-only preparation. Cryptographic signatures, actual
Learn/Drive connector behavior, account/OAuth permission, media upload,
course-progress writes, human content QA, and exactly-once delivery remain
explicitly unclaimed.
