# P01 artifact/cache foundation handoff

Task: `P01-ARTIFACTS-A`

Decision: `BUILD`

Reason: stage manifests, cache identity, resume safety, and artifact ownership are
project-owned control-plane behavior. Reusing a general cache/orchestration
framework here would add dependency and migration cost without replacing the
project-specific fingerprint inputs required by `PLAN.md`. Commodity engines
remain reusable behind this control plane; this slice deliberately owns only
the cache/artifact contract.

## Implemented

- `src/vi_dubber/artifacts.py`
  - canonical JSON hashing with SHA-256;
  - streaming file content fingerprints independent of filename/location;
  - caller-selected stage identity covering inputs/upstream/config/model/prompt/versions;
  - portable relative artifact paths constrained to the job directory;
  - artifact records containing relative path, SHA-256, and byte size;
  - stage manifest builder with explicit status and identity fingerprint;
  - atomic JSON persistence using flushed temporary files plus `os.replace`;
  - cache-safe manifest loading that rejects non-complete, corrupt, tampered,
    missing, changed, path-escaping, or fingerprint-mismatched artifacts.
- `tests/test_artifacts.py`
  - mapping-order-independent canonical hash;
  - content-based file identity and same-name/different-content invalidation;
  - source/text/reference/model/config/prompt/version/upstream invalidation;
  - unrelated runtime config excluded by the caller does not perturb the key;
  - relative artifact paths and outside-job rejection;
  - copied/moved job directory remains valid;
  - changed/missing/corrupt/incomplete artifacts are rejected;
  - tampered manifest identity and path traversal are rejected;
  - failed serialization preserves the previously committed manifest;
  - expected stage/fingerprint guards cache reuse.

## Validation

Focused:

`.\.venv\Scripts\python.exe -m pytest tests\test_artifacts.py -q`

Result: `10 passed in 0.18s`.

Full regression after the final P01 edits:

`.\.venv\Scripts\python.exe -m pytest -q`

Result: `42 passed in 6.50s`.

## Integration contract

Coordinator integration should build each stage fingerprint only from inputs
that truly affect that stage. Relevant config must be selected before calling
`stage_fingerprint`; unrelated UI/runtime settings must not be passed just
because they exist globally.

For a completed stage, persist outputs first, build the manifest from those
artifacts, then atomically commit the manifest. Resume/cache reuse should call
`load_stage_manifest(..., expected_stage=..., expected_fingerprint=...)`; a
`None` result means rebuild from the nearest valid upstream stage. File
existence by itself must not count as a cache hit.

This worker intentionally did not edit `pipeline.py`, `types.py`, `config.yaml`,
`translate.py`, `tts.py`, `semantic_qa.py`, or `PLAN.md`.

## Remaining risks

- No pipeline integration has been performed in this worker slice, so legacy
  `translations_cache.json`, TTS file-exists reuse, and old `stems.json` behavior
  remain unchanged until the coordinator wires this module in.
- File verification re-hashes artifacts. Large-video/source hashing cost should
  be benchmarked before choosing how often resume validation re-hashes immutable
  large files.
- Legacy artifact migration/backward compatibility is not implemented here;
  integration should either add a narrow loader or invalidate legacy stages
  explicitly.
