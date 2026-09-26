import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Download, LoaderCircle, Play, RotateCcw } from 'lucide-react';
import { useJob } from '@/context/JobContext';
import { fetchJobPreviews, rerenderJob } from '@/lib/api';
import type { PreviewArtifact } from '@/types';
import { formatSeconds } from '@/lib/utils';

export const LongformPreviewRail: React.FC = () => {
  const {
    activeJob,
    refreshJobs,
    selectedPreview,
    setSelectedPreview,
    requestSeek,
    setIsPlaying,
  } = useJob();
  const [previews, setPreviews] = useState<PreviewArtifact[]>([]);
  const [selectedChunkId, setSelectedChunkId] = useState<string>('');
  const [rerendering, setRerendering] = useState(false);
  const longform = activeJob?.metadata?.longform;
  const enabled = Boolean(longform?.enabled);

  useEffect(() => {
    if (!activeJob || !enabled) {
      setPreviews([]);
      setSelectedChunkId('');
      return;
    }
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await fetchJobPreviews(activeJob.id);
        if (cancelled) return;
        setPreviews(next);
        const storageKey = `vi_dubber_preview_${activeJob.id}`;
        const remembered = window.localStorage.getItem(storageKey) || '';
        const candidate = [selectedPreview?.chunk_id || '', remembered]
          .find(chunkId => chunkId && next.some(item => item.chunk_id === chunkId));
        const hadSelection = Boolean(selectedPreview?.chunk_id || remembered);
        const chosen = candidate || (!hadSelection && activeJob.status !== 'completed' ? next[0]?.chunk_id || '' : '');
        const artifact = next.find(item => item.chunk_id === chosen) || null;
        setSelectedPreview(artifact);
        setSelectedChunkId(chosen);
        if (chosen) window.localStorage.setItem(storageKey, chosen);
        else window.localStorage.removeItem(storageKey);
      } catch (error) {
        if (!cancelled) console.error('Failed fetching long-form previews:', error);
      }
    };
    refresh();
    const interval = activeJob.status === 'running' ? window.setInterval(refresh, 2000) : undefined;
    return () => {
      cancelled = true;
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [activeJob?.id, activeJob?.status, activeJob?.updated_at, enabled, selectedPreview?.chunk_id, setSelectedPreview]);

  const previewById = useMemo(
    () => new Map(previews.map(item => [item.chunk_id, item])),
    [previews],
  );
  const ready = longform?.preview_ready_chunks || previews.map(item => item.chunk_id);
  const stale = new Set(longform?.preview_stale_chunks || []);
  const blocked = new Set(longform?.preview_blocked_chunks || []);
  const total = longform?.preview_total_chunks || longform?.total_chunks || previews.length;
  const allChunkIds = useMemo(() => {
    const known = new Set<string>([...ready, ...stale, ...blocked, ...previews.map(item => item.chunk_id)]);
    for (let index = 1; index <= total; index += 1) {
      known.add(`chunk_${String(index).padStart(4, '0')}`);
    }
    return Array.from(known).sort();
  }, [ready, stale, blocked, previews, total]);
  const selected = previewById.get(selectedChunkId);
  const currentPhase = String(longform?.phase || activeJob?.stage || '').toUpperCase();
  const currentChunk = longform?.current_chunk || '';
  const eta = longform?.eta_seconds;

  if (!activeJob || !enabled) return null;

  const requestRerender = async () => {
    setRerendering(true);
    try {
      await rerenderJob(activeJob.id);
      await refreshJobs();
    } finally {
      setRerendering(false);
    }
  };

  return (
    <section className="shrink-0 rounded-xl border border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-950/70">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] font-semibold text-slate-800 dark:text-slate-100">
            <span>Long-form chunks</span>
            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-bold tracking-wide text-amber-700 dark:text-amber-300">
              PREVIEW
            </span>
            {activeJob.status === 'completed' && (
              <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-bold tracking-wide text-emerald-700 dark:text-emerald-300">
                FINAL READY
              </span>
            )}
          </div>
          <p className="truncate text-[10px] text-slate-500 dark:text-slate-400">
            {previews.length}/{total} preview đã publish
            {currentChunk ? ` · ${currentChunk.replace('chunk_', '#')} ${currentPhase || 'PROCESSING'}` : ''}
            {typeof eta === 'number' && eta >= 0 ? ` · ETA ${formatSeconds(eta)}` : ''}
          </p>
        </div>
        {activeJob.status === 'completed' && (
          <a
            href={`/api/jobs/${activeJob.id}/download`}
            download
            className="text-[10px] font-semibold text-emerald-700 hover:underline dark:text-emerald-300"
          >
            Tải Final
          </a>
        )}
      </div>

      <div className="mt-2 flex gap-1.5 overflow-x-auto pb-1">
        {allChunkIds.map(chunkId => {
          const preview = previewById.get(chunkId);
          const isStale = stale.has(chunkId);
          const isBlocked = blocked.has(chunkId);
          const isReady = Boolean(preview) && !isStale;
          const isCurrent = currentChunk === chunkId && !isReady && !isStale && !isBlocked;
          const active = selectedChunkId === chunkId;
          const statusLabel = isStale
            ? 'STALE'
            : isBlocked
              ? 'QA BLOCK'
              : isReady
                ? 'READY'
                : isCurrent
                  ? currentPhase || 'PROCESSING'
                  : 'QUEUED';
          return (
            <button
              key={chunkId}
              type="button"
              disabled={!isReady}
              onClick={() => {
                if (!preview) return;
                setSelectedChunkId(chunkId);
                setSelectedPreview(preview);
                requestSeek(preview.start);
                setIsPlaying(false);
                window.localStorage.setItem(`vi_dubber_preview_${activeJob.id}`, chunkId);
              }}
              className={`min-w-[92px] rounded-lg border px-2 py-1 text-left transition ${
                active
                  ? 'border-orange-500 bg-orange-500/10'
                  : 'border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/70'
              } ${isReady ? 'cursor-pointer hover:border-orange-400' : 'cursor-default opacity-70'}`}
            >
              <span className="block text-[10px] font-semibold text-slate-700 dark:text-slate-200">
                {chunkId.replace('chunk_', '#')}
              </span>
              <span className="mt-0.5 flex items-center gap-1 text-[9px] text-slate-500 dark:text-slate-400">
                {isStale || isBlocked ? (
                  <AlertTriangle className="h-2.5 w-2.5" />
                ) : isReady ? (
                  <CheckCircle2 className="h-2.5 w-2.5" />
                ) : (
                  <LoaderCircle className="h-2.5 w-2.5 animate-spin" />
                )}
                {statusLabel}
              </span>
            </button>
          );
        })}
      </div>

      {selected && (
        <div className="mt-1.5 flex items-center gap-2 border-t border-slate-200 pt-1.5 dark:border-slate-800">
          <Play className="h-3.5 w-3.5 shrink-0 text-orange-500" />
          <span className="min-w-0 flex-1 truncate text-[10px] font-medium text-slate-700 dark:text-slate-200">
            Đang xem {selected.chunk_id.replace('chunk_', 'Part ')} trên monitor
          </span>
          <span className="hidden whitespace-nowrap text-[9px] text-slate-500 xl:inline">
            {formatSeconds(selected.start)}–{formatSeconds(selected.end)}
          </span>
          {selected.download_available === true ? (
            <a
              href={selected.download_url}
              download
              className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
              title="Tải preview chunk"
            >
              <Download className="h-3.5 w-3.5" />
            </a>
          ) : (
            <span
              className="rounded px-1 py-0.5 text-[9px] font-semibold text-slate-400"
              title="Artifact chưa được máy chủ xác minh"
            >
              Chưa thể tải
            </span>
          )}
        </div>
      )}

      {stale.size > 0 && (
        <div className="mt-1.5 flex items-center justify-between gap-2 text-[10px] text-amber-700 dark:text-amber-300">
          <span>{stale.size} chunk stale sau edit; preview cũ đã bị khóa.</span>
          <button
            type="button"
            onClick={requestRerender}
            disabled={rerendering}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 font-semibold hover:bg-amber-500/10 disabled:opacity-60"
          >
            <RotateCcw className={`h-3 w-3 ${rerendering ? 'animate-spin' : ''}`} />
            Render lại
          </button>
        </div>
      )}
    </section>
  );
};
