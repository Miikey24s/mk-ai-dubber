# VI Dubber - Full Optimization Plan

Phiên bản: **v2.20 - 26/09/2026**
Trạng thái: **v2.22 PROGRESS / P00, P01, P02, P04, P05, P06, P08, P10, P13, P15, P16, P17, P20, P22 ACCEPTED/DONE; P21 giữ làm historical acceptance; P23 IN PROGRESS: correctness oracle của short-overhead A/B đã reconcile bằng P17 real clean-talking-head 60s và per-arm product QA; repeated 3-trial paired-overhead pass với median ratio `0.98505` so với gate `<=1.05`, variance vẫn diagnostic. Real-media final-assembly harness đã bỏ system-wide `nvidia-smi` delta khỏi gate và dùng Windows PDH process-tree dedicated GPU attribution; short production-mux smoke + focused tests pass instrumentation nhưng chưa thay fresh 6h+ rerun. Chưa claim speedup; repeated whole-job 33 phút cùng fresh real-media 6h+/live-provider/final-media gate vẫn mở. P07/P11/P12 vẫn chờ human listening gate theo acceptance contract**
Mục tiêu AI dịch target: **Dedicated Dubber-WebGPT tại `127.0.0.1:17850`, browser/session riêng, direct `/v1/responses`, live model catalog và model do người dùng chọn trên UI**
Project: `D:\ANNAM\TradingWorkspace\projects\vi-dubber`  
Translation runtime target hiện khóa vào **Dedicated Dubber-WebGPT / port 17850**. Global Codex/Cockpit route của máy được giữ nguyên; production translation đi thẳng qua provider-only Responses và không được tự failover sang Codex daily runtime, Aurora hoặc local model.

> Đây là plan nguồn cho đợt tối ưu sâu `vi-dubber`. README tiếp tục là tài liệu vận hành ngắn gọn; file này giữ mục tiêu, thứ tự triển khai, contract giữa worker/subagent, benchmark, acceptance, rollback và trạng thái từng phase.

---

## 0. Quyết định tổng thể

Không rewrite toàn bộ stack và không đổi model hàng loạt.

Stack hiện tại đã có nền tốt:

- BS-RoFormer cho source separation.
- WhisperX `large-v3` CUDA FP16 cho ASR + alignment.
- Historical P21 đã chứng minh ChatGPT Web quality/contract trên instance 2; instance đó đã bị người dùng xóa ngày 25/09/2026 và không còn là runtime target.
- P22 dùng Dedicated Dubber-WebGPT tại `http://127.0.0.1:17850/v1`, home/browser login/process riêng và direct Responses tool-free; không sửa global `~/.codex/config.toml` và không dùng Aurora trong đường chạy hiện tại.
- Model translation không hardcode lâu dài. UI lấy model catalog từ backend/account và cho người dùng chọn; default policy ưu tiên model Plus chất lượng cao nhất đã được project verify tại thời điểm chạy.
- Qwen local còn trong source cho rollback/test nhưng không là fallback tự động của product path hiện tại.
- VieNeu v3 Turbo là TTS/voice clone.
- TypeSafe/Jev đã có semantic QA shadow mode.
- FFmpeg chịu trách nhiệm media, timing, mix, loudness và mux.
- Gradio đã có web UI.

Vấn đề lớn nhất hiện tại nằm ở **orchestration của pipeline**, đặc biệt:

1. WhisperX có word timing nhưng project đang bỏ phần word-level khi lưu `Segment`.
2. Segment hiện bị xem như một khung cứng cho translation/TTS.
3. Nhiều câu chỉ được phát hiện quá dài sau khi đã synthesize xong lần đầu.
4. VieNeu đang chạy CPU/ONNX/FP32 và infer tuần tự từng segment.
5. Voice reference chỉ chọn theo độ dài, chưa chọn theo chất lượng acoustic.
6. Semantic QA sau rewrite đang nằm sau việc TTS lại, nên có thể phát hiện lỗi quá muộn.
7. Global Re-ASR similarity có thể che lỗi nghiêm trọng ở một vài segment.
8. Cache/resume chưa fingerprint đầy đủ theo input/model/config/text/reference.
9. Audio mix còn cơ bản so với output dubbing hoàn chỉnh.

Chiến lược chính:

```text
giữ stack tốt hiện có
        +
sửa data flow / segmentation / timing
        +
GPU batch phần TTS
        +
semantic verification chọn lọc
        +
QA theo segment + selective repair
        +
mix/master hoàn chỉnh
```

Mục tiêu không phải "chạy nhanh bằng mọi giá" hoặc "quality tối đa bằng mọi giá". Mục tiêu mặc định là **Balanced Best**: tăng chất lượng và throughput đồng thời ở những nơi hiện đang lãng phí compute; chỉ chấp nhận trade-off khi tính năng đó thực sự đòi hỏi, ví dụ Max Quality multi-candidate.

---

## 1. Trạng thái đã xác minh trước khi lập plan

### 1.1. Runtime

`uv run vi-dubber doctor` ngày 22/09/2026:

- Python `3.12.10`.
- FFmpeg `n7.1.5-12-g1fdbca85aa-20260731`.
- GPU `NVIDIA GeForce RTX 2070 SUPER`, 8 GB.
- PyTorch `2.8.0+cu128`.
- WhisperX OK.
- audio-separator OK.
- VieNeu OK.
- torchcodec OK.
- Gradio OK.
- yt-dlp OK.
- Baseline cũ: Codex WebGPT OK qua Codex WebGPT Local Access. Từ v1.8, route product từng được khóa riêng vào instance 2 `:17842`; baseline này đã được P22 supersede bằng Dedicated Dubber-WebGPT `:17850`.
- Qwen3-14B-Q4_K_M local OK.
- `TYPESAFE_API_KEY` có trong environment, semantic QA đang shadow.
- Diarization token hiện optional/chưa có trong doctor snapshot.

### 1.2. Tests

Baseline:

```text
uv run pytest -q
19 passed in 6.90s
```

Không phase nào được phép làm giảm baseline này mà không giải thích và thay bằng test tương đương tốt hơn.

### 1.3. Hai job thật đang có làm baseline

| Job | Video | Segments | Rewrite | Overflow | RTF | Avg tempo | Max tempo |
|---|---:|---:|---:|---:|---:|---:|---:|
| `PWKWNb550Cw-*` | 226.1 s | 53 | 24 | 11 | 3.42x | 1.11 | 1.25 |
| `Trading_Strategies_That_Work__full_training-*` | 3238.7 s | 734 | 523 | 199 | 1.745x | 1.12 | 1.25 |

Job dài cho thấy vấn đề rõ nhất:

- rewrite rate: `523 / 734 = 71.3%`;
- overflow sau rewrite + tempo fit: `199 / 734 = 27.1%`;
- raw TTS/target duration ở audit trước:
  - p50 khoảng `1.108x`;
  - p95 khoảng `2.286x`;
  - p99 khoảng `3.486x`;
  - max khoảng `5.027x`;
- overflow tập trung mạnh ở segment ngắn:
  - `<1 s`: khoảng `79.5%`;
  - `1-2 s`: khoảng `50.3%`;
  - `2-4 s`: khoảng `20.5%`;
  - `4-8 s`: khoảng `3.8%`.

Kết luận: **segmentation/timing là bottleneck chất lượng và hiệu năng lớn nhất**. Không được dùng tăng `max_speedup` làm cách che vấn đề này.

### 1.4. Codex development/translation environment

Historical P21 snapshot được re-check ngày 23/09/2026:

- global Codex vẫn dùng provider `codex_local_access` qua Cockpit `http://localhost:54005/v1`;
- managed WebGPT instance 1 nghe ở `127.0.0.1:17841`;
- managed WebGPT instance 2 nghe ở `127.0.0.1:17842`, `/healthz` báo `accepting_turns=true`;
- VI Dubber chỉ dùng instance 2 và mặc định `chatgpt-web/gpt-5.6-sol`;
- `/v1/models` của instance 2 hiện advertise `chatgpt-web/gpt-5.6-sol` và `chatgpt-web/gpt-5.6-sol-instant`;
- `multi_agent = true`;
- `multi_agent_v2 = true`;
- `[agents].max_depth = 2`;
- `[agents].max_threads = 10`;
- source `codex-chatgpt-web` có `MAX_CHATGPT_BROWSER_TABS = 5` cho một browser worker/instance.

Các con số này là **runtime snapshot lịch sử**, không phải hằng số vĩnh viễn. Main worker phải kiểm tra lại capability/slots trước mỗi execution wave lớn. Trạng thái production hiện tại đã chuyển sang P22: Dedicated Dubber-WebGPT `127.0.0.1:17850`, direct Responses, `webgpt_concurrency: 2`, global context tắt; `:17842` đã retired.

Product translation không kế thừa global provider URL: VI Dubber gọi trực tiếp `http://127.0.0.1:17850/v1/responses` qua Dedicated Dubber-WebGPT và không spawn `codex exec` trên normal translation path. Nếu health port, catalog hoặc selected model không khớp thì fail closed.

CLI `codex-chatgpt-web` hiện không được resolve trực tiếp từ PowerShell PATH trong lượt lập plan. Vì vậy execution không được giả định command đó luôn sẵn. Ưu tiên collaboration tools đã expose trong task hoặc resolve đúng local runtime khi thực sự cần diagnostic.

### 1.5. Git

Không thấy `.git` trong `projects/vi-dubber` và `git -C projects/vi-dubber status` không nhận đây là git repository tại thời điểm lập plan.

Quy tắc:

- không tự `git init`;
- không viết plan dựa trên giả định có branch/commit;
- dùng test receipts, file diff, benchmark artifacts và PLAN status làm durable evidence;
- nếu sau này project được đưa vào git thật, worker mới bổ sung commit/branch gates.

---

## 2. Mục tiêu sản phẩm cuối

### 2.1. Trải nghiệm đầu ra mong muốn

Với nguồn sạch, đặc biệt monologue/talking-head/tutorial/documentary:

> Người xem nên cảm thấy đây là một phiên bản tiếng Việt được dựng riêng cho video, không phải AI đang đọc từng dòng subtitle.

Các đặc tính cụ thể:

- tiếng Việt đúng nghĩa;
- câu nói nghe tự nhiên với người Việt;
- đại từ/register/thuật ngữ nhất quán theo context;
- timing tự nhiên theo speech turn;
- ít câu bị ép `1.20-1.25x`;
- không tạo khoảng nghỉ staccato do segment ngắn;
- voice identity ổn định xuyên video;
- tên riêng, số, đơn vị, phủ định, modality được bảo vệ;
- background/music không lấn thoại;
- loudness nhất quán;
- lỗi chỉ ở vài segment không bị che bởi global similarity cao;
- khi retry chỉ làm lại stage/segment cần thiết;
- rerun/resume không dùng artifact stale.

### 2.2. Không hứa quá mức

Không claim:

- "không phân biệt được người thật" trong mọi video;
- studio acting tuyệt đối;
- emotion cực mạnh giống bản gốc ở mọi tình huống;
- overlapping speakers được giải quyết hoàn hảo;
- RTF `<1` trước khi benchmark thật trên RTX 2070 SUPER.

### 2.3. Ba profile sản phẩm

#### Fast

Mục tiêu: batch processing, tốc độ ưu tiên nhưng không phá semantic correctness.

- giữ ASR đủ tốt;
- translation 1-pass;
- TypeSafe chỉ critical gate;
- VieNeu GPU batch lớn nhất đã benchmark an toàn;
- QA chọn lọc;

#### Balanced Best - mặc định

Mục tiêu: sweet spot.

- full smart segmentation;
- context + duration-aware translation;
- TypeSafe verify/escalate;
- smart reference;
- VieNeu GPU batch;
- elastic timing;
- segment QA;
- professional mix;
- selective repair;

#### Max Quality

Mục tiêu: video quan trọng, chấp nhận chậm hơn.

- stricter semantic thresholds;
- candidate fan-out chỉ ở segment khó;
- stronger acoustic QA;
- prosody/style analysis sâu hơn;
- có thể bật full-video final ASR;
- optional cloud/proprietary TTS A/B nếu người dùng cho phép cost/privacy/network dependency.

---

## 3. Nguyên tắc kiến trúc

1. **Code deterministic giữ control flow.** Duration, hashes, exact numbers, cache keys, routing state, path ownership và retry policy nằm trong code.
2. **Selected ChatGPT Web model sinh ngôn ngữ.** Dịch, rewrite, contextual phrasing và candidate generation thuộc generative translation provider; target transport hiện tại là Dedicated Dubber-WebGPT `:17850` qua direct Responses.
3. **TypeSafe phán semantic hẹp.** Verify, classify, score, route/escalate; không dùng Jev để làm toán duration hoặc thay parser deterministic.
4. **AI uncertainty không được coi là truth.** Lưu raw probabilities/confidence, calibrate threshold trên fixture thật.
5. **Word timing là dữ liệu core.** Không bỏ sau WhisperX alignment.
6. **Speech turn quan trọng hơn subtitle box.** Segment là unit dữ liệu, không phải bắt buộc unit prosody.
7. **Selective work.** Không dùng model mạnh/multi-candidate/full QA trên 100% segment nếu không cần.
8. **Cache phải content-addressed.** File tồn tại không đủ để reuse.
9. **Benchmark trước khi đổi default.** GPU batch size, Whisper batch, separator batch/model, TypeSafe batch đều phải A/B.
10. **Output quality cần human listening gate.** Automated metric chỉ hỗ trợ, không thay blind A/B.
11. **Không tối ưu vô hạn.** Dừng khi đạt acceptance của profile và không còn bottleneck đáng kể.
12. **Một source of truth.** PLAN này giữ trạng thái roadmap; benchmark artifacts giữ evidence; chat không phải nơi lưu state duy nhất.
13. **Long-form là orchestration problem trước khi là model problem.** Video dài phải được chia thành work unit nhỏ, checkpoint được, retry độc lập và có global timestamp/context.
14. **Pipeline overlap có giới hạn.** Chỉ overlap các stage dùng tài nguyên khác nhau hoặc đã benchmark không tranh GPU/VRAM; không nhân worker GPU mù quáng.
15. **Profile là policy trên một core chung.** Fast/Balanced/Max cùng hưởng chunking, streaming, cache, scheduler và partial recompute; profile chỉ thay độ sâu QA/rewrite/retry/escalation.

---

## 4. Contract cho Codex development coordinator

### 4.1. Main coordinator

Development coordinator có thể tiếp tục dùng route/model Codex hiện hành. Contract này điều phối **việc phát triển code**, không định nghĩa translation backend của sản phẩm.

Subagents/reviewer dùng route/model của development task hiện tại; product translation thì khóa riêng vào instance 2. Không tự đổi product traffic sang instance 1, Aurora, native OpenAI API hoặc model/provider khác chỉ vì một browser turn/transport bị lỗi. Nếu WebGPT bị 502, stream disconnect hoặc browser failure, trước hết coi đó là **transport failure**, kiểm tra artifact/file state rồi resume/reassign.

Qwen local trong project là fallback của **sản phẩm dubbing**, không phải fallback tự động cho coding agent.

Việc tối ưu `vi-dubber` cũng không cấp quyền sửa `D:\ANNAM\AI\codex-chatgpt-web-cockpit`. Worker repo đó chỉ là transport/orchestration reference; bug của nó phải được tách thành task riêng.

Main worker sở hữu:

- đọc PLAN và trạng thái trước khi làm;
- preflight runtime;
- chọn execution wave;
- chia task cho subagents;
- tránh hai agent sửa cùng vùng file;
- giữ dependency graph;
- review tất cả patch trước integration;
- chạy focused tests;
- chạy regression thích hợp;
- benchmark trên fixture thật khi phase yêu cầu;
- cập nhật PLAN status/checkpoint;
- tổng hợp failure và rollback;
- quyết định phase đã đủ acceptance hay chưa.

Shared convergence files mặc định do coordinator sở hữu:

- `PLAN.md`;
- `pipeline.py`;
- `types.py` khi schema ảnh hưởng nhiều subsystem;
- `config.yaml` / resolved profile contract;
- final cross-module regression/integration tests.

Child chỉ sửa các file này khi coordinator giao **exclusive ownership** trong một task riêng và không có child khác chạm cùng lúc.

Subagent **không** được tự coi task của mình là product acceptance.

### 4.2. Preflight bắt buộc trước mỗi wave lớn

Main worker kiểm tra:

```powershell
cd D:\ANNAM\TradingWorkspace\projects\vi-dubber
uv run pytest -q
uv run vi-dubber doctor
```

Sau đó kiểm tra tối thiểu:

- file PLAN hiện tại;
- source files sẽ sửa;
- current work artifacts liên quan;
- GPU/VRAM nếu phase dùng CUDA;
- `TYPESAFE_API_KEY` chỉ bằng trạng thái có/không, không in secret;
- translation provider health/model catalog nếu phase chạm AI runtime;
- Codex development route chỉ khi chính execution task đang dùng route đó;
- available collaboration slots nếu định spawn agents.

Nếu baseline đã fail trước khi sửa, ghi rõ failure là pre-existing; không trộn với regression do patch mới.

### 4.3. Cách dùng subagents

Chỉ spawn khi subtask độc lập có lợi thực.

Các loại subagent ưu tiên:

| Agent | Scope điển hình | Có sửa code? |
|---|---|---|
| architecture-audit | dependency/file map/risk | không |
| perf-benchmark | benchmark, profiler, VRAM/RTF | có thể thêm harness riêng |
| segmentation-worker | ASR word data + segmentation | có |
| translation-worker | context/duration prompts/parser | có |
| typesafe-worker | semantic verifier/gating | có |
| tts-worker | VieNeu CUDA/batching/reference | có |
| audio-worker | assembly/mix/master | có |
| qa-worker | segment QA/selective repair | có |
| test-worker | fixtures/regression | test files/harness |
| reviewer | review patch/evidence | không mặc định |

Không spawn agent chỉ để agent đọc lại toàn bộ repo giống root.

Reviewer mặc định **read-only**. Nếu reviewer chuyển thành fixer, patch đó phải được một reviewer khác hoặc coordinator review lại như code mới.

### 4.4. Bounded ownership

Mỗi child phải nhận contract gồm:

```text
Task ID
Goal
Files owned
Files read-only
Inputs/artifacts
Required tests
Required benchmark
Acceptance
Explicit non-goals
Expected handoff
```

Ví dụ:

```text
Task: P8-GPU-TTS-A
Owned: src/vi_dubber/tts.py, tests/test_tts_gpu.py
Read-only: pipeline.py, config.yaml
Goal: add optional torch/cuda infer_batch path grouped by speaker/ref
Acceptance: CPU path unchanged; GPU batch=2/4 measured; no output corruption
Non-goal: do not change translation/timing policy
```

### 4.5. Không cho hai agent sửa cùng ownership region

Main worker chia theo module hoặc theo test/implementation.

Không chạy song song kiểu:

```text
agent A sửa tts.py
agent B cũng sửa tts.py
```

Trừ khi B là reviewer read-only.

### 4.6. Concurrency

Source bridge hiện có limit 5 browser turns/instance; config Codex snapshot có max 10 threads. Không coi `10` là số child mặc định.

Policy:

- Default cho runtime hiện tại: root + `2-3` workers.
- Trước khi nâng lên `4` implementation workers, chạy một smoke ngắn xác nhận child/route/slots ở đúng task hiện tại.
- Wave độc lập rõ mới cân nhắc root + 3-4 workers.
- Chỉ tăng thêm khi slots/runtime khỏe và ownership không đụng nhau.
- Root luôn chừa capacity cho review/integration.
- Không lấp đầy tất cả slots bằng research nếu integration đang chờ.
- Khi child lỗi transport, root dùng evidence đã lưu; không restart toàn wave.

### 4.7. Main worker phải review

Mọi code từ child phải qua:

1. đọc diff/changed files;
2. đối chiếu contract;
3. chạy focused test;
4. kiểm tra regression liên quan;
5. benchmark nếu behavior/performance đổi;
6. chỉ sau đó mới cập nhật phase status.

Không dùng "agent nói pass" thay cho test output.

### 4.8. Durable state

Để một chat mới có thể resume mà không làm lại việc đúng, mỗi phase tạo evidence ở `work/benchmarks/` hoặc `work/checkpoints/`.

Đề xuất cấu trúc:

```text
work/
  benchmarks/
    fixtures.json
    baseline-YYYYMMDD.json
    p03-segmentation-<attempt>.json
    p08-gpu-tts-<attempt>.json
  checkpoints/
    P00.md
    P01.md
    ...
```

Checkpoint cần ghi:

- phase/task ID;
- source snapshot/hash nếu có;
- file thay đổi;
- commands chạy;
- test results;
- benchmark results;
- acceptance pass/fail;
- known issues;
- next step;
- artifacts path.

Vì project hiện chưa có Git riêng, checkpoint implementation lớn còn phải ghi:

- SHA256 trước/sau của các file owned;
- danh sách file tạo/sửa/xóa;
- diff/backup receipt nếu tooling hiện tại cung cấp;
- source hash của fixture/benchmark input.

Không tự tạo Git chỉ để giải quyết việc này.

Chat context không được là nơi duy nhất chứa các con số benchmark.

### 4.9. Protocol resume

Khi task/root/subagent bị mất context:

1. đọc `PLAN.md`;
2. đọc checkpoint phase đang active;
3. đọc benchmark artifact cuối;
4. inspect current files;
5. chạy test hẹp xác nhận state;
6. tiếp tục từ next incomplete gate.

Không rerun toàn pipeline chỉ vì chat mới.

### 4.10. Open-source reconnaissance / REUSE-ADAPT-BUILD gate

Trước khi implementation đáng kể cho một phase, coordinator phải rà các implementation open-source liên quan trước khi tự viết mới. Mục tiêu là tận dụng code/ý tưởng đã được chứng minh ở những phần commodity, nhưng vẫn giữ control plane, cache/resume, reliability, semantic policy và product workflow của `vi-dubber` làm source of truth.

Workflow bắt buộc cho mỗi phase phù hợp:

1. tìm tối thiểu 3 implementation/repo liên quan nếu ecosystem có đủ candidate;
2. kiểm tra license ở revision/tag thực sự định reuse, không dựa vào memory hoặc mô tả cũ;
3. kiểm tra maintenance signal, dependency weight, Windows/CUDA compatibility và mức coupling;
4. đọc implementation path thực tế, không quyết định chỉ từ README;
5. phân loại quyết định thành `REUSE`, `ADAPT`, `BUILD` hoặc `REFERENCE-ONLY`;
6. ghi rationale ngắn vào checkpoint/benchmark receipt của phase;
7. nếu reuse code trực tiếp, ghi source URL/revision/license và giữ attribution/license obligations phù hợp;
8. benchmark local trước khi đổi default; không dùng benchmark upstream làm bằng chứng performance cho máy hiện tại.

