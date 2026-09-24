import React, { useRef, useState, useEffect } from 'react';
import { useJob } from '@/context/JobContext';
import { useTranslation } from '@/context/I18nContext';
import { formatSeconds } from '@/lib/utils';
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Repeat,
  Volume2,
  VolumeX,
  Maximize2,
  Tv,
} from 'lucide-react';

export const VideoPlayer: React.FC = () => {
  const {
    activeJob,
    segments,
    activeSegmentIndex,
    setActiveSegmentIndex,
    currentTime,
    setCurrentTime,
    isPlaying,
    setIsPlaying,
  } = useJob();
  const { t } = useTranslation();

  const [isLoopingSegment, setIsLoopingSegment] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const activeSegment = segments[activeSegmentIndex] || segments[0];
  const totalDuration = activeJob?.result?.duration_seconds || 184.5;

  // Playback simulation ticker when video file is not an external mp4
  useEffect(() => {
    let interval: any;
    if (isPlaying) {
      interval = setInterval(() => {
        setCurrentTime((prev: number) => {
          const next = prev + 0.1 * playbackSpeed;
          if (isLoopingSegment && activeSegment && next > activeSegment.end) {
            return activeSegment.start;
          }
          if (next >= totalDuration) {
            setIsPlaying(false);
            return 0;
          }
          return next;
        });
      }, 100);
    }
    return () => clearInterval(interval);
  }, [isPlaying, playbackSpeed, isLoopingSegment, activeSegment, totalDuration, setCurrentTime, setIsPlaying]);

  const handlePrevSegment = () => {
    if (activeSegmentIndex > 0) {
      const prevIdx = activeSegmentIndex - 1;
      setActiveSegmentIndex(prevIdx);
      setCurrentTime(segments[prevIdx].start);
    }
  };

  const handleNextSegment = () => {
    if (activeSegmentIndex < segments.length - 1) {
      const nextIdx = activeSegmentIndex + 1;
      setActiveSegmentIndex(nextIdx);
      setCurrentTime(segments[nextIdx].start);
    }
  };

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen?.();
    } else {
      document.exitFullscreen?.();
    }
  };

  return (
    <div
      ref={containerRef}
      className="bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden shadow-sm flex flex-col font-mono shrink-0 select-none"
    >
      {/* Video Screen / Canvas Viewport */}
      <div className="relative aspect-video bg-gradient-to-b from-slate-900 via-slate-950 to-black flex items-center justify-center overflow-hidden group">
        {/* Synthetic Video Canvas Content */}
        <div className="absolute inset-0 flex flex-col items-center justify-center opacity-85">
          <div className="w-14 h-14 rounded-full bg-slate-800/80 border border-slate-700 flex items-center justify-center mb-2.5 text-slate-400 group-hover:scale-105 transition-transform">
            <Tv className="w-7 h-7 text-orange-500" />
          </div>
          <span className="text-xs font-semibold text-slate-200 tracking-wider">
            {activeJob?.metadata?.input_name || 'Trading_Strategies_Masterclass.mp4'}
          </span>
          <span className="text-xs text-slate-500 mt-0.5">
            24.00 FPS • 1920x1080 • ITU-R BT.709
          </span>
        </div>

        {/* Top Overlay: Speaker & HUD details */}
        <div className="absolute top-2.5 inset-x-2.5 flex items-center justify-between pointer-events-none">
          {activeSegment && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-900/90 backdrop-blur-md border border-slate-700/60 text-xs text-slate-200 font-semibold">
              <span className="w-2 h-2 rounded-full bg-orange-500" />
              <span>{activeSegment.speaker}</span>
            </div>
          )}
          <div className="px-2 py-0.5 rounded bg-black/80 backdrop-blur-sm border border-slate-800 text-xs text-emerald-400 font-bold">
            1080p DUB READY
          </div>
        </div>

        {/* Bottom Subtitle Overlay: Bilingual side-by-side subtitle */}
        {activeSegment && (
          <div className="absolute bottom-3 inset-x-3 flex flex-col items-center pointer-events-none space-y-1">
            <div className="px-3 py-1 rounded bg-black/90 backdrop-blur-md border border-slate-700/80 text-xs sm:text-sm font-sans font-medium text-amber-300 text-center max-w-xl shadow-lg">
              {activeSegment.vi}
            </div>
            <div className="text-xs font-sans text-slate-300 text-center drop-shadow-md">
              {activeSegment.text}
            </div>
          </div>
        )}

        {/* Center Play Button on Hover */}
        <button
          onClick={() => setIsPlaying(!isPlaying)}
          className="absolute inset-0 flex items-center justify-center bg-black/25 opacity-0 group-hover:opacity-100 transition-opacity"
        >
          <div className="w-12 h-12 rounded-full bg-orange-600/90 text-white flex items-center justify-center shadow-xl hover:scale-110 transition-transform">
            {isPlaying ? <Pause className="w-6 h-6" /> : <Play className="w-6 h-6 ml-0.5" />}
          </div>
        </button>
      </div>

      {/* Scrub Bar & Timeline */}
      <div className="px-3 pt-2 bg-slate-900 border-t border-slate-800">
        <div className="relative w-full h-2 bg-slate-800 rounded-full cursor-pointer group">
          {/* Segment Markers on Seek Bar */}
          {segments.map(seg => {
            const leftPct = (seg.start / totalDuration) * 100;
            const widthPct = ((seg.end - seg.start) / totalDuration) * 100;
            const isSegActive = seg.id === activeSegment?.id;
            return (
              <div
                key={seg.id}
                onClick={(e) => {
                  e.stopPropagation();
                  setActiveSegmentIndex(seg.id);
                  setCurrentTime(seg.start);
                }}
                className={`absolute top-0 bottom-0 rounded-full transition-opacity ${
                  isSegActive ? 'bg-orange-500/90' : 'bg-slate-700 hover:bg-slate-600'
                }`}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                title={`Seg #${seg.id}: ${seg.speaker}`}
              />
            );
          })}

          {/* Current Playhead */}
          <div
            className="absolute top-0 bottom-0 bg-orange-500 rounded-full"
            style={{ width: `${Math.min(100, (currentTime / totalDuration) * 100)}%` }}
          />
        </div>
      </div>

      {/* Media Controls Bar */}
      <div className="px-3 py-2 bg-slate-900 flex items-center justify-between gap-3 text-xs text-slate-300">
        <div className="flex items-center gap-2">
          {/* Play / Pause */}
          <button
            onClick={() => setIsPlaying(!isPlaying)}
            className="p-1 rounded hover:bg-slate-800 text-slate-200 hover:text-white transition"
            title={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? <Pause className="w-4 h-4 text-orange-400" /> : <Play className="w-4 h-4 text-orange-400" />}
          </button>

          {/* Prev / Next Segment */}
          <button
            onClick={handlePrevSegment}
            disabled={activeSegmentIndex === 0}
            className="p-1 rounded hover:bg-slate-800 disabled:opacity-30 transition"
            title="Previous Segment"
          >
            <SkipBack className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={handleNextSegment}
            disabled={activeSegmentIndex === segments.length - 1}
            className="p-1 rounded hover:bg-slate-800 disabled:opacity-30 transition"
            title="Next Segment"
          >
            <SkipForward className="w-3.5 h-3.5" />
          </button>

          {/* Loop Segment */}
          <button
            onClick={() => setIsLoopingSegment(!isLoopingSegment)}
            className={`p-1 rounded transition ${
              isLoopingSegment
                ? 'bg-orange-500/20 text-orange-400 border border-orange-500/40'
                : 'hover:bg-slate-800 text-slate-400'
            }`}
            title={t('monitor.loop_segment')}
          >
            <Repeat className="w-3.5 h-3.5" />
          </button>

          {/* Timecode */}
          <span className="text-xs tabular-nums text-slate-400 ml-1">
            <span className="text-slate-100 font-semibold">{formatSeconds(currentTime)}</span> / {formatSeconds(totalDuration)}
          </span>
        </div>

        {/* Right: Audio Mute, Speed, Fullscreen */}
        <div className="flex items-center gap-2">
          {/* Speed Selector */}
          <select
            value={playbackSpeed}
            onChange={(e) => setPlaybackSpeed(parseFloat(e.target.value))}
            className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded px-1.5 py-0.5 focus:outline-none cursor-pointer"
          >
            <option value="0.75">0.75x</option>
            <option value="1">1.0x</option>
            <option value="1.25">1.25x</option>
            <option value="1.5">1.5x</option>
          </select>

          {/* Mute */}
          <button
            onClick={() => setIsMuted(!isMuted)}
            className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition"
          >
            {isMuted ? <VolumeX className="w-3.5 h-3.5 text-rose-400" /> : <Volume2 className="w-3.5 h-3.5" />}
          </button>

          {/* Fullscreen */}
          <button
            onClick={toggleFullscreen}
            className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
};
