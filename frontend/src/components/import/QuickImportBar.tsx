import React, { useState, useRef } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import {
  Youtube,
  Upload,
  Sparkles,
  Sliders,
  FileVideo,
  AlertCircle,
  Loader2,
} from 'lucide-react';

export const QuickImportBar: React.FC = () => {
  const { createNewJob, setIsCreatorOpen } = useJob();
  const { t } = useTranslation();

  const [activeTab, setActiveTab] = useState<'youtube' | 'file'>('youtube');
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleStartDub = async () => {
    setErrorMessage(null);
    if (activeTab === 'youtube') {
      if (!youtubeUrl.trim()) {
        setErrorMessage('Vui lòng nhập link YouTube hợp lệ.');
        return;
      }
      setIsSubmitting(true);
      try {
        const jobId = await createNewJob({
          source: 'youtube',
          youtubeUrl: youtubeUrl.trim(),
          profile: 'balanced_best',
        });
        if (jobId) {
          setYoutubeUrl('');
        } else {
          setErrorMessage('Không thể khởi chạy tác vụ. Vui lòng thử lại.');
        }
      } catch (err: any) {
        setErrorMessage(err.message || 'Lỗi kết nối.');
      } finally {
        setIsSubmitting(false);
      }
    } else {
      if (!selectedFile) {
        fileInputRef.current?.click();
        return;
      }
      setIsSubmitting(true);
      try {
        const jobId = await createNewJob({
          source: 'file',
          file: selectedFile,
          profile: 'balanced_best',
        });
        if (jobId) {
          setSelectedFile(null);
        } else {
          setErrorMessage('Lỗi tải video lên. Vui lòng thử lại.');
        }
      } catch (err: any) {
        setErrorMessage(err.message || 'Lỗi tải video.');
      } finally {
        setIsSubmitting(false);
      }
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0]);
      setErrorMessage(null);
    }
  };

  return (
    <div className="bg-white dark:bg-slate-900/90 border border-slate-200 dark:border-slate-800 rounded-xl p-2.5 shadow-sm font-mono shrink-0 select-none transition-all">
      {/* Upper row: Header label + Mode pills + Deep Settings Trigger */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-orange-500 animate-pulse" />
          <span className="text-xs font-bold text-slate-800 dark:text-slate-200 tracking-wider">
            {t('import.quick_bar_label')}
          </span>
          <span className="text-[10px] text-slate-400 hidden sm:inline">
            // YouTube / Video File
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Tabs: YouTube / Local File */}
          <div className="flex items-center p-0.5 bg-slate-100 dark:bg-slate-950 rounded-lg border border-slate-200 dark:border-slate-800">
            <button
              type="button"
              onClick={() => {
                setActiveTab('youtube');
                setErrorMessage(null);
              }}
              className={`flex items-center gap-1 px-2 py-0.5 rounded text-xs transition cursor-pointer ${
                activeTab === 'youtube'
                  ? 'bg-white dark:bg-slate-800 text-orange-600 dark:text-orange-400 font-bold shadow-xs'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
            >
              <Youtube className="w-3.5 h-3.5 text-red-500" />
              <span>YouTube</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setActiveTab('file');
                setErrorMessage(null);
              }}
              className={`flex items-center gap-1 px-2 py-0.5 rounded text-xs transition cursor-pointer ${
                activeTab === 'file'
                  ? 'bg-white dark:bg-slate-800 text-orange-600 dark:text-orange-400 font-bold shadow-xs'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
            >
              <Upload className="w-3.5 h-3.5 text-sky-500" />
              <span>{t('import.file_tab')}</span>
            </button>
          </div>

          {/* Deep Settings Modal Trigger */}
          <button
            type="button"
            onClick={() => setIsCreatorOpen(true)}
            className="flex items-center gap-1 px-2 py-1 rounded bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 text-xs border border-slate-300 dark:border-slate-700 transition cursor-pointer"
            title="Mở toàn bộ cấu hình âm thanh, mô hình & voice clone"
          >
            <Sliders className="w-3 h-3 text-orange-500" />
            <span className="hidden sm:inline">Cài đặt sâu</span>
          </button>
        </div>
      </div>

      {/* Lower row: Interactive Input Field & Launch Action */}
      <div className="flex items-center gap-2">
        {activeTab === 'youtube' ? (
          <div className="flex-1 relative">
            <input
              type="url"
              value={youtubeUrl}
              onChange={(e) => {
                setYoutubeUrl(e.target.value);
                setErrorMessage(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleStartDub();
              }}
              placeholder={t('import.youtube_placeholder')}
              className="w-full pl-8 pr-3 py-1.5 bg-slate-50 dark:bg-slate-950 border border-slate-300 dark:border-slate-700/80 rounded-lg text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:outline-none focus:border-orange-500 transition"
              disabled={isSubmitting}
            />
            <Youtube className="w-4 h-4 text-red-500 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          </div>
        ) : (
          <div className="flex-1 min-w-0 flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*,audio/*"
              onChange={handleFileChange}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex-1 min-w-0 flex items-center justify-between px-2.5 py-1.5 bg-slate-50 dark:bg-slate-950 hover:bg-slate-100 dark:hover:bg-slate-800/80 border border-dashed border-slate-300 dark:border-slate-700 rounded-lg text-xs transition cursor-pointer text-left overflow-hidden"
            >
              <div className="flex items-center gap-1.5 truncate mr-1 min-w-0">
                <FileVideo className="w-3.5 h-3.5 text-sky-500 shrink-0" />
                <span className="truncate text-slate-700 dark:text-slate-300 text-[11px]">
                  {selectedFile ? selectedFile.name : t('import.drop_hint')}
                </span>
              </div>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-300 shrink-0 font-semibold whitespace-nowrap">
                {selectedFile ? `${(selectedFile.size / (1024 * 1024)).toFixed(1)} MB` : t('import.browse_files')}
              </span>
            </button>
          </div>
        )}

        {/* Action Button: Start Dub */}
        <button
          type="button"
          onClick={handleStartDub}
          disabled={isSubmitting}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-gradient-to-r from-orange-600 to-amber-600 hover:from-orange-500 hover:to-amber-500 text-white rounded-lg text-xs font-bold shadow-md shadow-orange-600/30 transition disabled:opacity-50 shrink-0 whitespace-nowrap cursor-pointer"
        >
          {isSubmitting ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <span>{t('import.processing')}</span>
            </>
          ) : (
            <>
              <Sparkles className="w-3.5 h-3.5" />
              <span>{t('import.quick_dub')}</span>
            </>
          )}
        </button>
      </div>

      {/* Error or Hint banner */}
      {errorMessage && (
        <div className="mt-1.5 flex items-center gap-1.5 text-xs text-rose-500 dark:text-rose-400">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}
    </div>
  );
};