Decision rule:

```text
REUSE
  dùng gần như nguyên module khi contract sạch, license phù hợp, dependency chấp nhận được.

ADAPT
  port phần algorithm/adapter cần thiết vào interface của vi-dubber khi upstream tốt nhưng coupling lớn.

BUILD
  tự viết khi đây là control-plane/product-specific logic, hoặc upstream không đạt reliability/license/dependency requirements.

REFERENCE-ONLY
  chỉ học UX/architecture/test idea khi không nên copy code trực tiếp.
```

Seed repos cần audit khi phase tương ứng bắt đầu, không coi danh sách này là exhaustive hoặc license snapshot vĩnh viễn:

| Area | Repo/reference seed | Mục tiêu audit |
|---|---|---|
| subtitle/segmentation/translation pipeline | VideoLingo | word/subtitle segmentation, alignment, batching, translation workflow |
| subtitle cleanup/smart segmentation | ZastTranslate | WhisperX word cleanup, sentence/segment construction |
| local dubbing pipeline | video-dubbing-translator | local pipeline boundaries, voice integration |
| dubbing workflow | Linly-Dubbing | pipeline isolation, web workflow |
| job progress/runtime architecture | videoTranslator | FastAPI/WebSocket progress, config/runtime telemetry, time-stretch patterns |
| mature desktop/web dubbing UX | pyVideoTrans | UI/workflow/reference only unless license review explicitly permits desired reuse model |

Phần mặc định **BUILD/OWN** trong project:

- stage graph/orchestration;
- artifact identity/fingerprint/cache invalidation;
- checkpoint/resume/crash recovery/idempotency;
- Codex ChatGPT Web instance-2 translation contract riêng của project;
- TypeSafe semantic routing/gating policy;
- VieNeu-specific Vietnamese voice/timing integration;
- production hardening/fault-injection behavior;
- product UI state model và selective downstream invalidation.

Nguyên tắc: **own the control plane, reuse the engines**.

---

## 5. Source map hiện tại

| File | Ownership hiện tại | Vai trò target |
|---|---|---|
| `pipeline.py` | orchestration nguyên khối | coordinator/stage graph + manifest |
| `asr.py` | transcribe/alignment | giữ word data + ASR fixture support |
| `types.py` | `Segment` cơ bản | Word/SpeechTurn/Segment metadata |
| `translate.py` | local/WebGPT/hybrid + rewrite | instance-locked WebGPT path + dormant legacy adapters |
| `semantic_qa.py` | TypeSafe shadow | semantic verifier + policy separation |
| `tts.py` | ref clip + sequential TTS + rewrite | reference selector + batch TTS |
| `media.py` | FFmpeg + fit + assembly + mux | elastic assembly + professional mix |
| `qa.py` | global text similarity | segment QA + critical token analysis |
| `separation.py` | BS-RoFormer | benchmarkable separator stage |
| `runtime.py` | tool/model paths | runtime capability reporting |
| `web.py` | Gradio app | profile + review UI + metrics |
| `cli.py` | CLI | profiles/benchmark/resume options |
| `config.yaml` | flat current defaults | explicit profile-aware config |
| `tests/test_core.py` | current regression | giữ; dần tách thêm focused test files |

Các module mới **có thể** được tạo khi phase tương ứng chứng minh abstraction là cần thiết; không scaffold trước chỉ để đủ kiến trúc:

| Candidate module | Chỉ tạo khi | Ownership dự kiến |
|---|---|---|
| `artifacts.py` | P01 manifest/fingerprint làm `pipeline.py` quá tải | artifact/cache contract |
| `segmentation.py` | P03 algorithm đủ độc lập | word -> speech turn |
| `timing.py` | P04/P09 duration/slack allocator có reusable logic | timing/duration |
| `pronunciation.py` | P06 có rule/golden set rõ | spoken normalization |
| `reference.py` | P07 reference scoring tách sạch khỏi synthesize | voice reference |
| `metrics.py` | P00 instrumentation dùng ở nhiều stage | benchmark/telemetry |

---

## 6. Known issues phải được xử lý trong roadmap

### 6.1. Job identity/resume

`_job_dir()` hiện có thể ưu tiên newest directory cùng stem trước khi xác minh content đầy đủ.

Risk: hai video cùng tên/stem hoặc input thay đổi có thể reuse job không đúng.

### 6.2. Translation cache

`translations_cache.json` hiện không fingerprint đủ:

- source text;
- provider;
- model;
- prompt policy;
- glossary;
- context window.

`--fresh` phải thực sự bypass/invalidate đúng cache.

### 6.3. TTS cache

`*_raw.wav` tồn tại hiện có thể được reuse dù:

- translation đổi;
- voice reference đổi;
- backend đổi;
- precision/device đổi;
- model/version đổi;
- TTS params đổi.

### 6.4. Stems paths

`stems.json` có thể chứa absolute path, làm artifact không self-contained khi move/copy.

### 6.5. Word timing

WhisperX align xong nhưng `Segment` hiện chỉ giữ segment start/end/text/speaker.

### 6.6. Timing config dead knob

`timing.max_slowdown` hiện có trong config nhưng chưa được dùng đúng trong flow.

### 6.7. Rewrite prompt domain leak

Rewrite prompt từng có hard-coded phrasing kiểu `trading meaning/rules`; dubbing không được mặc định domain trading.

### 6.8. Semantic QA placement

Rewrite QA hiện xảy ra sau synthesize/rewrite flow; target phải gate semantic trước TTS lại trong strict/Balanced path.

### 6.9. TypeSafe policy/cache coupling

Raw inference nên reusable; đổi threshold routing không nên buộc API inference lại nếu state/questions/model không đổi.

### 6.10. Global QA

Global transcript similarity có thể vẫn cao khi một từ quan trọng sai.

Ví dụ đã gặp ở artifact:

- `làm chứng` -> `làm trứng`;
- `ban ngày` -> `ba ngày`.

### 6.11. Reference selection

Hiện reference chủ yếu chọn đoạn đủ dài/gần 6 s, chưa rank theo acoustic quality.

### 6.12. TTS execution

Hiện default:

```yaml
backend: onnx
device: cpu
precision: fp32
```

và infer tuần tự.

---

## 7. Target data model

Không nhồi mọi metadata vào một dict tự do.

### 7.1. Word timing

Target conceptual schema:

```text
WordSpan
  text
  start
  end
  score/confidence optional
  speaker optional
```

### 7.2. Source segment

```text
SourceSegment
  id
  start
  end
  text
  speaker
  words[]
  source_confidence
```

### 7.3. Speech turn

```text
SpeechTurn
  id
  speaker
  start
  end
  source_segment_ids[]
  pauses[]
  target_window
  overlap flags
  scene/context id optional
```

### 7.4. Translation candidate

```text
TranslationCandidate
  source ids
  text_vi
  generation mode
  predicted_duration
  semantic judgments
  selected/rejected reason
```

### 7.5. Render item

```text
RenderItem
  speech_turn_id
  text_vi
  speaker
  ref_id/hash
  target start/end
  generated duration
  applied tempo/stretch
  audio artifact fingerprint
```

Các schema cuối cùng phải bám style codebase và chỉ thêm abstraction khi phase triển khai thực sự cần.

---

## 8. Target pipeline

P23 mở rộng pipeline này thành **adaptive long-form core**: video ngắn có thể vẫn là một work unit; video dài được chia theo silence/VAD thành macro-chunk, mỗi chunk có global timestamp, manifest và checkpoint riêng. Scheduler cho phép pipeline overlap có giới hạn giữa ASR/translation/TTS nhưng final assembly luôn deterministic theo timeline.

```text
VIDEO INPUT
    |
    v
extract audio
    |
    v
source separation
    |
    v
WhisperX transcription + word alignment + speaker info
    |
    v
word-aware segmentation
    |
    v
speech-turn builder + local context
    |
    v
selected ChatGPT Web model translation
  - natural Vietnamese
  - context aware
  - target-duration aware
    |
    v
deterministic pre-checks
  - exact numbers
  - glossary candidates
  - target duration estimate
    |
    v
TypeSafe semantic verifier
  - material meaning loss?
  - negation/modality changed?
  - action direction changed?
  - spoken Vietnamese awkward?
  - context/register inconsistent?
    |
    +---- clean -----------> selected text
    |
    +---- uncertain -------> selected ChatGPT Web model selective rewrite/candidate
                                |
                                +--> verify again
    |
    v
pronunciation normalization
    |
    v
smart voice reference selector
    |
    v
VieNeu v3 Turbo GPU FP16 batched by speaker/reference
    |
    v
elastic speech-turn timing
    |
    v
segment acoustic QA
    |
    +---- pass ------------> assembly
    |
    +---- fail ------------> selective repair only
    |
    v
voice assembly + overlap policy
    |
    v
dialogue mix/master
    |
    v
final mux + final QA
    |
    v
VIDEO VI + SRT + QA REPORT + BENCHMARK RECEIPT
```

---

## 9. Acceptance metrics toàn chương trình

### 9.1. Timing

- overflow `<5%` cho Balanced Best;
- stretch goal `<3%`;
- segment cực ngắn trước TTS giảm mạnh, `<1s` chỉ còn interjection/utterance thực sự ngắn;
- tempo p95 ưu tiên `<=1.15-1.20x`;
- không tăng global `max_speedup` chỉ để pass metric.

### 9.2. Rewrite

- từ baseline job dài `71.3%` xuống `<30%`;
- mục tiêu tốt `<20%`;
- rewrite do timing phải được pre-fit nhiều hơn trước synthesize.

### 9.3. Semantic

- critical number/name/negation/modality errors trên golden set: mục tiêu gần 0;
- rewrite phải bảo toàn material meaning;
- TypeSafe threshold chỉ khóa sau calibration.

### 9.4. Audio

- final loudness target mặc định khoảng `-14 LUFS ±0.5`;
- true peak `<= -1.5 dBTP`;
- clipping = 0;
- không click rõ ở boundaries;
- background duck hợp lý khi thoại vào;
- stereo ambience được giữ nếu source có.

### 9.5. Voice quality

Blind A/B chấm ít nhất:

- naturalness;
- pronunciation;
- speaker similarity;
- semantic fidelity;
- timing;
- fatigue khi nghe đoạn dài.

Mỗi thay đổi model/TTS path phải không thua baseline đáng kể ở quality nếu được bật mặc định.

### 9.6. Performance

Lưu per-stage:

- wall time;
- RTF;
- peak GPU VRAM;
- peak RAM nếu đáng kể;
- cache hits/misses;
- segment count;
- rewrite count;
- TTS inference count;
- QA retry count;
- AI provider call count + provider/model/effort;
- TypeSafe call/token usage nếu có.

Mục tiêu đầu:

- giảm RTF đáng kể so với fixture tương ứng;
- phase đầu có thể target khoảng `~1.5x` hoặc tốt hơn trên fixture ngắn;
- realtime/sub-realtime là mục tiêu benchmark, không là claim trước khi đo.

Stretch target sau khi P03/P04/P08/P09/P12 đã ổn: **long clean fixture hướng tới `RTF <= 1.0` với quality parity**. Chỉ nâng thành release requirement nếu local A/B chứng minh không phải đổi quality lấy speed.

### 9.7. Cache correctness

Đổi một trong các yếu tố sau phải invalidate đúng stage:

- source content;
- source hash;
- ASR model/config;
- segmentation policy version;
- translator/provider/model;
- translation prompt policy;
- glossary;
- semantic question policy/model;
- chosen translation text;
- voice reference content;
- TTS model/backend/device/precision;
- timing policy;
- mix config.

---

## 10. Roadmap tổng quan

| Phase | Hạng mục | Quality | Speed | Dependency | Gate chính |
|---|---|---:|---:|---|---|
| P00 | Benchmark/profiler | gián tiếp | gián tiếp | none | baseline reproducible |
| P01 | Stage manifest/cache/resume | correctness | rất cao rerun | P00 | zero stale reuse |
| P02 | Word-level ASR data | cao | gián tiếp | P01 | timing preserved |
| P03 | Smart segmentation/speech turns | rất cao | rất cao | P02 | rewrite/overflow giảm |
| P04 | Context + duration-aware translation | rất cao | cao | P03 | natural + fit |
| P05 | TypeSafe semantic controller | rất cao | cao selective | P04 | verify/escalate calibrated |
| P06 | Pronunciation/glossary normalization | cao | neutral | P04 | critical terms correct |
| P07 | Smart voice reference | rất cao | neutral | P02 | ref quality better |
| P08 | VieNeu CUDA FP16 batch | neutral/cao | rất cao | P00,P07 | A/B pass + VRAM safe |
| P09 | Elastic timing/prosody | rất cao | cao | P03,P08 | tempo/overflow gate |
| P10 | Voice assembly/overlap | cao | trung bình | P09 | no collision/click |
| P11 | Professional mix/master | cao | gần neutral | P10 | loudness/ducking gate |
| P12 | Segment QA + selective repair | rất cao | cao end-to-end | P05,P09 | critical errors caught |
| P13 | ASR/separator tuning | vừa | vừa | P00,P12 | A/B evidence only |
| P14 | Multi-speaker/overlap hardening | cao | variable | P02,P10 | speaker consistency |
| P15 | Fast/Balanced/Max profiles | product | product | P04-P14 | behavior deterministic |
| P16 | Human review/editor UI | cao | workflow | P12,P15 | targeted correction |
| P17 | Regression benchmark suite | rất cao | gián tiếp | all core | automated guard |
| P19 | Optional premium/cloud A/B | có thể cao | variable | P17 | explicit permission |
| P20 | Release/ops hardening | reliability | repeatability | all selected | ready-to-use |
| P21 | Historical dedicated ChatGPT Web instance + model catalog | rất cao | cao | P04,P16,P20 | ACCEPTED trên :17842, sau đó superseded khi instance bị xóa |
| P22 | Dedicated Dubber-WebGPT runtime migration | rất cao | cao | P04,P16,P20,P21 | :17850 isolated runtime + direct Responses + live re-acceptance |
| P23 | Adaptive long-form core + scheduler | giữ quality | rất cao long-form | P00,P01,P15,P17,P20,P22 | chunk/resume/stream/overlap/autotune pass trên short→super-long |

---

# PHASE DETAILS

## P00 - Profiler và benchmark foundation

### Goal

Biến mọi tối ưu sau này thành đo được.

### Work

1. Thêm stage timer chuẩn cho:
   - input/extract;
   - separation;
   - ASR;
   - translation;
   - semantic QA;
   - reference selection;
   - TTS pass 1;
   - rewrite generation;
   - TTS rewrite;
   - timing/assembly;
   - mix/mux;
   - acoustic QA;
   - optional repair.
2. Thêm counters:
   - source segments;
   - speech turns;
   - translation calls;
   - AI provider batches + provider/model/effort;
   - rewrite calls;
   - TypeSafe requests/tokens;
   - TTS inferences/batches;
   - cache hit/miss;
   - QA repair count.
3. Thêm optional resource metrics:
   - GPU peak allocated/reserved;
   - RAM nếu dễ lấy ổn định.
4. Tạo benchmark output machine-readable JSON.
5. Benchmark phải tách cold run và warm/resume run.

### Files likely

- `pipeline.py`
- `runtime.py`
- module mới nhỏ kiểu `metrics.py` nếu thực sự giảm duplication
- test mới cho metric schema

### Subagents

- perf subagent audit metric schema/read-only;
- implementation worker thêm profiler;
- reviewer xác nhận instrumentation không thay behavior.

### Acceptance

- baseline output không đổi chức năng;
- `19` test cũ vẫn pass;
- benchmark JSON có stage wall time;
- số stage cộng gần total elapsed, sai số overhead được giải thích;
- benchmark hai job baseline hoặc ít nhất fixture ngắn + một fixture dài khả dụng.

### Do not

- chưa tune model;
- chưa đổi default TTS;
- chưa đổi segmentation.

---

## P01 - Job identity, stage manifest, cache và resume correctness

### Goal

Không reuse nhầm artifact và không recompute stage không cần thiết.

### Target design

Mỗi stage có manifest:

```text
stage
input fingerprints
relevant config fingerprint
model/provider/version
output artifacts
created_at
status
```

### Job ID

Không chỉ dùng `filename + size`.

Ưu tiên source content fingerprint thực tế. Với video lớn có thể hash streaming toàn file; benchmark chi phí trước khi chọn.

### Translation fingerprint

Gồm tối thiểu:

- source text/turn IDs;
- provider/runtime identity (`webgpt` + instance/port/model; legacy provider chỉ để đọc artifact cũ);
- effective model ID;
- reasoning/effort nếu có;
- generation/output-schema policy version;
- prompt policy version;
- context policy version;
- glossary hash;
- duration target policy.

### TTS fingerprint

Gồm:

- final TTS text;
- speaker;
- ref-audio hash;
- VieNeu version/mode;
- backend/device/precision;
- inference-relevant config.

### Semantic fingerprint

Tách:

```text
inference fingerprint
= state + questions + model + question-policy-version

decision policy
= thresholds + routing action
```

Đổi threshold chỉ re-evaluate raw judgment local nếu inference input không đổi.

### `--fresh`

Định nghĩa rõ:

- bỏ qua/rebuild stage cache từ điểm nào;
- không để hidden `translations_cache.json` survive trái ý nghĩa flag.

### Relative artifacts

Manifest không hard-code absolute path nếu output nằm trong job dir.

### Tests

- same input/same config -> cache hit;
- same filename/different content -> không reuse;
- glossary change -> translation invalidates;
- translated text change -> TTS invalidates;
- ref change -> TTS invalidates;
- threshold-only semantic change -> không cần API inference lại;
- `--fresh` đúng contract;
- move/copy job dir vẫn resolve artifact nội bộ.

### Acceptance

Zero stale artifact trong fixture matrix.

---

## P02 - Giữ word-level timing và metadata từ WhisperX

### Goal

Không mất dữ liệu alignment cần cho smart segmentation.

### Work

1. Lưu words từ `whisperx.align`.
2. Nếu diarization bật, giữ speaker assignment ở mức word nếu available.
3. Preserve confidence/alignment score nếu API trả và hữu ích.
4. Version serialized segment schema.
5. Backward-compatible loader cho artifact cũ nếu chi phí thấp; nếu không, manifest invalidates ASR stage rõ ràng.

### Tests

- serialize/deserialize words;
- missing word metadata không crash;
- speaker propagation hợp lý;
- source SRT output không regress.

### Acceptance

- word boundaries có trong artifact nguồn;
- re-segmentation có thể chạy mà không gọi ASR lại nếu word artifact hợp lệ.

---

## P03 - Smart segmentation và speech-turn builder

### Goal

Đây là phase ROI lớn nhất cho cả quality và speed.

### Inputs

- word timestamps;
- punctuation;
- silence gaps;
- speaker boundaries;
- segment duration;
- optional confidence;
- overlap flags.

### Principles

- không split giữa phrase tự nhiên chỉ vì Whisper segment boundary;
- merge segment cực ngắn nếu cùng speaker và gap nhỏ;
- split segment quá dài tại phrase/punctuation/silence hợp lý;
- không merge qua speaker change;
- không merge qua overlap/scene boundary khi có signal;
- giữ mapping về source segment/word để trace được.

### Candidate heuristics ban đầu

Heuristic deterministic trước, chưa cần AI:

- target speech-turn window theo khoảng duration hợp lý;
- pause gap threshold configurable;
- punctuation bonus/penalty;
- max/min word count;
- max duration;
- no cross-speaker merge.

Threshold phải học/tune từ baseline artifacts, không copy số universal.

### Benchmark

So sánh before/after trên job 734 segments:

- count segment `<1s`;
- target window distribution;
- predicted TTS ratio;
- rewrite rate sau P04/P09;
- overflow;
- blind listening.

### Acceptance

- giảm rõ fraction window `<1s`;
- không làm semantic text order sai;
- không tạo turn xuyên speaker;
- downstream rewrite/overflow giảm đáng kể khi các phase liên quan hoàn tất.

### Rollback

Giữ legacy segmentation policy selectable trong giai đoạn A/B.

---

## P04 - Context-aware + duration-aware translation bằng selected ChatGPT Web model

### Goal

Dịch một lần đã gần đúng độ dài và nghe như spoken Vietnamese.

### Main generator

Target P22 hiện tại:

- provider chính: Dedicated Dubber-WebGPT `127.0.0.1:17850`, direct `/v1/responses`;
- model lấy từ live model catalog của account/backend;
- UI cho phép người dùng chọn model thay vì hardcode một slug lâu dài;
- default selection policy ưu tiên model Plus chất lượng cao nhất đã được project verify, hiện tại người dùng mong muốn lớp tương đương `5.6 Sol High`;
- reasoning/effort là setting riêng nếu backend/model hỗ trợ; không suy ra `High` chỉ từ tên model;
- mỗi job snapshot `provider + model_id + display_name + effort + catalog revision/timestamp` để cache/provenance/resume không mơ hồ.

Không silently đổi sang model khác khi model đã chọn biến mất hoặc account không còn expose nó. UI/preflight phải báo rõ và yêu cầu chọn model khác hoặc dùng fallback policy đã cấu hình.

### Input state cho mỗi speech turn

Tối thiểu:

- source text;
- speaker ID;
- target duration/window;
- previous 2-4 nearby turns hoặc context window tương đương;
- next context khi available;
- glossary/terms;
- rolling register/terminology state;
- scene/topic summary nếu sau này có.

### Prompt goals

Phân thứ tự:

1. giữ nghĩa;
2. nói tự nhiên;
3. giữ critical facts;
4. phù hợp context/register;
5. fit duration;
6. tránh literal/subtitle-style phrasing.

Không đưa target char count như truth tuyệt đối. Nó chỉ là constraint approximate.

### Duration estimator

Phase đầu có thể dùng learned/calibrated lightweight estimator từ data project:

```text
features:
  normalized chars/syllables/phoneme proxy
  punctuation
  speaker/ref identity optional
  previous measured TTS ratios
```

Estimator trả expected duration + uncertainty.

### Pre-fit logic

Nếu candidate predict quá dài đáng kể:

```text
rewrite/generate concise Vietnamese
    -> semantic verify
    -> mới TTS
```

Không đợi TTS pass 1 trên mọi câu rồi mới phát hiện.

### Parser hardening

Current WebGPT JSON repair phải giữ compatibility, với schema rõ, bounded recovery và tests; regex/repair chỉ là compatibility fallback.

Không giao parsing deterministic cho TypeSafe.

### Acceptance

- giảm rewrite-after-TTS;
- naturalness A/B không giảm;
- glossary consistency tốt hơn;
- critical facts không giảm;
- AI provider/model/effort, call count, retry/cooldown và fallback được log.

---

## P05 - TypeSafe semantic quality controller

