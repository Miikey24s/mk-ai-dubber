import React, { useEffect, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, Clock3, FolderSearch, Loader2, Search, X } from 'lucide-react';
import { useTranslation } from '@/context/I18nContext';
import { useJob } from '@/context/JobContext';
import { fetchCatalog } from '@/lib/api';
import { useFocusTrap } from '@/lib/useFocusTrap';
import { CatalogAvailability, CatalogItem, CatalogResponse } from '@/types';

interface CatalogPanelProps {
  open: boolean;
  onClose: () => void;
}

const AVAILABILITIES: Array<CatalogAvailability | ''> = ['', 'available', 'pending', 'stale', 'missing', 'failed', 'unknown'];

function availabilityClass(value: CatalogAvailability): string {
  if (value === 'available') return 'text-emerald-700 dark:text-emerald-300 bg-emerald-500/10 border-emerald-500/30';
  if (value === 'pending') return 'text-cyan-700 dark:text-cyan-300 bg-cyan-500/10 border-cyan-500/30';
  if (value === 'stale' || value === 'missing') return 'text-amber-700 dark:text-amber-300 bg-amber-500/10 border-amber-500/30';
  if (value === 'failed') return 'text-rose-700 dark:text-rose-300 bg-rose-500/10 border-rose-500/30';
  return 'text-slate-600 dark:text-slate-300 bg-slate-500/10 border-slate-500/30';
}

function AvailabilityIcon({ value }: { value: CatalogAvailability }) {
  if (value === 'available') return <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />;
  if (value === 'pending') return <Clock3 className="w-3.5 h-3.5" aria-hidden="true" />;
  if (value === 'failed') return <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" />;
  return <FolderSearch className="w-3.5 h-3.5" aria-hidden="true" />;
}

export const CatalogPanel: React.FC<CatalogPanelProps> = ({ open, onClose }) => {
  const { t } = useTranslation();
  const { setActiveJobId, jobs } = useJob();
  const [query, setQuery] = useState('');
  const [availability, setAvailability] = useState<CatalogAvailability | ''>('');
  const [data, setData] = useState<CatalogResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const dialogRef = useFocusTrap<HTMLElement>(open);

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      fetchCatalog(query, availability || undefined)
        .then(result => {
          if (!cancelled) setData(result);
        })
        .catch(reason => {
          if (!cancelled) {
            setData(null);
            setError(reason instanceof Error ? reason.message : 'catalog request failed');
          }
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, query, availability]);

  if (!open) return null;

  const selectItem = (item: CatalogItem) => {
    if (jobs.some(job => job.id === item.item_id)) setActiveJobId(item.item_id);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-end bg-slate-950/35 p-3 sm:p-5"
      role="presentation"
      onMouseDown={event => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="catalog-panel-title"
        className="flex max-h-[calc(100vh-1.5rem)] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900 sm:max-h-[calc(100vh-2.5rem)]"
      >
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <div>
            <h2 id="catalog-panel-title" className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-slate-100">
              <FolderSearch className="h-4 w-4 text-orange-500" aria-hidden="true" />
              {t('catalog.title')}
            </h2>
            <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">{t('catalog.metadata_only')}</p>
          </div>
          <button type="button" onClick={onClose} className="ui-button ui-button--neutral h-8 w-8 p-1.5" aria-label={t('common.close')}>
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        <div className="flex gap-2 border-b border-slate-200 p-3 dark:border-slate-800">
          <label className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-slate-400" aria-hidden="true" />
            <span className="sr-only">{t('catalog.search_placeholder')}</span>
            <input
              ref={searchRef}
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder={t('catalog.search_placeholder')}
              className="h-8 w-full rounded-md border border-slate-300 bg-slate-50 pl-8 pr-2 text-xs text-slate-900 outline-none focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            />
          </label>
          <label className="sr-only" htmlFor="catalog-availability">{t('catalog.availability')}</label>
          <select
            id="catalog-availability"
            value={availability}
            onChange={event => setAvailability(event.target.value as CatalogAvailability | '')}
            className="h-8 max-w-[9.5rem] rounded-md border border-slate-300 bg-slate-50 px-2 text-xs text-slate-800 outline-none focus:border-orange-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
          >
            {AVAILABILITIES.map(value => (
              <option key={value || 'all'} value={value}>{value ? t(`catalog.availability.${value}`) : t('catalog.filter_all')}</option>
            ))}
          </select>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-8 text-xs text-slate-500 dark:text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />{t('catalog.loading')}
            </div>
          )}
          {!loading && error && (
            <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-700 dark:text-rose-300">{t('catalog.error')}: {error}</div>
          )}
          {!loading && !error && data?.catalog_status !== 'ready' && (
            <div className="rounded-lg border border-slate-300 bg-slate-50 p-4 text-center text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">
              <FolderSearch className="mx-auto mb-2 h-5 w-5 text-slate-400" aria-hidden="true" />
              {t('catalog.unavailable')}
            </div>
          )}
          {!loading && !error && data?.catalog_status === 'ready' && data.items.length === 0 && (
            <div className="py-8 text-center text-xs text-slate-500 dark:text-slate-400">{t('catalog.empty')}</div>
          )}
          {!loading && !error && data?.catalog_status === 'ready' && data.items.length > 0 && (
            <div className="space-y-2">
              <p className="px-1 text-[11px] text-slate-500 dark:text-slate-400">{t('catalog.results', { count: data.pagination.returned })}</p>
              {data.items.map(item => (
                <button
                  key={item.item_id}
                  type="button"
                  onClick={() => selectItem(item)}
                  disabled={!jobs.some(job => job.id === item.item_id)}
                  className="w-full rounded-lg border border-slate-200 bg-white p-3 text-left transition hover:border-orange-400 hover:bg-orange-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/60 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-950 dark:hover:border-orange-500/60"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0 truncate text-xs font-semibold text-slate-900 dark:text-slate-100">{item.title}</span>
                    <span className={`flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${availabilityClass(item.availability)}`}>
                      <AvailabilityIcon value={item.availability} />{t(`catalog.availability.${item.availability}`)}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-500 dark:text-slate-400">
                    <span>{t('catalog.version')}: <b className="font-mono text-slate-700 dark:text-slate-300">{item.lineage.revision}</b></span>
                    <span>{t('catalog.segments')}: <b className="text-slate-700 dark:text-slate-300">{item.lineage.segment_count}</b></span>
                    <span>{t('catalog.review')}: <b className="text-slate-700 dark:text-slate-300">{item.review.review_state || t('catalog.no_review')}</b></span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
};
