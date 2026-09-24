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
  Upload,
  FolderOpen,
  UploadCloud,
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
    setIsCreatorOpen,
    setDroppedFile,
  } = useJob();
  const { t } = useTranslation();

  const [isLoopingSegment, setIsLoopingSegment] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      setDroppedFile(file);
      setIsCreatorOpen(true);
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const file = e.target.files[0];
      setDroppedFile(file);
      setIsCreatorOpen(true);
    }
  };

  return (
    <div
      ref={containerRef}
      className="bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden shadow-sm flex flex-col font-mono shrink-0 select-none"
    >
      {/* Hidden file input for direct video selection */}
      <input
        ref={fileInputRef}
        type="file"
        accept="video/*,audio/*"
        onChange={handleFileInputChange}
        className="hidden"
      />

      {/* Video Screen / Canvas Viewport with Drag & Drop (Compact Reference Monitor) */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className="relative h-[145px] sm:h-[155px] w-full bg-gradient-to-b from-slate-900 via-slate-950 to-black flex items-center justify-center overflow-hidden group"
      >
        {/* Drag-over active dropzone overlay */}
        {isDraggingOver && (
          <div className="absolute inset-0 z-30 bg-slate-950/95 border-2 border-dashed border-orange-500 flex flex-col items-center justify-center p-3 text-center animate-pulse">
            <UploadCloud className="w-9 h-9 text-orange-400 mb-1 animate-bounce" />
            <span className="text-xs font-bold text-white uppercase tracking-wider">
              {t('import.dropzone_active')}
            </span>
            <span className="text-[10px] text-orange-300 mt-0.5">
              MP4, MKV, MOV, WAV, FLAC (Auto Demucs + WhisperX)
            </span>
          </div>
        )}

        {/* Synthetic Video Canvas Content */}
        <div className="absolute inset-0 flex flex-col items-center justify-center opacity-85">
          <div className="w-10 h-10 rounded-full bg-slate-800/80 border border-slate-700 flex items-center justify-center mb-1 text-slate-400 group-hover:scale-105 transition-transform">
            <Tv className="w-5 h-5 text-orange-500" />
          </div>
          <span className="text-xs font-semibold text-slate-200 tracking-wider">
            {activeJob?.metadata?.input_name || 'Trading_Strategies_Masterclass.mp4'}
          </span>
          <span className="text-[10px] text-slate-500">
            24.00 FPS • 1920x1080 • ITU-R BT.709
          </span>
        </div>

        {/* Top Overlay: Speaker & HUD details + Import Action Button */}
        <div className="absolute top-2 inset-x-2 flex items-center justify-between z-20">
          {activeSegment ? (
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-900/90 backdrop-blur-md border border-slate-700/60 text-[11px] text-slate-200 font-semibold pointer-events-none">
              <span className="w-1.5 h-1.5 rounded-full bg-orange-500" />
              <span>{activeSegment.speaker}</span>
            </div>
          ) : <div />}

          <div className="flex items-center gap-1.5">
            {/* Prominent Import Video / YouTube Button on Monitor HUD */}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setIsCreatorOpen(true);
              }}
              className="flex items-center gap-1 px-2 py-0.5 rounded bg-orange-600 hover:bg-orange-500 backdrop-blur-md border border-orange-400 text-[11px] text-white font-bold shadow-md shadow-orange-600/30 hover:scale-105 transition cursor-pointer"
              title="Nhập Video Mới hoặc dán link YouTube"
            >
              <Upload className="w-3 h-3" />
              <span>{t('import.import_video')}</span>
            </button>

            <div className="px-1.5 py-0.5 rounded bg-black/80 backdrop-blur-sm border border-slate-800 text-[10px] text-emerald-400 font-bold pointer-events-none">
              1080p DUB READY
            </div>
          </div>
        </div>

        {/* Bottom Subtitle Overlay: Bilingual side-by-side subtitle */}
        {activeSegment && (
          <div className="absolute bottom-6 inset-x-2 flex flex-col items-center pointer-events-none space-y-0.5">
            <div className="px-2.5 py-0.5 rounded bg-black/90 backdrop-blur-md border border-slate-700/80 text-xs font-sans font-medium text-amber-300 text-center max-w-md shadow-lg truncate">
              {activeSegment.vi}
            </div>
          </div>
        )}

        {/* Bottom Bar: Discreet drag/drop hint button */}
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="absolute bottom-1.5 left-2 z-10 flex items-center gap-1 px-2 py-0.5 rounded bg-black/70 hover:bg-black/90 backdrop-blur-sm border border-slate-800 text-[9px] text-slate-300 hover:text-white transition cursor-pointer"
          title="Bấm để chọn file video từ máy"
        >
          <FolderOpen className="w-2.5 h-2.5 text-orange-400" />
          <span className="truncate max-w-[200px]">{t('import.drop_hint')}</span>
        </button>

        {/* Center Play Button on Hover */}
        <button
          onClick={() => setIsPlaying(!isPlaying)}
          className="absolute inset-0 flex items-center justify-center bg-black/25 opacity-0 group-hover:opacity-100 transition-opacity"
        >
          <div className="w-10 h-10 rounded-full bg-orange-600/90 text-white flex items-center justify-center shadow-xl hover:scale-110 transition-transform">
            {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5 ml-0.5" />}
          </div>
        </button>
      </div>

      {/* Scrub Bar & Timeline */}
      <div className="px-3 pt-1.5 bg-slate-900 border-t border-slate-800">
        <div className="relative w-full h-1.5 bg-slate-800 rounded-full cursor-pointer group">
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
