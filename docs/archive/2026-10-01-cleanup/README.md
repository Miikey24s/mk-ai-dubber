# VI Dubber documentation cleanup — 01/10/2026

Snapshot trước cleanup: `3a0a6789a71a07c0849fdd156f22ecdd4ab8f2b2` trên `main`.
[manifest.json](manifest.json) ghi original path, SHA-256, bytes, line count và base commit cho từng bản gốc. File `.original` giữ nguyên byte, không sửa link lịch sử bên trong; link relative của bản gốc được hiểu theo original path trong manifest.

Đã kiểm SHA-256, bytes và số dòng của cả bốn snapshot theo manifest. `PLAN.md`, `UI-FIX-PLAN.md` và `README.md` chụp working-tree CRLF nên khác byte Git blob LF tại base commit; nội dung khớp sau chuẩn hóa CRLF → LF. `PRODUCT-UI-CONTRACT.md` khớp Git blob cả byte. `.gitattributes` giữ `*.original -text` để Git không đổi line ending của archive.

| Tài liệu | Quyết định |
|---|---|
| [PLAN.md gốc](PLAN.md.original) | Đưa phase specs, research candidates, instructions/model assignment và changelog dài ra khỏi luồng đọc hằng ngày; [PLAN hiện tại](../../../PLAN.md) giữ current scope, mọi trạng thái phase, P23/Job12 QA, authority và queue. |
| [UI-FIX-PLAN gốc](frontend__UI-FIX-PLAN.md.original) | Thay 12-phase rewrite backlog đã lỗi thời bằng đối chiếu source và link đến canonical PLAN; giữ các gate UI chưa chứng minh. |
| [README gốc](README.md.original) | Thêm entrypoints, sửa mô tả TTS theo config hiện tại và bỏ lời hẹn lip-sync khỏi active scope. |
| [PRODUCT-UI-CONTRACT gốc](frontend__PRODUCT-UI-CONTRACT.md.original) | Giữ semantics; sửa câu routing tương lai M5 đã lỗi thời và nhắc phân biệt Final lifecycle với QA acceptance. |

## Phạm vi và exclusions

Chỉ sửa bốn tài liệu trên và thêm archive này. Không xóa source hoặc scratch vì chưa chứng minh được ownership/không còn tham chiếu. Không sửa AGENTS/skills/config, job state, failed QA, model, cache, runtime, database, media, output hoặc retained receipts. Không chạy provider/media/OAuth/export, không đánh dấu P23/Job12 accepted.

Cloud/Notion/Calendar mở rộng, distributed/SaaS, P19 premium/cloud và lip-sync được đưa ra khỏi danh sách cần hoàn thành hiện tại; không xóa local connector/OAuth/Learn implementation hoặc evidence.

## Kiểm chứng và phục hồi

Cleanup dùng diff review, current Job12 JSON, source đối chiếu UI, archive checksum/byte parity và local link/anchor check. Full suite, benchmark và rendered UI không được chạy lại cho thay đổi docs-only. Known Playwright fixture-order failure và Job12 `qa.passed=false/final_failed=122/full_track_skipped=true` giữ nguyên trong PLAN.

Có thể phục hồi tài liệu riêng lẻ bằng byte của file `.original` về đúng `original_path` sau khi kiểm tra SHA-256 trong manifest, hoặc lấy phiên bản từ base commit. Archive phục vụ lịch sử/phục hồi, không mang quyền chạy worker hay là PLAN thứ hai.

Hai commit trước cleanup được giữ nguyên: `e0f4ba7` (playback/timeout + build), `3a0a678` (PLAN audit + receipts). Cleanup tài liệu tách khỏi các thay đổi hành vi này để có thể review và phục hồi độc lập.
