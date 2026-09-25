# P07 smart voice reference isolated blind A/B - 2026-09-25

Status: **BLIND PACKAGE READY / HUMAN LISTENING STILL REQUIRED**

## Scope

This closes the missing machine-side setup for P07 without changing production
reference selection or TTS defaults. The comparison isolates exactly one
variable:

- baseline: legacy `first-length-valid` 3-8 s non-overlap reference;
- candidate: current smart acoustic + delivery reference selector.

VieNeu version, CUDA FP16 backend, batch size, Vietnamese texts, random seed and
output construction are held constant within each trial. Each listener-facing
trial uses a third, held-out source clip as the speaker reference so neither
candidate is judged against the exact clip it cloned from.

## Representative retained fixtures

Discovery found four retained single-speaker jobs where the current selector
actually differs from the legacy baseline, so all four were included rather
than cherry-picking a winner:

| source | legacy -> smart segment | selector score gain | SNR legacy -> smart | speech ratio legacy -> smart |
|---|---:|---:|---:|---:|
| `source-83s.mp4` | 0 -> 5 | +0.3234 | 10.62 -> 19.61 dB | 0.449 -> 0.641 |
| `source-90s.mp4` | 0 -> 19 | +0.3043 | 12.27 -> 26.63 dB | 0.533 -> 0.700 |
| `source-60s.mp4` | 0 -> 7 | +0.1830 | 10.56 -> 11.43 dB | 0.618 -> 0.658 |
| `CP2-short-smart-source.mp4` | 1 -> 6 | +0.0545 | 17.57 -> 22.35 dB | 0.657 -> 0.679 |

Aggregate machine-side change across the four trials:

- mean selector score gain: `+0.2163`;
- mean SNR: `12.76 -> 20.00 dB`;
- mean speech ratio: `0.565 -> 0.672`;
- all four smart selections differ from legacy and remain inside the 3-8 s,
  non-overlap eligibility contract.

These numbers validate that the selector is doing what it was designed to do;
they do not establish perceptual superiority.

## Artifacts

- `tools/p07_reference_ab.py`: reproducible discovery + isolated render harness.
- `work/benchmarks/p07-reference-discovery-20260925.json`: retained selector comparison.
- `work/benchmarks/p07-reference-ab-20260925/generation-receipt.json`: model/hardware/seed,
  selected segments, score details, text set, hashes and generated-audio metrics.
- `work/benchmarks/p07-reference-ab-20260925/study-spec.json`: source study spec.
- `work/benchmarks/p07-reference-ab-20260925/source-media/`: isolated references and
  baseline/smart candidate audio.
- `work/benchmarks/p07-reference-ab-20260925/blind-packet/packet.json`: listener-facing
  four-trial packet with only neutral A/B labels.
- `work/benchmarks/p07-reference-ab-20260925/blind-packet/organizer-receipt.json`: private
  mapping, source provenance and hashes.
- `work/benchmarks/p07-reference-ab-20260925/blind-packet/ballot-template.json`: blank ballot.
- `work/benchmarks/p07-reference-ab-20260925/blind-packet/decision-template.json`: blank human
  gate decision.
- `work/benchmarks/p07-reference-ab-20260925/blind-packet/evaluation-no-votes.json`: expected
  fail-closed receipt.

The packet contains 12 hardlinked media aliases: four held-out references and
eight randomized candidate aliases.

## Exact commands run

```powershell
uv run python tools\p07_reference_ab.py discover `
  --output work\benchmarks\p07-reference-discovery-20260925.json

uv run python tools\p07_reference_ab.py build `
  --job job-912ef48c16d4d0f3 `
  --job job-f8ee1a991ac4c443 `
  --job job-4fce48270d1f328c `
  --job job-4c8236a32c685a11 `
  --output-dir work\benchmarks\p07-reference-ab-20260925 `
  --seed 20260925 --batch-size 4

uv run python tools\listening_ab.py create `
  --spec work\benchmarks\p07-reference-ab-20260925\study-spec.json `
  --root . `
  --output-dir work\benchmarks\p07-reference-ab-20260925\blind-packet `
  --seed 20260925

uv run python tools\listening_ab.py evaluate `
  --packet work\benchmarks\p07-reference-ab-20260925\blind-packet\packet.json `
  --output work\benchmarks\p07-reference-ab-20260925\blind-packet\evaluation-no-votes.json

uv run pytest -q tests\test_listening_ab.py tests\test_voice_audio_core.py
uv run python -m py_compile tools\p07_reference_ab.py
```

## Validation result

- VieNeu: `3.7.1`, PyTorch `2.8.0+cu128`, NVIDIA GeForce RTX 2070 SUPER.
- Render path: PyTorch / CUDA / FP16 / batch 4.
- Blind packet media integrity: **PASS**, zero issues.
- No-vote evaluation: expected `status=pending_human_votes`, `gate_passed=false`,
  `valid_ballots=0`, no automatic winner.
- Focused tests: **19 passed in 4.59s**.
- Harness bytecode compile: **PASS**.

The first build invocation completed artifact generation but returned non-zero
only while printing UTF-8 Vietnamese JSON through the Windows console code page.
The harness output was changed to escaped JSON for console portability; generated
audio did not need to be regenerated.

## P07 closure decision

P07 **cannot be marked accepted yet** because its PLAN acceptance explicitly
requires blind human listening for speaker similarity, stability, pronunciation
clarity and noise bleed. Machine-side evidence and a causal blind packet are now
complete. Closure requires at least three valid independent ballots and an
explicit human pass decision through the existing `tools/listening_ab.py` gate.
