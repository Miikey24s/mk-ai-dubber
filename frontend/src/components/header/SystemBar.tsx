import React from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { formatBytes, formatRtf } from '@/lib/utils';
import {
  Cpu,
  Gauge,
  Wifi,
  Play,
  Pause,
  Square,
  CheckCircle2,
  Radio,
} from 'lucide-react';

export const SystemBar: React.FC = () => {
  const { activeJob, systemStatus, controlJob } = useJob();
  const { t } = useTranslation();

  const vramPercent = Math.round(
    (systemStatus.gpu_vram_used_bytes / systemStatus.gpu_vram_total_bytes) * 100
  );

  const rtfValue = activeJob?.result?.real_time_factor || 0.77;

  return (
    <div className="bg-slate-100/90 dark:bg-slate-950/80 border-b border-slate-200 dark:border-slate-800/80 px-4 sm:px-6 py-1.5 text-2xs font-mono text-slate-600 dark:text-slate-400 select-none">
      <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-3">
        {/* Left: System Telemetry Pills */}
        <div className="flex flex-wrap items-center gap-3 sm:gap-5">
          {/* GPU VRAM */}
          <div className="flex items-center gap-2" title={systemStatus.gpu_name}>
            <Cpu className="w-3.5 h-3.5 text-sky-500" />
            <span className="font-semibold text-slate-800 dark:text-slate-300">GPU VRAM:</span>
            <div className="flex items-center gap-1.5">
              <span>{formatBytes(systemStatus.gpu_vram_used_bytes)} / {formatBytes(systemStatus.gpu_vram_total_bytes)}</span>
              <div className="w-12 h-1.5 bg-slate-300 dark:bg-slate-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${
                    vramPercent > 85 ? 'bg-rose-500' : vramPercent > 70 ? 'bg-amber-500' : 'bg-sky-500'
                  }`}
                  style={{ width: `${vramPercent}%` }}
                />
              </div>
              <span className="text-slate-500">{vramPercent}%</span>
            </div>
          </div>

          {/* RTF Speed */}
          <div className="flex items-center gap-1.5">
            <Gauge className="w-3.5 h-3.5 text-emerald-500" />
            <span className="font-semibold text-slate-800 dark:text-slate-300">{t('system.rtf')}:</span>
            <span className="text-emerald-600 dark:text-emerald-400 font-bold">{formatRtf(rtfValue)}</span>
          </div>

          {/* WebGPT Status */}
          <div className="flex items-center gap-1.5 hidden md:flex">
            <Radio className="w-3.5 h-3.5 text-orange-500 animate-pulse" />
            <span className="font-semibold text-slate-800 dark:text-slate-300">WebGPT:</span>
            <span className="text-orange-600 dark:text-orange-400 truncate max-w-[130px]">
              {systemStatus.webgpt_model.replace('chatgpt-web/', '')}
            </span>
            <span className="text-slate-400 text-2xs">:{systemStatus.webgpt_port}</span>
          </div>

          {/* Connection Stream */}
          <div className="flex items-center gap-1.5">
            <Wifi className="w-3 h-3 text-emerald-500" />
            <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
              {systemStatus.websocket_connected ? t('system.websocket') : t('system.polling')}
            </span>
          </div>
        </div>

        {/* Right: Job Controls */}
        {activeJob && (
          <div className="flex items-center gap-1.5">
            <span className="text-slate-500 mr-1 hidden sm:inline">
              ID: <span className="text-slate-700 dark:text-slate-300">{activeJob.id.substring(0, 12)}</span>
            </span>

            {activeJob.status === 'running' ? (
              <button
                onClick={() => controlJob('pause')}
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 transition"
              >
                <Pause className="w-3 h-3" />
                <span>{t('common.pause')}</span>
              </button>
            ) : activeJob.status === 'paused' ? (
              <button
                onClick={() => controlJob('run')}
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 transition"
              >
                <Play className="w-3 h-3" />
                <span>{t('common.resume')}</span>
              </button>
            ) : (
              <div className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-bold">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>{activeJob.status.toUpperCase()}</span>
              </div>
            )}

            {(activeJob.status === 'running' || activeJob.status === 'paused') && (
              <button
                onClick={() => controlJob('cancel')}
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 transition"
              >
                <Square className="w-3 h-3" />
                <span>{t('common.abort')}</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
