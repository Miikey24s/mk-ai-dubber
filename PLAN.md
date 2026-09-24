# VI Dubber - Full Optimization Plan

Phiên bản: **v1.9 - 24/09/2026**  
Trạng thái: **IN PROGRESS / P21, P04, P16, P20 đã ACCEPTED; P00, P01, P02, P06, P10, P15 DONE; tiếp tục hoàn thiện các benchmark P17 và listening A/B**  
Mục tiêu AI dịch target: **Codex ChatGPT Web dedicated instance 2 tại `127.0.0.1:17842`, live model catalog và model do người dùng chọn trên UI**  
Project: `D:\ANNAM\TradingWorkspace\projects\vi-dubber`  
Translation runtime hiện khóa vào managed Codex ChatGPT Web **instance 2 / port 17842**. Global Codex/Cockpit route của máy được giữ nguyên; VI Dubber override provider URL theo từng invocation và không được tự failover sang instance 1 hoặc Aurora.

> Đây là plan nguồn cho đợt tối ưu sâu `vi-dubber`. README tiếp tục là tài liệu vận hành ngắn gọn; file này giữ mục tiêu, thứ tự triển khai, contract giữa worker/subagent, benchmark, acceptance, rollback và trạng thái từng phase.

---

## 0. Quyết định tổng thể

Không rewrite toàn bộ stack và không đổi model hàng loạt.

Stack hiện tại đã có nền tốt:

- BS-RoFormer cho source separation.
- WhisperX `large-v3` CUDA FP16 cho ASR + alignment.
- Codex Web GPT instance 2 là translator/rewrite backend hiện tại; live catalog ngày 23/09/2026 có `chatgpt-web/gpt-5.6-sol` và `chatgpt-web/gpt-5.6-sol-instant`.
- VI Dubber khóa transport vào `http://127.0.0.1:17842/v1` theo từng Codex invocation; không sửa global `~/.codex/config.toml` và không dùng Aurora trong đường chạy hiện tại.
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

Mục tiêu không phải "chạy nhanh bằng mọi giá" hoặc "quality tối đa bằng mọi giá". Mục tiêu mặc định là **Balanced Best**: tăng chất lượng và throughput đồng thời ở những nơi hiện đang lãng phí compute; chỉ chấp nhận trade-off khi tính năng đó thực sự đòi hỏi, ví dụ lip-sync hoặc Max Quality multi-candidate.

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
- Baseline cũ: Codex WebGPT OK qua Codex WebGPT Local Access. Từ v1.8, route product được khóa riêng vào instance 2 `:17842`.
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

Snapshot local được re-check ngày 23/09/2026:

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

Các con số này là **runtime snapshot**, không phải hằng số vĩnh viễn. Main worker phải kiểm tra lại capability/slots trước mỗi execution wave lớn.

Product translation không kế thừa global provider URL: mỗi `codex exec` do VI Dubber tạo phải override `model_provider=codex_local_access` và `model_providers.codex_local_access.base_url=http://127.0.0.1:17842/v1`. Nếu health port, catalog hoặc selected model không khớp thì fail closed.

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
- lip-sync hoàn hảo ở mọi góc mặt;
- RTF `<1` trước khi benchmark thật trên RTX 2070 SUPER.

### 2.3. Ba profile sản phẩm

#### Fast

Mục tiêu: batch processing, tốc độ ưu tiên nhưng không phá semantic correctness.

- giữ ASR đủ tốt;
- translation 1-pass;
- TypeSafe chỉ critical gate;
- VieNeu GPU batch lớn nhất đã benchmark an toàn;
- QA chọn lọc;
- lip-sync tắt.

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
- lip-sync tắt mặc định.

#### Max Quality

Mục tiêu: video quan trọng, chấp nhận chậm hơn.

- stricter semantic thresholds;
- candidate fan-out chỉ ở segment khó;
- stronger acoustic QA;
- prosody/style analysis sâu hơn;
- có thể bật full-video final ASR;
- optional lip-sync;
- optional cloud/proprietary TTS A/B nếu người dùng cho phép cost/privacy/network dependency.

---

## 3. Nguyên tắc kiến trúc

1. **Code deterministic giữ control flow.** Duration, hashes, exact numbers, cache keys, routing state, path ownership và retry policy nằm trong code.
2. **Selected ChatGPT Web model sinh ngôn ngữ.** Dịch, rewrite, contextual phrasing và candidate generation thuộc generative translation provider; target transport hiện tại là Codex WebGPT instance 2 `:17842`.
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
| local dubbing + lip-sync adapter | video-dubbing-translator | local pipeline boundaries, voice/lip-sync integration |
| dubbing + digital human | Linly-Dubbing | lip-sync/digital-human stage isolation, web workflow |
| job progress/runtime architecture | videoTranslator | FastAPI/WebSocket progress, config/runtime telemetry, time-stretch patterns |
| mature desktop/web dubbing UX | pyVideoTrans | UI/workflow/reference only unless license review explicitly permits desired reuse model |
| lip-sync engine | MuseTalk / LatentSync | optional P18 adapter, VRAM/perf/quality A/B |

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
optional lip-sync post-process
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
| P18 | Optional lip-sync | visual cao | chậm | core complete | optional only |
| P19 | Optional premium/cloud A/B | có thể cao | variable | P17 | explicit permission |
| P20 | Release/ops hardening | reliability | repeatability | all selected | ready-to-use |
| P21 | Dedicated ChatGPT Web backend + model catalog | rất cao | cao | P04,P16,P20 | instance 2 direct route verified + no cross-instance fallback |

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

