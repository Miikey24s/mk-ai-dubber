import React from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { formatBytes } from '@/lib/utils';
import { nonNegativeFinite, positiveFinite } from '@/lib/telemetry';
import { Cpu } from 'lucide-react';

export const GpuMonitorCard: React.FC = () => {
  const { activeJob, systemStatus, isBackendOnline } = useJob();
  const { t } = useTranslation();

  const cudaMetrics = activeJob?.metrics?.resources?.torch_cuda_allocator;
  const unavailable = t('common.unavailable');
  const allocated = cudaMetrics && nonNegativeFinite(cudaMetrics.allocated_bytes)
    ? cudaMetrics.allocated_bytes
    : null;
  const reserved = cudaMetrics && nonNegativeFinite(cudaMetrics.reserved_bytes)
    ? cudaMetrics.reserved_bytes
    : null;
  const peak = cudaMetrics && nonNegativeFinite(cudaMetrics.peak_allocated_bytes)
    ? cudaMetrics.peak_allocated_bytes
    : null;
  const total = isBackendOnline === true && positiveFinite(systemStatus.gpu_vram_total_bytes)
    ? systemStatus.gpu_vram_total_bytes
    : null;

  const allocatedPct = allocated !== null && total !== null
    ? Math.min(100, Math.max(0, Math.round((allocated / total) * 100)))
    : null;
  const reservedPct = reserved !== null && total !== null
    ? Math.min(100, Math.max(0, Math.round((reserved / total) * 100)))
    : null;

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3.5 shadow-sm font-mono space-y-3">
      <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
        <div className="flex items-center gap-2">
          <Cpu className="w-4 h-4 text-sky-500" />
          <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase">
            {t('telemetry.gpu_allocator')}
          </h4>
        </div>
        <span className="text-2xs text-slate-500 truncate max-w-[140px]">
          {cudaMetrics?.device_name || (isBackendOnline === true && systemStatus.gpu_name ? systemStatus.gpu_name.split('(')[0] : unavailable)}
        </span>
      </div>

      {/* VRAM Multi-Bar */}
      <div className="space-y-1.5">
        <div className="flex justify-between text-2xs">
          <span className="text-slate-500">Allocated / Reserved / Total:</span>
          <span className="text-slate-700 dark:text-slate-300 font-bold">
            {allocated === null ? unavailable : formatBytes(allocated)} / {reserved === null ? unavailable : formatBytes(reserved)} / {total === null ? unavailable : formatBytes(total)}
          </span>
        </div>

        <div className="relative w-full h-2.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
          {/* Reserved Bar */}
          <div
            className="absolute top-0 bottom-0 bg-sky-900/60 rounded-full"
            style={{ width: `${reservedPct ?? 0}%` }}
          />
          {/* Active Allocated Bar */}
          <div
            className="absolute top-0 bottom-0 bg-sky-500 rounded-full transition-all duration-500"
            style={{ width: `${allocatedPct ?? 0}%` }}
          />
        </div>
      </div>

      {/* Grid of 3 stats */}
      <div className="grid grid-cols-3 gap-2 text-2xs pt-1">
        <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
          <span className="text-slate-400 block">{t('telemetry.vram_allocated')}</span>
          <span className="font-bold text-sky-500 text-xs mt-0.5 block">{allocated === null ? unavailable : formatBytes(allocated)}</span>
          <span className="text-slate-500 text-3xs">{allocatedPct === null ? unavailable : `${allocatedPct}% utilization`}</span>
        </div>
        <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
          <span className="text-slate-400 block">{t('telemetry.vram_reserved')}</span>
          <span className="font-bold text-slate-700 dark:text-slate-300 text-xs mt-0.5 block">{reserved === null ? unavailable : formatBytes(reserved)}</span>
          <span className="text-slate-500 text-3xs">{reservedPct === null ? unavailable : `${reservedPct}% pool`}</span>
        </div>
        <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
          <span className="text-slate-400 block">{t('telemetry.vram_peak')}</span>
          <span className="font-bold text-amber-500 text-xs mt-0.5 block">{peak === null ? unavailable : formatBytes(peak)}</span>
          <span className="text-slate-500 text-3xs">High watermark</span>
        </div>
      </div>
    </div>
  );
};
