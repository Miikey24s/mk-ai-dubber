# VI Dubber — PLAN hiện tại

Cập nhật: **01/10/2026 — rút gọn tài liệu, không nâng mức acceptance**.
Trạng thái: **ACTIVE/PARTIAL · REVIEWED-PARTIAL · COMPACTED-RESUMABLE**. Baseline trước cleanup: `3a0a678`; phạm vi review và bản phục hồi ở [archive](docs/archive/2026-10-01-cleanup/README.md).
Đây là nguồn chính cho trạng thái, việc cần làm và gate của VI Dubber. Hướng dẫn vận hành ở [README](README.md); lịch sử chi tiết ở [archive](docs/archive/2026-10-01-cleanup/README.md).

**Core đã có; P23 còn mở. Job12 xử lý hoàn tất nhưng QA không đạt.** Dọn PLAN không mở quyền chạy provider, xử lý lại media hay export.

<a id="scope"></a>
## Phạm vi hiện tại

Sản phẩm local dịch/lồng tiếng Anh → Việt, review và sửa từng segment, giữ timeline, xem preview theo chunk và xuất video/phụ đề có provenance. Luồng chính: input → separation/ASR → segments/translation → TTS/timing → mix/mux → QA → owner review. Cache, manifest và revision quyết định phần nào được reuse hoặc phải tính lại.

- Giữ pipeline hiện có, React/FastAPI UI, catalog local, Learn reference local và Drive OAuth/session/connector ledger đã triển khai.
- Catalog/recovery/connector có bằng chứng offline `PREP_ONLY`; code tồn tại không đồng nghĩa cloud export hoặc whole-product acceptance.
- Lip-sync (P18) đã bỏ khỏi active scope. P19 premium/cloud A/B, cloud/Notion/Calendar mở rộng, distributed execution và SaaS không thuộc việc cần hoàn thành đợt này; không có lịch tự chạy lại.
- Không thay model/framework, tăng concurrency hay đưa thêm infra chỉ để đóng PLAN.

<a id="phase-status"></a>
<a id="23-trạng-thái-phase"></a>
## Trạng thái giữ lại

Các nhãn dưới đây kế thừa evidence theo fixture/phạm vi đã ghi; không phải kết quả vừa chạy lại ngày 01/10.

| Phase | Trạng thái | Ý nghĩa và giới hạn |
|---|---|---|
| P00 Profiler | DONE | Stage metrics, counters và benchmark fixtures đã có. |
| P01 Cache/resume | DONE | Identity/fingerprint, manifests và atomic artifact commits; giữ stale/tamper/move/fresh/resume contract. |
| P02 Word timing | DONE | Giữ word timing/confidence/speaker cho downstream. |
| P03 Segmentation | BENCHMARKED | Short/734-turn long baseline có evidence; config hiện vẫn `segmentation.mode: legacy`, không tự promote smart. |
| P04 Translation | ACCEPTED | Glossary/critical-token contract; P22 đã re-accept dedicated runtime. |
| P05 TypeSafe | ACCEPTED trong calibration scope | 35 mẫu human-labelled, `jev-1.13.0`; semantic QA vẫn là shadow/diagnostic. |
| P06 Pronunciation | DONE | Display/spoken split, deterministic normalization và fail-closed ambiguous tokens. |
| P07 Voice reference | OWNER HUMAN PASS | Owner 27/09 cho phép 1 ballot; smart selector thắng 4/4 blind trials. |
| P08 GPU TTS | ACCEPTED trên máy/fixture đã đo | CUDA FP16 batch 4; CPU ONNX fallback. Không suy rộng sang mọi GPU. |
| P09 Elastic timing | BENCHMARKED | 734 cửa sổ long baseline không collision; không thay toàn bộ quality gate. |
| P10 Assembly | DONE | Timeline/fades/overlap và windowed assembly có regression. |
| P11 Mix/master | OWNER HUMAN PASS | Current two-pass + limiter thắng blind A/B; giữ loudness/true-peak contract. |
| P12 Segment QA | OWNER HUMAN PASS trong tập đã nghe | 6 flagged + 3 unflagged đều audibly OK; 6/171 là observed audible false positives của tập đó, không miễn QA Job12. |
| P13 ASR/separator | ACCEPTED / KEEP CURRENT | Giữ WhisperX batch 4 + BS-RoFormer; batch 6 và clean-speech skip bị reject bởi A/B. |
| P14 Multi-speaker | OWNER HUMAN PASS trong fixture | 63 segments/3 speakers, owner nghe 3/3 clips; robustness với hội thoại/podcast/audio không ổn định còn theo dõi. |
| P15 Profiles | DONE | Fast/Balanced/Max dùng cùng core, có fingerprint theo resolved behavior. |
| P16 Review UI | ACCEPTED trong scope lịch sử | Catalog/model selector, edit/rerender, reload đã có evidence; lỗi fixture Playwright hiện tại vẫn mở. |
| P17 Regression fixtures | ACCEPTED trong scope lịch sử | Manifest có 10/10 loại fixture; không thay kiểm chứng trên thay đổi mới. |
| P18 Lip-sync | REMOVED | Không nằm trong active product scope. |
| P19 Premium/cloud | OUT OF CURRENT SCOPE | Chỉ xem lại khi có nhu cầu và authority riêng về cost/privacy/account. |
| P20 Ops | ACCEPTED trong fault matrix | Retry/model missing/disk/kill recovery/atomic publication; không là bảo đảm mọi job đều đạt QA. |
| P21 Runtime `:17842` | HISTORICAL / SUPERSEDED | Instance đã bị owner xóa ngày 25/09; không còn là target. |
| P22 Dedicated WebGPT | ACCEPTED trong evidence 25/09 | `:17850`, direct Responses, c2; live session phải preflight lại trước mỗi quyền chạy mới. |
| P23 Long-form/performance | IN PROGRESS | Các sub-gate bên dưới đã pass; whole-job/whole-pipeline và Job12 QA chưa đóng. |

