import React, { useEffect, useRef } from 'react';
import { StageDefinition, JobState } from '@/types';
import { useTranslation } from '@/context/I18nContext';
import { X, FileCode2 } from 'lucide-react';
import { Badge } from '@/components/common/Badge';
import { nonNegativeFinite } from '@/lib/telemetry';

interface StageDetailModalProps {
  stage: StageDefinition | null;
  job: JobState | null;
  onClose: () => void;
}

export const StageDetailModal: React.FC<StageDetailModalProps> = ({ stage, job, onClose }) => {
  const { language, t } = useTranslation();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!stage || !job) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    dialogRef.current?.focus();
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [stage, job, onClose]);

  if (!stage || !job) return null;

  // Find telemetry for this stage
  const stageTelemetry = job.metrics?.stages;
  let matchingTelemetry: { wall_seconds: number; calls: number; failed_calls: number } | null = null;

  if (stageTelemetry) {
    for (const key of Object.keys(stageTelemetry)) {
      if (stage.internalStageNames.some(name => key.includes(name))) {
        matchingTelemetry = stageTelemetry[key];
        break;
      }
    }
  }

  const unavailable = t('common.unavailable');
  const duration = matchingTelemetry && nonNegativeFinite(matchingTelemetry.wall_seconds)
    ? `${matchingTelemetry.wall_seconds.toFixed(2)}s`
    : unavailable;
  const calls = matchingTelemetry && nonNegativeFinite(matchingTelemetry.calls)
    ? Math.round(matchingTelemetry.calls)
    : unavailable;
  const failures = matchingTelemetry && nonNegativeFinite(matchingTelemetry.failed_calls)
    ? Math.round(matchingTelemetry.failed_calls)
    : unavailable;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="stage-detail-title"
        tabIndex={-1}
        className="w-full max-w-xl bg-slate-900 border border-slate-800 rounded-lg shadow-2xl overflow-hidden text-slate-100 font-mono focus:outline-none"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 bg-slate-950 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <span className="w-6 h-6 rounded bg-orange-500/20 text-orange-400 border border-orange-500/40 flex items-center justify-center text-xs font-bold">
              0{stage.index + 1}
            </span>
            <h3 id="stage-detail-title" className="text-sm font-semibold text-slate-200">
              {language === 'vi' ? stage.labelVi : stage.labelEn}
            </h3>
          </div>
          <button
            onClick={onClose}
            aria-label={language === 'vi' ? 'Đóng chi tiết bước' : 'Close stage details'}
            className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="p-5 space-y-4 text-xs">
          <p className="text-slate-300 font-sans text-sm leading-relaxed">
            {language === 'vi' ? stage.descriptionVi : stage.descriptionEn}
          </p>

          {/* Telemetry Metrics Box */}
          <div className="bg-slate-950 p-4 rounded-md border border-slate-800 space-y-2.5">
            <div className="text-2xs text-slate-500 uppercase tracking-wider">
              STAGE_TELEMETRY_METRICS
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <span className="text-slate-500 text-2xs block">Duration</span>
                <span className="text-sky-400 font-bold text-sm">
                  {duration}
                </span>
              </div>
              <div>
                <span className="text-slate-500 text-2xs block">Calls</span>
                <span className="text-slate-200 font-bold text-sm">
                  {calls}
                </span>
              </div>
              <div>
                <span className="text-slate-500 text-2xs block">Failures / Retries</span>
                <span className="text-emerald-400 font-bold text-sm">
                  {failures}
                </span>
              </div>
            </div>
          </div>

          {/* Subroutines / Internal Stages */}
          <div>
            <span className="text-2xs text-slate-500 uppercase tracking-wider block mb-1.5">
              Subroutines & Manifests
            </span>
            <div className="flex flex-wrap gap-1.5">
              {stage.internalStageNames.map(name => (
                <Badge key={name} variant="outline" className="bg-slate-800/60 text-slate-300">
                  <FileCode2 className="w-3 h-3 text-orange-400 mr-1" />
                  {name}.json
                </Badge>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-5 py-3 bg-slate-950/80 border-t border-slate-800 flex justify-end">
          <button
            onClick={onClose}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded transition"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
