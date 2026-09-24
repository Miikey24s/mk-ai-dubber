import React, { useState, useEffect } from 'react';
import { Segment } from '@/types';
import { useTranslation } from '@/context/I18nContext';
import { formatSeconds } from '@/lib/utils';
import { Badge } from '@/components/common/Badge';
import {
  Play,
  Check,
  RotateCcw,
  RefreshCw,
  Save,
  AlertTriangle,
  User,
  Volume2,
} from 'lucide-react';

interface SegmentCardProps {
  segment: Segment;
  isActive: boolean;
  onSelect: () => void;
  onSave: (id: number, text: string, speaker: string) => Promise<void>;
  onAccept: (id: number) => Promise<void>;
  onRerender: (id: number) => Promise<void>;
}

export const SegmentCard: React.FC<SegmentCardProps> = ({
  segment,
  isActive,
  onSelect,
  onSave,
  onAccept,
  onRerender,
}) => {
  const { t } = useTranslation();
  const sourceText = segment.text || segment.source_en || segment.en || '';
  const initialVi = segment.vi || segment.selected_vi || segment.translated_vi || '';
  const [editedVi, setEditedVi] = useState(initialVi);
  const [selectedSpeaker, setSelectedSpeaker] = useState(segment.speaker || 'SPEAKER_00');
  const [isSaving, setIsSaving] = useState(false);
  const [isRerendering, setIsRerendering] = useState(false);
  const [isDirty, setIsDirty] = useState(false);

  useEffect(() => {
    setEditedVi(segment.vi || segment.selected_vi || segment.translated_vi || '');
    setSelectedSpeaker(segment.speaker || 'SPEAKER_00');
    setIsDirty(false);
  }, [segment]);

  const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setEditedVi(e.target.value);
    setIsDirty(true);
  };

  const handleSpeakerChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedSpeaker(e.target.value);
    setIsDirty(true);
  };

  const handleSave = async () => {
    setIsSaving(true);
    await onSave(segment.id, editedVi, selectedSpeaker);
    setIsSaving(false);
    setIsDirty(false);
  };

  const handleAccept = async () => {
    await onAccept(segment.id);
  };

  const handleRerender = async () => {
    setIsRerendering(true);
    await onRerender(segment.id);
    setTimeout(() => setIsRerendering(false), 800);
  };

  const handleRevert = () => {
    setEditedVi(segment.vi || segment.selected_vi || segment.translated_vi || '');
    setSelectedSpeaker(segment.speaker || 'SPEAKER_00');
    setIsDirty(false);
  };

  // Character expansion calculation
  const enLen = sourceText.length;
  const viLen = (editedVi || '').length;
  const expansionPct = enLen > 0 ? Math.round(((viLen - enLen) / enLen) * 100) : 0;

  return (
    <div
      onClick={onSelect}
      className={`rounded-lg border p-4 transition-all duration-200 cursor-pointer font-mono ${
        isActive
          ? 'bg-orange-500/5 dark:bg-slate-900 border-orange-500 shadow-md ring-1 ring-orange-500/30'
          : 'bg-white dark:bg-slate-900/60 border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
      }`}
    >
      {/* Top Header: ID, Timecode, Speaker, Status Chips */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 mb-3 border-b border-slate-100 dark:border-slate-800/80">
        <div className="flex items-center gap-2">
          {/* Segment ID */}
          <span className="text-xs font-bold px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300">
            #{segment.id.toString().padStart(3, '0')}
          </span>

          {/* Time Range & Play CTA */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
            className="flex items-center gap-1 text-2xs text-slate-500 hover:text-orange-500 transition"
          >
            <Play className="w-3 h-3 text-orange-500" />
            <span className="tabular-nums">
              {formatSeconds(segment.start)} - {formatSeconds(segment.end)} ({(segment.end - segment.start).toFixed(2)}s)
            </span>
          </button>
        </div>

        {/* Right Header: Badges & Speaker */}
        <div className="flex flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
          {/* Speaker Selector */}
          <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded text-2xs">
            <User className="w-3 h-3 text-slate-400" />
            <select
              value={selectedSpeaker}
              onChange={handleSpeakerChange}
              className="bg-transparent text-slate-700 dark:text-slate-300 font-semibold focus:outline-none cursor-pointer"
            >
              <option value="SPEAKER_00">SPEAKER_00</option>
              <option value="SPEAKER_01">SPEAKER_01</option>
              <option value="SPEAKER_02">SPEAKER_02</option>
            </select>
          </div>

          {/* Status Badges */}
          {segment.review_status === 'accepted' && (
            <Badge variant="success">{t('review.status.accepted')}</Badge>
          )}
          {segment.review_status === 'needs_review' && (
            <Badge variant="warning">{t('review.status.needs_review')}</Badge>
          )}
          {segment.review_status === 'reviewed' && (
            <Badge variant="info">{t('review.status.reviewed')}</Badge>
          )}
          {segment.review_status === 'unreviewed' && (
            <Badge variant="outline">{t('review.status.unreviewed')}</Badge>
          )}

          {/* Acoustic & QA Badges */}
          {segment.overflow && (
            <Badge variant="error" title={t('review.overflow_warning')}>
              <AlertTriangle className="w-2.5 h-2.5 mr-0.5" />
              OVERFLOW
            </Badge>
          )}
          {segment.rewritten && (
            <Badge variant="purple" title={t('review.rewritten_badge')}>
              PREFIT
            </Badge>
          )}
          {segment.semantic_flag && (
            <Badge variant="warning" title={t('review.semantic_flag_badge')}>
              QA ALERT
            </Badge>
          )}
          {segment.speaker_overlap && (
            <Badge variant="default" title={t('review.overlap_badge')}>
              OVERLAP
            </Badge>
          )}
        </div>
      </div>

      {/* Body: Bilingual Side-by-Side View */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-sans">
        {/* Left: English Source (ASR) */}
        <div className="space-y-1">
          <div className="text-2xs font-mono text-slate-400 uppercase tracking-wider flex items-center gap-1">
            <Volume2 className="w-3 h-3 text-slate-400" />
            <span>{t('review.col_source')}</span>
          </div>
          <div className="p-3 bg-slate-50 dark:bg-slate-950/70 border border-slate-200 dark:border-slate-800 rounded-md text-slate-800 dark:text-slate-300 leading-relaxed text-sm select-text">
            {sourceText}
          </div>
        </div>

        {/* Right: Vietnamese Dub Editing (TTS) */}
        <div className="space-y-1" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between text-2xs font-mono text-slate-400 uppercase tracking-wider">
            <span className="text-orange-500 font-bold flex items-center gap-1">
              <span>{t('review.col_target')}</span>
            </span>
            <span
              className={`tabular-nums ${
                expansionPct > 25
                  ? 'text-amber-500 font-bold'
                  : expansionPct < -25
                  ? 'text-sky-500'
                  : 'text-slate-500'
              }`}
            >
              {viLen} chars ({expansionPct >= 0 ? `+${expansionPct}%` : `${expansionPct}%`})
            </span>
          </div>
          <textarea
            value={editedVi}
            onChange={handleTextChange}
            rows={2}
            className="w-full p-2.5 bg-white dark:bg-slate-950 border border-slate-300 dark:border-slate-700 rounded-md text-slate-900 dark:text-amber-200 font-medium text-sm leading-relaxed focus:outline-none focus:border-orange-500 focus:ring-1 focus:ring-orange-500 transition resize-none"
          />
        </div>
      </div>

      {/* Card Footer: Quick Actions */}
      <div
        className="flex flex-wrap items-center justify-between gap-2 mt-3 pt-3 border-t border-slate-100 dark:border-slate-800/80 text-xs"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-2xs font-mono text-slate-400">
          Target Duration: {segment.target_duration ? `${segment.target_duration.toFixed(2)}s` : '4.28s'}
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          {/* Revert if dirty */}
          {isDirty && (
            <button
              onClick={handleRevert}
              className="flex items-center gap-1 px-2.5 py-1 rounded bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 text-2xs transition"
            >
              <RotateCcw className="w-3 h-3" />
              <span>{t('review.revert')}</span>
            </button>
          )}

          {/* Save Button */}
          <button
            onClick={handleSave}
            disabled={!isDirty || isSaving}
            className={`flex items-center gap-1 px-2.5 py-1 rounded text-2xs font-semibold transition ${
              isDirty
                ? 'bg-orange-600 hover:bg-orange-500 text-white shadow-sm'
                : 'bg-slate-100 dark:bg-slate-800 text-slate-400 cursor-not-allowed opacity-50'
            }`}
          >
            <Save className="w-3 h-3" />
            <span>{isSaving ? 'Saving...' : t('review.save_changes')}</span>
          </button>

          {/* Re-render TTS */}
          <button
            onClick={handleRerender}
            disabled={isRerendering}
            className="flex items-center gap-1 px-2.5 py-1 rounded bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 text-2xs border border-slate-200 dark:border-slate-700 transition"
            title="Re-synthesize audio for this segment"
          >
            <RefreshCw className={`w-3 h-3 text-sky-500 ${isRerendering ? 'animate-spin' : ''}`} />
            <span>{t('review.rerender')}</span>
          </button>

          {/* Accept Segment */}
          <button
            onClick={handleAccept}
            className={`flex items-center gap-1 px-3 py-1 rounded text-2xs font-bold transition ${
              segment.review_status === 'accepted'
                ? 'bg-emerald-600/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/40'
                : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm'
            }`}
          >
            <Check className="w-3.5 h-3.5 stroke-[2.5]" />
            <span>{t('review.accept')}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
