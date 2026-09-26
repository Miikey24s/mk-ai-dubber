# VI Dubber

Pipeline lồng tiếng video English -> Vietnamese, có web dashboard cục bộ và nhận video từ máy hoặc YouTube.

Backend dịch hiện tại được khóa vào **Dedicated Dubber-WebGPT** tại `http://127.0.0.1:17850/v1`. Runtime này dùng home/browser login/process riêng cho VI Dubber và không thay đổi global Codex/Cockpit route. Model mặc định là `chatgpt-web/gpt-5.6-sol`, nhưng danh sách model được lấy trực tiếp từ live catalog của runtime riêng để tránh chạy một model đã biến mất hoặc bị đổi tên. Production translation dùng bounded concurrency `2`; global translation context vẫn tắt mặc định.

Aurora tạm dừng theo quyết định hiện tại. Adapter local/hybrid cũ vẫn được giữ trong source để rollback/test và để job lịch sử còn đọc được provenance, nhưng không phải lựa chọn product UI hiện tại và không được dùng làm fallback tự động cho Dedicated Dubber-WebGPT.

## Stack

- BS-Roformer: tách lời thoại khỏi nhạc/SFX.
- WhisperX large-v3: transcript + word timing.
- pyannote community-1: tách người nói khi có Hugging Face token.
- Dedicated Dubber-WebGPT: backend chất lượng cao qua runtime riêng (`127.0.0.1:17850`), mặc định `chatgpt-web/gpt-5.6-sol` và dùng direct Responses tool-free.
- Qwen3-14B GGUF + llama.cpp: backend local và fallback.
- VieNeu-TTS v3 Turbo FP32 ONNX: TTS tiếng Việt 48 kHz + instant voice cloning.
- FFmpeg: khớp thời lượng, mix, loudness và mux video.
- Re-ASR QA: nhận dạng lại track tiếng Việt rồi so với script đã render.
- TypeSafe semantic QA (shadow): so meaning English -> Vietnamese sau dịch và kiểm tra lại các câu bị rút gọn; không chặn pipeline.

## Cài đặt

```powershell
cd D:\ANNAM\TradingWorkspace\projects\vi-dubber
.\setup.ps1
```

Runtime và model cache nằm trong project trên ổ D. `setup.ps1` không cài FFmpeg vào PATH hệ thống; FFmpeg portable nằm trong `tools/`. WhisperX, BS-Roformer và VieNeu sẽ tải model của chúng vào `models/` ở lần dùng đầu.

Setup mặc định chỉ chuẩn bị đường chạy Codex WebGPT, không tự tải Qwen khoảng 9 GB. Qwen hiện không nằm trong đường chạy product mặc định; lệnh provision dưới đây chỉ cần khi chủ động quay lại local/hybrid để rollback hoặc test:

```powershell
.\setup.ps1 -ProvisionLocalModel
```

Lệnh opt-in này tải model qua Hugging Face vào `models/llm/<repo>/`, kiểm tra file và đối chiếu SHA-256 với LFS ETag của upstream khi có. Có thể provision từ file GGUF đã tải sẵn mà không dùng mạng; truyền checksum nếu muốn xác minh nguồn local trước khi thay file đích:

```powershell
.\setup.ps1 -ProvisionLocalModel `
    -LocalModelSource "D:\model-cache\Qwen3-14B-Q4_K_M.gguf" `
    -LocalModelSha256 "<64-ky-tu-hex>"
```

File được copy qua file `.partial` rồi mới thay atomically vào đích. Các lệnh trên chỉ ghi trong project, không sửa PATH hay system configuration. llama.cpp portable nằm trong `tools/llama/`.

## Chạy

Web dashboard:

```powershell
.\start-web.ps1
```

Mở `http://127.0.0.1:7860`, chọn tệp trên máy hoặc dán URL YouTube, sau đó chọn backend dịch. Dashboard hiển thị progress theo các stage thật của pipeline, video đầu ra, SRT và thống kê tác vụ.

Khi dùng WebGPT, pipeline không đổi route global trong `~/.codex/config.toml`. VI Dubber gọi trực tiếp `http://127.0.0.1:17850/v1/responses`, health-check port và live model catalog trước khi chạy; coding harness, MCP, cwd/filesystem và subagents không đi vào production translation request.

