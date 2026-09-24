# P04-P06 language-quality worker checkpoint

Date: 2026-09-22
Scope: `translate.py`, `semantic_qa.py`, `pronunciation.py`, `tests/test_language_quality.py`, `tests/test_semantic_cache.py` only.

## Result

- P04 deterministic duration pre-fit support is hardened. The estimator now measures the TTS-normalized form of numbers/units and classifies overflow from the conservative `expected + uncertainty` upper bound instead of the point estimate alone. Translation policy version is 3.
- P05 semantic routing now uses atomic issue heads even when `review_issue_above` is not explicitly configured. The provisional default is deliberately high (`0.90`) until real calibration exists. Rewrite/prefit stages also get the missing `rewrite_changed_material_meaning` Noul head; semantic question policy version is 3.
- P06 pronunciation keeps display text separate from TTS text and now reads signed critical numbers deterministically, including signed percentages and units.
- No live/billable TypeSafe request was made in this task. Tests use fake/offline responses only.

## Why this slice

The latest short E2E evidence handed to this worker had 43.4% overflow, tempo p95 1.25, and 2/53 translated segments flagged by semantic QA. The current pipeline already has a read-only pre-TTS prefit stage wired to `duration_fit_hint()` plus a semantic candidate gate, so the useful owned-file fix was to improve the helper/gate semantics rather than duplicate orchestration in `pipeline.py`.

TypeSafe design follows the current System One guidance already checked in this task: code owns deterministic control flow, independent semantic judgments stay narrow/typed, and probabilities drive explicit routing. Decision: BUILD/OWN for project-specific deterministic duration/pronunciation policy; ADAPT the TypeSafe atomic-judgment pattern without copying upstream code or adding dependencies.

## Validation

Commands:

```text
uv run pytest -q tests/test_language_quality.py tests/test_semantic_cache.py
18 passed in 0.56s

uv run pytest -q
161 passed in 14.07s
```

New regression coverage verifies:

- TTS-normalized numeric text has the same duration estimate as its spoken expansion.
- uncertainty alone can route a borderline candidate to pre-fit before TTS.
- a high atomic semantic issue routes to review without extra config.
- rewrite stages add `rewrite_changed_material_meaning`, translated stages do not.
- signed percent/unit values are pronounced with `âm` / `dương` instead of leaving a raw sign before a positive spoken number.

## SHA256 receipt

| File | Before | After |
| --- | --- | --- |
| `src/vi_dubber/translate.py` | `C218354359240247445AE8DAFA49ED49E3CBC5C1D69E2296DFA3E782AD255954` | `6E08AAD5668B10C45C193B476394A0C9B4AB5E195CD86D3D962DA0DDF545686D` |
| `src/vi_dubber/semantic_qa.py` | `64FB6DF0E8B3BDB2A5F573DD468B14FD69344CB73F296C82E6DB99FBB8F5A316` | `1A0E8CD6DCA8CF1B402AE3718AAAF2A91FF79B57C24BFDA4B70D2EEF6E237963` |
| `src/vi_dubber/pronunciation.py` | `21D656309BA03FBBEC3D8AC96ADEC5F999506505ACEF92ED88D70DA5A19DD8D8` | `2D53200CDC42F696D5EB8449C783270AFB4E178EBECB2118E482E06EBA2E902A` |
| `tests/test_language_quality.py` | `EE47F705FAB5F32234A5F6B7BFCB3A686B3586F6F79257D92CE2BEE288D45151` | `064AB98F5AC420AA613B611E518E42393C64AEA781A8E5AA86A76FA8967AB81C` |
| `tests/test_semantic_cache.py` | `4932C252A4EC91DEE5DA1A4FA116A6244DDF8A1D867D0ADB758B6EC51DDB2653` | unchanged |

## Remaining real-calibration gaps

- P04 is not quality-accepted until the short/medium/long fixtures rerun through the new prefit path and show lower real generated-audio overflow/rewrite rate. `13 chars/s`, uncertainty `0.20`, and overflow ratio `1.10` remain provisional knobs, not calibrated truth.
- The latest 43.4% overflow receipt predates this helper hardening, so it is a baseline/failure signal, not evidence that the new policy improves E2E output.
- P05 `0.90` atomic-issue review threshold is a conservative provisional safety gate. It must be calibrated on the labeled Vietnamese dubbing golden set (good, subtle loss, negation/modality reversal, number/name issue, awkward-but-faithful, valid compression) before production threshold lock/model pinning.
- No batch 8/16/32 TypeSafe accuracy/latency/cost calibration was run because this task explicitly forbids live/billable TypeSafe calls.
- P06 still intentionally avoids guessing ambiguous date/time/locale-number formats and has no project-wide acronym/name pronunciation lexicon yet. Add those only from explicit mappings or a labeled golden pronunciation set.
- Human listening A/B and naturalness acceptance remain coordinator-level gates; deterministic unit tests cannot substitute for them.
