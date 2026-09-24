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
    { id: 'all', labelKey: 'review.filter_all', icon: <Filter className="w-3 h-3" /> },
    { id: 'flagged', labelKey: 'review.filter_flagged', icon: <AlertTriangle className="w-3 h-3 text-amber-500" /> },
    { id: 'needs_review', labelKey: 'review.filter_needs_review', icon: <AlertTriangle className="w-3 h-3 text-rose-500" /> },
    { id: 'accepted', labelKey: 'review.filter_accepted', icon: <CheckCircle className="w-3 h-3 text-emerald-500" /> },
    { id: 'overflow', labelKey: 'review.filter_overflow', icon: <VolumeX className="w-3 h-3 text-sky-500" /> },
    { id: 'multispeaker', labelKey: 'review.filter_multispeaker', icon: <Users className="w-3 h-3 text-purple-500" /> },
  ];

  return (
    <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 font-mono text-xs">
      {/* Search Input */}
      <div className="relative flex-1 max-w-sm">
        <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={t('review.search_placeholder')}
          className="w-full pl-8 pr-3 py-1.5 bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-md text-xs placeholder:text-slate-400 focus:outline-none focus:border-orange-500 text-slate-900 dark:text-slate-100 transition"
        />
      </div>

      {/* Filter Chips */}
      <div className="flex flex-wrap items-center gap-1.5">
        {filterButtons.map(({ id, labelKey, icon }) => {
          const isActive = currentFilter === id;
          const count = counts[id] || 0;
          return (
            <button
              key={id}
              onClick={() => onSelectFilter(id)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-2xs transition font-medium ${
                isActive
                  ? 'bg-orange-500 text-white font-bold shadow-sm'
                  : 'bg-slate-100 dark:bg-slate-900 text-slate-600 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-800 border border-slate-200 dark:border-slate-800'
              }`}
            >
              {icon}
              <span>{t(labelKey)}</span>
              <span className={`ml-0.5 px-1 rounded-full ${isActive ? 'bg-orange-700 text-white' : 'bg-slate-200 dark:bg-slate-800 text-slate-500'}`}>
                {count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
};