<a id="p23"></a>
## P23: phần đã có và phần còn thiếu

Core đã triển khai adaptive chunking, windowed ASR/I/O, per-chunk cache/resume/invalidation, bounded scheduler/backpressure, chunked TTS, preview/API/UI, progress và ETA. Auto-tune có resolved-axis guard; chỉ promote bằng paired fixtures và repeated trials.

| Bằng chứng giữ lại | Kết luận được phép |
|---|---|
| Synthetic 6.25h control-plane | Queue/RSS/disk, crash/resume và completion order pass trong harness. |
| Short correctness + 3-trial paired overhead | Median candidate/baseline `0.98505 <= 1.05`; variance còn rộng, chưa claim speedup. |
| Real 6.01h final assembly (`21636s`) | Media/loudness/RSS/workspace và Windows PDH process-tree GPU sub-gate pass. Không bao gồm ASR/TTS/translation 6h. |
| Repeated whole-job khoảng 33 phút | Còn mở: cùng fixture/config/model, end-to-end correctness, wall time và reliability. |
| Whole-pipeline 6h+ / live provider stress | Còn mở: bounded RSS/VRAM/disk, provider pressure/restart, full QA và owner listening. |
| Job12 gần 12h | Có output nhưng QA failed; không được dùng để đóng whole-pipeline acceptance. |

Điều kiện promotion vẫn giữ: transcript/translation/timing/terminology/QA không regress; short overhead trong gate; medium/long không regress và có lợi ích đo được; resume/partial edit chỉ invalidate dependency cần thiết; queue/GPU bounded; out-of-order chunks giữ timeline; failed/stale chunk không làm mất chunk đúng; các profile dùng chung core. UI phải phản ánh persisted readiness sau reload, preview chỉ publish atomically sau local gates, Final và QA acceptance phải phân biệt. Engine reuse cần source/revision/license và local benchmark.

Evidence chính: [short paired gate](work/checkpoints/P23-short-overhead-paired-stat-reconcile-2026-09-26.md), [6h final-assembly reconcile](work/checkpoints/P23-real-media-final-assembly-6h-reconcile-2026-09-27.md), [autotune hardening](work/checkpoints/P23-autotune-harness-hardening-2026-09-26.md). Các receipt failed ban đầu được giữ để giải thích quá trình reconcile.

<a id="job12"></a>
## Job12: processing complete, QA failed

Đã đọc trực tiếp artifact ngày 01/10/2026, không chạy lại media/provider:

| Trường | Giá trị |
|---|---|
| Job | `work/job-8dc51f8a892aba21/` |
| Source | `work/youtube/IXSu0MClr34.mp4`; SHA-256 `8dc51f8a892aba2103728a8d2360b30f9a6319d5015ff8fd00ef4bdbc4b08ddf` |
| Processing | `status=completed`, `stage=complete`, `progress=1.0` |
| Output | `work/outputs/IXSu0MClr34_vi.mp4` tồn tại |
| QA | `qa.passed=false`, `mode=risk_segments`, `full_track_skipped=true`, `global_passed=null` |
| Segment QA | `final_failed=122`; 4.277 deterministic + 519 acoustic risk segments được kiểm tra |
| Repair | 2 attempted / 2 completed; failed categories có thể chồng lặp |
| Lease | Không có `run.lock` tại lúc kiểm tra |

Tỷ lệ similarity trên selected-risk segments hoặc terminology pass không thay cho full-track QA. Không tự sửa failed rows, hạ ngưỡng, xóa cache/receipt/lock, ghi đè output, dùng `--fresh` hay tạo worker thứ hai.

Authority và resume preflight nằm ở [Job12 audit](../../planning/checkpoints/workspace-next-stage/JOB12-SAFE-AUDIT-20261001/CHECKPOINT.md) và [workspace RESUME](../../planning/checkpoints/workspace-next-stage/RESUME.md). Lịch sử “translation failed/no MP4” trong checkpoint trước đó đã bị artifact hiện tại thay thế, nhưng lịch sử vẫn được giữ. Chỉ resume retained job/config sau khi owner mở đúng scope provider/media/QA và có single-worker, source/config identity, disk, session health preflight.

