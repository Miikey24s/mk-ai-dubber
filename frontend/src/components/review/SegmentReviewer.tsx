import React, { useState, useMemo } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { SegmentFilterBar, FilterType } from './SegmentFilterBar';
import { SegmentCard } from './SegmentCard';
import { CheckCircle2, SlidersHorizontal } from 'lucide-react';

export const SegmentReviewer: React.FC = () => {
  const {
    segments,
    activeSegmentIndex,
    setActiveSegmentIndex,
    setCurrentTime,
    updateSegment,
    acceptSegment,
    rerenderSegment,
  } = useJob();
  const { t } = useTranslation();

  const [currentFilter, setCurrentFilter] = useState<FilterType>('all');
  const [searchQuery, setSearchQuery] = useState('');

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

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-4 shadow-sm space-y-4">
      {/* Title & Stats */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-slate-100 dark:border-slate-800">
        <div>
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-orange-500" />
            <h3 className="text-sm font-bold font-mono text-slate-800 dark:text-slate-100 uppercase tracking-wide">
              {t('review.title')}
            </h3>
            <span className="text-2xs font-mono font-semibold px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300">
              {segments.length} TOTAL
            </span>
          </div>
          <p className="text-2xs text-slate-500 mt-0.5">
            {t('review.subtitle')}
          </p>
        </div>

        {/* Accepted Progress Badge */}
        <div className="flex items-center gap-2 text-xs font-mono">
          <CheckCircle2 className="w-4 h-4 text-emerald-500" />
          <span className="text-slate-600 dark:text-slate-400">
            Verification: <strong className="text-emerald-500">{acceptedCount}</strong> / {segments.length} ({Math.round((acceptedCount / (segments.length || 1)) * 100)}%)
          </span>
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

      {/* Segment Cards List */}
      <div className="space-y-3 max-h-[600px] overflow-y-auto pr-1">
        {filteredSegments.length === 0 ? (
          <div className="text-center py-12 text-slate-400 font-mono text-xs">
            No segments match current filter criteria.
          </div>
        ) : (
          filteredSegments.map(seg => (
            <SegmentCard
              key={seg.id}
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
            />
          ))
        )}
      </div>
    </div>
  );
};
