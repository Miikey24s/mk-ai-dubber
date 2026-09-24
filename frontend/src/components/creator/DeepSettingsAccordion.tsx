import React, { useState } from 'react';
import { useTranslation } from '@/context/I18nContext';
import { ChevronDown, ChevronRight, Sliders } from 'lucide-react';

interface DeepSettingsState {
  diarization: boolean;
  targetLufs: number;
  truePeak: number;
  maxSpeedup: number;
  maxSlowdown: number;
  rewriteThreshold: number;
  retryBudget: number;
  semanticThreshold: number;
}

interface DeepSettingsAccordionProps {
  settings: DeepSettingsState;
  onChange: (settings: DeepSettingsState) => void;
}

export const DeepSettingsAccordion: React.FC<DeepSettingsAccordionProps> = ({
  settings,
  onChange,
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  const update = (key: keyof DeepSettingsState, val: any) => {
    onChange({ ...settings, [key]: val });
  };

  return (
    <div className="border border-slate-200 dark:border-slate-800 rounded-lg overflow-hidden font-mono text-xs">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-slate-900/80 hover:bg-slate-100 dark:hover:bg-slate-900 text-left transition"
      >
        <div className="flex items-center gap-2">
          <Sliders className="w-4 h-4 text-orange-500" />
          <span className="font-bold text-slate-800 dark:text-slate-200 uppercase tracking-wide">
            {t('creator.deep_settings')}
          </span>
        </div>
        {isOpen ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
      </button>

      {isOpen && (
        <div className="p-4 bg-white dark:bg-slate-950 space-y-4 border-t border-slate-200 dark:border-slate-800">
          {/* Diarization Toggle */}
          <div className="flex items-center justify-between">
            <div>
              <span className="font-semibold text-slate-800 dark:text-slate-200 block">
                {t('deep.diarization')}
              </span>
              <span className="text-2xs text-slate-500">
                PyAnnote 3.1 neural speaker diarization clustering
              </span>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={settings.diarization}
                onChange={(e) => update('diarization', e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-9 h-5 bg-slate-300 peer-focus:outline-none rounded-full peer dark:bg-slate-800 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-orange-500"></div>
            </label>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
            {/* Target LUFS */}
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-2xs text-slate-500">{t('deep.ducking_lufs')}</span>
                <span className="font-bold text-slate-700 dark:text-slate-300">{settings.targetLufs} LUFS</span>
              </div>
              <input
                type="range"
                min="-24"
                max="-10"
                step="0.5"
                value={settings.targetLufs}
                onChange={(e) => update('targetLufs', parseFloat(e.target.value))}
                className="w-full accent-orange-500 cursor-pointer"
              />
            </div>

            {/* True Peak dBTP */}
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-2xs text-slate-500">{t('deep.true_peak')}</span>
                <span className="font-bold text-slate-700 dark:text-slate-300">{settings.truePeak} dBTP</span>
              </div>
              <input
                type="range"
                min="-3.0"
                max="-0.5"
                step="0.1"
                value={settings.truePeak}
                onChange={(e) => update('truePeak', parseFloat(e.target.value))}
                className="w-full accent-orange-500 cursor-pointer"
              />
            </div>

            {/* Max Audio Speedup */}
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-2xs text-slate-500">{t('deep.max_speedup')}</span>
                <span className="font-bold text-slate-700 dark:text-slate-300">{settings.maxSpeedup}x</span>
              </div>
              <input
                type="range"
                min="1.0"
                max="1.5"
                step="0.05"
                value={settings.maxSpeedup}
                onChange={(e) => update('maxSpeedup', parseFloat(e.target.value))}
                className="w-full accent-orange-500 cursor-pointer"
              />
            </div>

            {/* Rewrite Threshold */}
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-2xs text-slate-500">{t('deep.rewrite_threshold')}</span>
                <span className="font-bold text-slate-700 dark:text-slate-300">{settings.rewriteThreshold}x</span>
              </div>
              <input
                type="range"
                min="1.1"
                max="1.5"
                step="0.02"
                value={settings.rewriteThreshold}
                onChange={(e) => update('rewriteThreshold', parseFloat(e.target.value))}
                className="w-full accent-orange-500 cursor-pointer"
              />
            </div>

            {/* Retry Budget */}
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-2xs text-slate-500">{t('deep.retry_budget')}</span>
                <span className="font-bold text-slate-700 dark:text-slate-300">{settings.retryBudget} retries</span>
              </div>
              <input
                type="range"
                min="1"
                max="5"
                step="1"
                value={settings.retryBudget}
                onChange={(e) => update('retryBudget', parseInt(e.target.value))}
                className="w-full accent-orange-500 cursor-pointer"
              />
            </div>

            {/* Semantic QA Faithfulness Cutoff */}
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-2xs text-slate-500">{t('deep.semantic_threshold')}</span>
                <span className="font-bold text-slate-700 dark:text-slate-300">{settings.semanticThreshold}</span>
              </div>
              <input
                type="range"
                min="0.5"
                max="0.9"
                step="0.02"
                value={settings.semanticThreshold}
                onChange={(e) => update('semanticThreshold', parseFloat(e.target.value))}
                className="w-full accent-orange-500 cursor-pointer"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
