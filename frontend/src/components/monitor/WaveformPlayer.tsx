import React, { useRef, useEffect } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { formatSeconds } from '@/lib/utils';
import { Headphones, Volume2, Mic, Music } from 'lucide-react';

export const WaveformPlayer: React.FC = () => {
  const { segments, activeSegmentIndex, selectedAudioTrack, setSelectedAudioTrack, isPlaying } = useJob();
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const activeSegment = segments[activeSegmentIndex] || segments[0];

  // Draw simulated or real acoustic audio waveform
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrameId: number;
    let phase = 0;

    const render = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const width = canvas.width;
      const height = canvas.height;
      const centerY = height / 2;
      const barCount = 64;
      const barWidth = width / barCount - 1.5;

      // Color scheme based on track A vs B vs BGM
      let primaryColor = '#0284c7'; // B: Sky / Cyan
      if (selectedAudioTrack === 'a') primaryColor = '#ea580c'; // A: Orange
      if (selectedAudioTrack === 'bgm') primaryColor = '#8b5cf6'; // BGM: Purple

      for (let i = 0; i < barCount; i++) {
        // Generate waveform amplitude with pseudo-random harmonics
        const freq1 = Math.sin((i / barCount) * Math.PI * 4 + phase);
        const freq2 = Math.cos((i / barCount) * Math.PI * 8 - phase * 0.5);
        let amplitude = Math.abs(freq1 * 0.6 + freq2 * 0.4);

        if (isPlaying) {
          amplitude = Math.min(1.0, amplitude * (0.8 + Math.random() * 0.4));
        }

        const barHeight = Math.max(4, amplitude * (height * 0.8));
        const x = i * (barWidth + 1.5);
        const y = centerY - barHeight / 2;

        ctx.fillStyle = primaryColor;
        ctx.beginPath();
        ctx.roundRect(x, y, barWidth, barHeight, 2);
        ctx.fill();
      }

      if (isPlaying) {
        phase += 0.08;
      }
      animationFrameId = requestAnimationFrame(render);
    };

    render();

    return () => {
      cancelAnimationFrame(animationFrameId);
    };
  }, [selectedAudioTrack, isPlaying, activeSegmentIndex]);

  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg p-3.5 shadow-sm space-y-3 font-mono">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Headphones className="w-4 h-4 text-sky-500" />
          <h3 className="text-xs font-bold text-slate-800 dark:text-slate-200 uppercase">
            {t('monitor.ab_audition')}
          </h3>
        </div>

        {/* Active Segment Timecode Pill */}
        {activeSegment && (
          <span className="text-2xs text-slate-500 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded">
            SEG #{activeSegment.id} // {formatSeconds(activeSegment.start)} - {formatSeconds(activeSegment.end)}
          </span>
        )}
      </div>

      {/* A/B/BGM Track Switcher */}
      <div className="grid grid-cols-3 gap-1.5 p-1 bg-slate-100 dark:bg-slate-950 rounded-md text-xs">
        <button
          onClick={() => setSelectedAudioTrack('a')}
          className={`flex items-center justify-center gap-1.5 py-1.5 px-2 rounded transition-all font-semibold ${
            selectedAudioTrack === 'a'
              ? 'bg-orange-500 text-white shadow-sm'
              : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
          }`}
        >
          <Mic className="w-3.5 h-3.5" />
          <span className="truncate">{t('monitor.track_a')}</span>
        </button>

        <button
          onClick={() => setSelectedAudioTrack('b')}
          className={`flex items-center justify-center gap-1.5 py-1.5 px-2 rounded transition-all font-semibold ${
            selectedAudioTrack === 'b'
              ? 'bg-sky-600 text-white shadow-sm'
              : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
          }`}
        >
          <Volume2 className="w-3.5 h-3.5" />
          <span className="truncate">{t('monitor.track_b')}</span>
        </button>

        <button
          onClick={() => setSelectedAudioTrack('bgm')}
          className={`flex items-center justify-center gap-1.5 py-1.5 px-2 rounded transition-all font-semibold ${
            selectedAudioTrack === 'bgm'
              ? 'bg-purple-600 text-white shadow-sm'
              : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
          }`}
        >
          <Music className="w-3.5 h-3.5" />
          <span className="truncate">{t('monitor.track_bgm')}</span>
        </button>
      </div>

      {/* Waveform Canvas */}
      <div className="relative h-16 bg-slate-950 rounded-md p-2 flex items-center justify-center overflow-hidden border border-slate-800">
        <canvas
          ref={canvasRef}
          width={480}
          height={64}
          className="w-full h-full"
        />

        {/* Level lines */}
        <div className="absolute inset-x-0 top-1/2 h-px bg-slate-800 pointer-events-none" />
        <span className="absolute right-2 top-1 text-3xs font-mono text-slate-600">
          -14 LUFS TARGET
        </span>
      </div>
    </div>
  );
};
