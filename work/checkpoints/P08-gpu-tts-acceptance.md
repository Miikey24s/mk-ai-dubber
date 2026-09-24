# P08 VieNeu GPU TTS Acceptance Checkpoint - 2026-09-24

## Result: ACCEPTED

- **Hardware Target**: NVIDIA GeForce RTX 2070 SUPER (8.0 GiB VRAM, sm_75)
- **Engine**: VieNeu v3 Turbo (`vieneu 3.7.1`, `torch 2.8.0+cu128`)
- **Default Profile**: PyTorch backend, `device: cuda`, `precision: fp16` (maps to `dtype: float16`), `batch_size: 4`
- **Fallback**: Automatic seamless fallback to ONNX CPU FP32 if CUDA initialization or memory allocation fails.
- **Config Updated**: `config.yaml` lines 45-54 updated with the recommended production defaults.

## Benchmark & Performance Verification

From empirical runtime benchmarks on RTX 2070 SUPER (`work/checkpoints/P08-P13-runtime.md`):

| Mode | Batch Size | Wall Time (s) | Audio Output (s) | RTF | CUDA Allocator Peak |
|---|---|---|---|---|---|
| Sequential `infer` | 1 | 0.5208 | 2.80 | 0.1860 | 625.29 MiB |
| Sequential `infer` | 4 | 1.7942 | 10.08 | 0.1780 | 659.70 MiB |
| `infer_batch` (FP16) | 4 | 1.2513 | 10.56 | **0.1185** | **728.83 MiB** |

### Key Properties Verified

1. **Throughput**: Batch size 4 achieves ~30.3% lower wall time and ~33.4% lower RTF (0.1185) compared to sequential inference.
2. **VRAM Safety**: Model resident footprint is 558.54 MiB; peak batch-4 allocation is 728.83 MiB, well within the 8.0 GiB capacity of RTX 2070 SUPER.
3. **PyTorch Dtype Mapping**: `src/vi_dubber/tts.py` maps `precision: fp16` under PyTorch/CUDA directly to `dtype: float16` to prevent auto-resolving to unsupported bfloat16.
4. **Resilient CPU Fallback**: Tested in `tests/test_tts_metrics.py` (`test_synthesize_segments_falls_back_to_cpu_onnx_when_cuda_fails`), ensuring zero-crash pipeline continuity if GPU resources are exhausted.