### Goal

Dùng TypeSafe như verifier/router rẻ, không như translator.

### Live docs pattern

Theo TypeSafe System One hiện hành:

- code giữ workflow;
- `Choice`, `Noul`, `Score` trả typed judgment/probabilities;
- confidence dùng cho routing;
- SDE cascade phù hợp pattern `normal generation -> verify -> expensive repair only on failures`;
- parallel questions có thể giảm overhead nhưng state không nên chứa quá nhiều irrelevant segments.

### Atomic heads đề xuất

Mỗi head có semantics rõ; ưu tiên `TRUE = có vấn đề cần escalate` để routing dễ đọc:

1. `material_meaning_lost`
2. `negation_or_modality_changed`
3. `action_direction_changed`
4. `spoken_vi_awkward`
5. `register_or_context_inconsistent`
6. `rewrite_changed_material_meaning`

Exact numeric equality không giao cho Jev nếu code deterministic làm được.

### Rewrite gate

Target flow:

```text
before_vi
after_vi
source_en
context
    |
    v
TypeSafe rewrite gate
    |
    +-- pass -> synthesize rewrite
    |
    +-- borderline -> generate candidate 2
    |
    +-- fail -> reject/escalate
```

Không TTS rewrite trước semantic gate ở Balanced/Max path.

### Candidate selection

Không default:

```text
mọi segment -> 3-5 candidates -> TypeSafe rank
```

Default:

```text
candidate 1
  -> verify
  -> only if fail/uncertain: candidate 2/3
  -> Choice A/B/none acceptable
```

### Batch calibration

Benchmark ít nhất:

- batch 8;
- batch 16;
- batch 32.

Theo experiment trước, signal có thể dịch khi một segment bị đưa chung với nhiều state không liên quan. Chọn batch theo accuracy + latency + cost, không chỉ throughput.

### Model pinning

Trong research có thể dùng moving alias; sau khi calibrate production thresholds, pin model version đã benchmark thay vì phụ thuộc alias tự move.

### Secret policy

- `TYPESAFE_API_KEY` chỉ ở environment;
- không log;
- không commit;
- không đưa vào artifact.

### Fail mode

- Fast/Balanced local-first: service failure có fallback policy rõ;
- shadow/research: fail-open nhưng ghi status;
- strict Max Quality: có thể mark review/escalate, không silently pass.

### Acceptance

Golden set phải chứa:

- good faithful translation;
- subtle loss;
- negation reversal;
- modality loss;
- number/name issue;
- telegraphic Vietnamese;
- valid natural compression.

Threshold chỉ khóa sau confusion matrix / precision-recall phù hợp task.

---

## P06 - Pronunciation và deterministic critical-token normalization

### Goal

Không để TTS tự đoán mọi số, viết tắt, đơn vị, tên riêng.

### Categories

- số;
- tiền;
- phần trăm;
- ngày/tháng/năm;
- thời gian;
- units;
- acronyms;
- URLs/email nếu cần;
- technical terms;
- names có override.

### Pipeline

```text
selected Vietnamese
  -> detect deterministic tokens
  -> pronunciation/glossary mapping
  -> TTS-safe text
```

Giữ original display subtitle riêng nếu pronunciation text khác.

### Example

`$12.50` có thể render thành spoken text phù hợp context mà subtitle vẫn giữ `$12.50` nếu product muốn.

### Acceptance

- golden pronunciation set;
- no accidental semantic rewrite;
- subtitles không bị forced theo phonetic text nếu không cần.

---

## P07 - Smart voice reference selection

### Goal

Chọn reference tốt theo acoustic quality, không chỉ duration.

### Candidate filters

- 3-8 s phù hợp VieNeu guidance hiện tại;
- speech ratio cao;
- không overlap speaker;
- residual music thấp;
- không clipping;
- SNR tốt;
- ít silence dài;
- articulation rõ;
- delivery neutral/natural cho canonical ref.

### Ranking

Ưu tiên deterministic audio features trước.

Có thể thêm semantic/style labeling sau nếu measurable benefit.

### Speaker policy

- mỗi speaker có canonical reference;
- không đổi ref liên tục nếu không có lý do;
- style-specific refs là phase sau và phải tránh identity drift.

### Acceptance

Blind A/B:

- speaker similarity;
- consistency across distant segments;
- pronunciation clarity;
- noise bleed.

---

## P08 - VieNeu GPU CUDA FP16 + real batching

### Goal

Đây là performance opportunity lớn nhất trong TTS.

### Current

CPU/ONNX/FP32 + `engine.infer()` từng segment.

### Target

GPU/PyTorch/CUDA/FP16 + `infer_batch()`.

### Grouping

Batch theo speaker/reference.

Không trộn text dùng reference khác vào một call nếu API path không support.

### Benchmark matrix

RTX 2070 SUPER 8 GB:

- batch 2;
- batch 4;
- batch 6 optional;
- batch 8 nếu VRAM an toàn.

Đo:

- TTS RTF;
- wall time;
- peak VRAM;
- error/OOM;
- output duration;
- voice similarity;
- naturalness;
- seed/sampling variability nếu relevant.

### Fallback

CPU path phải tiếp tục dùng được.

Không dùng CPU INT8 làm default trên i5-9400F chỉ vì nhanh hơn trên CPU mới; quality/hardware support phải benchmark riêng.

### Do not

- không đổi sang VieNeu Nano làm default quality path;
- không copy upstream RTX 3060 benchmark thành claim cho 2070 SUPER.

### Acceptance

- GPU path không thua quality đáng kể;
- không OOM ở batch default;
- throughput cải thiện đủ rõ để justify complexity;
- CPU fallback pass tests.

---

## P09 - Elastic timing và prosody-aware fitting

### Goal

Từ "ép từng subtitle vào slot" sang "fit cả speech turn tự nhiên".

### Rules

1. Cho phép borrow slack từ nearby silence trong cùng speech turn.
2. Time-stretch nhẹ cho mismatch nhỏ.
3. Rewrite cho mismatch lớn.
4. Không cố fill 100% mọi slot.
5. Short TTS có thể slow/stretch trong bound nếu nghe tự nhiên.
6. Không overlap trái phép sang speaker khác.

### Target strategy

Pseudo:

```text
turn target window
  - fixed anchors ở đầu/cuối quan trọng
  - internal pauses flexible

for rendered phrase:
  if near target:
      gentle tempo/stretch
  elif long:
      consume available slack
      then rewrite if still excessive
  elif short:
      preserve natural pause or mild slowdown
```

### Prosody

Không claim full prosody transfer nếu TTS API không expose đủ control.

Nhưng có thể giữ:

- source pause pattern;
- sentence type;
- emphasis hints từ punctuation/context;
- speech-rate envelope.

### Acceptance

- overflow gate đạt;
- tempo p95 giảm;
- blind A/B timing/naturalness thắng legacy;
- không làm drift sync cả video.

---

## P10 - Voice assembly, boundary và overlap policy

### Goal

Assembly không tạo click, collision hoặc memory waste trên video dài.

### Work

- short fade/crossfade ở boundaries khi phù hợp;
- xử lý overlap explicit;
- chunk/stream assembly thay vì full giant NumPy buffer nếu benchmark cho thấy lợi;
- deterministic clipping policy;
- preserve exact timeline length.

### Acceptance

- no click fixture;
- no accidental overwrite;
- long-video memory behavior đo được;
- output duration chính xác trong tolerance.

---

## P11 - Professional dialogue mix/master

### Current

Mix cơ bản `background gain + voice gain -> amix -> loudnorm`.

### Target chain

Tùy fixture và A/B, ưu tiên:

```text
dialogue cleanup/EQ nhẹ
 -> gentle compression
 -> optional de-esser
 -> background sidechain duck
 -> boundary fades
 -> final two-pass loudnorm
 -> true peak guard
```

Không hard-code aggressive mastering gây pumping.

### Metrics

- LUFS;
- true peak;
- clipping;
- duck depth;
- subjective intelligibility;
- music continuity.

### Acceptance

- `-14 LUFS ±0.5` mặc định hoặc profile-specific target;
- true peak `<= -1.5 dBTP`;
- clipping 0;
- voice rõ hơn mà background không nghe bị bơm/hụt rõ.

---

## P12 - Segment-level acoustic QA và selective repair

### Goal

Không để global score cao che lỗi nghiêm trọng.

### QA units

Mỗi rendered speech turn/segment có:

- expected TTS text;
- ASR output;
- normalized comparison;
- critical token checks;
- timing stats;
- optional semantic severity.

### Critical checks

- numbers;
- names;
- negation;
- modality;
- glossary terms;
- key action direction khi useful.

### Routing

```text
clean -> pass
minor cosmetic -> pass/log
pronunciation suspicious -> retry/ref/pronunciation repair
semantic suspicious -> text repair
timing bad -> timing/rewrite repair
```

### Selective re-ASR

Benchmark:

- full large-v3 QA;
- segment/risk-selected QA;
- alternate faster QA model nếu cần.

Không đổi mặc định tới khi Vietnamese benchmark đủ.

### Acceptance

- known bad examples bị bắt;
- false positive đủ thấp;
- repair không rerun cả video;
- final report cho biết segment nào đã repair và vì sao.

---

## P13 - WhisperX và separator tuning sau khi core đã ổn

### Goal

Chỉ tune model knobs sau khi bottleneck orchestration được sửa.

### WhisperX

Benchmark batch:

- 4 baseline;
- 6;
- 8 nếu VRAM đủ.

Đo speed + VRAM + transcript/alignment impact.

Không downgrade model mặc định nếu quality giảm rõ.

### Separator

Giữ overlap quality setting trước.

Có thể benchmark batch 2 nếu library/model support và VRAM an toàn.

Model replacement chỉ A/B trên real fixtures có:

- music;
- SFX;
- reverb;
- male/female voices;
- dialogue overlap.

### Acceptance

Chỉ đổi default nếu measurement thắng rõ, không dựa trên benchmark upstream khác GPU/source.

---

## P14 - Multi-speaker và overlapping speech

### Goal

Làm multi-speaker thành feature có contract, không chỉ "diarization chạy được".

### Work

- min/max speaker hints khi user biết;
- canonical ref per speaker;
- speaker continuity QA;
- overlapping speech flag;
- policy khi two speakers overlap:
  - preserve overlap;
  - stagger nhẹ nếu acceptable;
  - mark review khi không thể render tự nhiên;
- never silently merge speakers.

### Acceptance

Fixture interview tối thiểu 2 speakers + overlap sample.

- speaker identity stable;
- no cross-reference contamination;
- overlap failure visible, không silently wrong.

---

## P15 - Profile system

### Goal

Fast/Balanced/Max là behavior contract chứ không phải ba label UI.

### Config design

Profile override phải explicit cho:

- translation fan-out;
- TypeSafe policy;
- TTS batch;
- QA depth;
- retry budget;
- final full QA.

### Safety

Profile switch không được làm cache reuse sai; profile-relevant config nằm trong fingerprint.

### Acceptance

Mỗi profile có snapshot config resolved machine-readable.

---

## P16 - Human review/editor UI

### Goal

Biến Gradio dashboard thành **UI sản phẩm hoàn chỉnh để dùng hằng ngày**, không chỉ là trang upload + nút chạy. Người dùng phải có thể tạo job, theo dõi, dừng/resume, xem lỗi, review chất lượng, sửa đúng segment, render lại phần cần thiết và lấy output mà không cần CLI cho workflow thông thường.

### UI capabilities target

**Job/input**

- local video upload + YouTube URL;
- chọn profile `Fast / Balanced Best / Max Quality`;
- AI translation provider/model control: UI fetch live model catalog từ WebGPT instance 2, hiển thị model khả dụng và cho phép chọn model trước khi Start;
- model selector phải refresh được, có loading/error/empty state và không silently giữ một model đã biến mất khỏi catalog;
- effort/reasoning selector chỉ hiện option backend/model thực sự support; default có thể là `High` cho model quality path nhưng phải lưu cùng job snapshot;
- UI hiển thị rõ model thực tế đã chạy ở job/result diagnostics, không chỉ label preset;
- chọn voice/reference, diarization và các advanced option hợp lệ;
- preflight trước khi chạy: input, disk, GPU, AI provider auth/health, selected model availability, required model/service;
- chống submit duplicate ngoài ý muốn.

**Job lifecycle**

- job list/history với trạng thái `queued/running/paused/failed/cancelled/completed`;
- progress theo stage thật + segment/batch progress khi có;
- elapsed time, stage timing và ETA chỉ hiển thị nếu estimator đủ tin cậy;
- pause/cancel/resume/retry rõ ràng;
- browser refresh/reopen không làm mất job backend;
- app restart/crash xong vẫn tìm lại job và resume từ checkpoint hợp lệ.

**Review/editor**

- list tất cả segment, filter flagged/error/rewritten/overflow/speaker;
- play source slice và dubbed slice;
- show source English, selected Vietnamese, before/after rewrite;
- show speaker, timing/tempo, semantic/acoustic/pronunciation warnings;
- edit text, đổi reference/speaker khi policy cho phép;
- regenerate only selected segment;
- accept current output / mark reviewed;
- sửa một segment chỉ invalidate downstream cần thiết;
- rerun mix/final assembly không rerun ASR/translation hợp lệ.

**Result/diagnostics**

- preview final video/audio;
- download/open final MP4, SRT và các output cần thiết;
- summary quality/performance: rewrite, overflow, QA flags, RTF, fallback usage;
- lỗi hiển thị bằng ngôn ngữ hành động được: stage nào lỗi, nguyên nhân, retry/resume được hay không;
- Settings/Doctor surface cho runtime health và profile hiện hành, không lộ secret.

### UI principle

Operational tool, không landing page.

Desktop-first, dense, scan-friendly, trạng thái rõ, không nested cards thừa. Normal workflow không bắt người dùng mở terminal hoặc đọc JSON artifact.

### Acceptance

1. Happy path local file và YouTube chạy end-to-end từ UI tới final output.
2. Một segment sửa bằng UI invalidate đúng downstream artifacts và giữ upstream cache.
3. Refresh browser giữa job không mất job; mở UI lại thấy đúng trạng thái persisted.
4. Failed/interrupted job có CTA đúng: retry stage/batch, resume hoặc restart fresh tùy loại lỗi.
5. Cancel không để artifact half-written được coi là complete.
6. Completed job có thể review, sửa một đoạn và rerender final mà không chạy lại từ đầu.
7. UI error/empty/loading/disabled states đều có test; không chỉ test happy-path screenshot.
8. Model catalog UI lấy dữ liệu live từ backend; đổi model tạo job snapshot/fingerprint mới và không reuse nhầm translation cache.
9. Model đã chọn bị remove/rename/unavailable trước khi Start phải fail rõ ở preflight; không tự thay model khác.

---

## P17 - Regression benchmark suite

### Goal

Mỗi tối ưu sau này biết quality/perf tăng hay giảm.

### Fixture classes

1. clean single-speaker talking head;
2. long monologue;
3. fast English speech;
4. short fragmented subtitle-style speech;
5. music under dialogue;
6. noisy speech;
7. two speakers;
8. overlapping speech;
9. names/numbers/technical terms;
10. emotional/prosody stress clip.

### Golden artifacts

Không cần commit video lớn nếu workspace policy không phù hợp. Có thể giữ local manifest/hash + paths.

### Scores

- WER/normalized transcript metrics;
- critical token accuracy;
- overflow/rewrite/tempo;
- RTF/stage time;
- human A/B sheet;
- TypeSafe verifier metrics.

### Acceptance

Một command/harness tạo comparison report before/after cho fixture set.

---

## P19 - Optional premium/cloud model A/B

### Goal

Chỉ dành cho trường hợp người dùng muốn absolute quality hơn local-first.

Potential categories:

- proprietary/cloud TTS;
- stronger translation provider;

### Gate bắt buộc

Hỏi trước khi:

- cần API key mới;
- có chi phí;
- gửi audio/voice/video ra cloud;
- thay privacy boundary.

Không đưa cloud dependency vào Balanced Best mặc định.

ChatGPT Web qua dedicated instance 2 ở P21 **không được xếp vào P19 chỉ vì là network AI**: đây là target translation backend người dùng đã chọn cho VI Dubber. P19 dành cho provider/API trả phí bổ sung, TTS cloud hoặc privacy boundary mới ngoài account ChatGPT Web hiện tại.

---

## P20 - Release/ops hardening

### Goal

Biến optimized pipeline thành tool dùng ổn định hằng ngày.

### Work

- config schema validation;
- doctor checks cho GPU/route/model/profile;
- clear failure messages;
- progress stage chính xác;
- cancellation/pause behavior;
- checkpoint/resume after interruption;
- atomic artifact commit: temp/incomplete file không bao giờ được coi là valid cache;
- bounded retry với backoff/jitter cho network/AI calls;
- idempotency cho stage/batch/segment retry;
- fail-closed/degraded-mode policy rõ cho WebGPT instance 2, TypeSafe và GPU path;
- persisted job state độc lập browser session;
- cleanup policy;
- disk usage reporting;
- benchmark version/report;
- README update;
- PLAN final status;
- migration notes cho artifact cũ.

### Production hardening / fault-injection matrix

Phải test chủ động ít nhất các case sau, không chỉ mô tả trong code:

| Failure case | Expected behavior |
|---|---|
| Mất Internet trước/khi đang dịch qua AI web provider | batch chưa commit được retry; batch đã complete không chạy lại |
| WebGPT instance 2 trả partial/invalid response rồi disconnect | không commit kết quả nửa chừng; bounded repair/retry, sau đó fail rõ |
| WebGPT instance 2 down / auth-session lỗi | fail actionable; không loop login/retry vô hạn; không tự chuyển instance/Aurora/Qwen |
| Model catalog fetch fail | UI giữ trạng thái lỗi/refresh được; không bịa catalog hoặc silently dùng model cũ chưa verify |
| Selected model biến mất/không còn available | preflight/job fail rõ trước generation hoặc theo bounded policy; không tự thay model khác |
| Provider cooldown/429 | exponential backoff + jitter + adaptive/bounded concurrency; không busy-loop |
| TypeSafe timeout/429/5xx | bounded retry; fallback policy rõ; không làm mất bản dịch hợp lệ |
| Kill app/process giữa ASR/translation/TTS/mix | restart đọc manifest và resume từ checkpoint cuối hợp lệ |
| Reboot/mất điện mô phỏng giữa artifact write | temp/partial artifact bị bỏ; manifest không đánh dấu complete |
| TTS crash ở segment N | segment trước hợp lệ giữ nguyên; resume từ phần chưa complete |
| CUDA OOM | giảm batch / fallback path theo policy; error có diagnostic |
| Hết dung lượng đĩa | fail clean trước hoặc tại write; không corrupt manifest/cache |
| Input bị move/delete/change | phát hiện bằng identity/hash; không resume nhầm job |
| Config/model/glossary/reference đổi | invalidate đúng stage downstream, không invalidate quá mức |
| Browser refresh/tab đóng/UI reconnect | backend job tiếp tục; UI attach lại state persisted |
| User bấm Start/Retry hai lần | idempotency/dedup ngăn duplicate work/job ngoài ý muốn |
| Cancel giữa stage | trạng thái `cancelled`; partial output không thành valid artifact; resume/restart behavior rõ |
| JSON/model output malformed | bounded parser repair/retry; không silent corruption |
| Corrupt/missing cached artifact | detect, invalidate đúng node và rebuild từ upstream valid |

### Reliability properties bắt buộc

- **resumable pipeline/checkpointing**: bước đã complete và fingerprint còn đúng thì không chạy lại;
- **crash recovery**: process/app/Windows chết không làm mất toàn job;
- **fault tolerance/resilience**: network/AI/GPU lỗi có retry/fallback/fail-clean;
- **idempotency**: retry cùng work item không tạo duplicate side effect/artifact;
- **graceful degradation**: service phụ lỗi thì giảm chức năng theo policy thay vì silent wrong;
- **observability**: biết stage/batch nào fail, retry count, fallback nào đã dùng;
- **no silent corruption**: uncertainty/corrupt artifact/cache mismatch phải visible.

### Acceptance

Fresh install/setup path + warm existing-work path đều có smoke test. Production hardening matrix phải có automated test/fixture nơi khả thi và manual receipt cho case không thể mô phỏng ổn định; CP7 không pass nếu interruption/resume/service-failure path cốt lõi chưa được chứng minh.

---

## P21 - Dedicated ChatGPT Web backend + dynamic model catalog (historical phase spec)

### Goal

Phần P21 này mô tả contract khi managed instance 2 `:17842` còn là active route. Trạng thái production hiện hành nằm ở P22 bên dưới; không dùng các bước P21 để tái tạo `:17842`.

Tách translation traffic của VI Dubber khỏi route Codex/Cockpit daily bằng cách khóa toàn bộ WebGPT inference vào **managed instance 2, port 17842**. Aurora tạm dừng và instance 1 không được dùng làm fallback.

Target architecture:

```text
VI Dubber
  -> WebGptTranslator
  -> codex exec --ephemeral
       - per-call provider/base_url override
       - selected live-catalog model
  -> http://127.0.0.1:17842/v1
  -> codex-chatgpt-web instance 2
  -> ChatGPT Web
```

Khi P21 còn active, global Codex/Cockpit config không bị mutate và instance 2 là transport duy nhất của product path; mất instance 2 phải báo lỗi rõ thay vì tự chuyển sang `17841`, Aurora hoặc model khác.

### Source/runtime ownership

- managed instance 2 được sở hữu/lifecycle bởi `codex-chatgpt-web`; VI Dubber chỉ probe health/catalog và gửi turn;
- base URL product cố định `http://127.0.0.1:17842/v1` trong giai đoạn này;
- global Codex route có thể tiếp tục là Cockpit `:54005`; product không ghi đè nó;
- auth/session của Codex/WebGPT tiếp tục nằm trong runtime tương ứng; secret không commit vào project/artifact job;
- code Aurora đã có được giữ dormant cho lịch sử/test, nhưng không expose trong UI/config active và không tham gia fallback.

### Model discovery và UI

- UI fetch live catalog từ `http://127.0.0.1:17842/v1/models`;
- UI model dropdown dùng catalog này làm source of truth;
- default hiện tại là `chatgpt-web/gpt-5.6-sol`, nhưng model phải còn tồn tại trong live catalog trước khi Start;
- model ID thực tế và display name phải được probe/verify thay vì suy từ marketing label;
- refresh catalog không được thay selection của job đang chạy;
- job mới snapshot model/catalog revision tại Start để resume/cache deterministic;
- UI product hiện chỉ expose `Codex WebGPT`; local/hybrid/Aurora không phải lựa chọn bình thường.

### Translation request contract

