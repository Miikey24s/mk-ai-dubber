import React, { useState, useEffect } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import {
  formatSeconds,
  resolveActiveStageIndex,
  PIPELINE_STAGES,
} from '@/lib/utils';
import { deriveEtaTelemetry, finiteNumber } from '@/lib/telemetry';
import { StageDefinition } from '@/types';
import { StageDetailModal } from '@/components/pipeline/StageDetailModal';
import {
  Wifi,
  Play,
  Pause,
  Square,
  CheckCircle2,
  Timer,
  Clock,
} from 'lucide-react';

export const SystemBar: React.FC = () => {
  const { activeJob, systemStatus, controlJob } = useJob();
  const { language, t } = useTranslation();

  const [liveElapsed, setLiveElapsed] = useState<number | null>(null);
  const [selectedStage, setSelectedStage] = useState<StageDefinition | null>(null);

  const isJobComplete = activeJob?.status === 'completed';
  const persistedElapsed = activeJob?.result?.elapsed_seconds ?? activeJob?.metrics?.total_wall_seconds;

  useEffect(() => {
    if (!activeJob || !finiteNumber(persistedElapsed) || persistedElapsed < 0) {
      setLiveElapsed(null);
      return;
    }
    setLiveElapsed(persistedElapsed);

    if (activeJob.status === 'running' && persistedElapsed >= 0) {
      const timer = setInterval(() => {
        setLiveElapsed(prev => (prev === null ? prev : prev + 1));
      }, 1000);
      return () => clearInterval(timer);
    }
  }, [activeJob, persistedElapsed]);

  const eta = deriveEtaTelemetry({
    status: activeJob?.status,
    progress: activeJob?.progress,
    elapsedSeconds: liveElapsed,
    durationSeconds: activeJob?.result?.duration_seconds,
    realTimeFactor: activeJob?.result?.real_time_factor,
  });
  const progress = eta.progress;

  const activeIndex = activeJob && activeJob.stage && progress !== null
    ? resolveActiveStageIndex(activeJob.stage, progress)
    : -1;

  const activeStage = activeIndex >= 0 ? PIPELINE_STAGES[activeIndex] ?? null : null;
  const stageName = activeStage ? (language === 'vi' ? activeStage.labelVi : activeStage.labelEn) : '';
  const unavailable = t('common.unavailable');

  return (
    <div className="h-[36px] shrink-0 bg-white dark:bg-[#090d16] border-b border-slate-200 dark:border-slate-800 px-3 flex items-center justify-between text-xs font-mono select-none overflow-x-auto no-scrollbar gap-4 w-full">
      {/* Section 1: Progress bar with stage label */}
      <div className="flex flex-1 items-center gap-3 min-w-[200px] max-w-xl">
        <button
          onClick={() => setSelectedStage(activeStage)}
          className="font-bold text-slate-800 dark:text-slate-200 hover:text-orange-500 dark:hover:text-orange-400 transition cursor-pointer whitespace-nowrap"
          title={language === 'vi' ? 'Xem chi tiết bước xử lý' : 'Click to view stage details'}
        >
          {activeStage ? t('system.stage_of', { current: activeIndex + 1, total: 7 }) : unavailable}: {stageName || unavailable} · {progress === null ? unavailable : `${Math.round(progress * 100)}%`}
        </button>
        <div className="flex-1 h-1.5 bg-slate-200 dark:bg-slate-800 rounded-full relative mx-2 cursor-pointer" onClick={() => setSelectedStage(activeStage)}>
          {/* 7 stage marker dots */}
          {PIPELINE_STAGES.map((_, i) => (
            <div key={i}
              className={`absolute top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full ${i < activeIndex ? 'bg-emerald-500' : i === activeIndex ? 'bg-orange-500 ring-2 ring-orange-500/30' : 'bg-slate-300 dark:bg-slate-700'}`}
              style={{ left: `${(i / 6) * 100}%` }}
            />
          ))}
          {/* Progress fill */}
          <div className="h-full bg-orange-500 rounded-full transition-all duration-500"
            style={{ width: progress === null ? '0%' : `${progress * 100}%` }} />
        </div>
      </div>

      {/* Section 2: ETA Timer */}
      <div className="flex items-center gap-2 shrink-0">
        <div className="flex items-center gap-1 text-slate-600 dark:text-slate-300">
          <Timer className="w-3.5 h-3.5 text-slate-500 dark:text-slate-400" />
            <span className="text-slate-800 dark:text-slate-100 font-bold tabular-nums">
            {eta.elapsedSeconds === null ? unavailable : formatSeconds(eta.elapsedSeconds)}
          </span>
        </div>
        <span className="text-slate-300 dark:text-slate-700">/</span>
        <div className="flex items-center gap-1">
          <Clock className="w-3.5 h-3.5 text-sky-500" />
          <span className="text-sky-600 dark:text-sky-400 font-bold tabular-nums">
            {isJobComplete ? t('stepper.completed') : eta.remainingSeconds === null ? unavailable : `ETA ${formatSeconds(eta.remainingSeconds)}`}
          </span>
        </div>
      </div>

      {/* Section 3: Stream Status & Job Control */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Stream Live Indicator */}
        <div className="flex items-center gap-1 text-[11px]" title="Telemetry Stream Connection">
          <Wifi className="w-3.5 h-3.5 text-emerald-500" />
          <span className="text-emerald-600 dark:text-emerald-400 font-semibold hidden sm:inline">
            {systemStatus.websocket_connected ? t('system.ws_live') : t('system.polling')}
          </span>
        </div>

        {/* Job Actions */}
        {activeJob && (
          <div className="flex items-center gap-1.5 ml-2">
            {activeJob.status === 'running' ? (
              <button
                onClick={() => controlJob('pause')}
                className="flex items-center gap-1.5 h-7 px-2.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-xs font-semibold transition cursor-pointer"
                title={t('common.pause')}
              >
                <Pause className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">{t('common.pause')}</span>
              </button>
            ) : activeJob.status === 'paused' ? (
              <button
                onClick={() => controlJob('run')}
                className="flex items-center gap-1.5 h-7 px-2.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-xs font-semibold transition cursor-pointer"
                title={t('common.resume')}
              >
                <Play className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">{t('common.resume')}</span>
              </button>
            ) : (
              <div className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-bold text-xs px-2.5 h-7">
                <CheckCircle2 className="w-4 h-4" />
                <span>{activeJob.status.toUpperCase()}</span>
              </div>
            )}

            {(activeJob.status === 'running' || activeJob.status === 'paused') && (
              <button
                onClick={() => controlJob('cancel')}
                className="flex items-center gap-1.5 h-7 px-2.5 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs font-semibold transition cursor-pointer"
                title={t('common.abort')}
              >
                <Square className="w-3 h-3" />
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
