# M6 integration contract prep — 2026-09-27

Status: **PREP_ONLY**. This checkpoint prepares a software-only boundary; it does not open a connector, OAuth flow, account, network call, media upload, or production integration.

Hardening update (27/09/2026): the I1 reference validator now requires an explicit UTC
provenance timestamp, a non-empty trusted-fixture `key_id`, and an exact URI scheme /
authority match. It rejects look-alike authorities, decoded path traversal,
backslashes, query/fragment mutation, and control characters before a reference can
cross the local contract boundary. The focused contract suite passes **18 tests**;
the generated receipt records four negative hardening cases and remains
`PREP_ONLY`.

## Scope

- **VI → Learn reference:** a VI-owned artifact reference keeps artifact ID, revision, source fingerprint, SHA-256, language, source timestamp, QA status, visible resource URI, and a trusted-fixture identity marker. The fixture explicitly denies answer-key exposure and auto-completion.
- **Export request/receipt:** a user-selected Drive-style destination is represented by `drive.file`, picker destination, data class, source revision/hash, and an idempotency key. Receipts distinguish `unknown`, `succeeded`, and `revoked` and keep external identity/checksum only when known.
- **Failure semantics:** same-intent retries dedupe; reuse of an idempotency key with a different intent is rejected; an unknown outcome is looked up before retry; a successful lookup is adopted without resend; a not-found lookup is retryable; revoke/cancel and stale events cannot reopen a terminal state.
- **Offline adapter skeleton:** `OfflineExportAdapter` provides the future connector seam for submit, unknown, reconcile, revoke, and cancel. It is an in-memory contract harness only; it performs no external I/O and is deliberately not a production store.
- **I1 URI/provenance hardening:** allowlist entries are parsed as URI components rather than matched by a raw string prefix; percent-decoded traversal and query/fragment mutation are rejected. Artifact timestamps must be parseable UTC values ending in `Z`; trusted fixture identity must carry a non-empty `key_id`.

## Evidence

- Fixture: `tests/fixtures/m6_integration_contract.json`
- Offline probe: `scripts/benchmark_m6_integration_contract.py`
- Tests: `tests/test_m6_integration_contract.py`
- Hardening receipt: `hardening.status=passed` in `M6-integration-contract-prep-2026-09-27.json` (authority-prefix bypass, path traversal, and non-UTC timestamp rejection).
- The probe returns `PREP_ONLY` and records excluded claims for actual connector behavior, OAuth/account permissions, cryptographic verification, upload/deletion, exactly-once delivery, and production API/schema implementation.

## Gate mapping

This is preparatory evidence for M6 I1/I2 and E5. It does not satisfy the master-plan gates requiring user-scoped permission, actual destination, or integration acceptance. The next implementation must keep VI artifact authority and add a durable intent/receipt map in the owning project; it must not create a second media source of truth.
