import React, { useState, useMemo, useEffect, useRef } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { SegmentFilterBar, FilterType } from './SegmentFilterBar';
import { SegmentCard } from './SegmentCard';
import { CheckCircle2, SlidersHorizontal, Keyboard, X, Sparkles, CheckCheck } from 'lucide-react';

export const SegmentReviewer: React.FC = () => {
  const {
    segments,
    activeSegmentIndex,
    setActiveSegmentIndex,
    setCurrentTime,
    isPlaying,
    setIsPlaying,
    updateSegment,
    acceptSegment,
    acceptAllSegments,
    rerenderSegment,
  } = useJob();
  const { t } = useTranslation();

  const [currentFilter, setCurrentFilter] = useState<FilterType>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [showHotkeysGuide, setShowHotkeysGuide] = useState(false);
  const [isAcceptingAll, setIsAcceptingAll] = useState(false);

  const cardRefs = useRef<Record<number, HTMLDivElement | null>>({});

  // Calculate filter counts
  const counts = useMemo(() => {
    return {
      all: segments.length,
      flagged: segments.filter(s => s.flagged || s.overflow || s.semantic_flag).length,
      needs_review: segments.filter(s => s.review_status === 'needs_review').length,
      accepted: segments.filter(s => s.review_status === 'accepted').length,
      overflow: segments.filter(s => s.overflow).length,
      multispeaker: segments.filter(s => s.multi_speaker || s.speaker_overlap).length,
    };
  }, [segments]);

  // Filter and search segments
  const filteredSegments = useMemo(() => {
    return segments.filter(seg => {
      // Filter criteria
      if (currentFilter === 'flagged' && !seg.flagged && !seg.overflow && !seg.semantic_flag) return false;
      if (currentFilter === 'needs_review' && seg.review_status !== 'needs_review') return false;
      if (currentFilter === 'accepted' && seg.review_status !== 'accepted') return false;
      if (currentFilter === 'overflow' && !seg.overflow) return false;
      if (currentFilter === 'multispeaker' && !seg.multi_speaker && !seg.speaker_overlap) return false;

      // Search query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchesEn = seg.text.toLowerCase().includes(q);
        const matchesVi = seg.vi.toLowerCase().includes(q);
        const matchesSpk = seg.speaker.toLowerCase().includes(q);
        const matchesId = seg.id.toString() === q;
        if (!matchesEn && !matchesVi && !matchesSpk && !matchesId) return false;
      }

      return true;
    });
  }, [segments, currentFilter, searchQuery]);

  const acceptedCount = segments.filter(s => s.review_status === 'accepted').length;

  // Auto-scroll active card into view
  useEffect(() => {
    const activeSeg = segments[activeSegmentIndex];
    if (activeSeg && cardRefs.current[activeSeg.id]) {
      cardRefs.current[activeSeg.id]?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      });
    }
  }, [activeSegmentIndex, segments]);

  // Global Keyboard Hotkeys Engine
  useEffect(() => {
    const handleGlobalKeyDown = async (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const isTyping =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable ||
          target.tagName === 'SELECT');

      // 1. Space: Toggle playback when not typing
      if (e.key === ' ' && !isTyping) {
        e.preventDefault();
        setIsPlaying(!isPlaying);
        return;
      }

      // 2. Navigation: ArrowDown or 'j' when not typing
      if (!isTyping && (e.key === 'ArrowDown' || e.key === 'j')) {
        e.preventDefault();
        if (activeSegmentIndex < segments.length - 1) {
          const nextIdx = activeSegmentIndex + 1;
          setActiveSegmentIndex(nextIdx);
          setCurrentTime(segments[nextIdx].start);
        }
        return;
      }

      // 3. Navigation: ArrowUp or 'k' when not typing
      if (!isTyping && (e.key === 'ArrowUp' || e.key === 'k')) {
        e.preventDefault();
        if (activeSegmentIndex > 0) {
          const prevIdx = activeSegmentIndex - 1;
          setActiveSegmentIndex(prevIdx);
          setCurrentTime(segments[prevIdx].start);
        }
        return;
      }

      // 4. Ctrl + Enter: Accept currently active segment and advance (if not captured by textarea)
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        if (!target || target.tagName !== 'TEXTAREA') {
          e.preventDefault();
          const activeSeg = segments[activeSegmentIndex];
          if (activeSeg) {
            await acceptSegment(activeSeg.id);
            if (activeSegmentIndex < segments.length - 1) {
              const nextIdx = activeSegmentIndex + 1;
              setActiveSegmentIndex(nextIdx);
              setCurrentTime(segments[nextIdx].start);
            }
          }
        }
        return;
      }

      // 5. Ctrl + S: Prevent browser save dialog
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
      }
    };

    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, [
    isPlaying,
    activeSegmentIndex,
    segments,
    setIsPlaying,
    setActiveSegmentIndex,
    setCurrentTime,
    acceptSegment,
  ]);

  // Handle Ctrl+Enter from inside a segment card's textarea
  const handleCtrlEnterFromCard = async (id: number, text: string, speaker: string) => {
    // 1. Save and accept
    await updateSegment(id, { vi: text, speaker, review_status: 'accepted' });
    // 2. Advance to next segment
    const curIdx = segments.findIndex(s => s.id === id);
    if (curIdx !== -1 && curIdx < segments.length - 1) {
      const nextIdx = curIdx + 1;
      setActiveSegmentIndex(nextIdx);
      setCurrentTime(segments[nextIdx].start);
    }
  };

  const handleAcceptAll = async () => {
    setIsAcceptingAll(true);
    await acceptAllSegments();
    setIsAcceptingAll(false);
  };

  return (
    <div className="h-full overflow-hidden flex flex-col bg-white dark:bg-slate-900/80 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm select-none">
      {/* Right Column Header */}
      <div className="p-3 border-b border-slate-200 dark:border-slate-800 space-y-2.5 shrink-0 bg-slate-50/50 dark:bg-slate-950/30">
        {/* Top Info Bar */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-orange-500 shrink-0" />
            <h3 className="text-xs font-bold font-mono text-slate-900 dark:text-slate-100 uppercase tracking-wide">
              {t('review.title')}
            </h3>
            <span className="text-xs font-mono font-semibold px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700">
              {segments.length} TOTAL
            </span>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded font-bold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
              <Sparkles className="w-3 h-3 text-emerald-500" />
              TỰ ĐỘNG 100% (QC TÙY CHỌN)
            </span>
          </div>

          <div className="flex items-center gap-2">
            {/* Accepted Progress Badge */}
            <div className="flex items-center gap-1.5 text-xs font-mono hidden xl:flex">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
              <span className="text-slate-600 dark:text-slate-400">
                Đã duyệt: <strong className="text-emerald-600 dark:text-emerald-400">{acceptedCount}</strong> / {segments.length}
              </span>
            </div>

            {/* Duyệt tất cả (Batch Approve) */}
            <button
              onClick={handleAcceptAll}
              disabled={isAcceptingAll || acceptedCount === segments.length}
              className={`flex items-center gap-1.5 h-7 px-2.5 rounded-lg text-xs font-mono font-semibold transition cursor-pointer ${
                acceptedCount === segments.length
                  ? 'bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 border border-slate-200 dark:border-slate-800 cursor-default'
                  : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-xs'
              }`}
              title="Duyệt tất cả các đoạn để hoàn tất xuất bản video"
            >
              <CheckCheck className="w-3.5 h-3.5" />
              <span>{isAcceptingAll ? 'Đang duyệt...' : `Duyệt tất cả (${segments.length})`}</span>
            </button>

            {/* Hotkeys Guide Chip */}
            <div className="relative">
              <button
                onClick={() => setShowHotkeysGuide(!showHotkeysGuide)}
                className="flex items-center gap-1.5 h-7 px-2 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-700 text-xs font-mono transition cursor-pointer"
                title="Xem hướng dẫn phím tắt Studio"
              >
                <Keyboard className="w-3.5 h-3.5 text-orange-500 shrink-0" />
                <span className="font-semibold hidden sm:inline">Phím tắt</span>
              </button>

              {/* Hotkeys Floating Tooltip / Drawer */}
              {showHotkeysGuide && (
                <div className="absolute right-0 mt-1 w-80 p-3 bg-white dark:bg-slate-900 rounded-lg shadow-xl border border-slate-200 dark:border-slate-800 text-xs font-mono z-50 animate-in fade-in zoom-in-95 duration-150">
                  <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-2 mb-2 font-bold text-slate-900 dark:text-slate-100">
                    <span className="flex items-center gap-1.5">
                      <Keyboard className="w-4 h-4 text-orange-500" />
                      Studio Cockpit Hotkeys
                    </span>
                    <button
                      onClick={() => setShowHotkeysGuide(false)}
                      className="p-0.5 rounded hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-400 hover:text-slate-600"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <div className="space-y-1.5 text-[11px]">
                    <div className="flex justify-between items-center py-0.5">
                      <span className="px-1.5 py-0.5 bg-slate-100 dark:bg-slate-800 rounded font-bold text-slate-700 dark:text-slate-300">Space</span>
                      <span className="text-slate-600 dark:text-slate-400">Phát / Tạm dừng video</span>
                    </div>
                    <div className="flex justify-between items-center py-0.5">
                      <span className="px-1.5 py-0.5 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 rounded font-bold border border-emerald-500/30">Ctrl + Enter</span>
                      <span className="text-slate-600 dark:text-slate-400">Lưu, Duyệt & Chuyển đoạn</span>
                    </div>
                    <div className="flex justify-between items-center py-0.5">
                      <span className="px-1.5 py-0.5 bg-orange-500/10 text-orange-600 dark:text-orange-400 rounded font-bold border border-orange-500/30">Ctrl + S</span>
                      <span className="text-slate-600 dark:text-slate-400">Lưu chỉnh sửa đoạn</span>
                    </div>
                    <div className="flex justify-between items-center py-0.5">
                      <span className="px-1.5 py-0.5 bg-slate-100 dark:bg-slate-800 rounded font-bold text-slate-700 dark:text-slate-300">↓ / j</span>
                      <span className="text-slate-600 dark:text-slate-400">Chuyển sang đoạn sau</span>
                    </div>
                    <div className="flex justify-between items-center py-0.5">
                      <span className="px-1.5 py-0.5 bg-slate-100 dark:bg-slate-800 rounded font-bold text-slate-700 dark:text-slate-300">↑ / k</span>
                      <span className="text-slate-600 dark:text-slate-400">Quay lại đoạn trước</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Filter and Search Bar */}
        <SegmentFilterBar
          currentFilter={currentFilter}
          onSelectFilter={setCurrentFilter}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          counts={counts}
        />
      </div>

      {/* Body: Scrollable list of segment cards */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3 scroll-smooth">
        {filteredSegments.length === 0 ? (
          <div className="text-center py-16 text-slate-500 dark:text-slate-400 font-mono text-xs">
            Không tìm thấy đoạn nào phù hợp với bộ lọc hiện tại.
          </div>
        ) : (
          filteredSegments.map(seg => (
            <div
              key={seg.id}
              ref={el => {
                cardRefs.current[seg.id] = el;
              }}
            >
              <SegmentCard
                segment={seg}
                isActive={seg.id === segments[activeSegmentIndex]?.id}
                onSelect={() => {
                  const idx = segments.findIndex(s => s.id === seg.id);
                  if (idx !== -1) {
                    setActiveSegmentIndex(idx);
                    setCurrentTime(seg.start);
                  }
                }}
                onSave={async (id, text, speaker) => {
                  await updateSegment(id, { vi: text, speaker, review_status: 'reviewed' });
                }}
                onAccept={async (id) => {
                  await acceptSegment(id);
                }}
                onRerender={async (id) => {
                  await rerenderSegment(id);
                }}
                onCtrlEnter={handleCtrlEnterFromCard}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
};
