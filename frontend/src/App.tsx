import React from 'react';
import { Header } from '@/components/header/Header';
import { SystemBar } from '@/components/header/SystemBar';
import { VideoPlayer } from '@/components/monitor/VideoPlayer';
import { WaveformPlayer } from '@/components/monitor/WaveformPlayer';
import { ProTelemetryGrid } from '@/components/telemetry/ProTelemetryGrid';
import { SegmentReviewer } from '@/components/review/SegmentReviewer';
import { JobCreatorModal } from '@/components/creator/JobCreatorModal';
import { JsonInspectModal } from '@/components/common/JsonInspectModal';

export const App: React.FC = () => {
  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col bg-slate-100 dark:bg-[#090d16] text-slate-900 dark:text-slate-100 font-sans select-none">
      {/* Top Bar: Header (46px fixed height) */}
      <Header />

      {/* System Ribbon: Compact 36px horizontal strip */}
      <SystemBar />

      {/* Main Cockpit Workspace */}
      <main className="flex-1 min-h-0 w-full p-2.5 grid grid-cols-12 gap-2.5 overflow-hidden">
        {/* Left Column: 16:9 Video Monitor + A/B Waveform Player + Tabbed Pro Telemetry Drawer */}
        <section
          aria-label="Media Monitor & Telemetry Rack"
          className="col-span-12 lg:col-span-5 h-full overflow-hidden flex flex-col gap-2.5"
        >
          {/* Upper: 16:9 Studio Master Video Monitor */}
          <VideoPlayer />

          {/* Middle: A/B Waveform Audition Player */}
          <WaveformPlayer />

          {/* Lower: Tabbed Pro Telemetry Drawer taking remaining vertical space */}
          <ProTelemetryGrid />
        </section>

        {/* Right Column: Bilingual Segment Reviewer */}
        <section
          aria-label="Bilingual Segment Review Deck"
          className="col-span-12 lg:col-span-7 h-full overflow-hidden"
        >
          <SegmentReviewer />
        </section>
      </main>

      {/* Studio Modals */}
      <JobCreatorModal />
      <JsonInspectModal />
    </div>
  );
};

export default App;
