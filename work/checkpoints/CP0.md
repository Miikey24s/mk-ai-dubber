# CP0 Baseline Freeze

Date: 2026-09-22
Status: PASS

## Runtime evidence

- `uv run pytest -q` -> `61 passed in 7.41s`.
- `uv run vi-dubber doctor` -> Python 3.12.10, FFmpeg available, RTX 2070 SUPER 8 GB / torch 2.8.0+cu128 CUDA OK, WhisperX/audio-separator/VieNeu/torchcodec/Gradio/yt-dlp OK, Codex WebGPT route `chatgpt-web/high` OK, Qwen local OK, TypeSafe key present in shadow mode, diarization token optional/not present.
- `uv run python tools/benchmark.py validate-manifest --manifest work/benchmarks/fixtures.json --root .` -> valid, no issues.
- Short baseline summary remains readable: 226.081 s video, 53 segments, RTF 3.4170, rewrite 45.28%, overflow 20.75%, QA similarity 0.98663.

Project still has no `.git`; do not infer branch/commit state and do not initialize Git for this roadmap.

## Source snapshot

SHA-256 at CP0:

```text
638F516996E61825DD361A5EF9F88A959A13E58FF1C0DC280BF2D875A297F28F src/vi_dubber/__init__.py
3CF5E6486BC6D5CB5EE221FC026EA3BD8CB691B1FFDE8635684661CF83098978 src/vi_dubber/artifacts.py
22F799326F4F2067E19CF42AFBBE6A0794B0A4C082DCE7F19EF2B12BFC52A21D src/vi_dubber/asr.py
23E3306BC2E2BFE69318DDC24B2028DC7D783E14175FAAD2327BF46D7AADD131 src/vi_dubber/cli.py
A5AA151B01F81EBE30E7BD7C0F2D425EB5FE81704F944B593AE460BACA724F34 src/vi_dubber/media.py
AF0B3821D64D40950241D26D8EC03C21415AE11F0B1A55F7C68FB8F7B564B2A3 src/vi_dubber/metrics.py
8CC51B339C82C77AB6436510B8DE9E690752707EA596C809AEAE171640E9586E src/vi_dubber/pipeline.py
915C57BB18B8AB20155F9955B08758FA045C63C01D52DE2BEF5BB51C7FAAFF28 src/vi_dubber/qa.py
2CD1B865F3FC7091201412382F53C4C3280E4CE3F8447B97AA060F219648CFB2 src/vi_dubber/runtime.py
426FF73D5F01768A11376E46AF167D0F9CEAAE000FFF1C6662A7CD591EF47814 src/vi_dubber/segmentation.py
0D57908BE6ED0E3EE857A51E4397D24D561D69BA58ADB1F0572B2F0B142383B8 src/vi_dubber/semantic_qa.py
65EFA1930EAB01FE513D3976A5FD9B35514BD4DAD0EEE575C1F21842493D25B6 src/vi_dubber/separation.py
A20AF4271C9D0E85C2083FF5FF32B894E5D268E11C1EBF1CABE936A3F88225BF src/vi_dubber/translate.py
D2A9B280AA4614EDAB98A5C56907C65F2E4432B14EDDE14452A92F50865E02A7 src/vi_dubber/tts.py
29E28813F2627042ADDE96D91F89C72D9795885AC697CA1E75BF3080AA9B2403 src/vi_dubber/types.py
E0AA42B1045C3EA5C6AF624C3E4857EC533D9AC9FD37A7254C6194E676D2EC21 src/vi_dubber/web.py
F09891ACA88BE4B6D6135C72322D3B7C6BCF86B97DACAC6CC0F1733941562C05 src/vi_dubber/youtube.py
B76C12150698884535A303E631019BE6D1C57A37D2FCF3B133039F223920248B config.yaml
C9E780A1DCA5ABE05BBF8D44948B0379A2376ACD984C151AB14F559DE47B499E pyproject.toml
FBF69145DD52C5E3C0D8BF59E8D9C35B8DAA052AC94974694287E3070CC88972 work/benchmarks/fixtures.json
```

## Existing roadmap evidence at resume

- P00 profiler module checkpoint exists and passed its focused/full tests when created.
- P01 artifact/cache module checkpoint exists and passed its focused/full tests when created.
- P02 word-level ASR checkpoint exists and passed its focused/full tests when created.
- P17 benchmark skeleton checkpoint exists; full P17 is not complete.
- Current `pipeline.py` already contains stage-manifest/cache and metrics integration beyond those worker checkpoints; CP1 must be reviewed and accepted against current files before moving forward.
- Smart segmentation is implemented behind `segmentation.mode=smart`, while the default remains `legacy` pending real A/B acceptance.

## Next gate

Finish CP1 review/integration evidence for P00 + P01, then proceed to CP2. Do not mark a later checkpoint complete merely because code exists.