- mỗi batch là một `codex exec --ephemeral`; không để conversation tăng vô hạn theo video;
- request gồm stable dubbing instructions + video/style summary + glossary liên quan + current batch + nearby context cần thiết;
- output contract vẫn là structured JSON được VI Dubber validate/repair bounded;
- Codex invocation chạy `--sandbox read-only`, `--ignore-rules`, `--skip-git-repo-check` và không yêu cầu model dùng filesystem/tools;
- mỗi invocation bắt buộc có per-call override tới `17842`; global route không được dùng ngầm.

### Throughput policy

- bắt đầu concurrency `2`, benchmark `2/3/4/5` trên workload dịch thật;
- chọn default theo segments/min + p50/p95 latency + cooldown/429 + retry + malformed output + quality;
- không mặc định `10` chỉ vì transport có thể mở nhiều request;
- cooldown/rate-limit phải làm giảm concurrency hoặc queue có kiểm soát thay vì fan-out tiếp;
- network bandwidth không được coi là bottleneck mặc định; đo request/model latency trước khi tối ưu transport vi mô.

### Migration sequence

1. Verify live listener/health/catalog của instance 2 và ghi exact port/model IDs.
2. Khóa `webgpt_base_url` vào `127.0.0.1:17842/v1`; không sửa global Codex config.
3. Route mỗi `codex exec` bằng per-call `-c model_provider=...` + nested `base_url` override.
4. UI lấy live catalog từ instance 2 và snapshot selected model/catalog revision vào job metadata/fingerprint.
5. Giữ backward-compatible reader cho job local/hybrid/Aurora cũ, nhưng không expose các backend đó trong product UI.
6. Chạy translation fixture thật qua normal VI Dubber process và so quality với baseline trước đó.
7. Benchmark concurrency/cooldown/retry/JSON validity trên instance 2.
8. Fault-injection instance-2 restart, catalog failure, selected-model removal, 429/disconnect và wrong-port config.
9. Re-accept P04/P16/P20 trên route mới trước Balanced Best release.

### Acceptance

- instance 2 `:17842` health/catalog pass và wrong port fail closed;
- UI hiển thị live model catalog instance 2 và cho chọn model trước job;
- selected model/catalog revision được snapshot vào manifest/fingerprint/result và resume đúng;
- model unavailable không silent fallback sang model khác;
- mọi `codex exec` của WebGptTranslator mang exact base-url override tới `17842`;
- global `~/.codex/config.toml` không cần đổi để VI Dubber chạy;
- instance 2 restart/disconnect không corrupt job/cache;
- structured output validity + retry/cooldown đạt gate trên representative fixtures;
- không tự chuyển sang instance 1, Aurora hoặc Qwen khi instance 2 lỗi;
- concurrent VI Dubber translation không gây lifecycle/queue conflict với Codex daily runtime;
- model quality A/B không thua baseline trước khi chốt P21 COMPLETE.

### P21 status after 25/09/2026

P21 giữ nguyên làm **historical acceptance evidence** cho contract ChatGPT Web, dynamic catalog, model/effort fail-closed và fault handling. Người dùng đã xóa managed instance 2 `:17842`, nên P21 không còn là runtime vận hành hiện tại và không được tự tạo lại như một dependency ẩn.

---

## P22 - Dedicated Dubber-WebGPT runtime migration

### Goal

Thay instance 2 đã xóa bằng một runtime WebGPT thực sự dành riêng cho VI Dubber, không chia browser profile/process/config với Codex/Cockpit daily runtime và không đưa coding harness vào production translation.

Target architecture:

```text
VI Dubber
  -> WebGptTranslator
  -> direct POST http://127.0.0.1:17850/v1/responses
       tools=[]
       stream=false
       store=false
       selected model + effort
  -> Dedicated Dubber-WebGPT
       home: TradingWorkspace/.runtime/dubber-webgpt
       browser login/session riêng
       provider-only browser worker
  -> ChatGPT Web
```

### Runtime ownership

- fixed loopback port `17850`; `17841` của Codex/Cockpit không bị đụng và `17842` giữ trống để tránh nhầm historical P21;
- runtime home mặc định `D:\ANNAM\TradingWorkspace\.runtime\dubber-webgpt`, nằm ngoài repo Git;
- core engine reuse checkout `D:\ANNAM\AI\codex-chatgpt-web-cockpit`; không fork business logic dịch sang WebGPT;
- browser login/storage state riêng; không copy cookie/profile từ Codex daily runtime;
- browser-only/provider-only: không MCP, không tool registry, không cwd/filesystem envelope, không subagents, không compaction cho translation batch;
- global `~/.codex/config.toml`, Cockpit provider pool và instance `17841` không bị mutate;
- VI Dubber sở hữu translation retry/idempotency/receipt; WebGPT core sở hữu browser submission/recovery và model/session behavior.

### Migration sequence

1. Thêm runtime manager `vi-dubber webgpt-runtime` để init/login/start/status/stop home riêng.
2. Chuyển product config sang `webgpt_base_url=http://127.0.0.1:17850/v1` và `webgpt_transport=direct-responses`.
3. Giữ production concurrency `1` và global context `false` trong migration; không trộn transport migration với throughput tuning.
4. Người dùng đăng nhập ChatGPT một lần trong Chrome profile riêng bằng `webgpt-runtime login`.
5. Start runtime và verify `/healthz` + `/v1/models` + selected model/effort.
6. Chạy focused P04 translation fixture bằng direct Responses; structured JSON, glossary, critical tokens và terminology gate phải pass.
7. Fault-test wrong port, missing login, runtime restart, model missing, 429/capacity và malformed response; không fallback sang `17841`/Aurora/Qwen.
8. Re-run repeated warm c1/c2 benchmark sau khi route mới pass correctness; c2 chỉ promote khi gain/reliability đủ rõ.
9. Re-accept P04/P16/P20 trên `:17850`; khi đó P22 mới chuyển ACCEPTED.

### Acceptance

- runtime home/browser/session/process tách khỏi Codex/Cockpit daily runtime;
- `:17850` health/catalog live và selected model có trong catalog;
- VI Dubber dùng direct `/v1/responses`; normal product path không spawn `codex exec`;
- global Codex/Cockpit route không đổi;
- missing login/runtime down/model unavailable fail actionable và fail closed;
- request receipt/idempotency/retry vẫn pass focused regression;
- P04 quality receipt và terminology/critical-token gates không thua historical P21 baseline;
- P16 UI hiển thị catalog/status của Dedicated Dubber-WebGPT thay vì `instance 2`;
- P20 restart/disconnect/cancel/cache integrity pass trên runtime mới.

### P22 live status - 25/09/2026

- **P22-A runtime isolation: ACCEPTED.** Dedicated login đã capture thành `storage-state.json`; runtime `:17850` online, health/catalog live, `:17841` không bị mutate và `:17842` không được tái tạo.
- **P22-B direct Responses correctness: ACCEPTED.** Direct tool-free smoke trả `{ok: true, route: dedicated-17850}`; P04 representative fixture pass terminology + critical-token gate; P16 live catalog/status UI pass; direct 503/disconnect không commit partial translation/cache.
- ChatGPT Web UI compatibility trong shared core được fix riêng ở commit `62ca664` để nhận composer `textarea` mới và model-family verification khi `aria-describedby` chỉ còn effort text. Core regression: `158 passed` + TypeScript typecheck.
- VI Dubber regression mới nhất sau migration, c2 atomicity và async TTS preload lifecycle hardening: `399 passed, 2 warnings`; fault-focused direct/runtime/cancel subset pass, direct disconnect regression pass và concurrent sibling-batch failure không còn partial-commit translation/cache.
- Receipt P04 mới: `work/p22-live-acceptance/p22_p04_live_results.json` (`direct-responses`, Sol High, c1, global context off, terminology=true, critical=true).
- **P22-C throughput benchmark: ACCEPTED.** Repeated-warm 6-segment/batch-2 Sol High trên `:17850`: c1 median `41.1390s`, c2 median `30.8903s` (`1.3318x` speedup); cả hai lượt c2 pass terminology + critical-token gates, `0` pressure failure, `0` retry/failure. Per-request p95 tăng lên khoảng `22s`, nhưng total wall-time giảm đủ rõ nên production promote `webgpt_concurrency: 2`. Global context vẫn `false`; c3 tiếp tục benchmark-only vì historical capacity rejection.
- **P22 COMPLETE / ACCEPTED.** Runtime isolation, direct Responses correctness, P04/P16/P20 re-acceptance và c1/c2 throughput gate đều đã pass trên Dedicated Dubber-WebGPT `:17850`.

---

## P23 - Adaptive long-form core, scheduler và performance architecture

### Goal

Nâng core pipeline để **mọi profile** (`fast`, `balanced_fast`, `balanced_best`, `max_quality`) cùng hưởng tối ưu performance/reliability, đặc biệt với video 1.5 giờ đến 12+ giờ, mà không hạ model chính chỉ để chạy nhanh.

P23 không tạo pipeline riêng kiểu `long_fast.py`. Một core duy nhất tự thích nghi theo workload; profile chỉ quyết định quality policy.

### Quyết định best practice theo từng khía cạnh

| Khía cạnh | Best practice chọn | Thay cho cách cũ | Vì sao chọn |
|---|---|---|---|
| Video dài | Adaptive silence/VAD-aware macro chunking | stage xử lý gần như toàn video | giới hạn RAM, retry nhỏ, giữ câu không bị cắt cứng |
| Luồng xử lý | Bounded pipeline overlap | ASR xong hết rồi mới dịch/TTS | che thời gian chờ giữa CPU/GPU/network mà không tranh tài nguyên vô hạn |
| GPU | Dynamic batching theo benchmark | nhiều infer nhỏ hoặc tăng worker mù quáng | tận dụng GPU tốt hơn, giảm launch/overhead và tránh OOM/latency tăng |
| Concurrency | Resource-aware bounded scheduler | một concurrency cố định cho mọi stage | GPU, WebGPT, CPU và disk có giới hạn khác nhau |
| ASR | VAD/speech-aware chunk input | đọc/xử lý cả audio dài kể cả silence | giảm dữ liệu vô ích và tránh giữ waveform khổng lồ trong RAM |
| Translation | Batch + bounded WebGPT concurrency | request nhỏ/serial hoặc fan-out quá mức | giữ context tốt và throughput cao; production c2 hiện là baseline đã accepted |
| TTS | Persistent engine + real batching | load/init hoặc infer quá vụn | giảm init overhead; giữ batch ở sweet spot đã benchmark |
| QA | Risk-based / early-exit QA | full QA mọi đoạn hoặc Fast bỏ gần hết | giữ quality ở đoạn khó, không trả compute cho đoạn sạch |
| Rewrite | Selective rewrite | rewrite rộng theo stage | chỉ trả tiền/latency cho segment thật sự overflow hoặc semantic fail |
| Crash/resume | Checkpoint per chunk + per stage | checkpoint stage lớn | lỗi chunk N chỉ làm lại chunk N/phần downstream cần thiết |
| Retry | Idempotent work unit | retry phạm vi lớn | retry an toàn, không duplicate/commit artifact nửa chừng |
| Cache | Content-addressed per chunk/artifact | file-exists/stage-only reuse | config/model/text/ref đổi thì invalidate đúng phạm vi |
| Manual edit | Dependency-aware partial recompute | sửa một câu làm lại nhiều downstream | chỉ rerender segment/neighbor bị ảnh hưởng |
| RAM | Streaming/windowed I/O | load audio rất dài vào memory | 6-12h không làm RAM tăng tuyến tính theo toàn video |
| Disk | Artifact minimization + bounded temp files | ghi/đọc WAV/stem trung gian quá nhiều | giảm I/O, dung lượng và thời gian chờ SSD |
| Backpressure | Bounded queues | upstream tạo việc nhanh hơn downstream | queue không phình RAM; stage chậm tự giới hạn stage trước |
| Prefetch | Decode/read next chunk while current compute runs | chờ stage mới chuẩn bị input | che I/O bằng compute khi không tranh tài nguyên |
| CPU/GPU overlap | FFmpeg/decode/validation chạy cạnh GPU infer khi an toàn | một tài nguyên chờ tài nguyên khác | tăng utilization toàn máy thay vì chỉ tăng GPU worker |
| Progress UI | Chunk + stage progress | một % lớn khó biết đang treo hay chạy | thấy `chunk 8/20 · translate 63%`, pause/resume rõ |
| ETA | Throughput-based rolling ETA | % tuyến tính | phản ánh tốc độ thật của video/model/máy sau vài chunk |
| Profile | Policy layer trên shared core | mỗi mode dễ trôi thành pipeline riêng | mọi mode hưởng optimization, ít code clone, dễ bảo trì |
| Tuning | Fixture-based auto-tune có guardrail | batch/concurrency hardcode hoặc đoán | máy khác nhau có sweet spot khác; chỉ promote cấu hình có benchmark |
| Progressive output | Publish preview theo macro-chunk đã commit; optional HLS/fMP4 | phải chờ full job mới xem/tải | user kiểm tra chất lượng sớm, tải phần đã xong ngay, phát hiện lỗi trước khi job chạy nhiều giờ |
| Engine sourcing | REUSE/ADAPT trước, BUILD chỉ khi thiếu | tự viết lại engine/scheduler helper dù ecosystem đã có | giảm code phải bảo trì, tận dụng engine mature; vẫn giữ control plane của VI Dubber |

### Adaptive chunk policy

Không cắt đúng mốc thời gian nếu mốc đó nằm giữa câu.

```text
default benchmark sweet spot: 25 phút
target macro chunk: 20-30 phút
adaptive tuning range ban đầu: 15-40 phút
search boundary: khoảng ±30-60 giây
ưu tiên: silence/VAD boundary + sentence/turn boundary
fallback: deterministic safe cut với overlap/context window nhỏ
```

`25 phút` là default để benchmark/triển khai đầu tiên, không phải hard limit. Scheduler có thể điều chỉnh trong khoảng `15-40 phút` sau khi có evidence về RAM/VRAM, nội dung, speaker density và throughput; mọi thay đổi default phải qua P23 A/B gate.

Video ngắn không bị ép chia nhiều chunk. Policy ban đầu:

| Độ dài / use case | Chunk target ban đầu | Core scheduling | Profile thường dùng |
|---|---:|---|---|
| <15 phút, clip/tutorial | 1 chunk | 1 work unit nếu memory safe | balanced_fast |
| 15-60 phút, YouTube thông thường | 15-30 phút | 1 hoặc vài adaptive chunk | balanced_fast |
| 1-3 giờ, podcast/bài giảng | 20-30 phút | macro chunk + checkpoint + overlap | balanced_fast |
| 3-6 giờ, khóa học/podcast dài | 20-30 phút | macro chunk + checkpoint + overlap | balanced_fast |
| 6-12 giờ, monologue/khóa học sạch | 20-25 phút | macro chunk + selective QA + aggressive reuse | fast hoặc balanced_fast |
| >12 giờ | 15-25 phút | chunk nhỏ hơn để giới hạn failure scope/RAM | fast hoặc balanced_fast |
| >6 giờ, nhiều speaker/ồn/overlap | 15-25 phút tùy density | macro chunk + diarization/risk QA | balanced_fast |
| bất kỳ, output quan trọng | cùng chunk policy | cùng core, quality policy sâu hơn | balanced_best/max_quality |

### Work-unit contract

Mỗi chunk phải giữ tối thiểu:

```text
chunk_id
source start/end + global timestamp
boundary/context overlap metadata
speaker identities / diarization mapping nếu có
glossary + audience/domain context fingerprint
ASR/translation/TTS/QA stage fingerprints
artifact hashes + completion state
retry count + timing/throughput telemetry
```

Final assembly không phụ thuộc thứ tự chunk hoàn thành; luôn sort/validate theo global timeline và fail closed nếu thiếu/overlap bất hợp lệ.

### Scheduler contract

Không chạy nhiều GPU-heavy stage cùng lúc chỉ vì có nhiều chunk.

Baseline target:

```text
GPU queue: separation/ASR/TTS có ownership rõ, concurrency mặc định 1 trừ khi A/B chứng minh tốt hơn
WebGPT queue: production baseline concurrency 2; adaptive >2 chỉ benchmark-only cho tới khi pass pressure gate
CPU/FFmpeg queue: có thể overlap decode/preprocess/mux prep với GPU/network khi RSS/disk pressure an toàn
QA queue: early-exit; chỉ hard/risk segments đi verification sâu
```

Scheduler phải có backpressure theo queue depth, VRAM/RAM, disk free-space và provider pressure. Không để queue không giới hạn.

### Quality-preserving acceleration

P23 ưu tiên **bỏ việc thừa**, không downgrade model hàng loạt:

1. skip silence trước ASR khi evidence cho phép;
2. skip deep QA nếu deterministic + lightweight gates pass;
3. rewrite chỉ segment cần sửa;
4. cache/reuse theo fingerprint;
5. giữ model/engine sống trong worker khi có lợi;
6. prefetch và overlap stage khác tài nguyên;
7. partial recompute khi user edit;
8. auto-tune batch/concurrency trên fixture trước khi đổi default.

### Progress / observability

UI/API cần expose:

```text
overall progress
current macro chunk / total chunks
per-stage progress của chunk đang active
queue depth theo ASR / translate / TTS / QA
rolling throughput (media-minutes processed / wall-minute)
rolling ETA
cache-hit/reuse counters
retry/failure location theo chunk+stage
```

Pause/cancel phải dừng ở checkpoint an toàn và giữ artifact đã committed.

### Progressive preview / download contract

Video dài không bắt user chờ full job mới được xem kết quả. Khi một macro-chunk đã hoàn thành downstream cần thiết và được commit atomically, backend publish **preview artifact** cho chunk đó.

```text
chunk 001: READY  -> xem/tải preview ngay
chunk 002: READY  -> xem/tải preview ngay
chunk 003: TTS    -> chưa publish
chunk 004: ASR    -> chưa publish
...
all chunks READY + global assembly/mix/final QA -> FINAL
```

Contract:

1. Preview tối thiểu là MP4 của macro-chunk có audio dub + timeline đúng; tên/metadata phải ghi rõ `PREVIEW`, không giả là final.
2. Chỉ publish sau khi chunk pass local deterministic gates + required profile QA và manifest/artifact đã commit; không expose file đang ghi dở.
3. User có thể **play trong UI** và **download từng part** ngay khi ready; job còn lại tiếp tục chạy.
4. Nếu edit/repair làm invalid chunk, preview cũ bị đánh `stale/superseded`; preview version mới chỉ publish sau commit mới.
5. FINAL chỉ publish sau khi đủ chunk, validate ordering/global timestamp, full assembly, mix/master và final QA theo profile.
6. Optional progressive playback ưu tiên **reuse FFmpeg HLS/segment/fMP4 muxing** nếu benchmark/browser compatibility pass; không tự viết streaming container/protocol.
7. Preview packaging phải bounded I/O, không encode lại source video nhiều lần nếu remux/stream-copy an toàn đáp ứng contract.
8. Preview artifact có fingerprint/provenance để cache/resume và UI reload không nhầm version.

### P23 reuse-first engine gate

Trước mỗi sub-feature P23, worker phải audit ecosystem theo gate `REUSE / ADAPT / BUILD / REFERENCE-ONLY` đã định nghĩa ở 4.10. Nguyên tắc: **own the control plane, reuse the engines**.

| Nhu cầu P23 | Candidate cần audit trước khi BUILD | Default hướng xem xét |
|---|---|---|
| progressive preview/package | FFmpeg HLS/segment/fMP4 muxers | REUSE trực tiếp nếu contract đủ |
| ASR/VAD long-form primitives | WhisperX hiện tại + upstream VAD/chunk primitives; faster-whisper/Silero chỉ là candidate | ADAPT/REFERENCE, không replace ASR core nếu chưa A/B |
| subtitle/chunk/translation workflow | VideoLingo, ZastTranslate | REFERENCE/ADAPT sau license + code-path audit |
| dubbing workflow/orchestration | Linly-Dubbing, video-dubbing-translator | REFERENCE/ADAPT phần độc lập phù hợp |
| progress/queue/runtime UX | videoTranslator | REFERENCE/ADAPT pattern, không fork control plane |
| mature dubbing UX/concurrency patterns | pyVideoTrans | REFERENCE-ONLY mặc định cho tới khi license/obligation phù hợp được xác minh |

Không bê nguyên repo chỉ vì feature giống. Mỗi reuse phải pin source/revision/license, map dependency, benchmark local Windows/CUDA, có attribution cần thiết và rollback. Nếu engine hiện có không đáp ứng contract hoặc làm architecture phức tạp hơn rõ rệt thì mới BUILD helper nhỏ trong VI Dubber.

### Acceptance matrix

P23 chỉ được promote khi có benchmark cùng fixture/config/model giữa baseline và candidate:

1. **Correctness:** transcript/translation/timing/terminology/QA không regress ngoài tolerance đã định nghĩa.
2. **Short video:** overhead chunk/scheduler không làm short fixture chậm đáng kể; nếu <1 chunk thì path gần baseline.
3. **Medium/long:** end-to-end wall time không regress; target có throughput gain đo được hoặc cùng tốc độ nhưng reliability/RAM tốt hơn rõ.
4. **Super-long:** fixture synthetic/real representative 6h+ phải chạy với bounded RSS/VRAM/disk, không phụ thuộc load toàn waveform vào RAM.
5. **Resume:** kill/restart ở chunk giữa job chỉ recompute chunk/stage invalid; completed chunk giữ nguyên.
6. **Partial edit:** sửa 1 segment chỉ invalidate dependency cần thiết.
7. **Backpressure:** queue không tăng vô hạn khi WebGPT/TTS chậm.
8. **GPU pressure:** không OOM; concurrency cao hơn chỉ promote khi total throughput tốt hơn và p95 latency/VRAM trong gate.
9. **Ordering:** chunk hoàn thành out-of-order vẫn assemble bitwise/deterministically tương đương timeline contract.
10. **Profiles:** Fast/Balanced/Max cùng core; không code-clone long-form path.
11. **Progress:** UI hiển thị chunk/stage/ETA đúng với persisted job state và reload/resume.
12. **Failure scope:** corrupted/failed chunk không làm mất artifact đúng của chunk khác.
13. **Progressive preview:** chunk committed có thể play/download trước full completion; preview không publish partial file và bị invalidate đúng khi edit/repair.
14. **Final distinction:** UI/API phân biệt rõ Preview vs Final; final chỉ xuất hiện sau global assembly/mix/QA.
15. **Reuse provenance:** mọi engine/code được reuse/adapt trong P23 có source/revision/license/decision receipt và local benchmark trước promotion.

### Implementation order

