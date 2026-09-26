import React from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { Header } from '@/components/header/Header';
import { SystemBar } from '@/components/header/SystemBar';
import { VideoPlayer } from '@/components/monitor/VideoPlayer';
import { LongformPreviewRail } from '@/components/monitor/LongformPreviewRail';
import { WaveformPlayer } from '@/components/monitor/WaveformPlayer';
import { SegmentReviewer } from '@/components/review/SegmentReviewer';
import { JobCreatorModal } from '@/components/creator/JobCreatorModal';
import { JsonInspectModal } from '@/components/common/JsonInspectModal';
import { EngineerDrawer } from '@/components/telemetry/EngineerDrawer';
import { AlertTriangle } from 'lucide-react';

export const App: React.FC = () => {
  const { isBackendOnline } = useJob();
  const { t } = useTranslation();

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col bg-slate-100 dark:bg-[#090d16] text-slate-900 dark:text-slate-100 font-sans select-none">
      {/* Top Bar: Header (46px fixed height) */}
      <Header />

      {/* Backend Offline Warning Banner */}
      {isBackendOnline === false && (
        <div className="bg-amber-500/10 dark:bg-amber-950/40 border-b border-amber-500/30 text-amber-700 dark:text-amber-300 text-xs py-1 px-4 font-mono flex items-center justify-center gap-2 shrink-0 z-30">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 text-amber-500" />
          <span>{t('system.backend_offline')} — {t('system.start_hint')}</span>
        </div>
      )}

      {/* System Ribbon: Compact 36px horizontal strip */}
      <SystemBar />

      {/* Main Cockpit Workspace */}
      <main className="flex-1 min-h-0 w-full p-2.5 grid grid-cols-12 gap-2.5 overflow-hidden">
        {/* Left Column: 16:9 Video Monitor + A/B Waveform Player */}
        <section
          aria-label="Media Monitor & Telemetry Rack"
          className="col-span-12 lg:col-span-4 h-full overflow-hidden flex flex-col gap-2 min-h-0"
        >
          {/* Upper: 16:9 Studio Master Video Monitor with Drag & Drop */}
          <VideoPlayer />

          <LongformPreviewRail />

          {/* Middle: A/B Waveform Audition Player */}
          <WaveformPlayer />
        </section>

        {/* Right Column: Bilingual Segment Reviewer */}
        <section
          aria-label="Bilingual Segment Review Deck"
          className="col-span-12 lg:col-span-8 h-full overflow-hidden min-h-0"
        >
          <SegmentReviewer />
        </section>
      </main>

      {/* Engineer Drawer Bottom Panel */}
      <EngineerDrawer />

      {/* Studio Modals */}
      <JobCreatorModal />
      <JsonInspectModal />
    </div>
  );
};

export default App;
