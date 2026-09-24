# P01 hash benchmark checkpoint

Task: `P01-HASH-BENCH`

Decision: `PASS` for the requested read-only benchmark/validation slice.

## What was validated

- Current helper: `vi_dubber.artifacts.fingerprint_file`, SHA-256 streamed in 1 MiB chunks.
- Real short source: `work/youtube/PWKWNb550Cw.mp4`, 46,856,668 bytes (44.686 MiB).
- Representative larger local media from the fixture manifest: `work/outputs/Trading Strategies That Work (full training)_vi.mp4`, 448,159,610 bytes (427.398 MiB).
- Five hash trials per file; all 10 digests matched the fixture registry.
- Manifest portability smoke: build/write/load under a temporary job, copy the job directory, then load from the copied location. Pre-copy load, post-copy load, relative artifact path, and fingerprint preservation all passed. Temporary files were created only under `work/` and removed after the smoke.

The long fixture's original source video remains explicitly unresolved in `work/benchmarks/fixtures.json`; this benchmark therefore did not guess a source mapping and used its registered final video only as the larger-media cost fixture.

## Results

| Input | First pass | Median of 5 | Median throughput | Range |
|---|---:|---:|---:|---:|
| short real source, 44.686 MiB | 0.1366 s | 0.1404 s | 318.27 MiB/s | 292.36-327.63 MiB/s |
| larger media, 427.398 MiB | 1.4212 s | 1.3758 s | 310.66 MiB/s | 277.18-314.93 MiB/s |

Observed cost is approximately linear over these two fixtures. On this machine, re-hashing a ~427 MiB artifact costs about 1.4 seconds. This supports full-content SHA-256 as a practical correctness primitive, but repeated verification of many large immutable artifacts should still be scheduled deliberately rather than redundantly inside one resume path.

Cache note: OS cache was not flushed or controlled. Trial 1 is labeled separately and later trials may benefit from the filesystem cache; these numbers are local cost measurements, not a cold-storage throughput guarantee.

## Source snapshots

- `src/vi_dubber/artifacts.py`: `3CF5E6486BC6D5CB5EE221FC026EA3BD8CB691B1FFDE8635684661CF83098978`
- `PLAN.md`: `B95F266DCC0AFFA9B79BA670B3BEC271C8D383BB2D848C47BFA86B1784215178`
- `work/benchmarks/fixtures.json`: `FBF69145DD52C5E3C0D8BF59E8D9C35B8DAA052AC94974694287E3070CC88972`
- benchmark JSON: `DEC4D1B413C2F7835A031AC86062728C1231D77485A69530133E25CF4AAEE4C6`

## Command receipt

- Benchmark runner used the project `.venv` and called `fingerprint_file()` directly for 5 trials per fixture; output: `work/benchmarks/p01-hash-benchmark.json`.
- Focused tests: `.\.venv\Scripts\python.exe -m pytest tests\test_artifacts.py -q` -> `10 passed in 0.18s`.
- Temporary benchmark runner and move/copy smoke directory were removed after execution.

## Acceptance

- Machine-readable benchmark artifact: PASS.
- Short real source measured: PASS.
- Representative larger fixture-manifest media measured: PASS.
- Fixture SHA validation: PASS (10/10 trials).
- Current manifest move/copy behavior validated: PASS.
- Production code modified: NO.

Artifact: `work/benchmarks/p01-hash-benchmark.json`

Checkpoint: `work/checkpoints/P01-hash-benchmark.md`
