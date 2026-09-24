import React from 'react';
import { useTranslation } from '@/context/I18nContext';
import { Search, Filter, AlertTriangle, CheckCircle, VolumeX, Users } from 'lucide-react';

export type FilterType = 'all' | 'flagged' | 'needs_review' | 'accepted' | 'overflow' | 'multispeaker';

interface SegmentFilterBarProps {
  currentFilter: FilterType;
  onSelectFilter: (filter: FilterType) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  counts: Record<FilterType, number>;
}

export const SegmentFilterBar: React.FC<SegmentFilterBarProps> = ({
  currentFilter,
  onSelectFilter,
  searchQuery,
  onSearchChange,
  counts,
}) => {
  const { t } = useTranslation();

  const filterButtons: { id: FilterType; labelKey: string; icon: React.ReactNode }[] = [
    { id: 'all', labelKey: 'review.filter_all', icon: <Filter className="w-3.5 h-3.5" /> },
    { id: 'flagged', labelKey: 'review.filter_flagged', icon: <AlertTriangle className="w-3.5 h-3.5 text-amber-500" /> },
    { id: 'needs_review', labelKey: 'review.filter_needs_review', icon: <AlertTriangle className="w-3.5 h-3.5 text-rose-500" /> },
    { id: 'accepted', labelKey: 'review.filter_accepted', icon: <CheckCircle className="w-3.5 h-3.5 text-emerald-500" /> },
    { id: 'overflow', labelKey: 'review.filter_overflow', icon: <VolumeX className="w-3.5 h-3.5 text-sky-500" /> },
    { id: 'multispeaker', labelKey: 'review.filter_multispeaker', icon: <Users className="w-3.5 h-3.5 text-purple-500" /> },
  ];

  return (
    <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-2 font-mono text-xs">
      {/* Search Input */}
      <div className="relative flex-1 min-w-[200px] max-w-sm">
        <Search className="w-3.5 h-3.5 text-slate-500 dark:text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={t('review.search_placeholder')}
          className="w-full h-8 pl-8 pr-3 bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-lg text-xs placeholder:text-slate-400 focus:outline-none focus:border-orange-500 focus:ring-1 focus:ring-orange-500 text-slate-900 dark:text-slate-100 transition"
        />
      </div>

      {/* Filter Chips */}
      <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5">
        {filterButtons.map(({ id, labelKey, icon }) => {
          const isActive = currentFilter === id;
          const count = counts[id] || 0;
          return (
            <button
              key={id}
              onClick={() => onSelectFilter(id)}
              className={`flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs transition font-semibold cursor-pointer shrink-0 whitespace-nowrap ${
                isActive
                  ? 'bg-orange-600 text-white font-bold shadow-xs'
                  : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700/80'
              }`}
            >
              {icon}
              <span>{t(labelKey)}</span>
              <span
                className={`ml-0.5 px-1.5 py-0.2 rounded-full text-[11px] font-bold ${
                  isActive
                    ? 'bg-orange-800 text-white'
                    : 'bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-300'
                }`}
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
};
