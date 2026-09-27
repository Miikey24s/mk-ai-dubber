import React, { useState } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { formatBytes, PIPELINE_STAGES } from '@/lib/utils';
import {
  Cpu,
  Clock,
  Sliders,
  Binary,
  Code2,
  Copy,
  ExternalLink,
  Check,
} from 'lucide-react';
import { Badge } from '@/components/common/Badge';
import { finiteNumber, nonNegativeFinite, positiveFinite } from '@/lib/telemetry';

export type TelemetryTab = 'cuda' | 'latency' | 'lufs' | 'tokens' | 'raw_json';

export const ProTelemetryGrid: React.FC = () => {
  const { activeJob, systemStatus, isBackendOnline, setIsRawJsonOpen } = useJob();
  const { language, t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TelemetryTab>('cuda');
  const [copied, setCopied] = useState(false);

  const cudaMetrics = activeJob?.metrics?.resources?.torch_cuda_allocator;
  const unavailable = t('common.unavailable');
  const hasSystemTelemetry = isBackendOnline === true;
  const allocated = cudaMetrics && nonNegativeFinite(cudaMetrics.allocated_bytes)
    ? cudaMetrics.allocated_bytes
    : null;
  const reserved = cudaMetrics && nonNegativeFinite(cudaMetrics.reserved_bytes)
    ? cudaMetrics.reserved_bytes
    : null;
  const peak = cudaMetrics && nonNegativeFinite(cudaMetrics.peak_allocated_bytes)
    ? cudaMetrics.peak_allocated_bytes
    : null;
  const total = hasSystemTelemetry && positiveFinite(systemStatus.gpu_vram_total_bytes)
    ? systemStatus.gpu_vram_total_bytes
    : null;
  const allocatedPct = allocated !== null && total !== null
    ? Math.min(100, Math.max(0, Math.round((allocated / total) * 100)))
    : null;
  const reservedPct = reserved !== null && total !== null
    ? Math.min(100, Math.max(0, Math.round((reserved / total) * 100)))
    : null;

  // Latency Metrics
  const stagesData = activeJob?.metrics?.stages;
  const totalSeconds = activeJob?.metrics?.total_wall_seconds;
  const stageLatencies = PIPELINE_STAGES.map(stg => {
    let sec = 0;
    let hasObservedMetric = false;
    if (stagesData) {
      for (const key of Object.keys(stagesData)) {
        if (stg.internalStageNames.some(name => key.includes(name))) {
          const value = stagesData[key]?.wall_seconds;
          if (nonNegativeFinite(value)) {
            hasObservedMetric = true;
            sec += value;
          }
        }
      }
    }
    const seconds = hasObservedMetric ? sec : null;
    const percent = seconds !== null && positiveFinite(totalSeconds)
      ? Math.min(100, Math.max(0, Math.round((seconds / totalSeconds) * 100)))
      : null;
    return { stage: stg, seconds, percent };
  });

  const mix = activeJob?.result?.mix;
  const mixStatus = mix && typeof mix.passes_loudness === 'boolean' && typeof mix.passes_true_peak === 'boolean'
    ? (mix.passes_loudness && mix.passes_true_peak ? 'COMPLIANT' : 'REVIEW')
    : unavailable;

  const qa = activeJob?.result?.qa;

  const counters = activeJob?.metrics?.counters;
  const formatMetric = (value: unknown, decimals: number, suffix = '') =>
    finiteNumber(value) ? `${value.toFixed(decimals)}${suffix}` : unavailable;
  const formatCount = (value: unknown) =>
    nonNegativeFinite(value) ? Math.round(value).toLocaleString() : unavailable;

  const handleCopyJson = () => {
    navigator.clipboard.writeText(JSON.stringify(activeJob || {}, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const tabs: { id: TelemetryTab; label: string; icon: React.ReactNode }[] = [
    { id: 'cuda', label: 'CUDA VRAM', icon: <Cpu className="w-3.5 h-3.5 shrink-0" /> },
    { id: 'latency', label: language === 'vi' ? 'Độ trễ' : 'Latency', icon: <Clock className="w-3.5 h-3.5 shrink-0" /> },
    { id: 'lufs', label: 'LUFS / Peak', icon: <Sliders className="w-3.5 h-3.5 shrink-0" /> },
    { id: 'tokens', label: 'Tokens', icon: <Binary className="w-3.5 h-3.5 shrink-0" /> },
    { id: 'raw_json', label: 'Raw JSON', icon: <Code2 className="w-3.5 h-3.5 shrink-0" /> },
  ];

  return (
    <div className="flex-1 min-h-0 overflow-hidden flex flex-col bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm font-mono select-none">
      {/* Tab Navigation Header */}
      <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 px-2.5 py-1 shrink-0 bg-slate-50/80 dark:bg-slate-950/60 gap-2">
        <div className="flex items-center gap-1 overflow-x-auto no-scrollbar">
          {tabs.map(tab => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs font-semibold whitespace-nowrap transition-all cursor-pointer border ${
                  isActive
                    ? 'bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/50 shadow-xs'
                    : 'border-transparent text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-100 dark:hover:bg-slate-800'
                }`}
              >
                {tab.icon}
                <span>[{tab.label}]</span>
              </button>
            );
          })}
        </div>

        <span className="text-[10px] px-2 py-0.5 rounded font-mono font-bold bg-slate-200/80 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border border-slate-300 dark:border-slate-700 whitespace-nowrap shrink-0 hidden sm:inline">
          ENGINEER // LIVE
        </span>
      </div>

      {/* Tab Content Drawer */}
      <div className="flex-1 min-h-0 overflow-y-auto p-2.5 text-xs">
        {/* Tab 1: CUDA VRAM */}
        {activeTab === 'cuda' && (
          <div className="space-y-2.5">
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-1.5">
              <span className="text-xs text-slate-800 dark:text-slate-200 font-bold truncate max-w-[200px]">
                {cudaMetrics?.device_name || (hasSystemTelemetry && systemStatus.gpu_name ? systemStatus.gpu_name.split('(')[0] : unavailable)}
              </span>
              <Badge variant="info">TORCH CUDA ALLOCATOR</Badge>
            </div>

            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-slate-600 dark:text-slate-300">Allocated / Reserved / Total:</span>
                <span className="text-slate-900 dark:text-slate-100 font-bold">
                  {allocated === null ? unavailable : formatBytes(allocated)} / {reserved === null ? unavailable : formatBytes(reserved)} / {total === null ? unavailable : formatBytes(total)}
                </span>
              </div>
              <div className="relative w-full h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                <div
                  className="absolute top-0 bottom-0 bg-sky-900/60 rounded-full"
                  style={{ width: `${reservedPct ?? 0}%` }}
                />
                <div
                  className="absolute top-0 bottom-0 bg-sky-500 rounded-full transition-all duration-500"
                  style={{ width: `${allocatedPct ?? 0}%` }}
                />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[10px]">{t('telemetry.vram_allocated')}</span>
                <span className="font-bold text-sky-600 dark:text-sky-400 text-xs sm:text-sm mt-0.5 block">{allocated === null ? unavailable : formatBytes(allocated)}</span>
                <span className="text-slate-500 dark:text-slate-400 text-[9px]">{allocatedPct === null ? unavailable : `${allocatedPct}% ${language === 'vi' ? 'sử dụng' : 'utilization'}`}</span>
              </div>
              <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[10px]">{t('telemetry.vram_reserved')}</span>
                <span className="font-bold text-slate-800 dark:text-slate-200 text-xs sm:text-sm mt-0.5 block">{reserved === null ? unavailable : formatBytes(reserved)}</span>
                <span className="text-slate-500 dark:text-slate-400 text-[9px]">{reservedPct === null ? unavailable : `${reservedPct}% pool`}</span>
              </div>
              <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[10px]">{t('telemetry.vram_peak')}</span>
                <span className="font-bold text-amber-600 dark:text-amber-400 text-xs sm:text-sm mt-0.5 block">{peak === null ? unavailable : formatBytes(peak)}</span>
                <span className="text-slate-500 dark:text-slate-400 text-[9px]">{language === 'vi' ? 'Mức đỉnh' : 'High watermark'}</span>
              </div>
            </div>
          </div>
        )}

        {/* Tab 2: Latency */}
        {activeTab === 'latency' && (
          <div className="space-y-2.5">
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
              <span className="text-xs text-slate-700 dark:text-slate-300 font-semibold uppercase">
                {t('telemetry.stage_latency')}
              </span>
              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-bold">
                WALL TIME: {positiveFinite(totalSeconds) ? `${totalSeconds.toFixed(1)}${t('common.sec')}` : unavailable}
              </span>
            </div>

            <div className="space-y-2">
              {stageLatencies.map(({ stage, seconds, percent }) => (
                <div key={stage.id} className="space-y-1">
                  <div className="flex justify-between items-center text-xs">
                    <span className="truncate max-w-[200px] font-sans font-medium text-slate-800 dark:text-slate-200">
                      0{stage.index + 1}. {language === 'vi' ? stage.labelVi : stage.labelEn}
                    </span>
                    <span className="text-slate-600 dark:text-slate-300 tabular-nums font-semibold">
                      {seconds === null ? unavailable : `${seconds.toFixed(1)}${t('common.sec')}`} {percent === null ? `(${unavailable})` : `(${percent}%)`}
                    </span>
                  </div>
                  <div className="w-full h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${percent === null ? 'bg-transparent' : 'bg-emerald-500/80'}`}
                      style={{ width: `${percent ?? 0}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tab 3: LUFS & Peak */}
        {activeTab === 'lufs' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
              <span className="text-xs text-slate-800 dark:text-slate-200 font-bold">
                ACOUSTIC MASTERING (EBU R128)
              </span>
              <Badge variant={mixStatus === 'COMPLIANT' ? 'success' : mixStatus === 'REVIEW' ? 'warning' : 'default'}>
                {mixStatus}
              </Badge>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[11px]">{t('telemetry.integrated_lufs')}</span>
                <span className="font-bold text-slate-900 dark:text-slate-100 text-sm mt-0.5 block">
                  {formatMetric(mix?.integrated_lufs, 1, ' LUFS')}
                </span>
                <span className="text-emerald-600 dark:text-emerald-400 text-[10px]">Target: {formatMetric(mix?.target_lufs, 1)}</span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[11px]">{t('telemetry.true_peak')}</span>
                <span className="font-bold text-slate-900 dark:text-slate-100 text-sm mt-0.5 block">
                  {formatMetric(mix?.true_peak_db, 2, ' dBTP')}
                </span>
                <span className="text-emerald-600 dark:text-emerald-400 text-[10px]">Target: {formatMetric(mix?.target_true_peak_db, 1)}</span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[11px]">{t('telemetry.loudness_range')}</span>
                <span className="font-bold text-slate-900 dark:text-slate-100 text-sm mt-0.5 block">
                  {formatMetric(mix?.loudness_range_lu, 1, ' LU')}
                </span>
                <span className="text-sky-600 dark:text-sky-400 text-[10px]">Cinema Dynamic</span>
              </div>
            </div>

            <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800 text-xs space-y-1">
              <div className="flex justify-between text-slate-600 dark:text-slate-300">
                <span>{language === 'vi' ? 'Tỷ lệ & Nguy cơ Clipping:' : 'Clipping Ratio & Risk:'}</span>
                <span className="text-emerald-600 dark:text-emerald-400 font-bold">
                  {formatMetric(activeJob?.result?.voice_track?.clipping_ratio, 2, '%')}
                </span>
              </div>
              <div className="flex justify-between text-slate-600 dark:text-slate-300">
                <span>{language === 'vi' ? 'Tiến trình Mastering Âm thanh:' : 'Acoustic Mastering Pass:'}</span>
                <span className="text-emerald-600 dark:text-emerald-400 font-bold">BS-RoFormer + Kokoro Align</span>
              </div>
            </div>
          </div>
        )}

        {/* Tab 4: TypeSafe Tokens */}
        {activeTab === 'tokens' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
              <span className="text-xs text-slate-800 dark:text-slate-200 font-bold">
                TYPESAFE AI COGNITIVE TELEMETRY
              </span>
              <Badge variant="purple">JEV v1.4</Badge>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[11px]">{t('telemetry.typesafe_tokens')}</span>
                <span className="font-bold text-sky-600 dark:text-sky-400 text-sm mt-0.5 block">
                  {formatCount(counters?.typesafe_tokens)}
                </span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[11px]">{t('telemetry.rewrite_calls')}</span>
                <span className="font-bold text-amber-600 dark:text-amber-400 text-sm mt-0.5 block">
                  {formatCount(counters?.rewrite_calls)} {language === 'vi' ? 'lần' : 'calls'}
                </span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[11px]">{language === 'vi' ? 'Độ tương đồng QA' : 'QA Similarity'}</span>
                <span className="font-bold text-emerald-600 dark:text-emerald-400 text-sm mt-0.5 block">
                  {finiteNumber(qa?.similarity) ? `${(qa.similarity * 100).toFixed(1)}%` : unavailable}
                </span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-300 block text-[11px]">{language === 'vi' ? 'Cache Trúng/Trượt' : 'Cache Hits/Miss'}</span>
                <span className="font-bold text-slate-800 dark:text-slate-200 text-sm mt-0.5 block">
                  {formatCount(counters?.cache_hits)} / {formatCount(counters?.cache_misses)}
                </span>
              </div>
            </div>

            <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs">
              <span className="text-slate-600 dark:text-slate-300">{language === 'vi' ? 'Số lô dịch WebGPT:' : 'WebGPT Sol Batches:'}</span>
              <span className="font-bold text-orange-600 dark:text-orange-400">
                {formatCount(counters?.webgpt_batches)} {language === 'vi' ? 'lô gửi (:8080)' : 'batch dispatched (:8080)'}
              </span>
            </div>
          </div>
        )}

        {/* Tab 5: Raw JSON */}
        {activeTab === 'raw_json' && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-700 dark:text-slate-300 font-semibold uppercase">
                {t('telemetry.title')}
              </span>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={handleCopyJson}
                  className="flex items-center gap-1 h-7 px-2.5 rounded bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 text-xs border border-slate-300 dark:border-slate-700 transition cursor-pointer"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copied ? t('common.copied') : t('common.copy')}</span>
                </button>
                <button
                  onClick={() => setIsRawJsonOpen(true)}
                  className="flex items-center gap-1 h-7 px-2.5 rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-600 dark:text-orange-400 text-xs border border-orange-500/30 transition cursor-pointer"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span>{t('telemetry.inspect_modal')}</span>
                </button>
              </div>
            </div>

            <pre className="p-2.5 bg-slate-50 dark:bg-slate-950 rounded-lg border border-slate-200 dark:border-slate-800 text-[11px] leading-tight overflow-x-auto text-slate-800 dark:text-slate-200 max-h-48 select-text">
              {JSON.stringify(activeJob || {}, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
};
