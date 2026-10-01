# UI Fix Plan — bảng đối chiếu lịch sử

Cập nhật 01/10/2026. Kế hoạch 1.020 dòng trước đây đã được [lưu nguyên byte](../docs/archive/2026-10-01-cleanup/README.md). Hướng dẫn worker/model, rewrite và thứ tự Phase 0–12 trong bản đó đã được thay bằng [PLAN chính](../PLAN.md#ui); không chạy lại như backlog mới.

| Phase cũ | Source hiện tại | Kết luận |
|---|---|---|
| 0: setup; 11: build/test | Package scripts và test harness đã có. Build 01/10 pass. | Dùng quy trình hiện có; fixture Playwright vẫn có lỗi mở. |
| 1: telemetry drawer | `App.tsx` dùng layout 4/8 và `EngineerDrawer`. | Không cần viết drawer lần nữa. |
| 2: video thật | `VideoPlayer` dùng `<video>`, media events/seek; final URL qua guarded download endpoint. | Capability đã có. `completed` không chứng minh QA pass. |
| 3: waveform/audio | `WaveformPlayer` dùng fetch/decodeAudioData, peaks và `<audio>`. | Không còn random waveform; memory của full-audio decode cho near-12h chưa được xác nhận. |
| 4: import trùng | `App.tsx` dùng `JobCreatorModal`, không mount QuickImportBar. | Không cần làm lại luồng import; không suy ra mọi file cũ đều có thể xóa. |
| 5–6: review/SystemBar | Review cards/filter/editor và ribbon/telemetry đã triển khai. | Giữ behavior; chỉ sửa vấn đề có evidence. |
| 7: API/mock | API thật, health/jobs/telemetry tách lỗi; mock chỉ opt-in qua `VITE_DEMO_MODE=true`. | Không tự fallback sang mock khi backend offline. |
| 8–10: i18n/theme/controls | Có VI/EN context, dark/light styles và interaction code. | Chưa đóng kiểm tra toàn bộ copy, contrast, touch target, keyboard và narrow layout. |
| 12: progressive preview | `LongformPreviewRail` cùng preview API/readiness/invalidation đã có. | Giữ full preview acceptance matrix trong PLAN; không claim đủ từ việc component tồn tại. |

Việc mở và thứ tự thực hiện nằm duy nhất tại [PLAN: queue](../PLAN.md#current-queue). Giữ [PRODUCT-UI-CONTRACT](PRODUCT-UI-CONTRACT.md) cho identity/timebase/readiness; không sửa semantics chỉ để checklist pass.

Preview cần chứng minh: ready play/download; queued/processing/stale/blocked không giả readiness; reload lấy persisted state; edit chỉ invalidate chunk liên quan; lỗi một chunk không làm mất chunk đúng; không tự nhảy preview → final; nhãn Final không che QA failed; VI/EN và layout 1440/768/390 dùng được. Đây là gate còn cần đối chiếu evidence, không phải yêu cầu xây lại từ đầu.
