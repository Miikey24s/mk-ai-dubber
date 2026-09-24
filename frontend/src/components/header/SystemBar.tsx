import React, { useState, useEffect } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import {
  formatBytes,
  formatRtf,
  formatSeconds,
  resolveActiveStageIndex,
  PIPELINE_STAGES,
} from '@/lib/utils';
import { StageDefinition } from '@/types';
import { StageDetailModal } from '@/components/pipeline/StageDetailModal';
import {
  Cpu,
  Gauge,
  Wifi,
  Play,
  Pause,
  Square,
  CheckCircle2,
  Radio,
  Timer,
  Clock,
  Check,
  Loader2,
} from 'lucide-react';

export const SystemBar: React.FC = () => {
  const { activeJob, systemStatus, controlJob } = useJob();
  const { language, t } = useTranslation();

  const [liveElapsed, setLiveElapsed] = useState<number>(0);
  const [selectedStage, setSelectedStage] = useState<StageDefinition | null>(null);

  const isJobComplete = activeJob?.status === 'completed';
  const progress = activeJob?.progress || 0;
  const rtfValue = activeJob?.result?.real_time_factor || 0.77;
  const audioDuration = activeJob?.result?.duration_seconds || 184.5;

  const vramPercent = Math.round(
    (systemStatus.gpu_vram_used_bytes / systemStatus.gpu_vram_total_bytes) * 100
  );

  // Live timer simulation / sync
  useEffect(() => {
    if (!activeJob) return;
    const initialElapsed =
      activeJob.result?.elapsed_seconds ||
      activeJob.metrics?.total_wall_seconds ||
      Math.max(1, Math.floor((Date.now() - new Date(activeJob.created_at).getTime()) / 1000));
    setLiveElapsed(initialElapsed);

    if (activeJob.status === 'running') {
      const timer = setInterval(() => {
        setLiveElapsed(prev => prev + 1);
      }, 1000);
      return () => clearInterval(timer);
    }
  }, [activeJob]);

  // ETA calculation
  let remainingSeconds = 0;
  if (!isJobComplete && progress > 0 && progress < 1) {
    const estimatedTotal = liveElapsed / progress;
    remainingSeconds = Math.max(0, estimatedTotal - liveElapsed);
  } else if (!isJobComplete && progress === 0) {
    remainingSeconds = audioDuration * rtfValue;
  }

  const activeIndex = activeJob
    ? resolveActiveStageIndex(activeJob.stage, activeJob.progress)
    : 0;

  return (
    <div className="h-[36px] shrink-0 bg-white dark:bg-[#090d16] border-b border-slate-200 dark:border-[#1e293b] px-3 flex items-center justify-between text-xs font-mono select-none overflow-x-auto no-scrollbar gap-3 w-full">
      {/* Section 1: System Telemetry (GPU, RTF, WebGPT, ETA) */}
      <div className="flex items-center gap-3 shrink-0">
        {/* GPU VRAM */}
        <div className="flex items-center gap-1.5" title={systemStatus.gpu_name}>
          <Cpu className="w-3.5 h-3.5 text-sky-500" />
          <span className="font-semibold text-slate-800 dark:text-slate-200">VRAM:</span>
          <span className="text-slate-700 dark:text-slate-300">
            {formatBytes(systemStatus.gpu_vram_used_bytes)}/{formatBytes(systemStatus.gpu_vram_total_bytes)}
          </span>
          <div className="w-10 h-1.5 bg-slate-200 dark:bg-slate-800 rounded-full overflow-hidden hidden sm:block">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                vramPercent > 85 ? 'bg-rose-500' : vramPercent > 70 ? 'bg-amber-500' : 'bg-sky-500'
              }`}
              style={{ width: `${vramPercent}%` }}
            />
          </div>
          <span className="text-slate-600 dark:text-slate-400">{vramPercent}%</span>
        </div>

        <div className="h-3 w-px bg-slate-200 dark:bg-slate-800" />

        {/* RTF Speed */}
        <div className="flex items-center gap-1.5" title={t('system.rtf')}>
          <Gauge className="w-3.5 h-3.5 text-emerald-500" />
          <span className="font-semibold text-slate-800 dark:text-slate-200">RTF:</span>
          <span className="text-emerald-600 dark:text-emerald-400 font-bold">{formatRtf(rtfValue)}</span>
        </div>

        <div className="h-3 w-px bg-slate-200 dark:bg-slate-800 hidden md:block" />

        {/* WebGPT Status */}
        <div className="items-center gap-1.5 hidden md:flex" title="Codex WebGPT Sol Engine">
          <Radio className="w-3.5 h-3.5 text-orange-500 animate-pulse" />
          <span className="font-semibold text-slate-800 dark:text-slate-200">WebGPT:</span>
          <span className="text-orange-600 dark:text-orange-400 truncate max-w-[110px]">
            {systemStatus.webgpt_model.replace('chatgpt-web/', '')}
          </span>
          <span className="text-slate-500 dark:text-slate-400">:{systemStatus.webgpt_port}</span>
        </div>

        <div className="h-3 w-px bg-slate-200 dark:bg-slate-800 hidden lg:block" />

        {/* Dynamic Job ETA */}
        <div className="items-center gap-2 hidden lg:flex">
          <div className="flex items-center gap-1 text-slate-600 dark:text-slate-400">
            <Timer className="w-3 h-3 text-slate-500" />
            <span className="text-slate-800 dark:text-slate-200 font-bold tabular-nums">
              {formatSeconds(liveElapsed)}
            </span>
          </div>
          <span className="text-slate-300 dark:text-slate-700">/</span>
          <div className="flex items-center gap-1">
            <Clock className="w-3 h-3 text-sky-500" />
            <span className="text-sky-600 dark:text-sky-400 font-bold tabular-nums">
              {isJobComplete ? 'COMPLETE' : `ETA ${formatSeconds(remainingSeconds)}`}
            </span>
          </div>
          <span className="text-slate-600 dark:text-slate-400 font-bold">
            ({Math.round(progress * 100)}%)
          </span>
        </div>
      </div>

      {/* Section 2: 7-Stage Pipeline Mini-Stepper */}
      <div className="flex items-center gap-1 shrink-0 overflow-x-auto no-scrollbar">
        {PIPELINE_STAGES.map((stage, idx) => {
          const isDone = isJobComplete || idx < activeIndex;
          const isCurrent = !isJobComplete && idx === activeIndex && activeJob?.status === 'running';
          const isPaused = !isJobComplete && idx === activeIndex && activeJob?.status === 'paused';
          const isWaiting = !isJobComplete && idx > activeIndex;

          const stageShortNames = ['PREP', 'SEP', 'ASR', 'SOL', 'TTS', 'MIX', 'QA'];
          const shortName = stageShortNames[idx] || `0${idx + 1}`;

          return (
            <button
              key={stage.id}
              onClick={() => setSelectedStage(stage)}
              className={`h-6 px-1.5 rounded flex items-center gap-1 text-[11px] font-mono border transition-all cursor-pointer ${
                isCurrent
                  ? 'bg-orange-500 text-white font-bold border-orange-600 shadow-sm animate-pulse'
                  : isDone
                  ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20'
                  : isPaused
                  ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30'
                  : 'bg-slate-100 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-800/80 hover:text-slate-800 dark:hover:text-slate-200'
              }`}
              title={`${stage.index + 1}. ${language === 'vi' ? stage.labelVi : stage.labelEn} (${stage.internalStageNames[0]}) - Click to inspect`}
            >
              {isCurrent && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
              {isDone && <Check className="w-2.5 h-2.5 stroke-[3]" />}
              {isPaused && <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-ping" />}
              {isWaiting && <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600" />}
              <span>{shortName}</span>
            </button>
          );
        })}
      </div>

      {/* Section 3: Stream Status & Job Control */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Stream Live Indicator */}
        <div className="flex items-center gap-1 text-[11px]" title="Telemetry Stream Connection">
          <Wifi className="w-3 h-3 text-emerald-500" />
          <span className="text-emerald-600 dark:text-emerald-400 font-semibold hidden sm:inline">
            {systemStatus.websocket_connected ? 'WS LIVE' : 'POLLING'}
          </span>
        </div>

        {/* Job Actions */}
        {activeJob && (
          <div className="flex items-center gap-1.5">
            {activeJob.status === 'running' ? (
              <button
                onClick={() => controlJob('pause')}
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-[11px] font-semibold transition"
                title="Pause Job"
              >
                <Pause className="w-3 h-3" />
                <span className="hidden sm:inline">{t('common.pause')}</span>
              </button>
            ) : activeJob.status === 'paused' ? (
              <button
                onClick={() => controlJob('run')}
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-[11px] font-semibold transition"
                title="Resume Job"
              >
                <Play className="w-3 h-3" />
                <span className="hidden sm:inline">{t('common.resume')}</span>
              </button>
            ) : (
              <div className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-bold text-[11px]">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>{activeJob.status.toUpperCase()}</span>
              </div>
            )}

            {(activeJob.status === 'running' || activeJob.status === 'paused') && (
              <button
                onClick={() => controlJob('cancel')}
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-[11px] font-semibold transition"
                title="Abort Job"
              >
                <Square className="w-2.5 h-2.5" />
                <span className="hidden sm:inline">{t('common.abort')}</span>
              </button>
            )}
          </div>
        )}
      </div>

      {/* Stage Telemetry Modal */}
      <StageDetailModal
        stage={selectedStage}
        job={activeJob}
        onClose={() => setSelectedStage(null)}
      />
    </div>
  );
};