Target P21 hiện tại:

- provider chính: Codex WebGPT instance 2 `127.0.0.1:17842`;
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
- optional lip-sync;
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

## P18 - Optional lip-sync

### Goal

Finishing pass, không core dependency.

### Candidate evaluation

RTX 2070 SUPER 8 GB ưu tiên tool phù hợp VRAM thật.

Không mặc định dùng version/model vượt VRAM.

### Trade-off

- render chậm;
- artifact vùng miệng/mặt;
- extra model/dependency;
- có thể giảm visual quality dù sync tăng.

### Gate

Chỉ làm sau audio dubbing core đạt P17 acceptance.

### Acceptance

A/B trên talking head:

- lip alignment;
- face identity;
- jitter;
- artifact;
- render time.

Nếu visual regress đáng kể, feature giữ optional/off.

---

## P19 - Optional premium/cloud model A/B

### Goal

Chỉ dành cho trường hợp người dùng muốn absolute quality hơn local-first.

Potential categories:

- proprietary/cloud TTS;
- stronger translation provider;
- external lip-sync.

### Gate bắt buộc

Hỏi trước khi:

- cần API key mới;
- có chi phí;
- gửi audio/voice/video ra cloud;
- thay privacy boundary.

Không đưa cloud dependency vào Balanced Best mặc định.

ChatGPT Web qua dedicated instance 2 ở P21 **không được xếp vào P19 chỉ vì là network AI**: đây là target translation backend người dùng đã chọn cho VI Dubber. P19 dành cho provider/API trả phí bổ sung, TTS/lip-sync cloud hoặc privacy boundary mới ngoài account ChatGPT Web hiện tại.

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

## P21 - Dedicated ChatGPT Web backend + dynamic model catalog

### Goal

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

Global Codex/Cockpit config không bị mutate. Instance 2 là transport duy nhất của product path hiện tại; mất instance 2 phải báo lỗi rõ thay vì tự chuyển sang `17841`, Aurora hoặc model khác.

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
| **CP7 Full Acceptance** | P17 regression + selected P18/P19 + P20 ops | representative short/medium/long + resume + blind A/B + perf regression pass |

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

- P18 lip-sync;
- P19 cloud/premium A/B;
- P20 final ops.

Không để optional extras chặn core Balanced Best release.

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
| lip-sync artifact | mặt méo/jitter | optional feature only |
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
- lip-sync trước audio quality;
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
22. P21 pass: WebGPT instance 2 direct route hoạt động, live model catalog trên UI chạy, selected model/catalog được snapshot/fingerprint, wrong port/model unavailable fail closed và không tự cross-instance/Aurora/Qwen.

Optional lip-sync không nằm trong Balanced Best DoD.

---

## 21. Definition of Done cho Max Quality

Ngoài Balanced Best:

