# P05 calibration checkpoint - 2026-09-23

## Result

Closed the machine-verifiable/offline slice of P05 calibration without making any new TypeSafe API call.

- Added a labeled golden set covering every P05 acceptance class: faithful, subtle loss, negation reversal, modality loss, number/name issue, telegraphic Vietnamese, and valid natural compression.
- Labels explicitly include `should_pass`, `should_retry`, `severe_semantic_error`, and `awkward_but_semantically_correct`.
- Added synthetic fixture probabilities separate from labels so the calibration math is deterministic and free.
- Added an offline calibration harness that can consume either those fixture probabilities or a previously recorded raw TypeSafe artifact. It never calls the TypeSafe API.
- Added confusion matrix, precision/recall/F1, asymmetric cost sweep, severe-error recall, awkward-language false-retry check, and model-pinning eligibility.
- Production thresholds and `config.yaml` model default remain unchanged.

## Cost policy used by the harness

The default offline sweep assigns:

- false pass cost = `5.0`: a case labeled `should_retry=true` is allowed through;
- false retry cost = `1.0`: a non-retry case is unnecessarily sent to retry.

This 5:1 ratio is a provisional regression policy, not a measured production business cost. Both values are CLI parameters and must be revisited with human-labeled evidence before locking production thresholds.

The retry-risk score is the maximum of material semantic signals only: `1 - P(faithful)`, `P(wrong)`, `1 - P(critical facts preserved)`, and the material semantic issue heads. `spoken_vi_awkward` is deliberately excluded from retry risk so an awkward-but-semantic-correct case can be reviewed without being treated as a semantic retry.

## Golden fixture result

Command:

```powershell
uv run python tools/typesafe_calibration.py
```

Receipt on the synthetic regression set:

- 7/7 required PLAN classes present;
- offline candidate threshold: `0.72`;
- confusion: TP=4, FP=0, TN=3, FN=0;
- precision=1.0, recall=1.0, F1=1.0;
- severe semantic errors caught: 3/3;
- awkward but semantically correct cases sent to retry: 0/1;
- `production_threshold_lock_allowed=false`.

The `0.72` value proves the sweep/harness behavior only. It is not production calibration evidence because both labels and probabilities are synthetic regression fixtures.

## TypeSafe live-doc snapshot and model pinning contract

Live docs read on 2026-09-23:

- `https://docs.typesafe.ai/models.md`: current Jev release is `jev-1.13.0`; `jev-latest` and `jev-preview` currently resolve to it. The docs explicitly say aliases move when a release ships and recommend pinning the versioned ID after thresholds are tuned against a specific version.
- `https://docs.typesafe.ai/api.md`: responses report the model that actually answered, so recorded calibration artifacts can retain a versioned response model.
- `https://docs.typesafe.ai/confidence.md`: confidence/probabilities are routing signals, not a substitute for target-domain validation.
- `https://docs.typesafe.ai/cookbooks/parallel_questions.md` and `https://docs.typesafe.ai/cookbooks/sde_cascade.md`: atomic parallel verifier heads and verify-then-escalate remain aligned with P05.

Pinning is fail-closed. The harness only marks `production_pinning_ready=true` when all of these are true:

1. labels declare `label_provenance=human`;
2. probabilities come from `recorded_typesafe_artifact`;
3. the artifact has exactly one response model;
4. that response model is a versioned Jev ID such as `jev-1.13.0`.

The current synthetic fixture records the docs snapshot model for contract testing, but does not qualify as evidence for a production pin. `config.yaml` therefore stays on `jev-latest` in this task.

## Validation

```text
uv run pytest -q tests/test_semantic_cache.py tests/test_language_quality.py
27 passed in 1.63s

uv run pytest -q tests/test_core.py tests/test_semantic_cache.py tests/test_language_quality.py
52 passed in 5.88s

uv run python -m py_compile src/vi_dubber/semantic_qa.py tools/typesafe_calibration.py
pass
```

## Remaining P05 gap

Still blocked by evidence that this task intentionally did not create:

- human labels on representative real dubbing output;
- recorded TypeSafe probabilities from that labeled set;
- batch 8/16/32 comparison for accuracy, latency, and token/cost behavior;
- threshold selection repeated on that real evidence;
- only then pin the exact response model version benchmarked and consider changing production thresholds/model defaults.

No API key was printed or persisted, and no paid live calibration call was made.
