import React, { useState, useEffect, useRef } from 'react';
import { Segment } from '@/types';
import { useTranslation } from '@/context/I18nContext';
import { formatSeconds } from '@/lib/utils';
import { Badge } from '@/components/common/Badge';
import {
  Check,
  RotateCcw,
  RefreshCw,
  Save,
  AlertTriangle,
} from 'lucide-react';

interface SegmentCardProps {
  segment: Segment;
  isActive: boolean;
  onSelect: () => void;
  onSave: (id: number, text: string, speaker: string) => Promise<void>;
  onAccept: (id: number) => Promise<void>;
  onRerender: (id: number) => Promise<void>;
  onCtrlEnter?: (id: number, text: string, speaker: string) => Promise<void>;
}

export const SegmentCard: React.FC<SegmentCardProps> = ({
  segment,
  isActive,
  onSelect,
  onSave,
  onAccept,
  onRerender,
  onCtrlEnter,
}) => {
  const { t } = useTranslation();
  const sourceText = segment.text || segment.source_en || segment.en || '';
  const initialVi = segment.vi || segment.selected_vi || segment.translated_vi || '';
  const [editedVi, setEditedVi] = useState(initialVi);
  const [selectedSpeaker, setSelectedSpeaker] = useState(segment.speaker || 'SPEAKER_00');
  const [isSaving, setIsSaving] = useState(false);
  const [isRerendering, setIsRerendering] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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
    if (isDirty) {
      setIsSaving(true);
      await onSave(segment.id, editedVi, selectedSpeaker);
      setIsSaving(false);
      setIsDirty(false);
    }
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

  const handleKeyDown = async (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (onCtrlEnter) {
        await onCtrlEnter(segment.id, editedVi, selectedSpeaker);
        setIsDirty(false);
      } else {
        await handleAccept();
      }
    } else if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      await handleSave();
    }
  };

  // Character expansion calculation
  const enLen = sourceText.length;
  const viLen = (editedVi || '').length;
  const expansionPct = enLen > 0 ? Math.round(((viLen - enLen) / enLen) * 100) : 0;

  return (
    <div
      onClick={onSelect}
      className={`rounded-lg p-2.5 transition-all duration-200 cursor-pointer space-y-1.5 ${
        isActive
          ? 'border-l-[3px] border-l-orange-500 border border-slate-200 dark:border-slate-800 bg-orange-50/50 dark:bg-orange-500/5'
          : 'border border-slate-200 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/50'
      }`}
    >
      {/* Top Header */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-mono text-slate-500 dark:text-slate-400">
          #{segment.id.toString().padStart(3, '0')}
        </span>
        <span className="text-[11px] font-mono text-slate-500 dark:text-slate-400">
          ▶ {formatSeconds(segment.start)} - {formatSeconds(segment.end)} ({(segment.end - segment.start).toFixed(2)}s)
        </span>
        <span className="text-[11px] font-mono text-slate-400 dark:text-slate-500">·</span>
        <select
          value={selectedSpeaker}
          onChange={handleSpeakerChange}
          onClick={(e) => e.stopPropagation()}
          className="bg-transparent text-[11px] font-mono font-semibold text-slate-700 dark:text-slate-300 focus:outline-none cursor-pointer"
        >
          <option value="SPEAKER_00" className="bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100">SPEAKER_00</option>
          <option value="SPEAKER_01" className="bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100">SPEAKER_01</option>
          <option value="SPEAKER_02" className="bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100">SPEAKER_02</option>
        </select>

        {/* Status Badges */}
        <div className="flex flex-wrap items-center gap-1.5 ml-auto" onClick={(e) => e.stopPropagation()}>
          {segment.review_status === 'accepted' && (
            <Badge variant="success">✓ {t('review.status.accepted')}</Badge>
          )}
          {segment.review_status === 'needs_review' && (
            <Badge variant="warning">{t('review.status.needs_review')}</Badge>
          )}
          {segment.review_status === 'reviewed' && (
            <Badge variant="info">{t('review.status.reviewed')}</Badge>
          )}
          {segment.overflow && (
            <Badge variant="error" title={t('review.overflow_warning')}>
              <AlertTriangle className="w-3 h-3 mr-0.5" />
              overflow
            </Badge>
          )}
          {segment.rewritten && (
            <Badge variant="purple" title={t('review.rewritten_badge')}>
              prefit
            </Badge>
          )}
          {segment.semantic_flag && (
            <Badge variant="warning" title={t('review.semantic_flag_badge')}>
              qa alert
            </Badge>
          )}
          {segment.speaker_overlap && (
            <Badge variant="default" title={t('review.overlap_badge')}>
              overlap
            </Badge>
          )}
        </div>
      </div>

      {/* Body: Stacked View */}
      <div className="flex flex-col gap-1.5">
        <div className="text-xs text-slate-600 dark:text-slate-300 pl-2 border-l-2 border-slate-300 dark:border-slate-700 select-text">
          {sourceText}
        </div>
        <div className="relative w-full" onClick={(e) => e.stopPropagation()}>
          <textarea
            ref={textareaRef}
            value={editedVi}
            onChange={handleTextChange}
            onKeyDown={handleKeyDown}
            spellCheck={false}
            rows={1}
            className="w-full text-sm text-slate-900 dark:text-slate-100 font-medium leading-relaxed p-2 bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-700 rounded-lg focus:ring-1 focus:ring-orange-500/30 focus:border-orange-500 focus:outline-none transition resize-none min-h-[40px] placeholder:text-slate-400 dark:placeholder:text-slate-500"
            placeholder={t('review.vi_placeholder')}
          />
        </div>
      </div>

      {/* Card Footer */}
      <div
        className="flex flex-wrap items-center justify-between gap-2 text-[11px]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 text-slate-600 dark:text-slate-400 font-mono">
          <span>{t('review.target_duration')}: {segment.target_duration ? `${segment.target_duration.toFixed(2)}s` : '4.28s'}</span>
          <span
            className={`font-semibold ${
              expansionPct > 25
                ? 'text-amber-600 dark:text-amber-400'
                : expansionPct < -25
                ? 'text-sky-600 dark:text-sky-400'
                : 'text-emerald-600 dark:text-emerald-400'
            }`}
          >
            {viLen} {t('review.char_expansion')} ({expansionPct >= 0 ? `+${expansionPct}%` : `${expansionPct}%`})
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          {isDirty && (
            <>
              <button
                onClick={handleRevert}
                className="flex items-center gap-1 h-7 px-2 rounded bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-semibold border border-slate-300 dark:border-slate-700 transition cursor-pointer"
                title={t('review.revert')}
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>{t('review.revert')}</span>
              </button>
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="flex items-center gap-1 h-7 px-2 rounded bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white font-semibold transition shadow-xs cursor-pointer"
                title={t('review.save')}
              >
                <Save className="w-3.5 h-3.5" />
                <span>{isSaving ? t('review.saving') : t('review.save')}</span>
              </button>
            </>
          )}

          <button
            onClick={handleRerender}
            disabled={isRerendering}
            className="flex items-center gap-1 h-7 px-2 rounded bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-50 text-slate-800 dark:text-slate-200 font-semibold border border-slate-300 dark:border-slate-700 transition cursor-pointer"
            title={t('review.rerender_tts')}
          >
            <RefreshCw className={`w-3.5 h-3.5 text-sky-500 ${isRerendering ? 'animate-spin' : ''}`} />
            <span>{t('review.rerender_tts')}</span>
          </button>

          <button
            onClick={handleAccept}
            className={`flex items-center gap-1 h-7 px-2.5 rounded font-bold transition shadow-xs cursor-pointer ${
              segment.review_status === 'accepted'
                ? 'bg-emerald-600/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/50 hover:bg-emerald-600/25'
                : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/30'
            }`}
            title={t('review.accept_segment')}
          >
            <Check className="w-3.5 h-3.5 stroke-[2.5]" />
            <span>{t('review.accept_segment')}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
