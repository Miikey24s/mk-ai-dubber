import React, { useState, useEffect } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { ProfileType } from '@/types';
import { DeepSettingsAccordion } from './DeepSettingsAccordion';
import {
  X,
  Youtube,
  Upload,
  Sparkles,
  Mic,
  ShieldCheck,
  Zap,
  CheckCircle2,
  AlertCircle,
  Loader2,
} from 'lucide-react';

export const JobCreatorModal: React.FC = () => {
  const { isCreatorOpen, setIsCreatorOpen, droppedFile, setDroppedFile, createNewJob } = useJob();
  const { t } = useTranslation();

  const [sourceMode, setSourceMode] = useState<'youtube' | 'file'>('youtube');
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [model, setModel] = useState('chatgpt-web/gpt-5.6-sol');
  const [effort, setEffort] = useState<'low' | 'medium' | 'high' | 'extra_high'>('high');
  const [profile, setProfile] = useState<ProfileType>('balanced_best');
  const [voiceCloneEnabled, setVoiceCloneEnabled] = useState(false);
  const [voicePreset, setVoicePreset] = useState('natural_male');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [deepSettings, setDeepSettings] = useState({
    diarization: true,
    targetLufs: -14.0,
    truePeak: -1.5,
    maxSpeedup: 1.25,
    maxSlowdown: 0.85,
    rewriteThreshold: 1.22,
    retryBudget: 3,
    semanticThreshold: 0.72,
  });

  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (droppedFile) {
      setSourceMode('file');
      setLocalFile(droppedFile);
    }
  }, [droppedFile]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isCreatorOpen) {
        setIsCreatorOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isCreatorOpen, setIsCreatorOpen]);

  if (!isCreatorOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    if (sourceMode === 'youtube' && !youtubeUrl.trim()) {
      setErrorMessage('Vui lòng nhập đường dẫn video YouTube hợp lệ.');
      return;
    }
    if (sourceMode === 'file' && !localFile) {
      setErrorMessage('Vui lòng chọn hoặc kéo thả tệp video từ máy.');
      return;
    }

    setIsSubmitting(true);
    try {
      const jobId = await createNewJob({
        source: sourceMode,
        youtubeUrl: youtubeUrl.trim(),
        file: localFile || undefined,
        profile,
        model,
        effort,
        deepSettings,
      });

      if (jobId) {
        setIsCreatorOpen(false);
        setDroppedFile(null);
      } else {
        setErrorMessage('Không thể khởi chạy tác vụ. Vui lòng kiểm tra lại cấu hình.');
      }
    } catch (err: any) {
      setErrorMessage(err.message || 'Lỗi khi khởi chạy tác vụ.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="w-full max-w-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg shadow-2xl overflow-hidden text-slate-900 dark:text-slate-100 font-mono my-8">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 bg-slate-50 dark:bg-slate-950 border-b border-slate-200 dark:border-slate-800">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-md bg-orange-500/20 text-orange-500 border border-orange-500/40 flex items-center justify-center">
              <Sparkles className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-800 dark:text-slate-100 uppercase tracking-wide">
                {t('creator.new_job')}
              </h2>
              <span className="text-2xs text-slate-500">
                End-to-End Demucs + WhisperX + GPT-5.6 Sol + Kokoro Pipeline
              </span>
            </div>
          </div>
          <button
            onClick={() => setIsCreatorOpen(false)}
            className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4 max-h-[75vh] overflow-y-auto text-xs">
          {/* Source Tabs */}
          <div className="space-y-2">
            <label className="text-2xs uppercase tracking-wider text-slate-500 font-bold block">
              {t('creator.source_type')}
            </label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setSourceMode('youtube')}
                className={`flex items-center justify-center gap-2 py-2 px-3 rounded border transition font-semibold ${
                  sourceMode === 'youtube'
                    ? 'bg-orange-500/10 border-orange-500 text-orange-600 dark:text-orange-400'
                    : 'bg-slate-50 dark:bg-slate-950 border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400'
                }`}
              >
                <Youtube className="w-4 h-4 text-red-500" />
                <span>YouTube Link</span>
              </button>
              <button
                type="button"
                onClick={() => setSourceMode('file')}
                className={`flex items-center justify-center gap-2 py-2 px-3 rounded border transition font-semibold ${
                  sourceMode === 'file'
                    ? 'bg-orange-500/10 border-orange-500 text-orange-600 dark:text-orange-400'
                    : 'bg-slate-50 dark:bg-slate-950 border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400'
                }`}
              >
                <Upload className="w-4 h-4 text-sky-500" />
                <span>Local File Upload</span>
              </button>
            </div>
          </div>

          {/* YouTube input or File upload dropzone */}
          {sourceMode === 'youtube' ? (
            <div className="space-y-1">
              <input
                type="url"
                value={youtubeUrl}
                onChange={(e) => setYoutubeUrl(e.target.value)}
                placeholder={t('creator.youtube_placeholder')}
                className="w-full p-2.5 bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-md focus:outline-none focus:border-orange-500 text-slate-900 dark:text-slate-100"
              />
              <span className="text-3xs text-slate-500">
                Supports standard video URLs, playlists, and shorts. Audio will be demuxed automatically.
              </span>
            </div>
          ) : (
            <div className="border-2 border-dashed border-slate-300 dark:border-slate-800 hover:border-orange-500/60 rounded-lg p-6 text-center bg-slate-50/50 dark:bg-slate-950/50 cursor-pointer transition">
              <Upload className="w-8 h-8 text-slate-400 mx-auto mb-2" />
              <div className="font-semibold text-slate-700 dark:text-slate-300">
                {localFile ? localFile.name : t('creator.drag_drop')}
              </div>
              <span className="text-2xs text-slate-500 mt-1 block">
                MP4, MKV, MOV, WAV, FLAC (Max 2.0 GB)
              </span>
              <input
                type="file"
                accept="video/*,audio/*"
                onChange={(e) => setLocalFile(e.target.files?.[0] || null)}
                className="hidden"
                id="file-upload"
              />
              <label
                htmlFor="file-upload"
                className="inline-block mt-3 px-3 py-1 bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 rounded text-2xs cursor-pointer transition"
              >
                Select File
              </label>
            </div>
          )}

          {/* Model & Effort */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <div className="space-y-1">
              <label className="text-2xs uppercase tracking-wider text-slate-500 font-bold block">
                {t('creator.translation_model')}
              </label>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="w-full p-2 bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-md text-slate-800 dark:text-slate-200 focus:outline-none focus:border-orange-500 cursor-pointer"
              >
                <option value="chatgpt-web/gpt-5.6-sol">GPT-5.6 Sol (Codex Web instance 2 :17842)</option>
                <option value="anthropic/claude-3-7-sonnet">Claude 3.7 Sonnet (Anthropic)</option>
                <option value="google/gemini-2.5-flash">Gemini 2.5 Flash (Google)</option>
                <option value="local/qwen-2.5-32b">Qwen 2.5 32B Local (RTX Offload)</option>
              </select>
            </div>

            <div className="space-y-1">
              <label className="text-2xs uppercase tracking-wider text-slate-500 font-bold block">
                {t('creator.effort')}
              </label>
              <select
                value={effort}
                onChange={(e) => setEffort(e.target.value as any)}
                className="w-full p-2 bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-md text-slate-800 dark:text-slate-200 focus:outline-none focus:border-orange-500 cursor-pointer"
              >
                <option value="low">Low (Fast Translation)</option>
                <option value="medium">Medium (Standard)</option>
                <option value="high">High (Recommended - Financial Glossary Fit)</option>
                <option value="extra_high">Extra High (Deep Reasoning & Syllable Matching)</option>
              </select>
            </div>
          </div>

          {/* Quality Profiles (3 cards) */}
          <div className="space-y-2 pt-1">
            <label className="text-2xs uppercase tracking-wider text-slate-500 font-bold block">
              {t('creator.quality_profile')}
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {/* Fast */}
              <div
                onClick={() => setProfile('fast')}
                className={`p-3 rounded-md border cursor-pointer transition ${
                  profile === 'fast'
                    ? 'bg-orange-500/10 border-orange-500 ring-1 ring-orange-500/50'
                    : 'bg-slate-50 dark:bg-slate-950 border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
                }`}
              >
                <div className="flex items-center gap-1.5 font-bold text-slate-800 dark:text-slate-200 mb-1">
                  <Zap className="w-3.5 h-3.5 text-amber-500" />
                  <span>{t('profile.fast')}</span>
                </div>
                <p className="text-3xs text-slate-500 leading-normal">
                  {t('profile.fast_desc')}
                </p>
              </div>

              {/* Balanced Best */}
              <div
                onClick={() => setProfile('balanced_best')}
                className={`p-3 rounded-md border cursor-pointer transition ${
                  profile === 'balanced_best'
                    ? 'bg-orange-500/10 border-orange-500 ring-1 ring-orange-500/50'
                    : 'bg-slate-50 dark:bg-slate-950 border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
                }`}
              >
                <div className="flex items-center gap-1.5 font-bold text-slate-800 dark:text-slate-200 mb-1">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                  <span>{t('profile.balanced_best')}</span>
                </div>
                <p className="text-3xs text-slate-500 leading-normal">
                  {t('profile.balanced_best_desc')}
                </p>
              </div>

              {/* Max Quality */}
              <div
                onClick={() => setProfile('max_quality')}
                className={`p-3 rounded-md border cursor-pointer transition ${
                  profile === 'max_quality'
                    ? 'bg-orange-500/10 border-orange-500 ring-1 ring-orange-500/50'
                    : 'bg-slate-50 dark:bg-slate-950 border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
                }`}
              >
                <div className="flex items-center gap-1.5 font-bold text-slate-800 dark:text-slate-200 mb-1">
                  <ShieldCheck className="w-3.5 h-3.5 text-sky-500" />
                  <span>{t('profile.max_quality')}</span>
                </div>
                <p className="text-3xs text-slate-500 leading-normal">
                  {t('profile.max_quality_desc')}
                </p>
              </div>
            </div>
          </div>

          {/* Voice Clone Option */}
          <div className="p-3 bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-md space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Mic className="w-4 h-4 text-purple-500" />
                <div>
                  <span className="font-semibold text-slate-800 dark:text-slate-200 block">
                    {t('creator.voice_clone')}
                  </span>
                  <span className="text-3xs text-slate-500">
                    {t('creator.voice_clone_desc')}
                  </span>
                </div>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={voiceCloneEnabled}
                  onChange={(e) => setVoiceCloneEnabled(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-9 h-5 bg-slate-300 peer-focus:outline-none rounded-full peer dark:bg-slate-800 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-600"></div>
              </label>
            </div>

            {voiceCloneEnabled && (
              <div className="grid grid-cols-2 gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
                <select
                  value={voicePreset}
                  onChange={(e) => setVoicePreset(e.target.value)}
                  className="p-1.5 bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-700 rounded text-2xs focus:outline-none"
                >
                  <option value="natural_male">Timbre: Warm Natural Male (Hà Nội)</option>
                  <option value="studio_female">Timbre: Clear Studio Female (Sài Gòn)</option>
                  <option value="deep_narrator">Timbre: Deep Technical Narrator</option>
                </select>
                <input
                  type="file"
                  accept="audio/*"
                  className="text-2xs file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-2xs file:bg-slate-200 dark:file:bg-slate-800 file:text-slate-700 dark:file:text-slate-300"
                />
              </div>
            )}
          </div>

          {/* Deep Settings Accordion */}
          <DeepSettingsAccordion
            settings={deepSettings}
            onChange={setDeepSettings}
          />

          {/* Error Message */}
          {errorMessage && (
            <div className="flex items-center gap-2 p-2.5 bg-rose-500/10 border border-rose-500/30 rounded-md text-xs text-rose-500 dark:text-rose-400">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{errorMessage}</span>
            </div>
          )}

          {/* Footer Actions */}
          <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-200 dark:border-slate-800">
            <button
              type="button"
              onClick={() => setIsCreatorOpen(false)}
              className="px-4 py-2 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 rounded font-semibold transition"
            >
              {t('creator.cancel')}
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-5 py-2 bg-gradient-to-r from-orange-600 to-amber-600 hover:from-orange-500 hover:to-amber-500 text-white rounded font-bold shadow-md shadow-orange-600/30 transition flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Starting Pipeline...</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" />
                  <span>{t('creator.start_button')}</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
