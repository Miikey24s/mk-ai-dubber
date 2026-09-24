import React, { useState, useEffect, useRef } from 'react';
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
      className={`rounded-xl border-2 p-3.5 transition-all duration-200 cursor-pointer ${
        isActive
          ? 'bg-orange-500/5 dark:bg-slate-900 border-orange-500 shadow-md ring-2 ring-orange-500/20'
          : 'bg-white dark:bg-slate-900/60 border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
      }`}
    >
      {/* Top Header: ID, Timecode, Speaker, Status Badges */}
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 mb-2.5 border-b border-slate-100 dark:border-slate-800/80">
        <div className="flex items-center gap-2">
          {/* Segment ID */}
          <span className="text-xs font-bold font-mono px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200 border border-slate-200 dark:border-slate-700">
            #{segment.id.toString().padStart(3, '0')}
          </span>

          {/* Time Range & Play CTA */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
            className="flex items-center gap-1.5 text-xs font-mono text-slate-600 dark:text-slate-400 hover:text-orange-500 transition font-medium"
          >
            <Play className="w-3.5 h-3.5 text-orange-500" />
            <span className="tabular-nums">
              {formatSeconds(segment.start)} - {formatSeconds(segment.end)} ({(segment.end - segment.start).toFixed(2)}s)
            </span>
          </button>
        </div>

        {/* Right Header: Badges & Speaker */}
        <div className="flex flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
          {/* Speaker Selector */}
          <div className="flex items-center gap-1.5 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 px-2 py-0.5 rounded-md text-xs font-mono">
            <User className="w-3.5 h-3.5 text-slate-500" />
            <select
              value={selectedSpeaker}
              onChange={handleSpeakerChange}
              className="bg-transparent text-slate-800 dark:text-slate-200 font-semibold focus:outline-none cursor-pointer"
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
              <AlertTriangle className="w-3 h-3 mr-0.5" />
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
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-sans">
        {/* Left: English Source (ASR) */}
        <div className="space-y-1.5">
          <div className="text-xs font-mono text-slate-600 dark:text-slate-400 uppercase tracking-wider flex items-center gap-1 font-semibold">
            <Volume2 className="w-3.5 h-3.5 text-slate-500" />
            <span>{t('review.col_source')}</span>
          </div>
          <div className="text-sm text-slate-800 dark:text-slate-200 font-medium leading-relaxed p-2.5 bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-lg select-text min-h-[76px]">
            {sourceText}
          </div>
        </div>

        {/* Right: Vietnamese Dub Editing (TTS) */}
        <div className="space-y-1.5" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between text-xs font-mono uppercase tracking-wider">
            <span className="text-orange-600 dark:text-orange-400 font-bold flex items-center gap-1">
              <span>{t('review.col_target')}</span>
            </span>
            {/* Character expansion badge: Prominent and readable */}
            <span
              className={`text-xs font-semibold px-2 py-0.5 rounded-full border tabular-nums ${
                expansionPct > 25
                  ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30'
                  : expansionPct < -25
                  ? 'bg-sky-500/10 text-sky-700 dark:text-sky-400 border-sky-500/30'
                  : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30'
              }`}
            >
              {viLen} chars ({expansionPct >= 0 ? `+${expansionPct}%` : `${expansionPct}%`})
            </span>
          </div>
          <textarea
            ref={textareaRef}
            value={editedVi}
            onChange={handleTextChange}
            onKeyDown={handleKeyDown}
            spellCheck={false}
            rows={2}
            className="w-full text-sm text-slate-900 dark:text-slate-100 font-medium leading-relaxed p-2.5 bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-lg focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 focus:outline-none transition resize-none min-h-[76px]"
            placeholder="Nhập nội dung lồng tiếng..."
          />
        </div>
      </div>

      {/* Card Footer: Quick Actions */}
      <div
        className="flex flex-wrap items-center justify-between gap-2.5 mt-2.5 pt-2.5 border-t border-slate-100 dark:border-slate-800/80 text-xs"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-xs font-mono text-slate-600 dark:text-slate-400 font-medium">
          Target Duration: {segment.target_duration ? `${segment.target_duration.toFixed(2)}s` : '4.28s'}
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          {/* Revert if dirty */}
          {isDirty && (
            <button
              onClick={handleRevert}
              className="flex items-center gap-1.5 h-8 px-3 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 text-xs font-semibold border border-slate-300 dark:border-slate-700 transition"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>{t('review.revert')}</span>
            </button>
          )}

          {/* Lưu chỉnh sửa (high contrast) */}
          <button
            onClick={handleSave}
            disabled={!isDirty || isSaving}
            className={`flex items-center gap-1.5 h-8 px-3.5 rounded-lg text-xs font-semibold transition ${
              isDirty
                ? 'bg-orange-600 hover:bg-orange-500 text-white shadow-md shadow-orange-600/30 cursor-pointer'
                : 'bg-slate-100 dark:bg-slate-800/80 text-slate-400 dark:text-slate-500 border border-slate-200 dark:border-slate-800 cursor-not-allowed opacity-60'
            }`}
          >
            <Save className="w-3.5 h-3.5" />
            <span>{isSaving ? 'Đang lưu...' : 'Lưu chỉnh sửa'}</span>
          </button>

          {/* Render lại TTS */}
          <button
            onClick={handleRerender}
            disabled={isRerendering}
            className="flex items-center gap-1.5 h-8 px-3 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-800 dark:text-slate-200 text-xs font-semibold border border-slate-300 dark:border-slate-700 transition"
            title="Re-synthesize audio for this segment"
          >
            <RefreshCw className={`w-3.5 h-3.5 text-sky-500 ${isRerendering ? 'animate-spin' : ''}`} />
            <span>Render lại TTS</span>
          </button>

          {/* Duyệt đoạn này (Ctrl+Enter) in prominent emerald green */}
          <button
            onClick={handleAccept}
            className={`flex items-center gap-1.5 h-8 px-3.5 rounded-lg text-xs font-bold transition shadow-sm ${
              segment.review_status === 'accepted'
                ? 'bg-emerald-600/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/50'
                : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/30 hover:shadow-md'
            }`}
          >
            <Check className="w-4 h-4 stroke-[2.5]" />
            <span>Duyệt đoạn này (Ctrl+Enter)</span>
          </button>
        </div>
      </div>
    </div>
  );
};
