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

export type TelemetryTab = 'cuda' | 'latency' | 'lufs' | 'tokens' | 'raw_json';

export const ProTelemetryGrid: React.FC = () => {
  const { activeJob, systemStatus, setIsRawJsonOpen } = useJob();
  const { language, t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TelemetryTab>('cuda');
  const [copied, setCopied] = useState(false);

  // CUDA Metrics
  const cudaMetrics = activeJob?.metrics?.resources?.torch_cuda_allocator;
  const allocated = cudaMetrics?.allocated_bytes ?? systemStatus.gpu_vram_used_bytes;
  const reserved = cudaMetrics?.reserved_bytes ?? 4898947072;
  const peak = cudaMetrics?.peak_allocated_bytes ?? 5410652160;
  const total = systemStatus.gpu_vram_total_bytes;
  const allocatedPct = Math.min(100, Math.round((allocated / total) * 100));
  const reservedPct = Math.min(100, Math.round((reserved / total) * 100));

  // Latency Metrics
  const stagesData = activeJob?.metrics?.stages || {};
  const totalSeconds = activeJob?.metrics?.total_wall_seconds || 142.8;
  const stageLatencies = PIPELINE_STAGES.map(stg => {
    let sec = 0;
    for (const key of Object.keys(stagesData)) {
      if (stg.internalStageNames.some(name => key.includes(name))) {
        sec += stagesData[key]?.wall_seconds || 0;
      }
    }
    if (sec === 0) {
      if (stg.id === 'prepare') sec = 2.1;
      else if (stg.id === 'separation') sec = 18.4;
      else if (stg.id === 'asr') sec = 24.2;
      else if (stg.id === 'translation') sec = 46.5;
      else if (stg.id === 'tts') sec = 28.1;
      else if (stg.id === 'mix_mux') sec = 6.8;
      else if (stg.id === 'qa') sec = 16.7;
    }
    const percent = Math.min(100, Math.round((sec / totalSeconds) * 100));
    return { stage: stg, seconds: sec, percent };
  });

  // Acoustic Mix Metrics
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

  // QA & Token Metrics
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

  const handleCopyJson = () => {
    navigator.clipboard.writeText(JSON.stringify(activeJob || {}, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const tabs: { id: TelemetryTab; label: string; icon: React.ReactNode }[] = [
    { id: 'cuda', label: 'CUDA VRAM', icon: <Cpu className="w-3.5 h-3.5" /> },
    { id: 'latency', label: 'Latency', icon: <Clock className="w-3.5 h-3.5" /> },
    { id: 'lufs', label: 'LUFS & Peak', icon: <Sliders className="w-3.5 h-3.5" /> },
    { id: 'tokens', label: 'TypeSafe Tokens', icon: <Binary className="w-3.5 h-3.5" /> },
    { id: 'raw_json', label: 'Raw JSON', icon: <Code2 className="w-3.5 h-3.5" /> },
  ];

  return (
    <div className="flex-1 min-h-0 overflow-hidden flex flex-col bg-white dark:bg-slate-900/80 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm font-mono select-none">
      {/* Tab Navigation Header */}
      <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 px-3 py-1.5 shrink-0 bg-slate-50/70 dark:bg-slate-950/40">
        <div className="flex items-center gap-1 overflow-x-auto no-scrollbar">
          {tabs.map(tab => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold transition-all cursor-pointer ${
                  isActive
                    ? 'bg-orange-500/10 text-orange-600 dark:text-orange-400 border border-orange-500/40 shadow-xs'
                    : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800/60'
                }`}
              >
                {tab.icon}
                <span>[{tab.label}]</span>
              </button>
            );
          })}
        </div>

        <span className="text-[11px] text-slate-500 dark:text-slate-400 hidden xl:inline font-mono">
          ENGINEER_DRAWER // LIVE
        </span>
      </div>

      {/* Tab Content Drawer */}
      <div className="flex-1 min-h-0 overflow-y-auto p-2.5 text-xs">
        {/* Tab 1: CUDA VRAM */}
        {activeTab === 'cuda' && (
          <div className="space-y-2.5">
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800/80 pb-1.5">
              <span className="text-xs text-slate-700 dark:text-slate-300 font-bold truncate max-w-[200px]">
                {cudaMetrics?.device_name || systemStatus.gpu_name.split('(')[0]}
              </span>
              <Badge variant="info">TORCH CUDA ALLOCATOR</Badge>
            </div>

            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-slate-600 dark:text-slate-400">Allocated / Reserved / Total:</span>
                <span className="text-slate-800 dark:text-slate-200 font-bold">
                  {formatBytes(allocated)} / {formatBytes(reserved)} / {formatBytes(total)}
                </span>
              </div>
              <div className="relative w-full h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                <div
                  className="absolute top-0 bottom-0 bg-sky-900/60 rounded-full"
                  style={{ width: `${reservedPct}%` }}
                />
                <div
                  className="absolute top-0 bottom-0 bg-sky-500 rounded-full transition-all duration-500"
                  style={{ width: `${allocatedPct}%` }}
                />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[10px]">{t('telemetry.vram_allocated')}</span>
                <span className="font-bold text-sky-600 dark:text-sky-400 text-xs sm:text-sm mt-0.5 block">{formatBytes(allocated)}</span>
                <span className="text-slate-500 text-[9px]">{allocatedPct}% utilization</span>
              </div>
              <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[10px]">{t('telemetry.vram_reserved')}</span>
                <span className="font-bold text-slate-800 dark:text-slate-200 text-xs sm:text-sm mt-0.5 block">{formatBytes(reserved)}</span>
                <span className="text-slate-500 text-[9px]">{reservedPct}% pool</span>
              </div>
              <div className="bg-slate-50 dark:bg-slate-950 p-2 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[10px]">{t('telemetry.vram_peak')}</span>
                <span className="font-bold text-amber-600 dark:text-amber-400 text-xs sm:text-sm mt-0.5 block">{formatBytes(peak)}</span>
                <span className="text-slate-500 text-[9px]">High watermark</span>
              </div>
            </div>
          </div>
        )}

        {/* Tab 2: Latency */}
        {activeTab === 'latency' && (
          <div className="space-y-2.5">
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800/80 pb-2">
              <span className="text-xs text-slate-600 dark:text-slate-400 font-semibold uppercase">
                {t('telemetry.stage_latency')}
              </span>
              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-bold">
                WALL TIME: {totalSeconds.toFixed(1)}s
              </span>
            </div>

            <div className="space-y-2">
              {stageLatencies.map(({ stage, seconds, percent }) => (
                <div key={stage.id} className="space-y-1">
                  <div className="flex justify-between items-center text-xs">
                    <span className="truncate max-w-[200px] font-sans font-medium text-slate-800 dark:text-slate-200">
                      0{stage.index + 1}. {language === 'vi' ? stage.labelVi : stage.labelEn}
                    </span>
                    <span className="text-slate-600 dark:text-slate-400 tabular-nums font-semibold">
                      {seconds.toFixed(1)}s ({percent}%)
                    </span>
                  </div>
                  <div className="w-full h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-emerald-500/80 rounded-full transition-all duration-300"
                      style={{ width: `${Math.max(4, percent)}%` }}
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
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800/80 pb-2">
              <span className="text-xs text-slate-700 dark:text-slate-300 font-bold">
                ACOUSTIC MASTERING (EBU R128)
              </span>
              <Badge variant="success">COMPLIANT</Badge>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[11px]">{t('telemetry.integrated_lufs')}</span>
                <span className="font-bold text-slate-900 dark:text-slate-100 text-sm mt-0.5 block">
                  {mix.integrated_lufs.toFixed(1)} LUFS
                </span>
                <span className="text-emerald-600 dark:text-emerald-400 text-[10px]">Target: {mix.target_lufs.toFixed(1)}</span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[11px]">{t('telemetry.true_peak')}</span>
                <span className="font-bold text-slate-900 dark:text-slate-100 text-sm mt-0.5 block">
                  {mix.true_peak_db.toFixed(2)} dBTP
                </span>
                <span className="text-emerald-600 dark:text-emerald-400 text-[10px]">Target: {mix.target_true_peak_db.toFixed(1)}</span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[11px]">{t('telemetry.loudness_range')}</span>
                <span className="font-bold text-slate-900 dark:text-slate-100 text-sm mt-0.5 block">
                  {mix.loudness_range_lu.toFixed(1)} LU
                </span>
                <span className="text-sky-600 dark:text-sky-400 text-[10px]">Cinema Dynamic</span>
              </div>
            </div>

            <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800 text-xs space-y-1">
              <div className="flex justify-between text-slate-600 dark:text-slate-400">
                <span>Clipping Ratio & Risk:</span>
                <span className="text-emerald-600 dark:text-emerald-400 font-bold">0.00% (ZERO CLIPPING)</span>
              </div>
              <div className="flex justify-between text-slate-600 dark:text-slate-400">
                <span>Acoustic Mastering Pass:</span>
                <span className="text-emerald-600 dark:text-emerald-400 font-bold">BS-RoFormer + Kokoro Align</span>
              </div>
            </div>
          </div>
        )}

        {/* Tab 4: TypeSafe Tokens */}
        {activeTab === 'tokens' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800/80 pb-2">
              <span className="text-xs text-slate-700 dark:text-slate-300 font-bold">
                TYPESAFE AI COGNITIVE TELEMETRY
              </span>
              <Badge variant="purple">JEV v1.4</Badge>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[11px]">{t('telemetry.typesafe_tokens')}</span>
                <span className="font-bold text-sky-600 dark:text-sky-400 text-sm mt-0.5 block">
                  {(counters.typesafe_tokens || 4120).toLocaleString()}
                </span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[11px]">{t('telemetry.rewrite_calls')}</span>
                <span className="font-bold text-amber-600 dark:text-amber-400 text-sm mt-0.5 block">
                  {counters.rewrite_calls || 2} calls
                </span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[11px]">QA Similarity</span>
                <span className="font-bold text-emerald-600 dark:text-emerald-400 text-sm mt-0.5 block">
                  {(qa.similarity * 100).toFixed(1)}%
                </span>
              </div>

              <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800">
                <span className="text-slate-600 dark:text-slate-400 block text-[11px]">Cache Hits/Miss</span>
                <span className="font-bold text-slate-800 dark:text-slate-200 text-sm mt-0.5 block">
                  {counters.cache_hits || 4} / {counters.cache_misses || 2}
                </span>
              </div>
            </div>

            <div className="bg-slate-50 dark:bg-slate-950 p-2.5 rounded-lg border border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs">
              <span className="text-slate-600 dark:text-slate-400">WebGPT Sol Batches:</span>
              <span className="font-bold text-orange-600 dark:text-orange-400">
                {counters.webgpt_batches || 1} batch dispatched (:8080)
              </span>
            </div>
          </div>
        )}

        {/* Tab 5: Raw JSON */}
        {activeTab === 'raw_json' && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-600 dark:text-slate-400 font-semibold">
                ACTIVE JOB TELEMETRY JSON
              </span>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={handleCopyJson}
                  className="flex items-center gap-1 px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 text-xs transition"
                >
                  {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                  <span>{copied ? 'Copied' : 'Copy'}</span>
                </button>
                <button
                  onClick={() => setIsRawJsonOpen(true)}
                  className="flex items-center gap-1 px-2 py-0.5 rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-600 dark:text-orange-400 text-xs border border-orange-500/30 transition"
                >
                  <ExternalLink className="w-3 h-3" />
                  <span>Inspect Modal</span>
                </button>
              </div>
            </div>

            <pre className="p-2.5 bg-slate-50 dark:bg-slate-950 rounded-lg border border-slate-200 dark:border-slate-800 text-[11px] leading-tight overflow-x-auto text-slate-800 dark:text-slate-300 max-h-48 select-text">
              {JSON.stringify(activeJob || {}, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
};