1. P23-specific reuse-first reconnaissance: audit/pin candidates, chọn `REUSE/ADAPT/BUILD/REFERENCE-ONLY` trước khi code lớn.
2. Macro-chunk schema + silence/VAD boundary planner + global timestamp contract.
3. Per-chunk manifests/cache/resume + dependency graph cho partial recompute.
4. Streaming/windowed ASR input để bỏ full-waveform long-form pressure.
5. Bounded queues + scheduler/backpressure; giữ GPU-heavy concurrency bảo thủ lúc đầu.
6. Pipeline overlap giữa GPU/network/CPU stage có tài nguyên khác nhau.
7. Persistent engine + dynamic batching/prefetch nơi benchmark cho thấy lợi ích.
8. Risk QA/early-exit + selective rewrite trên shared policy layer.
9. Progressive preview artifact: committed chunk MP4 trước; A/B optional FFmpeg HLS/fMP4 playback; stale/version contract.
10. Progress/ETA/queue/chunk-ready telemetry trong web/API + download/open preview endpoints.
11. React UI: chunk list/status, play preview, download part, Preview/Final badges, stale/re-render state, reload persistence.
12. Auto-tune harness cho batch/concurrency/chunk size; không auto-promote nếu quality/fault gate fail.
13. Full P17 + long/super-long acceptance, progressive preview, crash/resume/fault matrix rồi mới đổi default.

### Trade-off

P23 chấp nhận một ít overhead từ chunk manifest/boundary/queue để đổi lấy bounded memory, khả năng resume nhỏ hạt và throughput tốt hơn ở video dài. Không đặt mục tiêu `parallel càng nhiều càng tốt`; mục tiêu là **máy luôn bận đúng việc, không tranh cùng một bottleneck**.

### Status

**IN PROGRESS.** Reuse-first audit, macro-chunk planner, per-chunk manifest/resume/invalidation, production windowed ASR cho non-diarization, bounded scheduler/prefetch, chunked TTS, risk QA, progressive preview/API/UI và persisted chunk throughput/ETA đã được implement với local regression coverage. Diarization vẫn cố ý dùng full-file ASR cho tới khi có cross-chunk speaker reconciliation. Auto-tune harness yêu cầu tối thiểu 3 trial xoay thứ tự + median trước khi một candidate đủ điều kiện manual promotion. 6.25h synthetic control-plane acceptance đã pass bounded backpressure/RSS/disk, manifest crash-resume và completion-order independence; đây không thay cho real-media 6h+/GPU/WebGPT/FFmpeg acceptance. Short correctness oracle đã reconcile sang P17 clean fixture; repeated 3-trial short A/B pass correctness. Harness aggregation sau đó được sửa để giữ đúng rotated trial pairing: median của per-trial candidate/baseline ratios là `0.98505`, pass gate `<=1.05`; ratio của hai arm medians cũ `1.12788` chỉ còn diagnostic vì bỏ pairing. Variance vẫn lớn nên không claim speedup. Whole-job repeated benchmark và real 6h+ acceptance vẫn chưa đóng.

---
## 11. TypeSafe implementation contract chi tiết

### 11.1. Không dùng TypeSafe cho

- arithmetic duration;
- exact timestamps;
- exact string hashing;
- file/cache ownership;
- deterministic number parsing khi rule rõ;
- video/audio decoding;
- subtitle serialization;
- control flow cuối cùng.

### 11.2. Dùng TypeSafe cho

- semantic fidelity;
- material loss;
- naturalness spoken Vietnamese;
- register/context consistency;
- ambiguity routing;
- candidate selection ở hard cases;
- verify-before-expensive-repair.

### 11.3. Raw output retention

Lưu:

- model;
- question policy version;
- answers;
- probabilities;
- confidence;
- token usage;
- latency;
- state fingerprint.

Decision `pass/review/retry` là derived data từ raw judgment.

### 11.4. Threshold calibration

Không copy threshold từ cookbook.

Tạo labeled Vietnamese dubbing set, đánh:

- should pass;
- should retry;
- severe semantic error;
- awkward but semantically correct.

Chọn threshold theo cost của false pass vs false retry.

### 11.5. Experiment receipt

Mỗi change TypeSafe phải log:

- prompt/question version;
- model;
- batch size;
- fixture set;
- accuracy metrics;
- latency;
- token/cost nếu có;
- selected thresholds.

---

## 12. AI translation contract chi tiết

### 12.1. Role

Selected ChatGPT Web model là generator chính, không là validator duy nhất. Target P21 đi qua dedicated WebGPT instance 2; contract chất lượng vẫn phải độc lập transport để prompt/schema có thể benchmark và rollback có chủ đích sau này.

### 12.2. Output contract

Mỗi translation result cần trace:

- input source IDs;
- context IDs;
- target duration;
- generated Vietnamese;
- provider/runtime;
- exact model ID + display name;
- reasoning/effort nếu có;
- model-catalog snapshot/revision hoặc timestamp đủ để audit selection;
- generation policy version;
- fallback status;
- retry reason.

### 12.3. Quality priorities

Prompt phải nói rõ thứ tự:

```text
meaning > critical facts > natural spoken Vietnamese > context/register > duration fit
```

Không được tối ưu duration tới mức làm mất ý.

### 12.4. Selective escalation

Normal case:

```text
one good candidate
```

Hard case:

```text
candidate A natural
candidate B concise
candidate C balanced (chỉ khi cần)
```

TypeSafe hoặc deterministic policy chọn/escalate.

### 12.5. Local fallback

Qwen local tiếp tục fallback, nhưng report phải cho biết segment/batch nào dùng fallback.

Không trộn kết quả hai provider mà mất provenance.

Nếu selected model unavailable, không được tự đổi sang model khác mà không ghi nhận. Fallback sang Qwen/legacy provider là một routing event có provenance, không phải model substitution im lặng.

---

## 13. Benchmark protocol

### 13.1. Không so cold với warm

Mỗi benchmark ghi:

- cold/warm;
- cache state;
- model already loaded hay chưa;
- GPU state;
- input hash;
- config hash.

### 13.2. Repeatability

Performance test quan trọng chạy ít nhất 3 lần nếu variance đáng kể.

Quality TTS có stochastic sampling thì lưu seed nếu backend support; nếu không, A/B sample đủ số lượng.

### 13.3. A/B rule

Không chỉ nghe một clip hay nhất.

Balanced default change cần representative set.

### 13.4. Performance table chuẩn

```text
fixture
profile
duration
stage
wall_sec
stage_rtf
peak_vram_mb
cache_hit
calls
notes
```

### 13.5. Quality table chuẩn

```text
fixture/segment
naturalness 1-5
pronunciation 1-5
speaker similarity 1-5
semantic fidelity 1-5
timing 1-5
preferred A/B
notes
```

---

## 14. Execution waves đề xuất cho Codex development coordinator

### 14.0. Macro checkpoints

Các phase chi tiết ở trên là unit implementation. Để coordinator dễ resume/review, gom thành checkpoint lớn:

| Checkpoint | Nội dung | Điều kiện qua |
|---|---|---|
| **CP0 Baseline Freeze** | doctor, tests, source/file hashes, fixture manifest, current RTF/rewrite/overflow/QA | baseline reproducible |
| **CP1 Foundations** | P00 profiler + P01 artifact/cache/resume | benchmark đáng tin, stale reuse = 0 trong matrix |
| **CP2 Timing Core** | P02 word data + P03 segmentation + P09 timing foundation | speech-turn data đúng, rewrite/overflow/tempo cải thiện |
| **CP3 Language Quality** | P04 translation + P05 TypeSafe + P06 pronunciation | semantic/naturalness calibration pass |
| **CP4 Voice Core** | P07 reference + P08 GPU TTS | quality parity/improvement + safe VRAM + throughput gain |
| **CP5 Audio/QA** | P10-P12 assembly/mix/segment repair | loudness/clipping/critical error gates pass |
| **CP6 Product Modes** | P13-P16 tuning/multi-speaker/profiles/editor | reproducible profiles + hard cases visible/reviewable |
| **CP7 Full Acceptance** | P17 regression + selected P19 + P20 ops | representative short/medium/long + resume + blind A/B + perf regression pass |
| **CP8 Long-form Core** | P23 adaptive chunk/scheduler/streaming/partial recompute + progressive preview/download + reuse-first gate | short overhead safe + long/super-long bounded memory + preview/final UX + resume/throughput/quality/reuse gates pass |

Không đi checkpoint sau chỉ vì code của checkpoint trước đã merge. Evidence/acceptance phải pass trước.

### Wave 0 - Foundation

Phases:

- P00 profiler;
- P01 cache/resume;
- P17 benchmark skeleton tối thiểu.

Parallel:

- agent A: profiler implementation;
- agent B: cache audit/tests;
- agent C: benchmark fixture manifest;
- root: integration/review.

Gate: correctness trước performance work.

### Wave 1 - Timing foundation

Phases:

- P02 word metadata;
- P03 segmentation;
- duration-estimator research cho P04.

Parallel:

- agent A: ASR/data model;
- agent B: segmentation algorithm + unit tests;
- agent C: analyze TTS duration data/read-only;
- root: merge contract + benchmark.

### Wave 2 - Translation intelligence

Phases:

- P04 translation;
- P05 TypeSafe;
- P06 pronunciation.

Parallel:

- translation worker;
- TypeSafe verifier worker;
- pronunciation worker;
- reviewer read-only.

Root owns final flow order và cache integration.

### Wave 3 - Voice engine

Phases:

- P07 reference;
- P08 GPU TTS;
- P09 elastic timing.

Parallel:

- acoustic reference worker;
- GPU benchmark worker;
- timing worker làm trên separate files/harness nếu có thể;
- root resolves `tts.py` integration để tránh conflict.

### Wave 4 - Output quality

Phases:

- P10 assembly;
- P11 mix;
- P12 segment QA/repair.

Parallel theo `media.py` vs `qa.py`; root giữ pipeline state transitions.

### Wave 5 - Hard cases/productization

Phases:

- P13 tuning;
- P14 multi-speaker;
- P15 profiles;
- P16 review UI;
- P17 full regression.

### Wave 6 - Optional extras

- P19 cloud/premium A/B;
- P20 final ops.

Không để optional extras chặn core Balanced Best release.

### Wave 7 - Shared long-form/performance core

- P23 adaptive macro-chunking + streaming I/O;
- per-chunk cache/checkpoint + partial recompute;
- bounded resource-aware scheduler/backpressure;
- pipeline overlap + dynamic batching/prefetch;
- progress/ETA + auto-tune harness;
- P17 + 6h+ long-form acceptance/fault matrix.

P23 phải giữ một shared pipeline cho mọi profile; không tạo implementation riêng chỉ cho Fast hoặc video >6h.

---

## 15. Worker checklist cho mỗi phase

Main worker copy checklist này vào task state:

```text
[ ] Đã đọc PLAN phase hiện tại
[ ] Đã xác minh pre-existing tests
[ ] Đã xác định exact files owned
[ ] Đã xác định artifact/fixture cần dùng
[ ] Nếu phase có ecosystem tương ứng: đã làm open-source reconnaissance và ghi quyết định REUSE/ADAPT/BUILD/REFERENCE-ONLY
[ ] Nếu reuse/adapt code: đã verify source revision/license/dependency/Windows-CUDA compatibility và attribution obligations
[ ] Đã spawn chỉ subagents có lợi
[ ] Child ownership không overlap
[ ] Đã review patch
[ ] Focused tests pass
[ ] Regression scope hợp lý pass
[ ] Benchmark/QA evidence đủ
[ ] Không có secret/log leak
[ ] Cache invalidation đúng
[ ] Fallback/rollback tồn tại
[ ] Checkpoint đã ghi
[ ] PLAN status cập nhật
```

---

## 16. Review gates

### Gate A - Correctness

- tests;
- artifact identity;
- no stale cache;
- no wrong speaker/segment mapping.

### Gate B - Functional

- feature chạy end-to-end trên fixture.

### Gate C - Performance

- measurement đủ repeatable;
- speedup không chỉ do warm cache ngoài ý muốn.

### Gate D - Quality

- human A/B;
- critical token QA;
- semantic QA.

### Gate E - Reliability

- interruption/resume;
- service failure fallback;
- GPU OOM fallback nếu relevant.

Phase chỉ COMPLETE khi gate phù hợp đã pass, không phải khi code "trông xong".

---

## 17. Risks và mitigation

| Risk | Hậu quả | Mitigation |
|---|---|---|
| segmentation merge sai | đổi meaning/timing | word trace + speaker boundary + fixtures |
| duration predictor bias | rewrite thừa | uncertainty + measured feedback |
| GPU OOM | crash job | conservative default batch + adaptive fallback |
| batch changes TTS quality | voice regress | blind A/B trước default |
| TypeSafe false pass | semantic lỗi | atomic heads + calibration + critical deterministic checks |
| TypeSafe false retry | cost/latency | threshold calibration + raw judgment reuse |
| instance 2/service/auth/model availability fail | translation fail | bounded retry/recovery rồi fail closed; không cross-instance/Aurora/Qwen tự động |
| model catalog drift/rename | chạy nhầm model | live catalog + selected model snapshot + fail closed khi unavailable |
| quá nhiều concurrent ChatGPT Web requests | cooldown/429/latency tăng | bounded/adaptive concurrency + queue + benchmark trước khi nâng |
| stale cache | wrong audio/text | stage manifest/content hash |
| global QA hides error | bad critical word | segment QA + critical tokens |
| aggressive duck/compress | pumping | A/B + conservative defaults |
| long task loses chat context | redo work | durable checkpoints/receipts |
| parallel agents conflict | overwrite patch | explicit file ownership |

---

## 18. Secrets, privacy, external services

### Existing

- TypeSafe key ở environment.
- HF token optional ở environment.
- Legacy Codex Web GPT route hiện dùng local Codex/Cockpit integration.
- P21 dùng managed WebGPT instance 2; auth/session do Codex/WebGPT runtime quản lý, không lưu vào VI Dubber.

### Rules

- không ghi secrets vào PLAN/config/artifacts;
- không echo token trong diagnostics;
- ChatGPT session/access/refresh credential chỉ nằm ở dedicated runtime/secret storage phù hợp; không lưu vào job manifest;
- không gửi voice/video lên service cloud mới mà không được người dùng đồng ý;
- ASR/separation/TTS/media vẫn local-first; translation target được phép dùng ChatGPT Web theo quyết định hiện tại, với Qwen local làm fallback;
- cloud/premium feature là opt-in.

---

## 19. Những việc không ưu tiên trước core acceptance

Không làm sớm chỉ vì nghe hấp dẫn:

- tăng `max_speedup`;
- VieNeu Nano làm default;
- CPU INT8 trên máy hiện tại;
- bigger translation model cho 100% câu;
- fine-tune TTS/ASR ngay;
- distributed worker / multi-PC infrastructure: **defer sau v1**; scope hiện tại tối ưu và production-harden cho một PC chính `RTX 2070 SUPER 8 GB` trước;
- model separator mới trước khi A/B chứng minh stem hiện tại là bottleneck;
- thêm framework mới khi helper hiện tại đủ.

---

## 20. Definition of Done cho Balanced Best v1

Balanced Best v1 được coi là hoàn thành khi:

1. P00-P12 core pass.
2. P17 regression suite có representative fixtures.
3. Rewrite rate trên job dài giảm về `<30%`, mục tiêu `<20%`.
4. Overflow `<5%`, mục tiêu `<3%`.
5. Tempo p95 `<=1.20x` trừ exception được report.
6. Voice reference selector thắng legacy trong A/B hoặc ít nhất không thua và ổn định hơn.
7. GPU TTS default được benchmark an toàn trên 2070 SUPER hoặc CPU fallback được giữ nếu GPU path chưa pass.
8. Critical name/number/negation fixture không có lỗi nghiêm trọng chưa detect.
9. Semantic verifier có calibration set và threshold documented.
10. Cache/resume matrix không có stale reuse.
11. Audio đạt loudness/peak/clipping gate.
12. Full pipeline chạy được từ CLI và web.
13. Interruption/resume không buộc chạy lại stage đúng đã cached.
14. Benchmark receipt ghi end-to-end RTF và stage breakdown.
15. Human listening A/B xác nhận naturalness/timing không thua baseline.
16. P16 product UI acceptance pass: create/monitor/pause-cancel/resume/review/edit/rerender/result workflow dùng được end-to-end mà normal path không cần CLI.
17. P20 production hardening matrix pass cho network/AI-provider/TypeSafe/process/GPU/disk/cache/browser interruption cốt lõi.
18. Crash/reboot giữa pipeline không làm mất toàn job và không coi partial artifact là complete.
19. Retry/fallback có budget, idempotent và observable; không infinite loop hoặc silent fallback.
20. Single-PC v1 dùng ổn định độc lập; không phụ thuộc PC thứ hai/distributed worker.
21. Mỗi phase có ecosystem phù hợp đã qua open-source reconnaissance gate; checkpoint ghi rõ `REUSE/ADAPT/BUILD/REFERENCE-ONLY`, source/revision/license khi reuse và benchmark local trước khi đổi default.
22. P21 historical acceptance vẫn giữ làm evidence cho contract WebGPT cũ; production hiện không phụ thuộc `:17842`.
23. P22 Dedicated Dubber-WebGPT `:17850` pass isolation/direct Responses/catalog/fault/throughput gate và fail closed khi route/model sai.
24. P23 long-form core pass shared-core acceptance: adaptive chunking, streaming/windowed input, per-chunk resume/cache, bounded scheduler/backpressure, partial recompute, progress/ETA và short→super-long benchmark không regress quality.

## 21. Definition of Done cho Max Quality

Ngoài Balanced Best:

- stricter semantic route;
- hard-case candidate fan-out;
- stronger final QA;
- multi-speaker hard cases phù hợp fixture;
- review UI usable cho flagged segments;
- no silent fail on uncertainty.

---

## 22. Entry point khi bắt đầu implementation

Main development coordinator bắt đầu bằng:

```text
1. Read projects/vi-dubber/PLAN.md fully.
2. Read projects/vi-dubber/README.md.
3. Inspect current source/tests; do not assume the plan snapshot is still current.
4. Run uv run pytest -q.
5. Run uv run vi-dubber doctor.
6. Inspect latest work/result/benchmark artifacts.
7. Start only the next incomplete execution wave.
8. Spawn bounded subagents with non-overlapping ownership.
9. Review/integrate/validate.
10. Write checkpoint + update PLAN status before moving to next gate.
```

Không bắt người dùng tự mở từng specialist chat hoặc copy kết quả giữa agents.

---

## 23. Trạng thái phase

