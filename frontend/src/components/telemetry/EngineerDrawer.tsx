import React, { useState, useEffect } from 'react';
import { useTranslation } from '@/context/I18nContext';
import { ProTelemetryGrid } from '@/components/telemetry/ProTelemetryGrid';
import { Wrench, ChevronUp, ChevronDown } from 'lucide-react';

export const EngineerDrawer: React.FC = () => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem('vi_dubber_engineer_drawer');
    if (saved !== null) {
      setIsOpen(saved === 'true');
    }
  }, []);

  const toggleDrawer = () => {
    const newState = !isOpen;
    setIsOpen(newState);
    localStorage.setItem('vi_dubber_engineer_drawer', String(newState));
  };

  return (
    <div className="w-full bg-slate-100 dark:bg-[#090d16] border-t border-slate-200 dark:border-slate-800 flex flex-col transition-all duration-300 ease-in-out shrink-0" style={{ height: isOpen ? '35vh' : '32px' }}>
      {/* Header Bar */}
      <button
        onClick={toggleDrawer}
        className="w-full h-8 flex items-center justify-center gap-2 bg-slate-200 dark:bg-slate-900 hover:bg-slate-300 dark:hover:bg-slate-800 text-slate-800 dark:text-slate-200 transition-colors shrink-0 cursor-pointer"
        aria-label={t('telemetry.engineer_telemetry')}
      >
        <Wrench className="w-3.5 h-3.5 text-orange-500" />
        <span className="text-xs font-mono font-semibold tracking-wider">
          {t('telemetry.engineer_telemetry')}
        </span>
        {isOpen ? <ChevronDown className="w-4 h-4 text-slate-500 dark:text-slate-400" /> : <ChevronUp className="w-4 h-4 text-slate-500 dark:text-slate-400" />}
      </button>

      {/* Expanded Content */}
      <div className={`w-full overflow-hidden flex-1 ${isOpen ? 'opacity-100' : 'opacity-0'} transition-opacity duration-300`}>
        {isOpen && (
          <div className="w-full h-full p-2 overflow-hidden">
            <ProTelemetryGrid />
          </div>
        )}
      </div>
    </div>
  );
};
