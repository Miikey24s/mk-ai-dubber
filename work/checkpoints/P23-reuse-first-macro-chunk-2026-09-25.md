# P23 reuse-first + macro-chunk foundation — 2026-09-25

Scope: P23 implementation order items 1-2 only. This checkpoint does not claim scheduler, streaming ASR, progressive preview, or whole-job speedup.

## Reuse-first decision

No new dependency is required for the macro-chunk control plane.

| Need | Evidence checked | Decision | Reason |
|---|---|---|---|
| Existing ASR/VAD primitives | project lock contains WhisperX 3.8.6; installed `FasterWhisperPipeline.transcribe(..., chunk_size=30, ...)`; upstream `m-bain/whisperX` main `2cfd7b7c5c7bba144954364db747319b50e8232b`, BSD-2-Clause | ADAPT / REFERENCE | keep WhisperX as ASR core; later streaming slice can reuse its VAD/chunk primitives without replacing the model |
| faster-whisper | upstream `SYSTRAN/faster-whisper` master `ed9a06cd89a93e47838f564998a6c09b655d7f43`, MIT; already transitive through WhisperX | REFERENCE | no direct dependency or ASR replacement needed for the planner |
| Silero VAD | upstream `snakers4/silero-vad` master `5cd7945676eb32225748052e2e6a0580e4686a08`, MIT | REFERENCE | candidate for later VAD boundary evidence only; do not add until same-fixture A/B justifies it |
| Preview packaging | bundled FFmpeg `n7.1.5-12-g1fdbca85aa-20260731` exposes HLS, segment, stream_segment and MP4 muxers; upstream `FFmpeg/FFmpeg` master snapshot `6246f7a4cde6a39518aad3ed5bbc3ecfdb7f275e` | REUSE later | progressive preview should use FFmpeg muxers rather than a custom streaming/container implementation |
| Macro-chunk schema / global timeline policy | no external engine owns VI Dubber cache identity, source timestamps, resume/failure scope, profile policy, or manifest contract | BUILD small helper | this is VI Dubber control-plane logic; keeping it deterministic and dependency-free reduces coupling |

The upstream SHAs above are audit snapshots, not dependencies pinned into the runtime. Runtime behavior remains governed by `uv.lock` and the project-bundled FFmpeg binary until a later P23 gate explicitly promotes a change.

## Implemented in this slice

- `src/vi_dubber/longform.py`
  - `SpeechInterval` input contract;
  - `MacroChunkPolicy` defaults matching the P23 initial 25 min target / 15-40 min range;
  - deterministic macro-chunk IDs and global source ranges;
  - silence-first boundary choice near target, then bounded silence, then deterministic safe cut;
  - bounded context window metadata for future VAD/ASR windowing;
  - local-to-global timestamp mapping.
- `tests/test_longform.py`
  - short single-work-unit path;
  - silence-near-target and bounded-silence selection;
  - deterministic no-silence fallback;
  - exact global timeline coverage;
  - timestamp mapping and input validation.
- `src/vi_dubber/longform_state.py`
  - plan fingerprint bound to source/context/chunk boundaries;
  - per-chunk stage fingerprints and manifests using the existing artifact manifest implementation;
  - cache-safe resume validation per chunk;
  - explicit `ASR -> translation -> TTS -> QA -> preview` dependency order;
  - chunk-scoped invalidation that removes manifests only, preserving sibling chunks and leaving stale artifacts non-reusable until replacement commit.
- `tests/test_longform_state.py`
  - plan identity invalidation;
  - verified per-chunk cache hit and tamper fail-closed behavior;
  - dependency-aware partial invalidation;
  - failure scope proof that a sibling chunk keeps its committed preview.

## Known environment observation

Importing WhisperX emitted the existing TorchCodec warning that its decoder DLL could not load in the current environment. This slice does not use TorchCodec and does not modify the ASR runtime. Treat this as a separate runtime diagnostic if it reproduces in the actual ASR path.

## Next P23 gate

Integrate the planner with a cheap boundary source and streaming/windowed ASR input. The per-chunk manifest identity and synthetic resume/invalidation contract are now covered by focused tests; production pipeline wiring remains intentionally off until the ASR I/O gate is ready.
