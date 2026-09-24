# VI Dubber

Pipeline lồng tiếng video English -> Vietnamese, có web dashboard cục bộ và nhận video từ máy hoặc YouTube.

Backend dịch hiện tại được khóa vào **Codex ChatGPT Web instance 2** tại `http://127.0.0.1:17842/v1`. UI chỉ expose backend này trong giai đoạn hiện tại. Model mặc định là `chatgpt-web/gpt-5.6-sol`, nhưng danh sách model được lấy trực tiếp từ live catalog của instance 2 để tránh chạy một model đã biến mất hoặc bị đổi tên.

Aurora tạm dừng theo quyết định hiện tại. Adapter local/hybrid cũ vẫn được giữ trong source để rollback/test và để job lịch sử còn đọc được provenance, nhưng không phải lựa chọn product UI hiện tại và không được dùng làm fallback tự động cho instance 2.

## Stack

- BS-Roformer: tách lời thoại khỏi nhạc/SFX.
- WhisperX large-v3: transcript + word timing.
- pyannote community-1: tách người nói khi có Hugging Face token.
- Codex WebGPT: backend chất lượng cao qua dedicated instance 2 (`127.0.0.1:17842`), mặc định `chatgpt-web/gpt-5.6-sol`.
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

Khi dùng WebGPT, pipeline không đổi route global trong `~/.codex/config.toml`. Mỗi invocation Codex của VI Dubber tự override provider URL về đúng `http://127.0.0.1:17842/v1`, đồng thời health-check port và live model catalog trước khi chạy. Auth Codex hiện có vẫn được dùng; không cần copy API key thủ công vào project.

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

Lip-sync chưa nằm trong core: pipeline giữ nguyên pixel video và thay audio. Mục tiêu hiện tại là ưu tiên accuracy, translation, voice và timing; lip-sync có thể thêm sau như một post-process độc lập.
