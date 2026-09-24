import React from 'react';
import { useTranslation } from '@/context/I18nContext';
import { useTheme } from '@/context/ThemeContext';
import { useJob } from '@/context/JobContext';
import { Badge } from '@/components/common/Badge';
import {
  Volume2,
  Plus,
  Moon,
  Sun,
  Code2,
  Terminal,
  Layers,
  ChevronDown,
} from 'lucide-react';

export const Header: React.FC = () => {
  const { language, setLanguage, t } = useTranslation();
  const { theme, toggleTheme, mode, toggleMode } = useTheme();
  const { jobs, activeJobId, setActiveJobId, setIsRawJsonOpen, setIsCreatorOpen } = useJob();

  const activeJob = jobs.find(j => j.id === activeJobId) || jobs[0];

  return (
    <header className="border-b border-studio-border dark:border-slate-800 bg-studio-panel/90 dark:bg-slate-900/90 backdrop-blur-md sticky top-0 z-40 transition-colors">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
        {/* Left: Branding & Job Selector */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-orange-500 to-amber-600 flex items-center justify-center shadow-lg shadow-orange-500/20 text-white">
              <Volume2 className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-sm tracking-tight text-slate-900 dark:text-slate-100 font-mono">
                  VI-DUBBER
                </span>
                <span className="text-2xs font-semibold px-1.5 py-0.5 rounded bg-orange-500/10 text-orange-600 dark:text-orange-400 border border-orange-500/30 font-mono">
                  v2.0-PRO
                </span>
              </div>
              <p className="text-2xs text-slate-500 dark:text-slate-400 hidden sm:block">
                Neural Video Dubbing & Acoustic Suite
              </p>
            </div>
          </div>

          <div className="h-5 w-px bg-slate-300 dark:bg-slate-800 hidden md:block" />

          {/* Job Dropdown */}
          <div className="relative group">
            <button className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-slate-100 dark:bg-slate-800/80 hover:bg-slate-200 dark:hover:bg-slate-800 border border-slate-300 dark:border-slate-700/80 text-xs font-mono text-slate-800 dark:text-slate-200 transition">
              <span className="max-w-[140px] truncate">
                {activeJob?.metadata?.input_name || activeJob?.id || 'Select Job'}
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
              <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
            </button>

            {/* Dropdown Menu */}
            <div className="absolute left-0 mt-1 w-64 py-1 bg-white dark:bg-slate-900 rounded-md shadow-xl border border-slate-200 dark:border-slate-800 hidden group-hover:block z-50">
              <div className="px-3 py-1.5 text-2xs font-mono text-slate-400 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800">
                {t('common.all_jobs')} ({jobs.length})
              </div>
              <div className="max-h-60 overflow-y-auto">
                {jobs.map(job => (
                  <button
                    key={job.id}
                    onClick={() => setActiveJobId(job.id)}
                    className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-slate-100 dark:hover:bg-slate-800/80 transition ${
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
        <div className="flex items-center gap-2 sm:gap-3">
          {/* New Job CTA */}
          <button
            onClick={() => setIsCreatorOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-600 hover:bg-orange-500 text-white rounded-md text-xs font-semibold shadow-sm shadow-orange-600/30 transition font-mono"
          >
            <Plus className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">{t('creator.new_job')}</span>
          </button>

          {/* Mode Toggle: Standard Studio vs Pro Engineer */}
          <button
            onClick={toggleMode}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-mono border transition ${
              mode === 'engineer'
                ? 'bg-sky-500/10 dark:bg-sky-950/40 text-sky-600 dark:text-sky-400 border-sky-500/40 font-semibold'
                : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border-slate-300 dark:border-slate-700'
            }`}
            title="Toggle Standard Studio / Pro Engineer Mode"
          >
            {mode === 'engineer' ? <Terminal className="w-3.5 h-3.5" /> : <Layers className="w-3.5 h-3.5" />}
            <span className="hidden md:inline">
              {mode === 'engineer' ? t('mode.engineer') : t('mode.standard')}
            </span>
          </button>

          {/* Raw JSON Inspector */}
          <button
            onClick={() => setIsRawJsonOpen(true)}
            className="p-1.5 rounded-md text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 bg-slate-100 dark:bg-slate-800/80 border border-slate-300 dark:border-slate-700 transition"
            title={t('telemetry.raw_json')}
          >
            <Code2 className="w-4 h-4" />
          </button>

          {/* Language Toggle */}
          <button
            onClick={() => setLanguage(language === 'vi' ? 'en' : 'vi')}
            className="px-2 py-1 rounded-md text-xs font-mono font-bold bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200 border border-slate-300 dark:border-slate-700 hover:border-orange-500/50 transition"
            title="Switch Language (VI / EN)"
          >
            {language.toUpperCase()}
          </button>

          {/* Theme Toggle */}
          <button
            onClick={toggleTheme}
            className="p-1.5 rounded-md text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 bg-slate-100 dark:bg-slate-800/80 border border-slate-300 dark:border-slate-700 transition"
            title={theme === 'dark' ? t('theme.light') : t('theme.dark')}
          >
            {theme === 'dark' ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-slate-700" />}
          </button>
        </div>
      </div>
    </header>
  );
};