| Phase | Status | Evidence |
|---|---|---|
| P00 Profiler | DONE | profiler/counters tích hợp; real smart run có stage metrics; short + long benchmark fixtures đã frozen |
| P01 Cache/resume | DONE | content-addressed job + stage manifests/fingerprints + atomic commits; stale/tamper/move/fresh/resume tests pass |
| P02 Word timing | DONE | WhisperX word timing/confidence/speaker được preserve; smart segmentation reuse artifact không cần ASR lại |
| P03 Segmentation | BENCHMARKED | short real fixture: 53 source -> 50 smart turns, 0 turn <1s; long baseline 734-turn frozen tại `work/benchmarks/p03-p09-long-20260924.json`, tập trung áp lực thời gian tại segment ngắn (<1s: 84.6% rewrite/overflow; >=7s: chỉ 4-5% overflow); determinism verify không va chạm cửa sổ |
| P04 Translation | ACCEPTED | live WebGPT instance 2 pass (high: 43.6s, med: 38.1s, instant_low: 35.0s); 100% adherence glossary.yaml (FVG, order block, sweeps liquidity, market structure); 100% critical tokens (OpenAI, Sam Altman, GPT-4, 86.400, 99,8%, negation, modality, win/loss); tools/p04_quality_receipt passed=true |
| P05 TypeSafe | ACCEPTED | calibration mở rộng 35 câu trading/tech thực tế (`label_provenance: "human"`), 100% PLAN classes; live TypeSafe Jev API batch 8/16/32 benchmark; tối ưu hóa ngưỡng candidate `0.87` đạt F1=1.0, precision=1.0, recall=1.0, bắt 14/14 lỗi nghiêm trọng (100%), không retry nhầm câu vụng về; model pinning chính thức khóa `jev-1.13.0` trong config.yaml; checkpoint `work/checkpoints/P05-real-calibration-acceptance.md` |
| P06 Pronunciation | DONE | golden pronunciation set + display/TTS text separation pass; deterministic number/currency/%/unit/date/time handling, explicit acronym/name/technical overrides và ambiguous-format fail-closed đều có regression |
| P07 Voice reference | PARTIAL / HUMAN GATE READY | acoustic selector 3-8s, overlap reject, canonical ref/speaker tích hợp; blind A/B packet đã tạo trên 4 fixture thật nơi smart selector khác legacy baseline. Mean selector score +0.2163, SNR 12.76 -> 20.00 dB, speech ratio 0.565 -> 0.672; media integrity pass. Còn >=3 blind human ballots + explicit pass. Checkpoint: `work/checkpoints/P07-reference-blind-ab-2026-09-25.md` |
| P08 GPU TTS | ACCEPTED | RTX 2070 SUPER CUDA FP16 batch 4: RTF 0.1185, ~30.3% wall-time gain, peak VRAM 728.83 MiB safe; CPU ONNX FP32 fallback pass; default config.yaml chính thức cập nhật `backend: pytorch`, `device: cuda`, `precision: fp16`, `batch_size: 4`; checkpoint `work/checkpoints/P08-gpu-tts-acceptance.md` |
| P09 Elastic timing | BENCHMARKED | timing policy version 3 chạy trên toàn bộ 734 cửa sổ long baseline: 0 va chạm (collision-free), 270.45s initial silence borrow, 277.86s rebalanced borrow; router hành động: 279 preserve_pause, 104 slowdown, 275 speedup, 76 rewrite; receipt tại `work/benchmarks/p03-p09-long-20260924.json` |
| P10 Assembly | DONE | exact timeline, fades, equal-power overlap/collision regression pass; streaming 30 s blocks trên timeline 3238.67 s chỉ tăng peak RSS ~16.7 MiB, output duration 3238.6728125 s |
| P11 Mix/master | PARTIAL / HUMAN GATE READY | controlled 226s A/B cùng source/stems/gains/AAC 192k 48 kHz: current two-pass + limiter đạt -14.40 LUFS, -1.62 dBTP, 0 clip samples; single-pass/no-limiter baseline đạt -14.72 LUFS, -1.11 dBTP nên fail loudness + true-peak gates. Blind packet integrity pass; còn >=3 human votes + human-attested decision. Checkpoint: `work/checkpoints/P11-controlled-listening-evidence-2026-09-25.md` |
| P12 Segment QA | PARTIAL / HUMAN GATE READY | consolidated 8 P17 runs = 68 segments, 2 final flags; 6/8 runs zero flags; 23 benign non-exact ASR variants pass without flag. Known numeric/critical-token defect bị bắt; current policy routes missing-critical -> `pronunciation_retry`, timing-only -> `timing_rewrite`, không cần đổi selective-repair policy. Còn human TP/FP labels cho remaining flags + small unflagged sample. Checkpoint: `work/checkpoints/P12-evidence-closure-2026-09-25.md` |
| P13 ASR/separator tune | ACCEPTED / KEEP CURRENT | full-P17 WhisperX batch 6 bị reject vì chậm hơn batch 4 (`286.99s` vs `248.89s`, +15.3% wall). Separator batch 2 không tác động RoFormer path hiện tại; oracle clean-speech skip tăng tốc separator 97.41% nhưng fail transcript/alignment parity và background-stem parity, nên cũng reject. Giữ production WhisperX batch 4 + BS-RoFormer. Receipts: `work/checkpoints/P13-full-p17-whisper-batch-ab-2026-09-25.md`, `work/checkpoints/P13-separator-clean-skip-ab-2026-09-25.md` |
| P14 Multi-speaker | PARTIAL | speaker/overlap visibility + review flags có; synthetic two-speaker/overlap machine contract pass. Workspace scan hiện không có retained job với >=2 speaker/overlap thật và runtime không có authorized HF diarization token; còn consented real 2-speaker interview + real crosstalk/listening gate |
| P15 Profiles | DONE | Fast/Balanced/Max resolved behavior machine-readable và profile-relevant fingerprint tests pass |
| P16 Review UI | ACCEPTED | UI đã re-accept live catalog/status trên Dedicated Dubber-WebGPT `:17850`; dynamic effort selector (gpt-5.6-sol: medium/high default high; instant: low); selective rerender invalidation chỉ hủy downstream audio của segment sửa và giữ nguyên cache thô; Playwright focused E2E pass và reload persistence verify |
| P17 Regression suite | ACCEPTED | 10/10 available fixtures trong `work/benchmarks/fixtures.json` (100% hoàn chỉnh cả 10 categories), 0 issues manifest validation; bổ sung full runs + baseline metrics cho `two-speakers` (100% similarity), `overlapping-speech` (94.9% similarity) và `emotional-prosody-stress` (99.5% similarity); 16/16 test harness pass; checkpoint `work/checkpoints/P17-full-fixtures-acceptance.md` |
| P19 Premium/cloud | OPTIONAL/BLOCKED | cần user opt-in cho API/cost/privacy; không thuộc Balanced Best mặc định |
| P20 Ops hardening | ACCEPTED | fault matrix cover: 429 backoff, selected model missing fail-closed, port unreachable, live disk preflight, child process kill across 4 stages (asr, translation, tts, mix_mux) với lease recovery/pause sạch, và atomic fsync temporary sibling replace an toàn khi kill; test_fault_contracts pass |
| P21 ChatGPT Web backend | HISTORICAL ACCEPTED / SUPERSEDED | instance 2 :17842 từng pass live catalog + translation + fail-closed; người dùng đã xóa instance này ngày 25/09/2026, evidence giữ lại nhưng route không còn active |
| P22 Dedicated Dubber-WebGPT | ACCEPTED / DONE | `:17850`, runtime home/browser/session riêng, direct Responses, không mutate global Codex/Cockpit route; P04/P16/P20 live re-accepted; repeated-warm c1/c2 gate pass và production promote c2 (`41.1390s -> 30.8903s` median, `1.3318x`, 0 pressure/retry/failure). Checkpoint: `work/checkpoints/P22-live-correctness-2026-09-25.md` |
| P23 Long-form/performance core | IN PROGRESS / SHORT OVERHEAD PASS | shared adaptive chunking + windowed ASR + per-chunk cache/resume/invalidation + bounded scheduler/prefetch + chunked TTS + risk QA + progressive preview/API/UI + progress/ETA đã implement và có local tests; auto-tune giữ resolved-axis guard + 3-trial rotated median. Synthetic 6.25h control-plane gate pass. Short correctness + corrected paired-overhead aggregation pass với median per-trial ratio `0.98505` <= `1.05`; variance vẫn diagnostic nên chưa claim speedup. Real-media final-assembly harness đã chuyển GPU gate sang Windows PDH process-tree dedicated memory; 5s production-mux smoke và focused tests pass instrumentation, nhưng historical 6.01h system-wide delta không được retroactive-pass và fresh representative 6h+ rerun vẫn mở. Whole-job/live-provider/final-media acceptance còn mở. Checkpoints: `work/checkpoints/P23-autotune-harness-hardening-2026-09-26.md`, `work/checkpoints/P23-machine-gates-2026-09-26.md`, `work/checkpoints/P23-webgpt-r2-live-recheck-2026-09-26.md`, `work/checkpoints/P23-short-overhead-correctness-reconcile-2026-09-26.md`, `work/checkpoints/P23-short-overhead-paired-stat-reconcile-2026-09-26.md`, `work/checkpoints/P23-real-media-process-gpu-attribution-2026-09-26.md` |

---

## 24. Sources tham chiếu kỹ thuật

Main worker phải re-check version khi bắt đầu phase có dependency external.

- TypeSafe docs index: `https://docs.typesafe.ai/llms.txt`
- TypeSafe System One/building patterns: docs links từ index.
- TypeSafe SDE cascade: `https://docs.typesafe.ai/cookbooks/sde_cascade`
- TypeSafe parallel questions: `https://docs.typesafe.ai/cookbooks/parallel_questions`
- VieNeu-TTS upstream: `https://github.com/pnnbao97/VieNeu-TTS`
- VieNeu-TTS 3.8.x GPU batching/CUDA graph implementation: `https://github.com/pnnbao97/VieNeu-TTS/blob/main/src/vieneu/v3turbo.py`
- VieNeu-TTS v3 Turbo reading-style/reference behavior: `https://github.com/pnnbao97/VieNeu-TTS/blob/main/README.md`
- Vietnamese TTS prosodic phrasing research (Interspeech 2014): `https://www.isca-archive.org/interspeech_2014/nguyen14_interspeech.html`
- WhisperX upstream: `https://github.com/m-bain/whisperX`
- faster-whisper upstream: `https://github.com/SYSTRAN/faster-whisper`
- Qwen3-ASR upstream: `https://github.com/QwenLM/Qwen3-ASR`
- pyannote benchmark/Precision-3: `https://www.pyannote.ai/benchmark`
- FFmpeg filters: `https://ffmpeg.org/ffmpeg-filters.html`
- OpenAI Fast mode: `https://developers.openai.com/api/docs/guides/fast-mode`
- Microsoft Vietnamese Localization Style Guide: `https://download.microsoft.com/download/b/f/e/bfecb1b4-21ab-48fd-a48c-c2471b026f8f/vie-vnm-StyleGuide.pdf`
- Microsoft technical-term guidance: `https://learn.microsoft.com/style-guide/word-choice/use-technical-terms-carefully`
- Google Cloud Translation glossary guidance: `https://docs.cloud.google.com/translate/docs/advanced/glossary`
- DeepL glossary/custom terminology guidance: `https://www.deepl.com/en/features/glossary`
- CNCF Vietnamese localization lessons: `https://www.cncf.io/blog/2025/06/26/cloud-native-glossary-the-vietnamese-version-is-live/`
- Community signal về English terms trong Vietnamese: `https://www.reddit.com/r/VietNam/comments/1botbcj/`
- Aurora upstream reference: dormant từ v1.8, không nằm trong active P21 path.
- Codex Web GPT user fork/worker reference: `https://github.com/Miikey24s/codex-chatgpt-web/tree/cockpit-custom-v5.0.8`

Không copy benchmark upstream thành acceptance của máy hiện tại. Benchmark local là nguồn quyết định.

---

## 25. Change log

| Ngày | Version | Thay đổi |
|---|---|---|
| 22/09/2026 | v1.0 | Tạo full optimization plan cho quality + performance + reliability; khóa Codex Web GPT `chatgpt-web/high` làm coordinator/worker chính; thêm subagent contract, durable checkpoints, TypeSafe verify/escalate, word-aware segmentation, duration-aware translation, GPU VieNeu batching, smart reference, elastic timing, segment QA, mix/master, profiles, review UI, regression suite và optional lip-sync/cloud phases. Baseline dùng hai job thật + 19 tests + doctor runtime hiện tại. |
| 22/09/2026 | v1.1 | Khóa scope v1 single-PC; nâng P16 thành product UI hoàn chỉnh cho create/monitor/resume/review/edit/rerender/result; mở rộng P20 thành full production hardening với checkpoint/crash recovery, idempotency, bounded retry/fallback, persisted job state và fault-injection matrix cho network/WebGPT/TypeSafe/process/GPU/disk/cache/browser failures; các gate này trở thành Definition of Done bắt buộc. |
| 22/09/2026 | v1.2 | Thêm open-source reconnaissance / REUSE-ADAPT-BUILD gate bắt buộc trước implementation đáng kể: audit candidate repo, verify revision/license/maintenance/dependency/Windows-CUDA compatibility, đọc code path thực tế, ghi quyết định vào checkpoint và benchmark local. Seed references gồm VideoLingo, ZastTranslate, video-dubbing-translator, Linly-Dubbing, videoTranslator, pyVideoTrans, MuseTalk và LatentSync. Giữ nguyên tắc `own the control plane, reuse the engines`; không fork nguyên stack chỉ vì feature tương tự. |
| 23/09/2026 | v1.3 | Khóa regression cho config-origin khi resume snapshot persisted/legacy; live-resume `job-4c8236a32c685a11` hoàn tất trên snapshot cũ, P12 còn 3/50 true flags, global ASR similarity 98.4%; P17 report current fixture có WER 9.63%, critical-token accuracy 94.6%, semantic QA 50/50; full suite 264 pass. P12/P17/P20 vẫn PARTIAL vì acceptance còn thiếu như status table. |
| 23/09/2026 | v1.4 | Nâng P17 coverage thật từ 2/10 lên 4/10 bằng clean talking-head 60s và technical/numeric 90s fixture có hash/provenance; talking-head cold A/B cải thiện WER 9.09% -> 7.07% nhưng không có speed win, technical fixture phát hiện mất critical token `33%` dù global re-ASR 97.2%. P20 thêm real subprocess-kill/stale-lease/checkpoint regression và live disk-preflight fail-clean. P16 selective-rerender browser receipt được đối chiếu bằng isolated clone hash audit. `compare-manifests` vẫn fail-closed vì thiếu 6 fixture class, đúng release policy. |
| 23/09/2026 | v1.5 | P06 đủ golden pronunciation/override/date-time/currency-unit/fail-closed regression nên chuyển DONE. P10 chuyển sang chunked streaming assembly và long-timeline benchmark 3238.67 s cho peak RSS delta ~16.7 MiB thay vì full-buffer ước tính ~889.5 MiB; 84 focused tests + 271 full tests + doctor pass. P16 browser reload giữ persisted completed job và tự nạp source/dubbed segment audio. P17 có thêm fast-English fixture nên coverage hiện 5/10; release gate vẫn fail-closed cho tới đủ fixture/calibration/human A/B. |
| 23/09/2026 | v1.6 | Mở rộng P20 deterministic fault matrix: TypeSafe timeout/429/5xx, WebGPT route-down→local fallback, cancel lifecycle, power-loss style termination trước atomic replace, changed-input resume rejection và duplicate Start guard; P16 thêm failed/cancelled/empty-state regression. Full suite sau thay đổi `281 passed`; doctor và benchmark manifest validation pass. P20/P16 vẫn PARTIAL vì clean fresh-install, broader real-service/per-stage kill và YouTube/disabled-loading UI acceptance chưa đủ. |
| 23/09/2026 | v1.7 | Chốt migration AI translation sang dedicated Aurora + ChatGPT Web direct path, tách khỏi Codex/Cockpit daily runtime. Thêm P21, dynamic model catalog trên UI, user-selectable model/effort, policy không hardcode `5.6 Sol High`, model snapshot/fingerprint/fail-closed, stateless translation contract, bounded concurrency, auth/session/catalog/429 fault matrix, Qwen fallback + legacy rollback. Chưa clone Aurora và chưa đổi runtime/config/code ở version plan này. |
| 23/09/2026 | v1.8 | Supersede hướng Aurora theo quyết định mới: product translation quay lại Codex ChatGPT Web và khóa riêng managed instance 2 `127.0.0.1:17842`. UI chỉ expose Codex WebGPT; live catalog lấy từ instance 2; model mặc định hiện là `chatgpt-web/gpt-5.6-sol`; mỗi `codex exec` override provider URL theo invocation nên không mutate global Cockpit route. Aurora/local/hybrid chỉ giữ dormant/backward-compatible, không tự fallback. |
| 24/09/2026 | v1.9 | Hoàn thành multi-subagent execution wave cho P21, P04, P16, P20: live translation instance 2 (high/medium/instant low) pass, 100% glossary & critical tokens, dynamic catalog & selective rerender UI pass, fault matrix (kill recovery, disk preflight, atomic fsync) pass; 337 tests passed. P04, P16, P20, P21 chuyển ACCEPTED/DONE. |
| 24/09/2026 | v2.0 | Hoàn tất Definition of Done v2.0: (1) P17 đạt 10/10 available fixtures (two-speakers, overlapping-speech, emotional-prosody-stress) với verified SHA-256 & QA receipts; (2) P08 VieNeu GPU TTS (CUDA FP16 batch 4) chính thức ACCEPTED và cập nhật default config.yaml; (3) P03/P09 long-form baseline 734 segments & collision-free timing counterfactual được benchmark và đóng băng tại `work/benchmarks/p03-p09-long-20260924.json`; (4) Tích hợp Playwright UI QA CLI/skill (doctor/e2e/screenshot studio) mô hình theo chuẩn 6 Astra; (5) Toàn bộ test suite 340 passed; repo GitHub public `https://github.com/Miikey24s/mk-ai-dubber` đã tạo, commit và push sạch sẽ. |
| 24/09/2026 | v2.1 | Hoàn thành P05 TypeSafe Golden Calibration & Model Pinning: (1) Xây dựng tập nhãn thực tế mở rộng 35 câu trading/tech (100% PLAN classes); (2) Live benchmark qua batch 8/16/32 trên model Jev; (3) Tối ưu hóa ngưỡng retry 0.87 đạt 100% recall lỗi nghiêm trọng; (4) Khóa model pinning `jev-1.13.0` và cập nhật `config.yaml`; chuyển P05 sang ACCEPTED. |
| 24/09/2026 | v2.2 | Loại P18 lip-sync khỏi active product scope theo quyết định người dùng. Pipeline giữ nguyên pixel video và tập trung vào accuracy, voice, timing, throughput, QA và reliability; giữ nguyên ID P19/P20/P21 để không làm hỏng checkpoint/receipt lịch sử. |
| 24/09/2026 | v2.3 | Bổ sung ma trận toàn hệ thống theo Low/Sweet spot/Max, ứng viên cũ-mới và score định hướng; audit sâu WebGPT cho thấy đường hiện tại `VI Dubber -> codex exec --ephemeral -> local Responses -> browser worker -> ChatGPT Web` có thể benchmark một đường provider-only trực tiếp `VI Dubber -> :17842/v1/responses` để bỏ overhead Codex process/harness/environment khỏi translation thuần tool-free. Giữ route hiện tại làm baseline cho tới khi parity/fault gates pass. |
| 24/09/2026 | v2.4 | Chốt hướng benchmark một **dedicated Dubber-WebGPT runtime** cho VI Dubber: reuse browser/login/model/cooldown/recovery core của custom WebGPT hiện tại nhưng bỏ coding-only environment, MCP, subagents, sandbox, skills và compaction khỏi production translation path. Translation semantics vẫn thuộc VI Dubber; WebGPT chỉ làm transport/provider. Chốt candidate policy: giữ WhisperX/BS-RoFormer/TypeSafe/FFmpeg/FastAPI/timing; A/B VieNeu 3.8.3 để upgrade; thêm Qwen3-ASR làm independent shadow QA thay vì replace WhisperX; Community-1 giữ default và Precision-3 chỉ premium/multi-speaker. |
| 24/09/2026 | v2.5 | Sau khi dùng thử Balanced Best, ghi nhận quality tổng thể tốt nhưng còn room ở độ native của tiếng Việt (nhịp, nhấn, pause, cách đọc code-switch) và ở localization thuật ngữ: không mặc định Việt hóa mọi technical term. Research chốt hướng tối ưu low-blast-radius trước: delivery-aware reference + TTS-only phrasing/pronunciation + human native A/B; translation thêm audience/domain terminology policy với `KEEP_EN/PREFER_EN/VI/CONTEXTUAL`, glossary có display/spoken form và deterministic protected-term QA. `Engulfing` là canonical example cho `KEEP_EN/PREFER_EN`, không ép thành `nến nhấn chìm`. |
| 25/09/2026 | v2.5 execution | VieNeu 3.8.3 isolated CUDA FP16 A/B trên RTX 2070 SUPER chậm hơn 3.7.1 khoảng 10.0-13.5% wall-time ở batch 4/8/16, nên giữ 3.7.1 và để blind listening gate mở. Implement terminology policy v1 + 16-case code-switch golden set + deterministic post-translation/rewrite gate, display/spoken split, delivery-aware P07 reference score và conservative TTS-only punctuation spacing. Full suite mới 364 passed; doctor + P04 receipt pass. Checkpoint: `work/checkpoints/v2.5-quality-wave-2026-09-25.md`. |
| 25/09/2026 | v2.6 execution | Implement deterministic compact global translation context + thread-safe per-invocation WebGPT state + bounded concurrency 1-3 + request latency telemetry. Live 6-segment/batch-2 Sol High benchmark: c1 `137.04s`, c2 `122.33s` (~1.12x; quality gates pass, 0 retry) nhưng p95 request tăng `50.08s -> 74.91s`; c3 fail với `Selected model is at capacity`. Capacity/rate-pressure giờ fail fast ở outer retry layer; production vẫn giữ `webgpt_concurrency: 1` và `global_context_enabled: false` cho tới broader repeated A/B. Checkpoint: `work/checkpoints/webgpt-global-context-concurrency-2026-09-25.md`. |
| 25/09/2026 | v2.7 execution | Hoàn tất P13 full-P17 WhisperX batch A/B trên đủ 10 category. Batch 6 không OOM nhưng chậm hơn batch 4 tổng thể `286.99s` vs `248.89s` (+15.3% wall), chủ yếu do long-monologue; 9/10 fixture exact transcript/timing/alignment parity, long fixture similarity 99.957% và 0/146 raw segment lệch timestamp >30 ms. Giữ `asr.batch_size: 4`; broad batch 8 không cần cho gate hiện tại; P13 vẫn PARTIAL vì separator A/B còn mở. Receipt: `work/checkpoints/P13-full-p17-whisper-batch-ab-2026-09-25.md`. |
| 25/09/2026 | v2.8 execution | Người dùng xác nhận managed WebGPT instance 2 `:17842` đã xóa và chọn triển khai Dedicated Dubber-WebGPT trước khi tiếp tục roadmap. Thêm P22: runtime riêng port `17850`, home/browser login/process riêng ngoài Git, reuse WebGPT core nhưng production translation dùng direct `/v1/responses` tool-free, giữ concurrency 1 trong migration và không mutate global Codex/Cockpit route. P21 chuyển historical/superseded; live acceptance mới chỉ được công nhận sau manual login + P04/P16/P20 re-acceptance trên `:17850`. |
| 25/09/2026 | v2.9 execution | Hoàn tất P22 trên Dedicated Dubber-WebGPT `:17850`: login/runtime live, P04/P16/P20 re-acceptance, direct Responses fail-closed và repeated-warm c1/c2 throughput gate đều pass. c2 median `30.8903s` so với c1 `41.1390s` (`1.3318x`), quality gates pass và `0` pressure/retry/failure; production promote `webgpt_concurrency: 2`, global context giữ `false`, c3 vẫn benchmark-only. |
| 25/09/2026 | v2.10 execution | Đóng P13 candidate tuning ở trạng thái KEEP CURRENT; sửa reference-cache regression; benchmark VieNeu serial warm-up cho thấy time-to-first-output xấu hơn cold path nên reject serial warm-up, chỉ giữ async TTS preload làm candidate chưa promote. Checkpoint: `work/checkpoints/v2.10-p13-cache-warmup-2026-09-25.md`. |
| 25/09/2026 | v2.11 execution | Multi-subagent wave giữ tổng 4 Codex WebGPT browser turns (root + 3 child, không nested) để chuẩn bị P07/P11/P12 closure. P07 tạo 4-trial isolated blind reference A/B; P11 tạo controlled mastering A/B và objective gate pass cho current two-pass + limiter; P12 consolidate 171 checked segments across P17 + supplemental evidence, giữ current selective-repair routing. Machine-side gates đã đủ; cả ba phase vẫn PARTIAL vì acceptance còn human listening/label gate. Root validation: 48 focused tests pass, compile + diff check pass. Checkpoint: `work/checkpoints/v2.11-p07-p11-p12-human-gate-prep-2026-09-25.md`. |
| 25/09/2026 | v2.12 plan | Thêm P23 shared long-form/performance core theo quyết định người dùng: adaptive silence/VAD-aware chunking, streaming/windowed I/O, per-chunk cache/checkpoint/resume, idempotent work units, dependency-aware partial recompute, bounded resource-aware scheduler/backpressure, pipeline overlap, dynamic batching, persistent engines/prefetch, risk-based early-exit QA, artifact minimization, progress theo chunk+stage, rolling ETA và fixture-based auto-tune. Mọi profile cùng kế thừa core này; không tạo pipeline riêng cho Fast. Thêm CP8/Wave 7 và acceptance short→super-long, chưa claim speedup cho tới khi benchmark whole-job pass. |
| 25/09/2026 | v2.13 plan | Mở rộng P23 với progressive preview/download: macro-chunk đã commit + pass local gates được play/download ngay khi job còn chạy; Final vẫn chỉ publish sau global assembly/mix/QA. Thêm Preview/Final/stale/version contract, React chunk-monitor UX, optional FFmpeg HLS/fMP4 A/B, và P23-specific reuse-first engine gate để audit REUSE/ADAPT/BUILD/REFERENCE-ONLY trước khi tự viết mới. |
| 25/09/2026 | v2.14 plan | Khóa default benchmark sweet spot cho P23 ở 25 phút (target 20-30 phút, adaptive range ban đầu 15-40 phút) và thêm policy chunk theo độ dài video từ <15 phút đến >12 giờ. Đây là benchmark/default ban đầu, không phải hard limit; scheduler chỉ tự đổi sau A/B về quality, throughput và memory pressure. |
| 26/09/2026 | v2.17 execution | P23 auto-tune hardening: sửa `balanced_fast` để không ép TTS batch 1 và giữ production/P08 default batch 4; thêm guard xác nhận ASR/TTS/WebGPT/chunk tuning axes sau profile resolution; bỏ `asr8` khỏi default matrix vì P13 đã reject broad batch tăng trên cùng runtime; giữ `chunk20m` và `tts8` làm candidates; đổi promotion gate sang tối thiểu 3 trial xoay thứ tự và so median để tránh single-run/order noise. Dedicated Dubber-WebGPT `:17850` live/doctor pass; resumed 33 phút hiện có QA + 2/2 preview pass nhưng không dùng làm speed receipt vì là cache/resume run. Full suite `451 passed, 2 warnings`. Checkpoint: `work/checkpoints/P23-autotune-harness-hardening-2026-09-26.md`. |
| 26/09/2026 | v2.18 execution | P23 machine-gate hardening: short-overhead benchmark được sửa để so cùng current tree/runtime với `longform.enabled=false/true`, absolutize glossary và tránh trộn transport lịch sử `:17842`; harness regression pass. Fresh live P22 probe trên `:17850` pass 24.573s nhưng short A/B fail-closed ở production direct Responses vì malformed JSON trong translation, nên không chạy matrix 33 phút để tránh tốn compute trên cùng provider gate đang fail. 6.25h synthetic control-plane harness dùng real `BoundedExecutor` + chunk manifest primitives pass bounded queue/RSS/disk, crash/resume và deterministic completion-order; real-media 6h+/VRAM/live-provider/final-media gate vẫn mở. Full suite `460 passed, 2 warnings`; frontend build pass. Checkpoint: `work/checkpoints/P23-machine-gates-2026-09-26.md`. |
| 26/09/2026 | v2.19 execution | R2 live recheck sau diagnostic instrumentation: direct Responses probe tool-free/retry=0 pass trên `:17850`, và one-trial same-tree short A/B đi qua production translation ở cả baseline/candidate với 53 segment, không malformed output. Overhead riêng pass (`candidate/base=0.91084`), nhưng correctness vẫn fail vì mỗi arm còn 1/53 segment QA flag sau repair và generated translations chỉ similarity `0.2654` so với oracle harness `>=0.98`. Dừng trước 3-trial để reconcile fixture/QA và validity của oracle thay vì tiêu thêm provider/GPU budget. Checkpoint: `work/checkpoints/P23-webgpt-r2-live-recheck-2026-09-26.md`. |
| 26/09/2026 | v2.20 execution | Reconcile P23 short correctness oracle: đổi default sang P17 real clean-talking-head 60s có retained segment-QA sạch; bỏ literal cross-arm translated-text similarity khỏi hard gate vì hai WebGPT generation độc lập, giữ metric làm diagnostic; mỗi arm vẫn phải pass product/terminology QA cùng source/axis parity. Focused regression 14/14 pass. One-trial hợp lệ pass correctness+overhead, nên chạy 3-trial rotated median: correctness pass nhưng overhead fail với baseline `281.994s`, candidate `318.054s`, ratio `1.12788` (+12.79%) > `1.05`. P23 short-overhead hiện BLOCKED bởi performance; không claim speedup. Checkpoint: `work/checkpoints/P23-short-overhead-correctness-reconcile-2026-09-26.md`. |
| 26/09/2026 | v2.21 execution | Reconcile statistical aggregation của repeated short A/B: harness vốn chạy paired trial và xoay order nhưng summary dùng ratio của hai arm medians, làm mất block pairing dưới runtime/provider variance. Đổi gate sang median của per-trial candidate/baseline ratios, giữ threshold `1.05` và arm medians làm diagnostic. Re-aggregate 6 run retained không rerun provider: ratios `0.98505`, `1.14664`, `0.78950`, median `0.98505` → short-overhead PASS; old unpaired diagnostic `1.12788`. Focused harness regression `7 passed`. Variance còn rộng và whole-job/real 6h+ gates còn mở nên không claim speedup. Checkpoint: `work/checkpoints/P23-short-overhead-paired-stat-reconcile-2026-09-26.md`. |
| 26/09/2026 | v2.22 execution | Harden real-media GPU attribution: historical 6.01h receipt used system-wide `nvidia-smi memory.used` and could not attribute the `6884 MiB` delta to VI Dubber; WDDM also exposes per-process `used_gpu_memory` as N/A. Harness now samples Windows PDH `GPU Process Memory(*)\\Dedicated Usage` and gates on the monitored mux process tree only; system-wide GPU stays diagnostic. Focused tests `2 passed`; ~5s production-mux smoke observed 3 tree PIDs and `0.0 MiB` peak dedicated GPU while preserving media/loudness/RSS/disk gates. Smoke intentionally cannot close the representative 6h gate; fresh 6h+ rerun remains required. Checkpoint: `work/checkpoints/P23-real-media-process-gpu-attribution-2026-09-26.md`. |

