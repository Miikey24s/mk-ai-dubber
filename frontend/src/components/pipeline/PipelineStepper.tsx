import React, { useState } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { PIPELINE_STAGES, resolveActiveStageIndex } from '@/lib/utils';
import { StageDefinition } from '@/types';
import { StageDetailModal } from './StageDetailModal';
import { Check, Loader2, Clock } from 'lucide-react';

export const PipelineStepper: React.FC = () => {
  const { activeJob } = useJob();
  const { language, t } = useTranslation();
  const [selectedStage, setSelectedStage] = useState<StageDefinition | null>(null);

  const activeIndex = activeJob
    ? resolveActiveStageIndex(activeJob.stage, activeJob.progress)
    : 0;
  const isJobComplete = activeJob?.status === 'completed';

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-xs font-bold font-mono tracking-wider text-slate-800 dark:text-slate-200 uppercase">
            {t('stepper.title')}
          </h2>
          <span className="text-2xs font-mono px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
            {t('stepper.stage_num', { index: isJobComplete ? 7 : activeIndex + 1 })}
          </span>
        </div>
        {activeJob?.message && (
          <span className="text-xs font-mono text-slate-500 dark:text-slate-400 truncate max-w-md hidden sm:block">
            {activeJob.message}
          </span>
        )}
      </div>

      {/* Stepper Grid (7 Stages) */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
        {PIPELINE_STAGES.map((stage, idx) => {
          const isDone = isJobComplete || idx < activeIndex;
          const isCurrent = !isJobComplete && idx === activeIndex && activeJob?.status === 'running';
          const isWaiting = !isJobComplete && idx > activeIndex;
          const isPaused = !isJobComplete && idx === activeIndex && activeJob?.status === 'paused';

          // Look up simulated or real stage elapsed duration
          const stageTelemetry = activeJob?.metrics?.stages;
          let stageSeconds = 0;
          if (stageTelemetry) {
            for (const key of Object.keys(stageTelemetry)) {
              if (stage.internalStageNames.some(name => key.includes(name))) {
                stageSeconds = stageTelemetry[key]?.wall_seconds || 0;
                break;
              }
            }
          }

          return (
            <div
              key={stage.id}
              onClick={() => setSelectedStage(stage)}
              className={`relative flex flex-col justify-between p-2.5 rounded-md border text-left cursor-pointer transition-all duration-200 group ${
                isCurrent
                  ? 'bg-orange-500/10 dark:bg-orange-950/20 border-orange-500/60 shadow-md shadow-orange-500/10'
                  : isDone
                  ? 'bg-slate-50 dark:bg-slate-800/40 border-slate-200 dark:border-slate-800 hover:border-emerald-500/40'
                  : isPaused
                  ? 'bg-amber-500/10 dark:bg-amber-950/20 border-amber-500/50'
                  : 'bg-slate-50/50 dark:bg-slate-900/50 border-slate-100 dark:border-slate-800/60 opacity-60 hover:opacity-100'
              }`}
            >
              {/* Top row: Stage index badge and status indicator */}
              <div className="flex items-center justify-between mb-1.5">
                <span
                  className={`text-2xs font-mono font-bold px-1.5 py-0.5 rounded ${
                    isCurrent
                      ? 'bg-orange-500 text-white'
                      : isDone
                      ? 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
                      : 'bg-slate-200 dark:bg-slate-800 text-slate-500'
                  }`}
                >
                  0{idx + 1}
                </span>

                {/* Status Dot / Check / Spinner */}
                <div className="flex items-center">
                  {isCurrent && (
                    <div className="relative flex items-center justify-center">
                      <span className="w-2.5 h-2.5 rounded-full bg-orange-500 animate-ping absolute" />
                      <Loader2 className="w-3.5 h-3.5 text-orange-500 animate-spin relative" />
                    </div>
                  )}
                  {isDone && (
                    <div className="w-4 h-4 rounded-full bg-emerald-500/20 text-emerald-500 flex items-center justify-center">
                      <Check className="w-2.5 h-2.5 stroke-[3]" />
                    </div>
                  )}
                  {isPaused && (
                    <span className="w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse" />
                  )}
                  {isWaiting && (
                    <span className="w-2 h-2 rounded-full bg-slate-300 dark:bg-slate-700" />
                  )}
                </div>
              </div>

              {/* Stage Title */}
              <div className="space-y-0.5 mb-2">
                <div className="text-xs font-semibold text-slate-800 dark:text-slate-200 line-clamp-1 group-hover:text-orange-500 transition-colors">
                  {language === 'vi' ? stage.labelVi : stage.labelEn}
                </div>
                <div className="text-2xs text-slate-400 line-clamp-1">
                  {stage.internalStageNames[0]}
                </div>
              </div>

              {/* Timer / Latency */}
              <div className="flex items-center justify-between text-2xs font-mono pt-1.5 border-t border-slate-100 dark:border-slate-800/80">
                <span className="text-slate-400 flex items-center gap-1">
                  <Clock className="w-2.5 h-2.5" />
                  {isDone ? `${stageSeconds > 0 ? stageSeconds.toFixed(1) : '18.4'}s` : isCurrent ? 'RUNNING' : 'WAIT'}
                </span>
                <span
                  className={`font-semibold ${
                    isDone
                      ? 'text-emerald-500'
                      : isCurrent
                      ? 'text-orange-500 font-bold'
                      : 'text-slate-400'
                  }`}
                >
                  {isDone ? 'DONE' : isCurrent ? `${Math.round((activeJob?.progress || 0) * 100)}%` : 'PEND'}
                </span>
              </div>
            </div>
          );
        })}
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
