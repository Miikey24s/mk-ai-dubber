import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from '@/context/I18nContext';
import { useTheme } from '@/context/ThemeContext';
import { useJob } from '@/context/JobContext';
import { Badge } from '@/components/common/Badge';
import {
  Volume2,
  Moon,
  Sun,
  Code2,
  ChevronDown,
  UploadCloud,
  Download,
  Library,
  Link2,
} from 'lucide-react';
import { CatalogPanel } from '@/components/header/CatalogPanel';
import { ConnectorFlowPanel } from '@/components/common/ConnectorFlowPanel';
import { isFinalJobReady } from '@/lib/utils';

export const Header: React.FC = () => {
  const { language, setLanguage, t } = useTranslation();
  const { theme, toggleTheme } = useTheme();
  const { jobs, activeJobId, setActiveJobId, setIsRawJsonOpen, setIsCreatorOpen } = useJob();
  const [isJobMenuOpen, setIsJobMenuOpen] = useState(false);
  const [isCatalogOpen, setIsCatalogOpen] = useState(false);
  const [isConnectorOpen, setIsConnectorOpen] = useState(false);
  const jobMenuRef = useRef<HTMLDivElement>(null);

  const activeJob = jobs.find(j => j.id === activeJobId) || jobs[0];

  useEffect(() => {
    if (!isJobMenuOpen) return;
    const handleOutsideClick = (event: MouseEvent) => {
      if (jobMenuRef.current && !jobMenuRef.current.contains(event.target as Node)) {
        setIsJobMenuOpen(false);
      }
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsJobMenuOpen(false);
    };
    document.addEventListener('mousedown', handleOutsideClick);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isJobMenuOpen]);

  return (
    <>
    <header className="h-[46px] shrink-0 border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-[#090d16] text-slate-900 dark:text-slate-100 sticky top-0 z-40 select-none transition-colors">
      <div className="w-full px-3 sm:px-4 h-full flex items-center justify-between gap-3">
        {/* Left: Branding & Job Selector */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-gradient-to-br from-orange-500 to-amber-600 flex items-center justify-center shadow-md shadow-orange-500/20 text-white shrink-0">
              <Volume2 className="w-4 h-4" />
            </div>
            <div className="flex items-baseline gap-2">
              <span className="font-bold text-xs tracking-tight text-slate-900 dark:text-slate-100 font-mono">
                VI-DUBBER
              </span>
              <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-orange-500/10 text-orange-600 dark:text-orange-400 border border-orange-500/30 font-mono">
                v2.0-PRO
              </span>
              <span className="text-xs text-slate-600 dark:text-slate-300 hidden xl:inline font-sans">
                {t('app.subtitle')}
              </span>
            </div>
          </div>

          <div className="h-4 w-px bg-slate-200 dark:bg-slate-800 hidden md:block" />

          {/* Job Dropdown */}
          <div
            ref={jobMenuRef}
            className="relative group"
            onMouseEnter={() => setIsJobMenuOpen(true)}
            onMouseLeave={() => setIsJobMenuOpen(false)}
          >
            <button
              type="button"
              onClick={() => setIsJobMenuOpen(open => !open)}
              aria-haspopup="menu"
              aria-expanded={isJobMenuOpen}
              aria-controls="header-job-menu"
              className="flex items-center gap-2 h-8 px-2.5 rounded bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-300 dark:border-slate-700 text-xs font-mono text-slate-800 dark:text-slate-200 transition cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/60"
            >
              <span className="max-w-[150px] truncate">
                {activeJob?.metadata?.input_name || activeJob?.id || t('common.active_job')}
              </span>
              {activeJob?.status === 'running' && (
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
              )}
              {activeJob?.status === 'completed' && (
                <span className="w-2 h-2 rounded-full bg-emerald-400" />
              )}
              {activeJob?.status === 'paused' && (
                <span className="w-2 h-2 rounded-full bg-amber-400" />
              )}
              <ChevronDown className="w-3.5 h-3.5 text-slate-600 dark:text-slate-300" />
            </button>

            {/* Dropdown Menu */}
            <div
              id="header-job-menu"
              role="menu"
              aria-label={t('common.all_jobs')}
              className={`absolute left-0 mt-1 w-72 py-1 bg-white dark:bg-slate-900 rounded-md shadow-xl border border-slate-200 dark:border-slate-700 ${isJobMenuOpen ? 'block' : 'hidden'} z-50`}
            >
              <div className="px-3 py-1.5 text-xs font-mono text-slate-600 dark:text-slate-300 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800">
                {t('common.all_jobs')} ({jobs.length})
              </div>
              <div className="max-h-60 overflow-y-auto">
                {jobs.map(job => (
                  <button
                    key={job.id}
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setActiveJobId(job.id);
                      setIsJobMenuOpen(false);
                    }}
                    className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-slate-100 dark:hover:bg-slate-800 transition cursor-pointer ${
                      job.id === activeJobId ? 'bg-orange-500/10 text-orange-600 dark:text-orange-400 font-semibold' : 'text-slate-700 dark:text-slate-300'
                    }`}
                  >
                    <span className="truncate pr-2">{job.metadata?.input_name || job.id}</span>
                    <Badge
                      variant={
                        job.status === 'completed'
                          ? 'success'
                          : job.status === 'running'
                          ? 'info'
                          : job.status === 'paused'
                          ? 'warning'
                          : 'default'
                      }
                    >
                      {job.status.toUpperCase()}
                    </Badge>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Right: Controls & Toggles */}
        <div className="flex items-center gap-2">
          {/* Local metadata catalog */}
          <button
            type="button"
            onClick={() => setIsCatalogOpen(true)}
            className="ui-button ui-button--neutral flex h-8 items-center gap-1.5 px-2.5 text-xs font-semibold"
            title={t('catalog.open')}
            aria-label={t('catalog.open')}
          >
            <Library className="h-4 w-4" aria-hidden="true" />
            <span className="hidden xl:inline">{t('catalog.open')}</span>
          </button>

          {/* Project-owned connector preflight; external OAuth is intentionally not invoked here. */}
          <button
            type="button"
            onClick={() => setIsConnectorOpen(true)}
            className="ui-button ui-button--neutral flex h-8 items-center gap-1.5 px-2.5 text-xs font-semibold"
            title="Project connectors"
            aria-label="Project connectors"
          >
            <Link2 className="h-4 w-4" aria-hidden="true" />
            <span className="hidden xl:inline">Connect</span>
          </button>

          {/* Import Video & New Job CTA */}
          <button
            onClick={() => setIsCreatorOpen(true)}
            aria-label={t('creator.new_job')}
            className="relative flex items-center gap-1.5 h-8 px-4 bg-gradient-to-r from-orange-600 via-orange-500 to-amber-600 hover:from-orange-500 hover:to-amber-500 text-white rounded-md text-xs sm:text-sm font-bold shadow-lg shadow-orange-600/40 hover:scale-[1.03] transition-all font-mono cursor-pointer border border-orange-400/50"
            title={t('creator.new_job')}
          >
            <UploadCloud className="w-4 h-4" />
            <span className="hidden sm:inline">{t('creator.new_job')}</span>
          </button>

          {/* Final download is available only after the job lifecycle completes. */}
          {isFinalJobReady(activeJob) && (
            <a
              href={`/api/jobs/${activeJob.id}/download`}
              download
              className="relative flex items-center gap-1.5 h-8 px-3.5 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white rounded-md text-xs sm:text-sm font-bold shadow-lg shadow-emerald-600/40 hover:scale-[1.03] transition-all font-mono cursor-pointer border border-emerald-400/50"
              title={t('common.download_video')}
              aria-label={t('common.download_video')}
            >
              <Download className="w-4 h-4" />
              <span>{t('common.download_video')}</span>
            </a>
          )}

          {/* Raw JSON Inspector */}
          <button
            onClick={() => setIsRawJsonOpen(true)}
            className="ui-button ui-button--neutral h-8 w-8 min-h-[32px] min-w-[32px] p-1.5 flex items-center justify-center transition"
            title={t('telemetry.raw_json')}
            aria-label={t('telemetry.raw_json')}
          >
            <Code2 className="w-4 h-4" />
          </button>

          {/* Language Toggle */}
          <button
            onClick={() => setLanguage(language === 'vi' ? 'en' : 'vi')}
            className="h-8 min-w-[36px] px-2.5 flex items-center justify-center rounded text-xs font-mono font-bold bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200 border border-slate-300 dark:border-slate-700 hover:border-orange-500/50 transition cursor-pointer"
            title={language === 'vi' ? 'Switch to English' : 'Chuyển sang Tiếng Việt'}
            aria-label="Switch language"
          >
            {language.toUpperCase()}
          </button>

          {/* Theme Toggle */}
          <button
            onClick={toggleTheme}
            className="h-8 w-8 min-h-[32px] min-w-[32px] p-1.5 flex items-center justify-center rounded text-slate-700 dark:text-slate-200 hover:text-slate-900 dark:hover:text-slate-100 bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 transition cursor-pointer"
            title={theme === 'dark' ? t('theme.light') : t('theme.dark')}
            aria-label={theme === 'dark' ? t('theme.light') : t('theme.dark')}
          >
            {theme === 'dark' ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-slate-700 dark:text-slate-300" />}
          </button>
        </div>
      </div>
    </header>
    <CatalogPanel open={isCatalogOpen} onClose={() => setIsCatalogOpen(false)} />
    <ConnectorFlowPanel open={isConnectorOpen} onClose={() => setIsConnectorOpen(false)} />
    </>
  );
};