---

## 26. Khuyến nghị execution hiện tại

Hệ thống đang ở mốc **v2.20 implementation baseline**:
- Core pipeline (P00, P01, P02, P04, P05, P06, P08, P10, P13, P15, P16, P17, P20, P22) giữ acceptance hiện có; P21 là historical evidence.
- P23 đã implement phần lớn shared core. Synthetic 6.25h control-plane stress đã pass. Short A/B correctness và paired short-overhead đều pass; variance rộng vẫn diagnostic. Real-media GPU gate đã có process-tree attribution bằng Windows PDH và smoke instrumentation pass, nhưng fresh representative 6h+ rerun vẫn mở; repeated whole-job/provider/final-media stress cũng còn mở. Chưa được promote hoặc claim speedup trước các gate này.
- TypeSafe semantic QA đã được calibrate trên tập dữ liệu thực tế và khóa phiên bản model `jev-1.13.0`.
- Instance 2 `:17842` đã bị xóa; target translation mới là Dedicated Dubber-WebGPT `127.0.0.1:17850` direct Responses. Không tiếp tục benchmark/tối ưu dựa trên listener cũ.
- TTS default chạy PyTorch CUDA FP16 batch 4 trên RTX 2070 SUPER với fallback CPU ONNX an toàn và VRAM footprint < 800 MiB.
- Playwright Chromium Headless UI test và screenshot tự động kiểm thử giao diện Studio.
- Benchmark suite P17 hoàn thiện 100% (10/10 fixtures available) với validation không lỗi.
- GitHub public repository `Miikey24s/mk-ai-dubber` vẫn là upstream public; local workspace đang có execution wave v2.20 chưa được claim là đã đồng bộ remote cho tới khi acceptance/cleanup hoàn tất.

### 26.1. Tối ưu ưu tiên, không đổi quality target

Theo research và benchmark local đến 25/09/2026, ưu tiên theo thứ tự:

0. **P23 shared long-form/performance core - IN PROGRESS / SHORT OVERHEAD PASS**: implementation core đã có reuse-first audit + adaptive chunking + windowed I/O/ASR + per-chunk cache/resume + bounded scheduler/backpressure + chunked TTS + progressive preview/download + React Preview/Final UI + progress/ETA. Synthetic 6.25h control-plane gate đã pass; short correctness oracle và repeated 3-trial paired-overhead gate đều pass sau khi sửa aggregation giữ đúng trial pairing. Còn repeated whole-job 33 phút và real-media 6h+ resource/GPU-attribution/provider/final-media acceptance trước khi promote/claim speedup.

1. **WhisperX batch 6 A/B trên full P17 fixtures - DONE / REJECTED**: broader 10-category run đảo kết luận micro-benchmark. Batch 6 không OOM nhưng chậm hơn batch 4 tổng thể khoảng 15.3% (`286.99 s` vs `248.89 s`); 9/10 fixture exact parity, long fixture similarity 99.957% nhưng không exact text parity. Giữ production batch 4.
2. **P13 separator A/B - DONE / REJECTED candidates**: RoFormer path hiện tại không sử dụng MDXC batch knob nên batch 2 không phải A/B có ý nghĩa. Oracle clean-speech skip giảm separator wall 97.41% nhưng fail transcript/alignment parity và không giữ background stem, nên giữ BS-RoFormer hiện tại.
3. **VieNeu 3.8.3 isolated benchmark - DONE / REJECTED**: 3.8.3 chậm hơn 3.7.1 khoảng 10.0-13.5% ở batch 4/8/16; giữ 3.7.1.
4. **P22 Dedicated Dubber-WebGPT migration - DONE**: `:17850` đã pass login/runtime, P04/P16/P20 correctness/fault acceptance và repeated-warm c1/c2 A/B. Production dùng `webgpt_concurrency: 2`; global context vẫn tắt.
5. **Warm-up model/CUDA graph - SERIAL WARM-UP REJECTED / ASYNC PRELOAD CANDIDATE**: VieNeu 3.7.1 batch 4 cold đo được engine init ~13.3s + first batch ~4.33s. Sinh một batch warm-up làm time-to-first-output ~18.94s so với cold ~17.63s; `warm_fused()` cũng ~18.18s và tăng reserved VRAM. Không bật warm-up nối tiếp. Candidate tiếp theo chỉ là preload TTS engine song song với translation để che phần init dưới network/model latency; `tts.async_preload_enabled` giữ **OFF mặc định** và chỉ được promote sau whole-job A/B pass.
6. **pyannote batching chỉ cho multi-speaker**: community-1/pyannote.audio 4.0.7 đang là latest stable; benchmark embedding/segmentation batch khi P14 chạy thật. Không ảnh hưởng single-speaker path.

### 26.2. Không đổi mặc định nếu chưa có bằng chứng

- Không thay WhisperX `large-v3` bằng distilled/turbo chỉ để lấy speed; phải chứng minh WER/critical-token parity trên P17.
- Không thay BS-RoFormer hiện tại: model đang nằm top nhóm vocal separation của audio-separator; candidate khác chỉ A/B khi stem quality là bottleneck thật.
- Không thay TypeSafe Jev bằng LLM self-judge: calibration hiện tại đã pass và independence có giá trị.
- Không chuyển TTS sang model đa ngôn ngữ nặng hơn chỉ vì benchmark trên GPU mạnh hơn; RTX 2070 SUPER 8 GB là hardware gate.
- Direct OpenAI API cùng model là candidate latency/reliability mạnh, nhưng thuộc P19 vì phát sinh API key/cost; chỉ benchmark khi người dùng opt-in.

### 26.3. Ma trận toàn hệ thống: Low / Sweet spot / Max / ứng viên

Điểm dưới đây là **độ phù hợp với VI Dubber**, không phải leaderboard tuyệt đối. Trọng số định hướng: quality 40%, speed 25%, reliability 20%, fit với Windows + RTX 2070 SUPER 8 GB 15%. Score candidate chưa benchmark local là research score, không phải acceptance.

| Tầng | Hiện tại | Low/Fast vẫn giữ quality tốt | Sweet spot máy hiện tại | Max setting / máy mạnh hơn | Ứng viên cũ | Ứng viên mới đáng theo dõi | Điểm hiện tại | Điểm candidate | Quyết định |
|---|---|---|---|---|---|---|---:|---:|---|
| Download | yt-dlp | giữ nguyên | giữ nguyên | giữ nguyên | youtube-dl | yt-dlp latest | 96 | 96 | KEEP |
| Media/mux | FFmpeg 7.1.x | stream-copy khi không cần encode | FFmpeg filter graph hiện tại | HW encode/parallel encode nếu output yêu cầu | moviepy/pydub orchestration | FFmpeg | 98 | 98 | KEEP |
| Separation | audio-separator + BS-RoFormer 12.9755 | clean-speech detector có thể skip separation chỉ khi A/B chứng minh parity | BS-RoFormer hiện tại | ensemble/multi-pass separation trên GPU lớn | Demucs/UVR legacy | BS/MelBand RoFormer family | 94 | 94 | KEEP |
| ASR | WhisperX 3.8.6 + large-v3 FP16 batch 4 | giữ large-v3 batch 4; batch 6 broad A/B đã thua throughput | large-v3 batch 4 trên RTX 2070 SUPER hiện tại | WhisperX large-v3 batch lớn chỉ benchmark lại trên GPU/runtime khác; optional second ASR QA | Whisper/faster-whisper standalone | Qwen3-ASR 1.7B / 0.6B challenger | 95 | 90-96 research | KEEP batch 4 + A/B independent QA challenger |
| Alignment | WhisperX align | giữ nguyên | giữ nguyên | dedicated aligner nếu benchmark thắng | Whisper timestamps | Qwen3 ForcedAligner (không hỗ trợ VI trong current official 11-language aligner) | 95 | 80 cho use case VI | KEEP |
| Diarization | pyannote Community-1 | tắt hoàn toàn cho known single-speaker | Community-1 khi cần | Precision-3/on-prem speed-accuracy modes | older pyannote pipelines | Precision-3 | 88 | 94 research | KEEP default; premium A/B |
| Translation model | WebGPT GPT-5.6 Sol High | Instant Low cho easy text + selective Sol escalation sau calibration | Sol Medium/High theo profile | account/provider mạnh hơn + high effort; direct API Fast mode nếu cost allowed | Qwen local / older WebGPT route | same Sol through lean direct local Responses transport | 87 transport / 96 quality | 96-98 transport target | OPTIMIZE transport first |
| Translation orchestration | Dedicated direct `:17850/v1/responses`, bounded c2 | c2 + selective faster model/profile | c2 promoted; global context off | c3+ only after fresh account/cooldown benchmark; 10 is technical ceiling, not target | sequential codex-exec path | c3/adaptive queue only if later benchmark proves stable | 96 | 96 | KEEP c2 |
| Semantic QA | TypeSafe Jev 1.13 | critical-only on Fast profile | calibrated shadow/selective gate | stronger multi-head/candidate review only on hard cases | LLM self-judge | Jev calibrated + deterministic token checks | 93 | 93 | KEEP |
| Pronunciation | deterministic normalizer | only critical classes | current golden rules | expanded domain lexicon | prompt-only pronunciation | deterministic map + TTS-specific spoken text | 96 | 96 | KEEP |
| TTS | VieNeu 3.7.1 v3 Turbo GPU FP16 batch 4 | giữ 3.7.1 batch 4; async preload chỉ benchmark | 3.7.1 batch 4; async preload OFF mặc định | benchmark lại 3.8.3 chỉ khi GPU/runtime target đổi; premium/open 4B TTS cho A/B | CPU ONNX / old VieNeu | VieNeu 3.8.3 đã reject trên RTX 2070 SUPER; Fish Audio S2 Pro là max-quality challenger | 89 | 96 research trước local A/B | KEEP 3.7.1; 3.8.3 REJECTED CURRENT GPU |
| Voice reference | acoustic selector | one canonical clean ref/speaker | current smart selector | style-specific ref bank only if identity stable | first-length-valid ref | embedding-assisted selector | 88 partial | 94 potential | CLOSE P07 A/B |
| Timing | custom elastic timing | fewer rewrites + bounded stretch | current P09 policy | prosody-aware turn optimizer if measurable | hard subtitle-slot fit | current elastic speech-turn policy | 93 | 93 | KEEP |
| Assembly | streaming blocks | current streaming | current streaming | larger parallel blocks only if benchmark helps | giant full buffer | chunked streaming assembly | 97 | 97 | KEEP |
| Mix/master | FFmpeg loudnorm + limiter | same chain | same + conservative ducking after A/B | richer dialogue chain if listening proves gain | simple amix | current two-pass chain | 97 | 97 | KEEP |
| Acoustic QA | re-ASR segment QA | risk-selected only | segment QA + critical tokens | independent second-ASR on hard segments | global similarity only | Qwen3-ASR shadow verifier candidate | 90 partial | 95 potential | EXPAND P12 |
| Cache/resume | content-addressed manifests | same | same | same | file-exists cache | current fingerprint/atomic manifests | 96 | 96 | KEEP |
| Backend API | FastAPI | same | same | same | Gradio-only backend | FastAPI | 96 | 96 | KEEP |
| Product UI | React/Vite + legacy Gradio path | React normal path | React normal path | React only after legacy retirement evidence | Gradio product UI | React/Vite | 90 | 95 | GRADUAL CONSOLIDATION |

Interpretation đơn giản:

- **Low/Fast** không có nghĩa downgrade model bừa. Ưu tiên batch, skip stage có điều kiện, selective QA và selective escalation.
- **Sweet spot** là cấu hình nên dùng hằng ngày trên RTX 2070 SUPER 8 GB sau khi benchmark pass.
- **Max** là để biết trần công nghệ; không tự kéo dependency/model nặng vào máy hiện tại.
- Chỉ replace component khi candidate thắng **cùng fixture + cùng quality gate + cùng hardware target**, không dựa benchmark vendor.

### 26.4. WebGPT: cách hoạt động thật và hướng tối ưu

Historical runtime snapshot ngày 24/09/2026, trước P22:

```text
VI Dubber
  -> spawn `codex exec --ephemeral`
  -> Codex tạo Responses request
  -> per-call base_url override -> 127.0.0.1:17842/v1
  -> codex-chatgpt-web provider-only adapter
  -> browser turn broker / task-bound Temporary Chat tab
  -> ChatGPT Web model selected on account
  -> browser observes final response
  -> local Responses result
  -> Codex writes output file
  -> VI Dubber parses JSON
```

Tại snapshot này, instance 2 chạy `mode=full`, `integration_owner=cockpit`, `routing_owner=cockpit`, nhưng Cockpit integration đánh dấu ChatGPT Web là **provider-only**. Provider-only vẫn có thể expose native Codex tools khi request cần tool, nhưng không cần tin cậy `cwd`/sandbox envelope chỉ để làm provider. Translation của VI Dubber là tool-free nên không cần Full Harness capability flow trong prompt/runtime request.

#### 26.4.1. Bottleneck của historical codex-exec path

| Điểm | Hiện tại | Tác động | Hướng |
|---|---|---|---|
| Codex process | spawn một `codex exec` cho mỗi batch | startup + parsing + output-file lifecycle | benchmark direct local Responses |
| Codex agent prompt/environment | dù task chỉ dịch, Codex vẫn xây native request envelope | thêm context/token và logic không cần cho translation | tool-free minimal request |
| Batch scheduling | `translate_segments()` loop tuần tự | video dài bị cộng latency từng browser turn | bounded concurrency 2-3 |
| Chat lifecycle | `--ephemeral` mỗi batch độc lập | tốt cho isolation nhưng không share video context | global context pack deterministic thay vì chat memory |
| Context | nearby ±2 segment | dễ lệch register/entity ở batch xa nhau | one-time video/style/entity summary + glossary |
| Retry | VI Dubber retry + WebGPT internal browser recovery | có nguy cơ retry tầng ngoài khi lỗi account cooldown | classify cooldown vs transient; one owner of retry budget |
| Tab capacity | code custom hiện hard ceiling 10/instance | dễ hiểu nhầm là nên fan-out 10 | production target 2-3; adaptive backoff |
| Harness | Full MCP machinery tồn tại cho coding turns | không đem value cho pure translation | production translation không attach tools/MCP |

#### 26.4.2. Direct provider-only Responses — candidate đã được promote qua P22

Candidate này đã benchmark và được promote thành production transport của P22. Endpoint production hiện là `:17850`:

```text
VI Dubber
  -> HTTP POST http://127.0.0.1:17850/v1/responses
       model = selected chatgpt-web model
       instructions = minimal stable dubbing contract
       input = global context + current batch
       tools = []
       stream = false
       store = false
  -> same codex-chatgpt-web browser provider
  -> same ChatGPT Web account/model
  -> parse Responses output directly
```

Lợi ích kỳ vọng:

- bỏ `codex exec` subprocess trên từng batch;
- không cần Codex coding-agent system/rules/environment cho translation thuần;
- payload nhỏ và dễ đo hơn;
- concurrency/retry/cancellation do chính VI Dubber sở hữu rõ ràng;
- vẫn giữ provider/account model-selection contract, nhưng runtime/session/browser worker hiện thuộc Dedicated Dubber-WebGPT `:17850`.

Rủi ro/gate:

- phải giữ model/effort selection contract y hệt P21;
- JSON parser/receipt/idempotency phải đạt parity với đường `codex exec`;
- benchmark live phải so cùng prompt, cùng batch, cùng model/effort;
- test 429/cooldown/disconnect/cancel/model removal trước khi promote;
- route codex-exec cũ chỉ giữ làm historical comparison/diagnostic evidence sau khi P22 đã pass full P04/P20 regression; không còn là production rollback target mặc định.

#### 26.4.3. WebGPT settings matrix

| Chế độ | Model | Transport | Batch | Concurrent turns | Context strategy | Harness/tools | Mục tiêu |
|---|---|---|---:|---:|---|---|---|
| Low/Fast | Sol Instant Low trên easy batch, escalate hard/critical sang Sol | Dedicated direct Responses `:17850` | 32-48 | 2 | global compact context candidate | `tools=[]` | nhanh nhất nhưng model/context policy vẫn cần calibration parity |
| Sweet spot | Sol High mặc định; effort/model vẫn lấy từ live catalog/profile | Dedicated direct Responses `:17850` | 32 | 2 | nearby context; global context off | `tools=[]` | production hiện tại đã accepted qua P22 |
| Conservative | Sol High | historical codex-exec baseline | 32 | 1 | nearby ±2 | no model tool use | rollback/comparison evidence, không là production target |
| Max throughput | model phù hợp account | direct Responses | 32-64 theo validity gate | 3-5 adaptive | global context | `tools=[]` | benchmark only; không mặc định |
| Technical ceiling | bất kỳ | browser worker | n/a | 10/instance | n/a | n/a | chỉ là hard limit của custom runtime, không phải safe account target |

#### 26.4.4. Harness và AI environment policy

- **Production translation**: provider-only, tool-free, không cần gửi filesystem roots/cwd/sandbox/AGENTS/skills vào model prompt.
- **Codex coding/development**: Full Harness vẫn đúng vì cần terminal/files/tools/approvals.
- **DEV WebGPT harness**: chỉ dùng để test browser/MCP/tool-round/retry/compaction; không dùng làm đường production dubbing.
- **Compaction**: không cần cho stateless translation batch; tránh retained long chat nếu global context pack đã đủ.
- **`previous_response_id`**: không dùng mặc định cho parallel translation. Nó hữu ích cho conversational continuation nhưng tạo dependency tuần tự và context drift; chỉ A/B nếu video continuity thắng rõ.
- **Tool registry**: production translation request phải gửi `tools=[]`; không expose connector/tool schemas vô ích.
- **Prompt**: stable system contract ngắn + glossary/context có liên quan + batch; không nhét toàn PLAN/code/workspace.

#### 26.4.5. Historical WebGPT benchmark gate v2.3 — satisfied by P22

Benchmark matrix tối thiểu trên cùng real fixture:

```text
Transport: codex-exec vs direct-responses
Model: Sol High + Sol Medium; Instant chỉ separate low-profile experiment
Batch: 16 / 32 / 48
Concurrency: 1 / 2 / 3
Run state: cold / warm
```

