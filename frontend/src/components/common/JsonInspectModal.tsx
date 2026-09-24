import React, { useState } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { X, Copy, Check, Download, FileJson } from 'lucide-react';

export const JsonInspectModal: React.FC = () => {
  const { activeJob, segments, isRawJsonOpen, setIsRawJsonOpen } = useJob();
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<'state' | 'metrics' | 'segments' | 'result'>('state');
  const [copied, setCopied] = useState(false);

  if (!isRawJsonOpen || !activeJob) return null;

  let currentData: any = {};
  if (activeTab === 'state') currentData = activeJob;
  else if (activeTab === 'metrics') currentData = activeJob.metrics || {};
  else if (activeTab === 'segments') currentData = segments;
  else if (activeTab === 'result') currentData = activeJob.result || {};

  const jsonString = JSON.stringify(currentData, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(jsonString);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    const blob = new Blob([jsonString], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${activeJob.id}_${activeTab}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
      <div className="flex flex-col w-full max-w-5xl h-[85vh] bg-slate-900 border border-slate-700 rounded-lg shadow-2xl overflow-hidden text-slate-100">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 bg-slate-950 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <FileJson className="w-5 h-5 text-orange-500" />
            <h2 className="text-sm font-semibold tracking-wide font-mono text-slate-200">
              RAW_STATE_INSPECTOR // {activeJob.id}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-mono bg-slate-800 hover:bg-slate-700 text-slate-200 rounded border border-slate-700 transition"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              {copied ? t('common.copied') : t('common.copy')}
            </button>
            <button
              onClick={handleDownload}
              className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-mono bg-slate-800 hover:bg-slate-700 text-slate-200 rounded border border-slate-700 transition"
              title="Download JSON"
            >
              <Download className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setIsRawJsonOpen(false)}
              className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Tab selector */}
        <div className="flex items-center gap-1 px-4 py-2 bg-slate-900 border-b border-slate-800 text-xs font-mono">
          {(['state', 'metrics', 'segments', 'result'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-1 rounded transition-colors ${
                activeTab === tab
                  ? 'bg-orange-500/20 text-orange-400 border border-orange-500/40 font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
              }`}
            >
              {tab.toUpperCase()}.JSON
            </button>
          ))}
        </div>

        {/* JSON Code Viewer */}
        <div className="flex-1 p-4 overflow-auto bg-slate-950/80 font-mono text-xs text-sky-300 leading-relaxed select-text">
          <pre className="whitespace-pre">{jsonString}</pre>
        </div>
      </div>
    </div>
  );
};
