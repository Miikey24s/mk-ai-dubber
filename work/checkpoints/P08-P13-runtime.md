# P08/P13 runtime benchmark - RTX 2070 SUPER

Date: 2026-09-22

Machine-readable evidence: `work/benchmarks/p08-p13-runtime-20260922.json`

## Scope

- Benchmark only; no `src/`, config, PLAN, README, or tests were changed.
- Final measurement run was offline (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`).
- VieNeu GPU artifacts were missing from the local Hugging Face cache initially. The exact upstream v3 Turbo PyTorch model and MOSS Audio Tokenizer Nano artifacts were prefetched once, then the benchmark was rerun offline.
- WhisperX used an existing real vocals fixture: `work/job-cafbd0adb862d5ae/stems/original_(Vocals)_model_bs_roformer_ep_317_sdr_12.wav`, slice 3-26 s. No WhisperX model/audio download was performed.

## Machine

- Windows 11, Python 3.12.10
- NVIDIA GeForce RTX 2070 SUPER, 8.0 GiB, compute capability 7.5
- torch 2.8.0+cu128
- vieneu 3.7.1
- whisperx 3.8.6

## P08 VieNeu findings

Supported/observed paths:

- ONNX + CPU + FP32: pass. One representative phrase: 1.7401 s wall for 2.72 s output audio.
- ONNX + CPU + INT8: not available in the current offline cache (`onnx_int8` artifacts missing). This is not evidence that upstream INT8 is unsupported.
- PyTorch + CUDA + explicit `dtype=float16`: pass. Model resident CUDA allocator after load: 558.54 MiB.
- PyTorch + CUDA + explicit `dtype=float32`: load pass, 598.04 MiB model resident allocator. Inference performance was not benchmarked because P08 targets FP16.
- ONNX + `device=cuda`: not a real CUDA path in VieNeu v3 Turbo 3.7.1; the ONNX constructor is CPU-backed here.

Important config finding: VieNeu v3 Turbo's `precision` argument controls ONNX/CPU only. On PyTorch, dtype is controlled by `dtype`. On this runtime, `precision=fp16` with `dtype=auto` resolved to `torch.bfloat16`, not FP16. Therefore P08 must explicitly request/map `dtype=float16` if the contract says CUDA FP16.

### FP16 infer vs infer_batch

Tiny representative Vietnamese phrase set, one run per row after warm-up:

| Method | N | Wall s | Output audio s | RTF | Peak CUDA allocator MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| sequential `infer` | 1 | 0.5208 | 2.80 | 0.1860 | 625.29 |
| `infer_batch` | 1 | 0.5809 | 2.80 | 0.2075 | 625.29 |
| sequential `infer` | 2 | 1.0354 | 5.60 | 0.1849 | 625.94 |
| `infer_batch` | 2 | 1.4025 | 5.52 | 0.2541 | 657.70 |
| sequential `infer` | 4 | 1.7942 | 10.08 | 0.1780 | 659.70 |
| `infer_batch` | 4 | 1.2513 | 10.56 | 0.1185 | 728.83 |

Measured interpretation:

- Batch 4 reduced wall time about 30.3% versus four sequential calls and reduced RTF about 33.4% on this fixture.
- Batch 2 was about 35.5% slower than two sequential calls, so smaller batches do not justify batching from this measurement.
- Batch 4 peak PyTorch allocator was 728.83 MiB. This is allocator evidence only, not total process/driver VRAM, and the fixture is intentionally small.

P08 measured default recommendation: `backend=pytorch`, `device=cuda`, explicit `dtype=float16`, TTS batch size 4. Keep CPU ONNX FP32 as fallback. This recommendation is performance/stability only; voice similarity/naturalness quality parity is still pending.

## P13 WhisperX findings

WhisperX large-v3 CUDA FP16, existing real 23 s vocals slice:

| batch_size | Wall s | RTF | Peak CUDA allocator MiB | Transcript vs batch 4 |
| ---: | ---: | ---: | ---: | --- |
| 4 | 1.9176 | 0.0834 | 280.57 | baseline |
| 6 | 1.6475 | 0.0716 | 280.57 | identical |
| 8 | 1.7040 | 0.0741 | 280.57 | identical |

- Batch 6 was fastest: about 14.1% lower wall time than batch 4 on this fixture.
- Batch 8 was also faster than batch 4, but slower than batch 6.
- All three produced exactly the same normalized transcript text on this fixture.
- Alignment was not rerun per batch because project code passes `asr.batch_size` only to `model.transcribe`; `whisperx.align` has no corresponding batch-size knob in this path.
- Import emitted the existing pyannote/TorchCodec DLL warning, but `whisperx.load_audio` and transcription completed successfully with bundled FFmpeg on PATH.

P13 measured default recommendation: batch size 6 is the best candidate from this short fixture. Keep the production default change behind a broader real-fixture quality/regression gate; this benchmark alone does not prove transcript/alignment parity generally.

## Acceptance status

- Runtime stability/performance evidence: pass for VieNeu CUDA FP16 batch 4 and WhisperX batch 4/6/8 on the measured fixtures.
- Quality parity: **pending**. No claim is made about voice similarity, naturalness, prosody, or general ASR quality from these speed-only measurements.