<a id="ui"></a>
## UI: triển khai đã có, việc còn mở

[UI-FIX-PLAN](frontend/UI-FIX-PLAN.md) nay chỉ là bảng đối chiếu bản cũ với source; không là một hàng đợi độc lập. [PRODUCT-UI-CONTRACT](frontend/PRODUCT-UI-CONTRACT.md) giữ identity/timebase/readiness semantics.

- Đã có: EngineerDrawer, video/audio thật, waveform từ audio, review/edit, import modal, offline health, demo mode opt-in, VI/EN, theme, LongformPreviewRail, catalog/connector panels.
- Giữ mở: lỗi shared Gradio/Playwright fixture-order ở test reload; kiểm tra đầy đủ i18n, contrast/keyboard/touch/narrow layout và preview matrix chưa được build/test source đơn lẻ chứng minh.
- Playback “Final” hiện đi theo lifecycle `completed`; không được diễn giải badge đó là QA pass. Job12 là counterexample phải giữ trong review UI/QA kế tiếp.
- Waveform hiện tải/decode toàn audio để tạo peaks; đã có waveform thật nhưng chưa có bằng chứng memory budget cho near-12h.

<a id="current-queue"></a>
## Thứ tự tiếp tục

| Ưu tiên | Slice cụ thể | Oracle / giới hạn |
|---|---|---|
| 1 — offline | Sửa hoặc quarantine có lý do lỗi fixture-order trong `tests/test_web_e2e_playwright.py`; chỉ sửa fixture/runtime liên quan sau khi tái hiện. | Module 3 tests, isolated reload, two-test controls, M3/M5 UI contracts; giữ lỗi thấy được nếu chưa sửa. |
| 2 — offline | Hoàn thiện evidence UI còn mở trong mục trên bằng fixture; không biến thành redesign hoặc viết lại các phase đã có. | Ready/stale/blocked, refresh/edit invalidation, Preview/Final/QA, VI/EN, 1440/768/390, keyboard/contrast, memory nơi có rủi ro. |
| 3 — owner-gated | Review/repair 122 failed segments và full-track/human QA Job12. | Giữ provenance/receipt, không waive bằng processing completed; fresh preflight và đúng retained job. |
| 4 — owner-gated | Đóng repeated whole-job 33m và whole-pipeline 6h+ P23. | Correctness + resource/reliability + repeated paired benchmark; chỉ claim speedup khi evidence đủ. |

Trước code UI tiếp theo, ghi bounded slice ngay trong PLAN này: files, oracle, rollback và evidence path. Không tạo thêm PLAN cho mỗi lượt. Baseline lỗi fixture: [audit 30/09](../../planning/checkpoints/workspace-next-stage/VI-safe-continuation-20260930/CHECKPOINT.md); báo cáo lịch sử `671 passed, 1 skipped, 1 failed`, chưa được cleanup này sửa.

<a id="runtime-gates"></a>
## Runtime và authority

- Cấu hình hiện tại: `profile: balanced_fast`, WhisperX large-v3 FP16 batch 4, BS-RoFormer, VieNeu PyTorch CUDA FP16 batch 4, 48 kHz; TTS CPU ONNX là fallback đã có.
- Translation product: Dedicated Dubber-WebGPT `http://127.0.0.1:17850/v1`, `direct-responses`, `tools=[]`, concurrency 2, global context off. Model lấy từ live catalog; config hiện lưu `chatgpt-web/gpt-5.6-sol`, không là cam kết model luôn tồn tại.
- Runtime home `D:\ANNAM\TradingWorkspace\.runtime\dubber-webgpt`; core `D:\ANNAM\AI\vi-dubber-webgpt-core`. Không tự đổi global Codex/Cockpit, route cũ `:17842/:54005`, Aurora hoặc local Qwen để né provider gate.
- Historical P22 acceptance không chứng minh session hôm nay online. Lượt cleanup không start runtime, login, canary, provider turn, OAuth, upload/export, media run hoặc paid API.
- Drive OAuth local giữ nguyên implementation: owner-configured Web application client, `drive.file`, access token in memory, epoch/revocation guards; export vẫn `PREP_ONLY`. Không mở rộng scope/account và không coi connector ChatGPT là backend sản phẩm.
- Offline code/docs/tests có thể tiếp tục trong scope được giao. Job12/provider/media/listening và tác động external chỉ làm khi có authority tương ứng; deployment/public release là gate riêng.

<a id="verification"></a>
## Kiểm chứng và cách đọc evidence

Ngày 01/10 trước cleanup docs: frontend build pass; UI contract 4 checks, telemetry 5 cases pass; focused API download/segments/preview + media tests `13 passed, 1 skipped` (symlink unavailable). Đây không phải full suite hoặc whole-product UI acceptance.

Cleanup này chỉ kiểm tra archive checksum/byte parity, links/anchors, current artifact JSON và `git diff --check`; không chạy lại benchmark/media/provider. Human receipts và local cache/media/model/runtime/database giữ nguyên. [Archive README](docs/archive/2026-10-01-cleanup/README.md) ghi các file rút gọn và đường phục hồi.
