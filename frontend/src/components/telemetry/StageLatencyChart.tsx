import React from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { PIPELINE_STAGES } from '@/lib/utils';
import { nonNegativeFinite, positiveFinite } from '@/lib/telemetry';
import { Clock } from 'lucide-react';

export const StageLatencyChart: React.FC = () => {
  const { activeJob } = useJob();
  const { language, t } = useTranslation();

  const stagesData = activeJob?.metrics?.stages;
  const totalSeconds = activeJob?.metrics?.total_wall_seconds;
  const unavailable = t('common.unavailable');

  // Map the 7 stages to elapsed seconds
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
    return {
      stage: stg,
      seconds,
      percent,
    };
  });

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3.5 shadow-sm font-mono space-y-3">
      <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-emerald-500" />
          <h4 className="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase">
            {t('telemetry.stage_latency')}
          </h4>
        </div>
        <span className="text-2xs text-slate-500 font-bold">
          TOTAL: {positiveFinite(totalSeconds) ? `${totalSeconds.toFixed(1)}s` : unavailable}
        </span>
      </div>

      {/* Latency Bars */}
      <div className="space-y-2 text-2xs">
        {stageLatencies.map(({ stage, seconds, percent }) => (
          <div key={stage.id} className="space-y-1">
            <div className="flex justify-between items-center text-slate-600 dark:text-slate-400">
              <span className="truncate max-w-[170px] font-sans font-medium text-slate-700 dark:text-slate-300">
                0{stage.index + 1}. {language === 'vi' ? stage.labelVi : stage.labelEn}
              </span>
              <span className="text-slate-500 tabular-nums">
                {seconds === null ? unavailable : `${seconds.toFixed(1)}s`} {percent === null ? `(${unavailable})` : `(${percent}%)`}
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
  );
};