- stricter semantic route;
- hard-case candidate fan-out;
- stronger final QA;
- multi-speaker hard cases phù hợp fixture;
- optional lip-sync A/B nếu user bật;
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
| P03 Segmentation | PARTIAL | short real fixture: 53 source -> 50 smart turns, 0 turn <1s; smart E2E còn 3/50 overflow; thiếu long 734-turn A/B + listening |
| P04 Translation | ACCEPTED | live WebGPT instance 2 pass (high: 43.6s, med: 38.1s, instant_low: 35.0s); 100% adherence glossary.yaml (FVG, order block, sweeps liquidity, market structure); 100% critical tokens (OpenAI, Sam Altman, GPT-4, 86.400, 99,8%, negation, modality, win/loss); tools/p04_quality_receipt passed=true |
| P05 TypeSafe | PARTIAL | atomic semantic heads, rewrite gate, raw-cache reuse và retry đã có; còn labeled golden calibration/model pinning |
| P06 Pronunciation | DONE | golden pronunciation set + display/TTS text separation pass; deterministic number/currency/%/unit/date/time handling, explicit acronym/name/technical overrides và ambiguous-format fail-closed đều có regression |
| P07 Voice reference | PARTIAL | acoustic selector 3-8s, overlap reject, canonical ref/speaker tích hợp; còn blind A/B |
| P08 GPU TTS | PARTIAL | RTX 2070 SUPER CUDA FP16 batch 4: RTF 0.1185, ~30% wall-time gain; CPU fallback pass; còn quality A/B/default switch |
| P09 Elastic timing | PARTIAL | timing windows/borrow/action policy + cache version wired; smart E2E overflow 3/50; còn tempo/sync + listening acceptance |
| P10 Assembly | DONE | exact timeline, fades, equal-power overlap/collision regression pass; streaming 30 s blocks trên timeline 3238.67 s chỉ tăng peak RSS ~16.7 MiB, output duration 3238.6728125 s |
| P11 Mix/master | PARTIAL | two-pass loudnorm + limiter + metrics integrated; current 226s remux đạt -14.45 LUFS / -1.61 dBTP / no clipping; còn listening A/B |
| P12 Segment QA | PARTIAL | real 50-segment E2E: 3/50 flag đều là lỗi thật (2 thiếu critical `cần`, 1 timing overflow), repair 1 đoạn, global ASR similarity 98.4%; còn xử lý 3 lỗi còn lại + broader FPR/listening evidence |
| P13 ASR/separator tune | PARTIAL | WhisperX 4/6/8 benchmark + CUDA OOM fallback/cleanup; chưa đủ broader parity/separator A/B để đổi default |
| P14 Multi-speaker | PARTIAL | speaker/overlap visibility + review flags có; còn real 2-speaker + overlap fixture |
| P15 Profiles | DONE | Fast/Balanced/Max resolved behavior machine-readable và profile-relevant fingerprint tests pass |
| P16 Review UI | ACCEPTED | web.py kết nối live catalog instance 2 (:17842); dynamic effort selector (gpt-5.6-sol: medium/high default high; instant: low); selective rerender invalidation chỉ hủy downstream audio của segment sửa và giữ nguyên cache thô; test_web_review pass (44/44); Playwright Chromium headless E2E pass (3/3), chụp screenshot studio thật, reload persistence verify |
| P17 Regression suite | PARTIAL | harness fail-closed + WER/critical-token/semantic metrics đã có, fixture coverage thật 5/10 (nguồn 10/10); clean talking-head cold A/B cải thiện WER 9.09% -> 7.07%, fast-English fixture WER 2.84%/critical token 100%, technical/numeric fixture bắt được lỗi mất `33%` dù global QA 97.2%; còn 3 synthetic fixtures chưa process |
| P18 Lip-sync | OPTIONAL/BLOCKED | giữ optional; chỉ đánh giá sau khi P17 core acceptance pass |
| P19 Premium/cloud | OPTIONAL/BLOCKED | cần user opt-in cho API/cost/privacy; không thuộc Balanced Best mặc định |
| P20 Ops hardening | ACCEPTED | fault matrix cover: 429 backoff, selected model missing fail-closed, port unreachable, live disk preflight, child process kill across 4 stages (asr, translation, tts, mix_mux) với lease recovery/pause sạch, và atomic fsync temporary sibling replace an toàn khi kill; test_fault_contracts pass |
| P21 ChatGPT Web backend | ACCEPTED | instance 2 :17842 live connected; live catalog discovery (gpt-5.6-sol, gpt-5.6-sol-instant); live translation pass across high/medium/instant_low; fail-closed khi gọi effort không hợp lệ (low trên sol); per-invocation codex override khóa instance 2; test_webgpt_retry pass |

---

## 24. Sources tham chiếu kỹ thuật

Main worker phải re-check version khi bắt đầu phase có dependency external.

- TypeSafe docs index: `https://docs.typesafe.ai/llms.txt`
- TypeSafe System One/building patterns: docs links từ index.
- TypeSafe SDE cascade: `https://docs.typesafe.ai/cookbooks/sde_cascade`
- TypeSafe parallel questions: `https://docs.typesafe.ai/cookbooks/parallel_questions`
- VieNeu-TTS upstream: `https://github.com/pnnbao97/VieNeu-TTS`
- WhisperX upstream: `https://github.com/m-bain/whisperX`
- faster-whisper upstream: `https://github.com/SYSTRAN/faster-whisper`
- FFmpeg filters: `https://ffmpeg.org/ffmpeg-filters.html`
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

---

## 26. Khuyến nghị execution hiện tại

P21, P04, P16, P20 đã hoàn thành acceptance thông qua wave kiểm thử đa subagent. Trọng tâm tiếp theo:

```text
P17: Hoàn thiện 3 synthetic audio fixtures còn lại (two-speakers, overlapping-speech, emotional-prosody-stress)
 -> Đạt 10/10 available fixtures
 -> Chạy blind listening A/B và Typesafe golden calibration
 -> Đánh giá P18 (optional lip-sync) sau khi P17 pass toàn diện
```

Không clone Aurora cho active path. Không đổi global Codex/Cockpit route chỉ để phục vụ VI Dubber; route product phải tiếp tục được cô lập bằng exact instance-2 override.

Sau P21, mục tiêu chính vẫn giữ nguyên: **tiếng Việt tự nhiên hơn rõ, ít cảm giác AI đọc subtitle, giữ voice tốt hơn và throughput nhanh hơn mà không phải hạ model quality làm mặc định**.
