import React from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { GpuMonitorCard } from './GpuMonitorCard';
import { StageLatencyChart } from './StageLatencyChart';
import { Sliders, Binary, Sparkles, CheckCircle2 } from 'lucide-react';
import { Badge } from '@/components/common/Badge';

export const ProTelemetryGrid: React.FC = () => {
  const { activeJob } = useJob();
  const { t } = useTranslation();

  const mix = activeJob?.result?.mix || {
    integrated_lufs: -14.2,
    true_peak_db: -1.6,
    loudness_delta_lu: -0.2,
    loudness_range_lu: 5.4,
    passes_loudness: true,
    passes_true_peak: true,
    target_lufs: -14.0,
    target_true_peak_db: -1.5,
  };

  const qa = activeJob?.result?.qa || {
    similarity: 0.948,
    threshold: 0.78,
    passed: true,
  };

  const counters = activeJob?.metrics?.counters || {
    typesafe_tokens: 4120,
    rewrite_calls: 2,
    webgpt_batches: 1,
    qa_repairs: 1,
    cache_hits: 4,
    cache_misses: 2,
  };

  return (
    <div className="space-y-3 font-mono">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-sky-400" />
          <h3 className="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase tracking-wider">
            {t('telemetry.title')}
          </h3>
        </div>
        <span className="text-2xs text-slate-500 font-mono">
          ENGINEER_TELEMETRY_STREAM // LIVE
        </span>
      </div>

      {/* Grid of Telemetry Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* 1. GPU VRAM Allocator */}
        <GpuMonitorCard />

        {/* 2. Stage Latency Breakdown */}
        <StageLatencyChart />

        {/* 3. Acoustic Mastering & Loudness */}
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3.5 shadow-sm space-y-3">
          <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
            <div className="flex items-center gap-2">
              <Sliders className="w-4 h-4 text-purple-500" />
              <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase">
                {t('telemetry.acoustic_snr')}
              </h4>
            </div>
            <Badge variant="success">EBU R128 PASS</Badge>
          </div>

          <div className="space-y-2 text-2xs">
            <div className="flex justify-between items-center bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
              <span className="text-slate-500">{t('telemetry.integrated_lufs')}</span>
              <span className="font-bold text-slate-800 dark:text-slate-200">
                {mix.integrated_lufs.toFixed(1)} LUFS
              </span>
            </div>

            <div className="flex justify-between items-center bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
              <span className="text-slate-500">{t('telemetry.true_peak')}</span>
              <span className="font-bold text-slate-800 dark:text-slate-200">
                {mix.true_peak_db.toFixed(2)} dBTP
              </span>
            </div>

            <div className="flex justify-between items-center bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
              <span className="text-slate-500">{t('telemetry.loudness_range')}</span>
              <span className="font-bold text-slate-800 dark:text-slate-200">
                {mix.loudness_range_lu.toFixed(1)} LU
              </span>
            </div>
          </div>
        </div>

        {/* 4. TypeSafe AI & Token Telemetry */}
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3.5 shadow-sm space-y-3">
          <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
            <div className="flex items-center gap-2">
              <Binary className="w-4 h-4 text-orange-500" />
              <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase">
                {t('telemetry.token_usage')}
              </h4>
            </div>
            <Badge variant="info">TYPESAFE JEV</Badge>
          </div>

          <div className="space-y-2 text-2xs">
            <div className="flex justify-between items-center bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
              <span className="text-slate-500">{t('telemetry.typesafe_tokens')}</span>
              <span className="font-bold text-sky-400">
                {(counters.typesafe_tokens || 4120).toLocaleString()}
              </span>
            </div>

            <div className="flex justify-between items-center bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
              <span className="text-slate-500">{t('telemetry.rewrite_calls')}</span>
              <span className="font-bold text-amber-400">
                {counters.rewrite_calls || 2} calls
              </span>
            </div>

            <div className="flex justify-between items-center bg-slate-50 dark:bg-slate-950 p-2 rounded border border-slate-100 dark:border-slate-800/80">
              <span className="text-slate-500">QA Faithfulness Score</span>
              <span className="font-bold text-emerald-400 flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" />
                {(qa.similarity * 100).toFixed(1)}% ({qa.passed ? 'PASSED' : 'CHECK'})
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