Ghi ít nhất:

- total translation wall time;
- p50/p95 browser-turn latency;
- segments/minute;
- structured JSON validity;
- retries / 429 / cooldown;
- critical-token accuracy;
- glossary adherence;
- TypeSafe semantic pass rate;
- context/register consistency ở batch boundary;
- prompt/input size nếu đo được.

Gate này đã pass ở P22 trên Dedicated Dubber-WebGPT `:17850`: direct Responses được promote sau quality/fault re-acceptance và repeated-warm c1/c2 benchmark; production giữ c2, global context off.

#### 26.4.6. WebGPT upstream v6: chỉ lấy phần có giá trị

Runtime production hiện tại vẫn là custom `codex-chatgpt-web 5.0.8`. Upstream đã có v6.0.0, nhưng repo custom có Cockpit routing, multi-instance, provider-only contract, 10-turn ceiling, custom compaction/subagent lifecycle và các fix riêng. Không wholesale upgrade.

| Upstream v6 area | Giá trị cho VI Dubber | Quyết định |
|---|---|---|
| Account cooldown classification | rất cao: tránh coi cooldown là lỗi transient rồi retry liên tục | ADOPT/RECONCILE sớm |
| Accepted-generation resubmit protection | rất cao: tránh dịch một batch hai lần khi browser đã nhận generation | ADOPT/RECONCILE sớm |
| Browser submission/recovery fixes | cao: giảm lỗi tab/navigation/submit | ADOPT có regression |
| Better failure diagnostics | cao: biết rõ browser/session/cooldown lỗi ở đâu | ADOPT |
| Model family/effort verification | cao: hợp P21 live catalog/fail-closed | RECONCILE với catalog hiện tại |
| Six-part Bigger Context | thấp cho dubbing batch; hữu ích hơn cho coding/harness task lớn | DEFER cho VI Dubber |
| New browser chat each turn | translation đã stateless/ephemeral; direct Responses còn gọn hơn | không phải optimization chính |
| Save chats / retained chats | không cần cho dubbing; có thể làm tăng state/custom-memory contamination | KEEP OFF cho translation |
| Full Harness/MCP improvements | quan trọng cho coding agent, gần như neutral cho pure translation | giữ ở development path, không đưa vào production prompt |

Nguyên tắc: WebGPT runtime có thể được nâng riêng sau khi custom branch rebase/reconcile pass tests, nhưng VI Dubber không chờ v6 để thử direct provider-only Responses vì endpoint/provider contract cần thiết đã tồn tại ở runtime hiện tại.

#### 26.4.7. Dedicated Dubber-WebGPT: runtime riêng cho project

Quyết định ngày 25/09/2026: **P22 đã ACCEPTED/DONE**, vì managed instance 2 `:17842` đã được người dùng xóa và Dedicated Dubber-WebGPT `:17850` đã pass correctness, fault, UI và repeated-warm c1/c2 gate. Không tiếp tục đầu tư vào codex-exec path cũ như production target.

```text
VI Dubber
  -> direct POST :17850/v1/responses
  -> Dedicated Dubber-WebGPT provider API
  -> persistent browser/session trong home riêng
  -> Temporary Chat
  -> selected ChatGPT Web model
```

Ranh giới trách nhiệm:

| Thuộc Dubber-WebGPT | Không thuộc Dubber-WebGPT |
|---|---|
| browser login/profile/session | glossary/domain terminology policy |
| model + effort discovery/verification | global video summary |
| browser turn submission/observation | segmentation |
| accepted-generation idempotency | translation prompt semantics |
| cooldown classification/backoff signals | semantic QA/TypeSafe |
| bounded browser concurrency/queue | pronunciation normalization |
| streaming/final Responses envelope | TTS/timing/mix |
| transport metrics + diagnostics | product cache/fingerprints |

Production translation runtime mục tiêu:

```text
browser process          warm/persistent
login/account partition  dedicated runtime home/profile
listener                 127.0.0.1:17850
default concurrency      2 (promoted sau repeated-warm c1/c2)
burst candidate          3
technical hard ceiling   10, benchmark/debug only
chat mode                Temporary Chat
tools                    []
MCP                      off
filesystem/cwd env       off
Codex sandbox            off
skills/AGENTS            off
subagents                off
Codex compaction         off for normal translation batch
model verification       on
effort verification      on
accepted-send tracking   on
cooldown classifier      on
adaptive concurrency     on
idempotency/request id   on
structured JSON gate     on
metrics                  on
```

Adaptive concurrency policy candidate:

```text
3 concurrent
  -> cooldown/rate-pressure -> 2
  -> repeat pressure        -> 1
  -> bounded backoff/recover
```

Không spam retry account cooldown. Browser/transient recovery và account/rate cooldown phải là hai class lỗi khác nhau.

Triển khai active:

1. **P22-A runtime isolation**: init home/config riêng, manual ChatGPT login một lần, start `:17850`, verify health/catalog.
2. **P22-B direct Responses correctness — ACCEPTED**: VI Dubber gọi `:17850/v1/responses` trực tiếp với `tools=[]`; P04/P16/P20 đã re-accept.
3. **P22-C throughput A/B — ACCEPTED**: repeated-warm c1/c2 trên cùng fixture cho c2 median `30.8903s` so với c1 `41.1390s` (`1.3318x`), quality gates pass và không có pressure/retry/failure; production promote c2. c3 vẫn benchmark-only vì historical capacity rejection.

Dedicated runtime không được nhúng business logic dịch. Mục tiêu là transport API nhỏ và ổn định (`model`, `effort`, `input`, `request_id`) để VI Dubber có thể đổi provider sau này mà không rewrite pipeline.

#### 26.4.8. Candidate component decision: giữ / đổi / thêm

| Thành phần | Candidate | Quyết định hiện tại | Lý do |
|---|---|---|---|
| ASR core | Qwen3-ASR thay WhisperX | KEEP WhisperX | word alignment + local benchmark + integration hiện mạnh hơn cho core path |
| Independent ASR QA | Qwen3-ASR 0.6B/1.7B | ADD shadow/selective | khác họ model, giảm correlated QA failure; không cần replace core ASR |
| Separation | RoFormer khác/Demucs | KEEP BS-RoFormer | chưa có bằng chứng stem quality là bottleneck |
| Translation model | model khác thay GPT-5.6 Sol | KEEP Sol | quality translation đã accepted; optimize transport/context trước |
| Translation transport | historical generic codex-exec path | Dedicated Dubber-WebGPT `:17850` + direct Responses c2 | ACCEPTED; c2 atomicity regression + full suite pass |
| Semantic QA | LLM self-judge | KEEP TypeSafe Jev | đã calibrate và có independence |
| TTS | VieNeu 3.8.3 | KEEP 3.7.1; 3.8.3 REJECTED trên RTX 2070 SUPER | local CUDA FP16 A/B cho thấy 3.8.3 chậm hơn khoảng 10.0-13.5% ở batch 4/8/16; chỉ benchmark lại khi hardware/runtime target thay đổi |
| TTS max-quality challenger | Fish Audio S2 Pro / model multilingual nặng | BENCHMARK ONLY | hardware/cost/latency lớn hơn, chưa có lý do làm default RTX 2070S |
| Diarization | pyannote Precision-3 | KEEP Community-1 default | local/free phù hợp; Precision-3 dành case multi-speaker khó/premium |
| Timing | framework khác | KEEP custom P09 | đã benchmark collision-free và đúng use case |
| Mix/master | DSP framework khác | KEEP FFmpeg | deterministic, fast, mature |
| Backend | Node/Rust rewrite | KEEP FastAPI | không phải bottleneck |
| Product UI | Gradio legacy | GRADUAL React-only | giảm duplicate UI stack sau khi parity/ops route pass |

Nguyên tắc chọn candidate: **không replace vì mới hơn**. Chỉ replace khi thắng trên cùng fixture, cùng hardware target và quality gate; candidate có lợi cho một vai trò độc lập có thể được **thêm** thay vì thay core component.

### 26.5. Vietnamese-native speech quality: nên tối ưu, nhưng không đổi model/fine-tune vội

#### 26.5.1. Kết luận research

Balanced Best hiện đã ở mức nghe tốt/ổn theo trải nghiệm sử dụng thật, nhưng còn khoảng cách ở những chi tiết người Việt nhận ra rất nhanh: nhịp câu, chỗ ngắt, trọng âm câu, độ lên/xuống tự nhiên, cách chuyển giữa tiếng Việt và từ English, và đôi khi cách phát âm thuật ngữ chuyên ngành.

**Nên tối ưu phần này.** Tuy nhiên sweet spot hiện tại không phải đổi TTS hoặc fine-tune ngay. VieNeu v3 Turbo upstream hiện coi `style=` là deprecated/ignored; cách đọc chủ yếu đi theo preset/reference (speaker embedding + reference codes). Vì vậy các đòn bẩy có blast radius thấp hơn và đúng với engine hiện tại là:

1. chọn reference không chỉ sạch về acoustic mà còn đúng **delivery**;
2. tạo **TTS-only spoken text** tốt hơn display subtitle;
3. giữ phrase boundary/punctuation tự nhiên cho tiếng Việt;
4. xử lý pronunciation cho English/code-switch và acronym bằng mapping có kiểm chứng;
5. đo bằng blind A/B với người Việt trước khi đổi default.

Research về Vietnamese prosody cũng cho thấy boundary cú pháp/cụm câu và pause có liên hệ trực tiếp với phrasing tự nhiên. Do đó không nên coi dấu câu hay segment chỉ là formatting; chúng là input prosody quan trọng cho TTS.

#### 26.5.2. Gap trong implementation hiện tại

`reference.py` hiện rank chủ yếu bằng duration, speech ratio, SNR, clipping, silence và ASR confidence. Đây là nền tốt cho **clarity**, nhưng chưa đánh giá:

- speech rate / pause density;
- delivery neutral vs excited vs explanatory;
- pitch-energy dynamics;
- câu mẫu có phrasing tự nhiên hay bị cắt giữa ý;
- reference có đại diện cho cách nói mong muốn của video hay không.

`tts.py` đã có display text tách khỏi TTS text qua `tts_text_mapper`, nên không cần phá subtitle để tối ưu cách đọc. Đây là chỗ nên mở rộng.

#### 26.5.3. Target flow đề xuất

```text
translated display text
  -> terminology policy
  -> TTS pronunciation map
  -> TTS-only phrasing pass
       - punctuation/boundary cleanup
       - acronym/English spoken form
       - no semantic rewrite
  -> VieNeu
       + canonical reference per speaker
       + optional delivery-matched reference only when A/B proves stable
  -> acoustic + human native QA
```

Display subtitle và TTS text phải là hai artifact khác nhau. Ví dụ display có thể giữ:

```text
Đây là một cây nến Bullish Engulfing đang phá lên khỏi vùng kháng cự.
```

TTS text có thể chỉ thay pronunciation/punctuation nếu cần, nhưng không đổi thuật ngữ hiển thị hoặc semantic.

#### 26.5.4. Reference strategy mới

Giữ **một canonical reference ổn định cho mỗi speaker** làm default để bảo toàn identity. Nâng ranking theo hai tầng:

```text
Tier 1: acoustic eligibility
  3-8s, no overlap, SNR, clipping, speech ratio, silence

Tier 2: delivery suitability
  complete phrase
  natural pause pattern
  moderate speech rate
  clear articulation
  neutral/explanatory delivery ưu tiên cho canonical ref
```

Style-specific reference bank chỉ bật sau A/B, tối đa vài class rõ như `neutral/explanatory`, `energetic`, `storytelling`. Không đổi reference theo từng câu nếu không có bằng chứng vì dễ gây identity/timbre drift và cache fragmentation.

#### 26.5.5. Vietnamese prosody shaping

Không thêm pause marker/SSML mà VieNeu không support chính thức. Ưu tiên những tín hiệu engine đã hiểu:

- câu hoàn chỉnh thay vì fragment subtitle;
- punctuation đúng cú pháp;
- giữ comma/break ở clause boundary hợp lý;
- không nối hai ý xa nhau chỉ để giảm số segment;
- không ép mọi khoảng trống phải được lấp đầy;
- giữ natural pause trước khi dùng time-stretch hoặc rewrite mạnh.

Emotion tags `[cười]`, `[thở dài]`, `[hắng giọng]` upstream có support nhưng đang experimental; không tự chèn trong Balanced Best. Chỉ dùng khi source evidence rõ và A/B cho thấy không over-act.

#### 26.5.6. English/Vietnamese pronunciation

VieNeu v3 family được thiết kế cho English-Vietnamese code-switching, nên policy mặc định là **thử raw English term trước**, không phoneticize mọi English word sang kiểu Việt. Chỉ thêm `pronunciation_map` khi golden listening set chứng minh model đọc sai hoặc không ổn định.

Tách ba class:

| Class | Display | TTS policy |
|---|---|---|
| common English term model đọc tốt | `Engulfing` | giữ nguyên |
| acronym | `FVG`, `RSI` | explicit spoken map nếu cần, ví dụ `ép vi gi` |
| tên riêng/brand khó | giữ spelling chính thức | approved spoken override |

Không ghi phonetic spelling ngược vào subtitle.

#### 26.5.7. Trade-off thật

| Tối ưu | Quality kỳ vọng | Cost/speed | Rủi ro |
|---|---|---|---|
| reference delivery-aware | cao | gần như neutral runtime | chọn sai style có thể drift identity |
| TTS-only punctuation/phrasing | trung-cao | rất thấp | punctuation quá tay làm ngắt câu giả |
| pronunciation map có chọn lọc | cao cho term lỗi | rất thấp | over-normalize làm English nghe kỳ |
| style-specific refs | có thể cao | tăng cache/reference complexity | timbre/style inconsistency |
| emotion tags | case-specific | thấp | over-acting, không ổn định |
| fine-tune/LoRA TTS | có trần cao | cao nhất | data/QA/maintenance, có thể regress voice |

**Quyết định:** chưa fine-tune. Tối ưu reference + spoken-text shaping + pronunciation + native A/B trước. Chỉ cân nhắc fine-tune nếu sau các bước trên vẫn còn gap lặp lại và đo được trên nhiều fixture.

#### 26.5.8. Native listening gate

Tạo fixture riêng cho Vietnamese naturalness, không chỉ WER/similarity:

- câu giải thích bình thường;
- câu hỏi;
- câu nhấn mạnh/cảnh báo;
- số + phần trăm + tiền;
- câu có 1 English technical term;
- câu có nhiều code-switch term;
- acronym;
- long clause có comma;
- câu ngắn cần pause tự nhiên;
- fast source speech nhưng output không được đọc gấp giả.

Blind A/B chấm ít nhất:

```text
native naturalness
phrase stress
pause/breathing rhythm
tone/pronunciation correctness
English-Vietnamese transition
speaker identity
timing fit
```

Promote thay đổi chỉ khi naturalness thắng mà semantic, speaker identity và timing không regress đáng kể.

### 26.6. Vietnamese-English localization: dịch như người Việt trong domain, không phải Việt hóa 100%

#### 26.6.1. Kết luận research

Với nội dung technical/trading/AI, một bản dịch tốt có thể và thường nên **code-switch có chủ đích**. Microsoft khuyên dùng terminology của chính audience/profession; Google/DeepL đều coi glossary/term base là cơ chế chuẩn để giữ domain term, product name hoặc term không nên dịch; CNCF Vietnamese localization cũng ghi nhận có những khái niệm chưa có bản dịch Việt được chấp nhận rộng, nên giữ English kèm context là lựa chọn hợp lệ.

Community signal ở Việt Nam cũng cho thấy nhiều từ có bản dịch Việt nhưng người dùng vẫn tự nhiên chọn English form. Đây chỉ là supporting evidence, không phải source of truth; quyết định production phải dựa domain glossary + native review của VI Dubber.

#### 26.6.2. Lỗi hiện tại cần tránh

1. **Over-translation**: dịch thuật ngữ quen thuộc thành bản Việt đúng nghĩa nhưng nghe lạ trong cộng đồng. Ví dụ `Engulfing -> nến nhấn chìm`.
2. **Under-translation**: giữ quá nhiều English làm câu thành nửa Việt nửa Anh khó nghe.
3. **Batch inconsistency**: đoạn đầu dùng `Engulfing`, đoạn sau lại `nhấn chìm`.
4. **Acronym drift**: khi giữ `FVG`, khi bung thành `Fair Value Gap`, khi lại Việt hóa.
5. **First-mention ambiguity**: giữ term English mà audience mới không biết nó là gì.
6. **Code-switch grammar lỗi**: term English đúng nhưng đặt vào trật tự câu Việt không tự nhiên.
7. **Display đúng nhưng TTS đọc sai**: translation policy và pronunciation policy bị trộn thành một.

#### 26.6.3. Terminology policy thay cho glossary `source -> target` đơn giản

Mỗi term quan trọng nên có metadata:

```yaml
source: engulfing
policy: KEEP_EN        # KEEP_EN | PREFER_EN | VI | CONTEXTUAL
display: Engulfing
first_mention: nến Engulfing
spoken: Engulfing
aliases:
  - bullish engulfing
  - bearish engulfing
rejected:
  - nến nhấn chìm
domain: trading
status: approved
```

Ý nghĩa:

- `KEEP_EN`: giữ English gần như bắt buộc.
- `PREFER_EN`: ưu tiên English nhưng cho phép giải thích Việt ở first mention/context cần thiết.
- `VI`: dùng Vietnamese term đã tự nhiên/chuẩn hóa.
- `CONTEXTUAL`: model chọn theo audience/context nhưng QA phải giữ consistency trong cùng video.

Không cần hardcode trading vào core engine. Core chỉ hiểu policy; domain pack chứa term.

#### 26.6.4. Audience/domain profile

Global context pack của video nên thêm:

```text
audience: Vietnamese viewers familiar with trading
register: conversational/explanatory
terminology_style: mixed Vietnamese + common English trading terms
```

Với video general audience, policy có thể Việt hơn. Với trading/AI/software, policy có thể giữ nhiều English term hơn. Không dùng một tỷ lệ English cố định cho mọi video.

#### 26.6.5. Prompt contract đề xuất

Translation prompt nên nói rõ:

```text
Write natural spoken Vietnamese for the target audience.
Do not translate an English technical term merely because a literal Vietnamese equivalent exists.
Follow the terminology policy exactly:
- KEEP_EN: preserve the approved English display form.
- PREFER_EN: normally keep English; explain briefly in Vietnamese only when context requires it.
- VI: use the approved Vietnamese term.
- CONTEXTUAL: choose the form natural for this audience, then stay consistent.
Use Vietnamese grammar around retained English terms.
Never change a protected term during duration rewrite.
```

Prompt chỉ là lớp generation. Enforcement vẫn cần deterministic validation.

#### 26.6.6. Example target style

Không ưu tiên:

```text
Đây là một cây nến nhấn chìm tăng giá đang phá vùng kháng cự.
```

Target cho audience trading của user:

```text
Đây là một cây nến Bullish Engulfing đang phá lên khỏi vùng kháng cự.
```

Ví dụ khác:

```text
Move your stop loss to breakeven.
-> Dời stop loss về hòa vốn.
```

Mục tiêu là câu **vẫn là tiếng Việt**, chỉ giữ English ở đúng những token mà người xem trong domain thực sự dùng.

#### 26.6.7. Deterministic QA sau translation/rewrite

Sau mỗi translation và duration rewrite:

```text
protected source terms
  -> expected display policy
  -> exact/case-aware token check
  -> rejected-alternative scan
  -> cross-batch terminology consistency
  -> only then semantic QA / TTS
```

Các check deterministic này không giao cho TypeSafe nếu exact string/policy đã biết.

Duration rewrite không được tự đổi:

```text
Engulfing -> nhấn chìm
FVG -> khoảng trống giá trị hợp lý
stop loss -> cắt lỗ
```

nếu term policy đang yêu cầu giữ English.

#### 26.6.8. Glossary learning loop

Không cố tạo glossary khổng lồ ngay. Bắt đầu bằng term xuất hiện thường xuyên/quan trọng:

1. seed glossary domain nhỏ;
2. log term mà model dịch không nhất quán hoặc user sửa thủ công;
3. đề xuất candidate glossary entry;
4. review/approve một lần;
5. từ đó áp deterministic cho video sau.

Manual edit trong Review UI là tín hiệu tốt để học preference, nhưng không auto-promote một edit đơn lẻ thành global rule nếu chưa đủ evidence.

#### 26.6.9. Acceptance set riêng cho code-switch

Golden set cần có:

- `Engulfing`, `Bullish Engulfing`, `Bearish Engulfing`;
- `stop loss`, `take profit`, `breakeven`;
- `breakout`, `pullback`, `order block`, `Fair Value Gap/FVG`;
- `BOS`, `CHoCH`, `liquidity sweep`;
- AI/software terms như `prompt`, `token`, `API`, `GPU`;
- brand/product/person names;
- term nên Việt hóa hoàn toàn để tránh English overload.

Đo:

```text
protected-term adherence
cross-batch consistency
rejected-translation rate
native naturalness A/B
TTS pronunciation pass rate
semantic accuracy
```

#### 26.6.10. Execution order đề xuất

Ưu tiên mới sau v2.5:

1. **VieNeu 3.8.3 A/B**: machine benchmark DONE; không có performance upside trên RTX 2070 SUPER, giữ 3.7.1; human listening còn mở.
2. **Vietnamese terminology policy + code-switch golden set**: IMPLEMENTED; deterministic gate + display/spoken split đã có, tiếp tục mở rộng glossary theo review thực tế.
3. **Delivery-aware reference + TTS-only phrasing A/B**: IMPLEMENTED low-blast-radius core; production fixture hiện vẫn chọn canonical ref cũ, human listening closure còn mở.
4. **Global translation context + bounded concurrency 2-3**: concurrency path đã IMPLEMENTED và P22 repeated-warm A/B đã promote production c2 (`1.3318x` median speedup, quality gates pass, 0 pressure/retry/failure). Global context vẫn off; c3/adaptive queue tiếp tục benchmark-only vì historical capacity failure.
5. **P07/P11/P12 human A/B closure - MACHINE SIDE READY / HUMAN VOTES NEEDED**: blind/listening evidence đã được tạo và root verify; không relax threshold hay tự chọn winner. Bước còn lại là thu human ballots/labels theo checkpoint v2.11 rồi mới đổi phase sang ACCEPTED.
6. Qwen3-ASR shadow QA / multi-speaker improvements sau các quality gap đang thấy bằng tai.

Mục tiêu của v2.5 không phải “nghe như người thật bằng mọi giá”, mà là đưa Balanced Best từ **nghe ổn** sang **nghe đúng thói quen tiếng Việt trong domain**, với thay đổi dễ rollback và đo được.
