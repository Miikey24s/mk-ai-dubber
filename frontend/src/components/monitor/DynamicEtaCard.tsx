import React, { useState, useEffect } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { formatSeconds, formatRtf, resolveActiveStageIndex, PIPELINE_STAGES } from '@/lib/utils';
import { Clock, Zap, Timer, CheckCircle, Activity } from 'lucide-react';

export const DynamicEtaCard: React.FC = () => {
  const { activeJob } = useJob();
  const { language, t } = useTranslation();

  const [liveElapsed, setLiveElapsed] = useState<number>(0);

  const isCompleted = activeJob?.status === 'completed';
  const progress = activeJob?.progress || 0;
  const rtf = activeJob?.result?.real_time_factor || 0.77;
  const audioDuration = activeJob?.result?.duration_seconds || 184.5;

  // Running elapsed timer
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

  // Remaining ETA formula based on running RTF and remaining progress
  let remainingSeconds = 0;
  if (!isCompleted && progress > 0 && progress < 1) {
    const estimatedTotal = liveElapsed / progress;
    remainingSeconds = Math.max(0, estimatedTotal - liveElapsed);
  } else if (!isCompleted && progress === 0) {
    remainingSeconds = audioDuration * rtf;
  }

  const completionDate = new Date(Date.now() + remainingSeconds * 1000);
  const completionTimeStr = completionDate.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  const activeStageIdx = activeJob
    ? resolveActiveStageIndex(activeJob.stage, activeJob.progress)
    : 0;
  const currentStageDef = PIPELINE_STAGES[activeStageIdx] || PIPELINE_STAGES[0];

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-4 shadow-sm font-mono">
      <div className="flex items-center justify-between mb-3 border-b border-slate-100 dark:border-slate-800/80 pb-2">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-orange-500 animate-pulse" />
          <h3 className="text-xs font-bold tracking-wider text-slate-800 dark:text-slate-200 uppercase">
            {t('eta.title')}
          </h3>
        </div>
        <span
          className={`text-2xs font-semibold px-2 py-0.5 rounded-full ${
            isCompleted
              ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
              : 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
          }`}
        >
          {isCompleted ? 'PIPELINE_COMPLETE' : 'REALTIME_ESTIMATE'}
        </span>
      </div>

      {/* Progress Bar */}
      <div className="space-y-1 mb-4">
        <div className="flex justify-between text-2xs text-slate-500">
          <span>{language === 'vi' ? currentStageDef.labelVi : currentStageDef.labelEn}</span>
          <span className="font-bold text-slate-700 dark:text-slate-300">
            {Math.round(progress * 100)}%
          </span>
        </div>
        <div className="w-full h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-300 ${
              isCompleted
                ? 'bg-emerald-500'
                : 'bg-gradient-to-r from-orange-500 to-amber-500 animate-pulse'
            }`}
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
      </div>

      {/* 4-Column Metric Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        {/* Elapsed */}
        <div className="bg-slate-50 dark:bg-slate-950/60 p-2.5 rounded border border-slate-100 dark:border-slate-800">
          <div className="text-2xs text-slate-400 flex items-center gap-1 mb-1">
            <Timer className="w-3 h-3 text-slate-400" />
            <span>{t('eta.elapsed')}</span>
          </div>
          <div className="text-sm font-bold text-slate-800 dark:text-slate-200 tabular-nums">
            {formatSeconds(liveElapsed)}
          </div>
        </div>

        {/* Remaining */}
        <div className="bg-slate-50 dark:bg-slate-950/60 p-2.5 rounded border border-slate-100 dark:border-slate-800">
          <div className="text-2xs text-slate-400 flex items-center gap-1 mb-1">
            <Clock className="w-3 h-3 text-sky-400" />
            <span>{t('eta.remaining')}</span>
          </div>
          <div className="text-sm font-bold text-sky-600 dark:text-sky-400 tabular-nums">
            {isCompleted ? '00:00.0' : formatSeconds(remainingSeconds)}
          </div>
        </div>

        {/* RTF Speed */}
        <div className="bg-slate-50 dark:bg-slate-950/60 p-2.5 rounded border border-slate-100 dark:border-slate-800">
          <div className="text-2xs text-slate-400 flex items-center gap-1 mb-1">
            <Zap className="w-3 h-3 text-amber-400" />
            <span>{t('eta.speed')}</span>
          </div>
          <div className="text-sm font-bold text-amber-600 dark:text-amber-400">
            {formatRtf(rtf)}
          </div>
        </div>

        {/* Est. Completion Clock */}
        <div className="bg-slate-50 dark:bg-slate-950/60 p-2.5 rounded border border-slate-100 dark:border-slate-800">
          <div className="text-2xs text-slate-400 flex items-center gap-1 mb-1">
            <CheckCircle className="w-3 h-3 text-emerald-400" />
            <span>{t('eta.completion')}</span>
          </div>
          <div className="text-sm font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">
            {isCompleted ? 'DONE' : completionTimeStr}
          </div>
        </div>
      </div>
    </div>
  );
};