Khởi tạo runtime riêng một lần:

```powershell
uv run vi-dubber webgpt-runtime init
uv run vi-dubber webgpt-runtime login
uv run vi-dubber webgpt-runtime start
uv run vi-dubber webgpt-runtime status
```

`login` mở Chrome profile riêng để người dùng tự đăng nhập ChatGPT. Runtime home mặc định nằm ngoài Git tại `D:\ANNAM\TradingWorkspace\.runtime\dubber-webgpt`; có thể override bằng `VI_DUBBER_WEBGPT_HOME`. Core WebGPT mặc định lấy từ `D:\ANNAM\AI\codex-chatgpt-web-cockpit`; có thể override bằng `VI_DUBBER_WEBGPT_CORE`.

Semantic QA dùng TypeSafe khi `TYPESAFE_API_KEY` có trong environment. Nội dung English/Vietnamese của các segment được gửi tới TypeSafe để chấm `faithful / partial / wrong` và kiểm tra các fact quan trọng. Chế độ hiện tại là `shadow`: kết quả được lưu vào `work/<job>/semantic_qa.json`, nhưng lỗi TypeSafe hoặc đoạn bị flag không làm dừng tác vụ.

Terminology domain được cấu hình trong `glossary.yaml`. Ngoài mapping cũ `source -> target`, glossary có thể khai báo policy `KEEP_EN`, `PREFER_EN`, `VI` hoặc `CONTEXTUAL`, audience profile, dạng hiển thị và dạng đọc TTS riêng. Pipeline kiểm tra deterministic sau dịch và sau duration rewrite để các term đã khóa như `Engulfing`, `FVG`, `order block` không bị Việt hóa hoặc đổi form ngoài policy; subtitle vẫn giữ display form còn TTS có thể dùng spoken override riêng.

CLI:

Một người nói, không cần token:

```powershell
.\run.ps1 dub "D:\video\input.mp4"
```

Backend CLI hiện tại:

```powershell
.\run.ps1 dub "D:\video\input.mp4" --translator webgpt
```

Đầu ra mặc định là `input_vi.mp4` và `input_vi.vi.srt` nằm cạnh video gốc.

Dùng một voice reference 3-8 giây thay vì tự lấy reference từ video:

```powershell
.\run.ps1 dub "D:\video\input.mp4" --voice-ref "D:\voice\ref.wav"
```

Nhiều người nói: chấp nhận điều kiện model `pyannote/speaker-diarization-community-1`, tạo Hugging Face read token, set token trong session rồi chạy:

```powershell
$env:HUGGINGFACE_TOKEN = "..."
.\run.ps1 dub "D:\video\interview.mp4" --diarize
```

Không ghi token vào config/project.

## Kết quả trung gian

Mỗi tác vụ có thư mục riêng trong `work/`: stems, transcript English, snapshot bản dịch ban đầu (`segments_translated.json`), script tiếng Việt cuối sau timing rewrite (`segments_vi.json`), reference clips, từng TTS segment, final voice track, timing stats, translation metadata, `semantic_qa.json` và `qa.json`. YouTube downloads nằm trong `work/youtube/`, output web nằm trong `work/outputs/`. Có thể chạy lại với `--resume`; dùng `--fresh` khi muốn làm lại transcript/translation từ đầu.

Video dài hơn ngưỡng `longform.single_chunk_threshold_seconds` dùng macro-chunk theo silence nhưng vẫn giữ timestamp toàn cục. ASR/TTS/QA/preview có manifest theo chunk để resume và invalidation cục bộ; translation vẫn chạy trên ngữ cảnh toàn video. Dashboard chỉ phát preview khi manifest và hash artifact còn hợp lệ, luôn gắn nhãn **PREVIEW**; sửa nội dung một segment sẽ làm stale riêng chunk chứa segment đó và khóa preview cũ cho tới khi render lại. Diarization hiện vẫn dùng đường full-file để tránh đổi speaker ID giữa các chunk.

Lip-sync chưa nằm trong core: pipeline giữ nguyên pixel video và thay audio. Mục tiêu hiện tại là ưu tiên accuracy, translation, voice và timing; lip-sync có thể thêm sau như một post-process độc lập.
