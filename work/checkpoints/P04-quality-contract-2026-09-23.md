# P04 quality contract checkpoint - 2026-09-23

## Result

- Added an offline, deterministic P04 quality contract and receipt generator. It passes all six machine-verifiable sections: bounded context, duration-fit classifications, glossary snapshot, golden checker conformance, retained translation artifacts, and retained A/B critical-fact parity.
- The golden set contains positive and deliberate negative cases for glossary terms, names, numbers, negation, modality, and critical action/value relationships. Negative cases must fail their labeled checks for the contract itself to pass.
- Audited 5 retained translation artifacts by SHA-256 and 20 labeled segments by exact source/translation plus token/fact checks.
- The retained clean-talking-head before/after pair preserves labeled coverage for names, numbers, negation, and modality across the segmentation change. This is **critical-fact parity only**, not evidence that the after translation is more natural or semantically superior.
- No production source was changed and no external/paid call was made.

## Behavior and data flow

`tests/fixtures/p04_quality_contract.json` is the source of labeled expectations. `tools/p04_quality_receipt.py`:

1. runs the real `build_translation_payload()` and `duration_fit_hint()` helpers against golden inputs;
2. verifies the current `glossary.yaml` snapshot;
3. proves the deterministic checker accepts positive cases and rejects omission/reversal cases;
4. resolves retained artifacts inside the project root, verifies their SHA-256, exact segment source/translation, and labeled checks;
5. emits a fail-closed JSON receipt and returns exit code 1 when any section fails.

The receipt explicitly records:

- `naturalness_assessed=false`;
- `semantic_equivalence_assessed=false`;
- `human_ab_required=true`.

## Current receipt

Command:

```text
uv run python tools/p04_quality_receipt.py
```

Summary:

```text
passed=true
context=true
duration_fit=true
glossary_snapshot=true
golden_checker_conformance=true
retained_artifacts=true
retained_ab_critical_fact_parity=true
retained_artifacts=5
retained_labeled_segments=20
category_case_counts={critical_fact:6, glossary:2, modality:8, name:9, negation:16, number:10}
fixture_sha256=421182947941722109c06d7977516d54ed5778d42ed8e9d4a22f70095846dad6
```

The 5 retained inputs are the P12 routing E2E segments, P17 clean-talking-head before/after, P17 fast-English, and P17 technical/numeric artifacts. Their expected hashes are stored in the fixture; drift or a missing artifact makes the receipt fail.

## Validation

```text
uv run pytest -q tests/test_p04_quality_contract.py
5 passed in 0.58s

uv run pytest -q tests/test_p04_quality_contract.py tests/test_language_quality.py tests/test_pronunciation.py
37 passed in 1.22s

uv run pytest -q
314 passed in 21.80s

uv run python -m py_compile tools/p04_quality_receipt.py tests/test_p04_quality_contract.py
pass
```

The tests also mutate one retained artifact hash and confirm that the aggregate receipt fails closed.

## Decisions and trade-offs

- Checks are label-driven, accent-insensitive substring/occurrence assertions. This is transparent and deterministic for known terms/facts, but intentionally does not pretend to infer full meaning.
- Retained artifacts are checked in place instead of copied into a second fixture source of truth. Hashes plus exact segment expectations make provenance drift visible.
- The before/after pair changed segmentation, so the A/B receipt compares labeled category/fact preservation over selected segment sets rather than assuming one-to-one segment alignment.
- Duration fit remains the production proxy with provisional calibration; the contract verifies classification behavior, not real TTS timing accuracy.

## Exact remaining gaps

1. Human blind listening A/B is still required for natural spoken Vietnamese, register, subtitle-like phrasing, and overall preference. This task makes no naturalness claim.
2. No retained before/after artifact contains the configured FVG/order-block/liquidity-sweep/market-structure glossary set. The contract proves current glossary content and omission detection, but **not** real glossary-consistency improvement in model output.
3. The retained A/B proves labeled critical-fact parity only. It does not prove full semantic equivalence or a quality improvement; a labeled human/semantic review set is still needed.
4. No fresh short/medium/long run was made, so P04 still lacks measured reduction in rewrite-after-TTS, overflow, WebGPT call count, and retry rate under the current pre-fit policy.
5. The duration estimator constants (`13 chars/s`, uncertainty `0.20`, overflow ratio `1.10`) remain provisional until calibrated against generated Vietnamese audio.
6. P04 should remain PARTIAL. P06 deterministic pronunciation remains DONE; P12/P17 broader false-positive, calibration, fixture-completeness, and human gates remain unchanged.

## File hashes

```text
tests/fixtures/p04_quality_contract.json  421182947941722109c06d7977516d54ed5778d42ed8e9d4a22f70095846dad6
tools/p04_quality_receipt.py              fc9323b59c7cee392e8d622cccbd8e87e1c7210f90bc9223944d21f1e97f9ef5
tests/test_p04_quality_contract.py        615c52e2efcb309bd8a15d7c04926e26ad1b63865f5d526e86303ae5c8d4b9c8
```
