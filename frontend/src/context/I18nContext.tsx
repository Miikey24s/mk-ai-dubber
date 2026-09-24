import React, { createContext, useContext, useState } from 'react';
import { Language } from '@/types';

interface I18nContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const translations: Record<Language, Record<string, string>> = {
  en: {
    // Nav & System
    'app.title': 'VI Dubber Studio',
    'app.subtitle': 'Neural Video Dubbing & Acoustic Mastering Suite',
    'mode.standard': 'Standard Studio',
    'mode.engineer': 'Pro / Engineer Mode',
    'theme.dark': 'Dark Mode',
    'theme.light': 'Light Mode',
    'system.vram': 'GPU VRAM',
    'system.rtf': 'RTF Speed',
    'system.online': 'ONLINE',
    'system.offline': 'DISCONNECTED',
    'system.polling': 'POLLING 1.5s',
    'system.websocket': 'LIVE STREAM',
    'system.webgpt_connected': 'Codex WebGPT Connected',
    'system.webgpt_disconnected': 'Codex WebGPT Standby',

    // Pipeline Stepper
    'stepper.title': 'Neural Pipeline Stepper',
    'stepper.stage_num': 'STAGE {index} OF 7',
    'stepper.running': 'PROCESSING',
    'stepper.completed': 'COMPLETED',
    'stepper.waiting': 'WAITING',
    'stepper.failed': 'FAILED',
    'stepper.inspect_stage': 'Inspect Stage Telemetry',

    // ETA & Monitor
    'eta.title': 'Execution Telemetry & ETA',
    'eta.elapsed': 'Elapsed Time',
    'eta.remaining': 'Est. Remaining',
    'eta.speed': 'Real-Time Factor (RTF)',
    'eta.completion': 'Est. Completion',
    'eta.faster_than_realtime': 'x faster than real-time',
    'eta.running_stage': 'Current Stage',

    // A/B Audio & Video
    'monitor.title': 'Video & Acoustic Monitor',
    'monitor.ab_audition': 'A/B Track Audition',
    'monitor.track_a': 'Original Vocal (A)',
    'monitor.track_b': 'Dubbed Vietnamese (B)',
    'monitor.track_bgm': 'BGM & Ambience',
    'monitor.loop_segment': 'Loop Segment',
    'monitor.seek_start': 'Jump to Start',
    'monitor.speed': 'Playback Speed',
    'monitor.no_video': 'No video loaded for current job',

    // Segment Reviewer
    'review.title': 'Bilingual Segment Reviewer',
    'review.subtitle': 'Synchronized English transcript vs Vietnamese neural dub',
    'review.search_placeholder': 'Filter segments by text, speaker, or keywords...',
    'review.filter_all': 'All Segments',
    'review.filter_flagged': 'Flagged Issues',
    'review.filter_needs_review': 'Needs Review',
    'review.filter_accepted': 'Accepted',
    'review.filter_overflow': 'Duration Overflow',
    'review.filter_multispeaker': 'Multi-Speaker',
    'review.col_time': 'Time / Speaker',
    'review.col_source': 'English Source (ASR)',
    'review.col_target': 'Vietnamese Dub (Neural TTS)',
    'review.col_actions': 'Verification & Actions',
    'review.save_changes': 'Save Edit',
    'review.accept': 'Accept Segment',
    'review.rerender': 'Re-render TTS',
    'review.revert': 'Revert to Original',
    'review.saved_toast': 'Segment changes saved successfully',
    'review.accepted_toast': 'Segment verified and marked as accepted',
    'review.rerender_toast': 'Triggered downstream TTS re-render for segment',
    'review.status.unreviewed': 'Unreviewed',
    'review.status.needs_review': 'Review Required',
    'review.status.reviewed': 'Reviewed',
    'review.status.accepted': 'Accepted',
    'review.overflow_warning': 'Audio exceeds original segment boundary',
    'review.rewritten_badge': 'Prefit Rewritten',
    'review.semantic_flag_badge': 'QA Divergence Alert',
    'review.overlap_badge': 'Speaker Overlap',

    // Pro Telemetry Cards
    'telemetry.title': 'Deep Engineering Telemetry',
    'telemetry.gpu_allocator': 'PyTorch CUDA Allocator',
    'telemetry.vram_allocated': 'Allocated VRAM',
    'telemetry.vram_reserved': 'Reserved VRAM',
    'telemetry.vram_peak': 'Peak VRAM',
    'telemetry.stage_latency': 'Latency Breakdown (Per Stage)',
    'telemetry.acoustic_snr': 'Acoustic Mastering (LUFS & Peak)',
    'telemetry.integrated_lufs': 'Integrated Loudness',
    'telemetry.true_peak': 'True Peak dBTP',
    'telemetry.loudness_range': 'Loudness Range (LRA)',
    'telemetry.token_usage': 'TypeSafe & LLM Token Telemetry',
    'telemetry.typesafe_tokens': 'TypeSafe Verification Tokens',
    'telemetry.webgpt_batches': 'Translation Fanout Batches',
    'telemetry.rewrite_calls': 'Prefit Syllable Rewrites',
    'telemetry.qa_repairs': 'QA Automatic Repairs',
    'telemetry.raw_json': 'Inspect Raw JSON State',

    // Import & Ingest
    'import.title': 'Video & Media Ingest',
    'import.import_video': 'Import Video / YouTube',
    'import.youtube_tab': 'YouTube URL',
    'import.file_tab': 'Local Media File',
    'import.youtube_placeholder': 'Paste YouTube video URL (e.g. https://youtube.com/watch?v=...)',
    'import.drop_hint': 'Drag & drop video (.mp4, .mkv, .mov) here or click to browse',
    'import.dropzone_active': 'Drop video file here to start dubbing now!',
    'import.browse_files': 'Browse Files',
    'import.quick_dub': 'Start Dubbing',
    'import.quick_bar_label': 'VIDEO INGEST',
    'import.change_video': 'Change Video',
    'import.processing': 'Ingesting & Launching...',

    // Job Creator
    'creator.new_job': 'Import Video / YouTube',
    'creator.source_type': 'Input Media Source',
    'creator.youtube_url': 'YouTube Video URL',
    'creator.youtube_placeholder': 'https://www.youtube.com/watch?v=...',
    'creator.or_upload': 'Or Upload Local Media File (.mp4, .mkv, .wav)',
    'creator.drag_drop': 'Drag and drop media file here or click to browse',
    'creator.translation_model': 'Neural Translation Model',
    'creator.effort': 'Reasoning Effort',
    'creator.quality_profile': 'Quality Profile Policy',
    'creator.voice_clone': 'Zero-Shot Voice Clone Reference',
    'creator.voice_clone_desc': 'Upload 10-30s clean speech sample to clone natural timbre',
    'creator.deep_settings': 'Deep Acoustic & AI Parameters',
    'creator.start_button': 'Launch Pipeline Dubbing',
    'creator.cancel': 'Cancel',

    // Profiles
    'profile.fast': 'Fast (Throughput)',
    'profile.fast_desc': 'Single pass, batch 1, bypass QA, rapid turnaround',
    'profile.balanced_best': 'Balanced Best (Recommended)',
    'profile.balanced_best_desc': 'Prefit syllable matching, verify-escalate QA, quality safe',
    'profile.max_quality': 'Max Quality (Production Dub)',
    'profile.max_quality_desc': 'Fanout translation, strict semantic audit, iterative TTS repair',

    // Deep Settings
    'deep.diarization': 'Speaker Diarization (PyAnnote Clustering)',
    'deep.ducking_lufs': 'Target Master Loudness (LUFS)',
    'deep.true_peak': 'True Peak Ceiling (dBTP)',
    'deep.max_speedup': 'Max Audio Speedup Ratio',
    'deep.max_slowdown': 'Max Audio Slowdown Ratio',
    'deep.rewrite_threshold': 'Syllable Rewrite Threshold',
    'deep.retry_budget': 'Pipeline Retry Budget',
    'deep.semantic_threshold': 'Faithfulness Cutoff (Semantic QA)',

    // Common
    'common.close': 'Close',
    'common.copy': 'Copy to Clipboard',
    'common.copied': 'Copied!',
    'common.active_job': 'Active Job',
    'common.all_jobs': 'All Jobs Archive',
    'common.pause': 'Pause Pipeline',
    'common.resume': 'Resume Pipeline',
    'common.abort': 'Abort Job',
    'common.search': 'Search...',
    'common.sec': 's',
  },
  vi: {
    // Nav & System
    'app.title': 'VI Dubber Studio',
    'app.subtitle': 'Hệ thống lồng tiếng Video AI & Xử lý âm thanh đa kênh',
    'mode.standard': 'Chế độ Tiêu chuẩn',
    'mode.engineer': 'Chế độ Kỹ sư (Pro)',
    'theme.dark': 'Chế độ Tối',
    'theme.light': 'Chế độ Sáng',
    'system.vram': 'VRAM GPU',
    'system.rtf': 'Tốc độ RTF',
    'system.online': 'TRỰC TUYẾN',
    'system.offline': 'MẤT KẾT NỐI',
    'system.polling': 'POLLING 1.5s',
    'system.websocket': 'KẾT NỐI TRỰC TIẾP',
    'system.webgpt_connected': 'Đã kết nối Codex WebGPT',
    'system.webgpt_disconnected': 'Codex WebGPT Chờ',

    // Pipeline Stepper
    'stepper.title': 'Tiến trình Xử lý 7 Bước (Pipeline Stepper)',
    'stepper.stage_num': 'BƯỚC {index}/7',
    'stepper.running': 'ĐANG XỬ LÝ',
    'stepper.completed': 'HOÀN TẤT',
    'stepper.waiting': 'ĐANG CHỜ',
    'stepper.failed': 'LỖI',
    'stepper.inspect_stage': 'Xem chi tiết bước xử lý',

    // ETA & Monitor
    'eta.title': 'Dự toán Thời gian & Tốc độ',
    'eta.elapsed': 'Thời gian đã chạy',
    'eta.remaining': 'Dự kiến còn lại',
    'eta.speed': 'Hệ số Thời gian Thực (RTF)',
    'eta.completion': 'Dự kiến hoàn thành',
    'eta.faster_than_realtime': 'lần nhanh hơn thời gian thực',
    'eta.running_stage': 'Bước đang chạy',

    // A/B Audio & Video
    'monitor.title': 'Trình phát Video & Giám sát Âm thanh',
    'monitor.ab_audition': 'Nghe kiểm âm đối chiếu A/B',
    'monitor.track_a': 'Giọng gốc Tiếng Anh (A)',
    'monitor.track_b': 'Giọng lồng Tiếng Việt (B)',
    'monitor.track_bgm': 'Nhạc nền & Hiệu ứng',
    'monitor.loop_segment': 'Lặp đoạn này',
    'monitor.seek_start': 'Về đầu đoạn',
    'monitor.speed': 'Tốc độ phát',
    'monitor.no_video': 'Chưa tải video cho tác vụ này',

    // Segment Reviewer
    'review.title': 'Biên tập & Kiểm định Từng Đoạn Thoại',
    'review.subtitle': 'Đối chiếu song song bản gốc ASR tiếng Anh và bản dịch neural tiếng Việt',
    'review.search_placeholder': 'Tìm kiếm theo từ khóa, người nói hoặc nội dung...',
    'review.filter_all': 'Tất cả đoạn thoại',
    'review.filter_flagged': 'Có cảnh báo',
    'review.filter_needs_review': 'Cần kiểm tra',
    'review.filter_accepted': 'Đã duyệt',
    'review.filter_overflow': 'Tràn độ dài (Overflow)',
    'review.filter_multispeaker': 'Đa người nói',
    'review.col_time': 'Thời gian / Nhân vật',
    'review.col_source': 'Tiếng Anh gốc (ASR)',
    'review.col_target': 'Tiếng Việt lồng tiếng (TTS)',
    'review.col_actions': 'Duyệt & Thao tác',
    'review.save_changes': 'Lưu chỉnh sửa',
    'review.accept': 'Duyệt đoạn này',
    'review.rerender': 'Render lại TTS',
    'review.revert': 'Khôi phục ban đầu',
    'review.saved_toast': 'Đã lưu chỉnh sửa đoạn thoại',
    'review.accepted_toast': 'Đã xác nhận đạt chuẩn và lưu trạng thái',
    'review.rerender_toast': 'Đã gửi yêu cầu tổng hợp lại giọng đọc TTS cho đoạn này',
    'review.status.unreviewed': 'Chưa duyệt',
    'review.status.needs_review': 'Cần duyệt',
    'review.status.reviewed': 'Đã xem',
    'review.status.accepted': 'Đã duyệt',
    'review.overflow_warning': 'Thời lượng đọc vượt mốc giới hạn đoạn gốc',
    'review.rewritten_badge': 'Đã rút gọn âm tiết',
    'review.semantic_flag_badge': 'Cảnh báo lệch ngữ nghĩa',
    'review.overlap_badge': 'Ghi đè âm (Overlap)',

    // Pro Telemetry Cards
    'telemetry.title': 'Thông số Kỹ thuật & Telemetry Chuyên sâu',
    'telemetry.gpu_allocator': 'Bộ cấp phát PyTorch CUDA',
    'telemetry.vram_allocated': 'VRAM đã cấp phát',
    'telemetry.vram_reserved': 'VRAM đang giữ (Reserved)',
    'telemetry.vram_peak': 'VRAM Đỉnh (Peak)',
    'telemetry.stage_latency': 'Độ trễ từng bước xử lý',
    'telemetry.acoustic_snr': 'Mastering Âm thanh (LUFS & Peak)',
    'telemetry.integrated_lufs': 'Độ lớn tổng thể (LUFS)',
    'telemetry.true_peak': 'Đỉnh biên độ (True Peak dBTP)',
    'telemetry.loudness_range': 'Dải động (LRA)',
    'telemetry.token_usage': 'Thống kê Token TypeSafe & LLM',
    'telemetry.typesafe_tokens': 'Token xác thực TypeSafe',
    'telemetry.webgpt_batches': 'Số lô dịch fanout WebGPT',
    'telemetry.rewrite_calls': 'Số lần viết lại khớp âm tiết',
    'telemetry.qa_repairs': 'Số lần sửa lỗi tự động QA',
    'telemetry.raw_json': 'Kiểm tra trạng thái JSON gốc',

    // Import & Ingest
    'import.title': 'Nhập & Tải Video / Âm thanh',
    'import.import_video': 'Import Video / YouTube',
    'import.youtube_tab': 'Đường dẫn YouTube',
    'import.file_tab': 'Tệp từ máy tính',
    'import.youtube_placeholder': 'Dán link video YouTube (VD: https://youtube.com/watch?v=...)',
    'import.drop_hint': 'Kéo thả tệp video (.mp4, .mkv, .mov) vào đây hoặc bấm để chọn tệp',
    'import.dropzone_active': 'Thả tệp video vào đây để bắt đầu lồng tiếng ngay!',
    'import.browse_files': 'Chọn tệp',
    'import.quick_dub': 'Bắt đầu Dub',
    'import.quick_bar_label': 'NHẬP VIDEO',
    'import.change_video': 'Đổi Video Mới',
    'import.processing': 'Đang nạp video & khởi chạy...',

    // Job Creator
    'creator.new_job': '+ Import Video / YouTube',
    'creator.source_type': 'Nguồn Video/Audio',
    'creator.youtube_url': 'Đường dẫn YouTube',
    'creator.youtube_placeholder': 'https://www.youtube.com/watch?v=...',
    'creator.or_upload': 'Hoặc tải lên tệp từ máy (.mp4, .mkv, .wav)',
    'creator.drag_drop': 'Kéo thả tệp video vào đây hoặc bấm để chọn',
    'creator.translation_model': 'Mô hình Dịch thuật AI',
    'creator.effort': 'Cường độ suy luận (Effort)',
    'creator.quality_profile': 'Cấu hình Chất lượng (Profile)',
    'creator.voice_clone': 'Mẫu Giọng Đọc để Clone (Tùy chọn)',
    'creator.voice_clone_desc': 'Tải lên đoạn audio 10-30s giọng đọc mẫu trong trẻo để clone âm sắc',
    'creator.deep_settings': 'Thông số Kỹ thuật Nâng cao',
    'creator.start_button': 'Khởi chạy Tiến trình Lồng tiếng',
    'creator.cancel': 'Hủy',

    // Profiles
    'profile.fast': 'Nhanh (Ưu tiên Tốc độ)',
    'profile.fast_desc': 'Chạy 1 lượt, batch 1, bỏ qua kiểm thử QA, tốc độ tối đa',
    'profile.balanced_best': 'Cân bằng Tốt nhất (Khuyên dùng)',
    'profile.balanced_best_desc': 'Tự động khớp âm tiết prefit, kiểm tra ngữ nghĩa nhiều lớp, ổn định',
    'profile.max_quality': 'Chất lượng Cao cấp (Phim & Studio)',
    'profile.max_quality_desc': 'Dịch đa luồng fanout, kiểm định ngữ nghĩa nghiêm ngặt, tự động sửa lỗi',

    // Deep Settings
    'deep.diarization': 'Tự động phân tách người nói (PyAnnote)',
    'deep.ducking_lufs': 'Mức âm lượng chuẩn đầu ra (LUFS)',
    'deep.true_peak': 'Trần âm lượng đỉnh an toàn (dBTP)',
    'deep.max_speedup': 'Hệ số tăng tốc audio tối đa',
    'deep.max_slowdown': 'Hệ số giảm tốc audio tối đa',
    'deep.rewrite_threshold': 'Ngưỡng kích hoạt viết lại rút gọn âm tiết',
    'deep.retry_budget': 'Số lần thử lại khi gặp lỗi',
    'deep.semantic_threshold': 'Ngưỡng độ tin cậy ngữ nghĩa (Semantic QA)',

    // Common
    'common.close': 'Đóng',
    'common.copy': 'Sao chép JSON',
    'common.copied': 'Đã sao chép!',
    'common.active_job': 'Tác vụ đang chọn',
    'common.all_jobs': 'Lịch sử tác vụ',
    'common.pause': 'Tạm dừng',
    'common.resume': 'Tiếp tục',
    'common.abort': 'Hủy tác vụ',
    'common.search': 'Tìm kiếm...',
    'common.sec': 'giây',
  },
};

const I18nContext = createContext<I18nContextType | undefined>(undefined);

export const I18nProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [language, setLanguageState] = useState<Language>(() => {
    const saved = localStorage.getItem('vi_dubber_lang');
    return (saved === 'vi' || saved === 'en') ? saved : 'vi';
  });

  const setLanguage = (lang: Language) => {
    setLanguageState(lang);
    localStorage.setItem('vi_dubber_lang', lang);
  };

  const t = (key: string, params?: Record<string, string | number>): string => {
    let text = translations[language]?.[key] || translations['en']?.[key] || key;
    if (params) {
      Object.entries(params).forEach(([paramKey, paramVal]) => {
        text = text.replace(new RegExp(`\\{${paramKey}\\}`, 'g'), String(paramVal));
      });
    }
    return text;
  };

  return (
    <I18nContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </I18nContext.Provider>
  );
};

export const useTranslation = () => {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error('useTranslation must be used within an I18nProvider');
  }
  return context;
};
