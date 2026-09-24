import React from 'react';
import { useTheme } from '@/context/ThemeContext';
import { Header } from '@/components/header/Header';
import { SystemBar } from '@/components/header/SystemBar';
import { PipelineStepper } from '@/components/pipeline/PipelineStepper';
import { DynamicEtaCard } from '@/components/monitor/DynamicEtaCard';
import { VideoPlayer } from '@/components/monitor/VideoPlayer';
import { WaveformPlayer } from '@/components/monitor/WaveformPlayer';
import { ProTelemetryGrid } from '@/components/telemetry/ProTelemetryGrid';
import { SegmentReviewer } from '@/components/review/SegmentReviewer';
import { JobCreatorModal } from '@/components/creator/JobCreatorModal';
import { JsonInspectModal } from '@/components/common/JsonInspectModal';

export const App: React.FC = () => {
  const { mode } = useTheme();

  return (
    <div className="min-h-screen bg-slate-100 dark:bg-studio-dark text-slate-900 dark:text-slate-100 flex flex-col font-sans transition-colors duration-200">
      {/* Top Header */}
      <Header />

      {/* Live System & Telemetry Bar */}
      <SystemBar />

      {/* Main Studio Viewport */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 space-y-5">
        {/* 1. 7-Stage Pipeline Stepper */}
        <section aria-label="Pipeline Stepper">
          <PipelineStepper />
        </section>

        {/* 2. Dynamic ETA & Time Estimation */}
        <section aria-label="Dynamic ETA & Telemetry">
          <DynamicEtaCard />
        </section>

        {/* 3. Media Monitor: Video Player & A/B Waveform Audition */}
        <section aria-label="Media Monitor" className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
          {/* Left: Video Player (7 Cols) */}
          <div className="lg:col-span-7">
            <VideoPlayer />
          </div>

          {/* Right: Waveform & A/B Audition (5 Cols) */}
          <div className="lg:col-span-5 space-y-4">
            <WaveformPlayer />
          </div>
        </section>

        {/* 4. Pro / Engineer Mode Telemetry (Visible when in Engineer Mode) */}
        {mode === 'engineer' && (
          <section aria-label="Engineer Telemetry Cards" className="pt-2 animate-in fade-in duration-300">
            <ProTelemetryGrid />
          </section>
        )}

        {/* 5. Bilingual Segment Reviewer */}
        <section aria-label="Bilingual Segment Reviewer">
          <SegmentReviewer />
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-200 dark:border-slate-800/80 py-4 px-6 text-center text-2xs font-mono text-slate-500 bg-white/50 dark:bg-slate-950/50">
        VI Dubber Studio Pro • BS-RoFormer v2 + WhisperX + GPT-5.6 Sol (Codex Web) + Kokoro TTS • Zero-Artifact Acoustic Mastering
      </footer>

      {/* Modals */}
      <JobCreatorModal />
      <JsonInspectModal />
    </div>
  );
};

export default App;
