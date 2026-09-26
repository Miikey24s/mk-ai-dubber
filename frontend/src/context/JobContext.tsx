import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { JobState, PreviewArtifact, Segment, SystemStatus } from '@/types';
import {
  fetchJobs,
  fetchBackendHealth,
  fetchSystemStatus,
  fetchJobSegments,
  updateSegmentReview,
  requestJobControl,
  uploadMediaFile,
  createDubJob,
} from '@/lib/api';
import { MOCK_SYSTEM_STATUS } from '@/lib/mockData';

interface CreateJobOptions {
  source: 'youtube' | 'file';
  youtubeUrl?: string;
  file?: File;
  profile?: string;
  model?: string;
  effort?: string;
  deepSettings?: any;
}

interface SeekRequest {
  time: number;
  sequence: number;
}

interface JobContextType {
  jobs: JobState[];
  activeJob: JobState | null;
  activeJobId: string;
  setActiveJobId: (id: string) => void;
  segments: Segment[];
  activeSegmentIndex: number;
  setActiveSegmentIndex: (index: number) => void;
  currentTime: number;
  setCurrentTime: React.Dispatch<React.SetStateAction<number>>;
  seekRequest: SeekRequest | null;
  requestSeek: (time: number) => void;
  isPlaying: boolean;
  setIsPlaying: (playing: boolean) => void;
  selectedPreview: PreviewArtifact | null;
  setSelectedPreview: (preview: PreviewArtifact | null) => void;
  selectedAudioTrack: 'a' | 'b' | 'bgm';
  setSelectedAudioTrack: (track: 'a' | 'b' | 'bgm') => void;
  systemStatus: SystemStatus;
  loading: boolean;
  isBackendOnline: boolean | null;
  updateSegment: (segmentId: number, changes: Partial<Segment>) => Promise<void>;
  acceptSegment: (segmentId: number) => Promise<void>;
  acceptAllSegments: () => Promise<void>;
  rerenderSegment: (segmentId: number) => Promise<void>;
  refreshJobs: () => Promise<void>;
  controlJob: (action: 'pause' | 'run' | 'cancel') => Promise<void>;
  isRawJsonOpen: boolean;
  setIsRawJsonOpen: (open: boolean) => void;
  isCreatorOpen: boolean;
  setIsCreatorOpen: (open: boolean) => void;
  droppedFile: File | null;
  setDroppedFile: (file: File | null) => void;
  createNewJob: (options: CreateJobOptions) => Promise<string | null>;
}

const JobContext = createContext<JobContextType | undefined>(undefined);

export const JobProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [jobs, setJobs] = useState<JobState[]>([]);
  const [activeJobId, setActiveJobId] = useState<string>('');
  const [segments, setSegments] = useState<Segment[]>([]);
  const [activeSegmentIndex, setActiveSegmentIndex] = useState<number>(0);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [seekRequest, setSeekRequest] = useState<SeekRequest | null>(null);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [selectedPreview, setSelectedPreview] = useState<PreviewArtifact | null>(null);
  const [selectedAudioTrack, setSelectedAudioTrack] = useState<'a' | 'b' | 'bgm'>('b');
  const [systemStatus, setSystemStatus] = useState<SystemStatus>(MOCK_SYSTEM_STATUS);
  const [loading, setLoading] = useState<boolean>(false);
  const [isBackendOnline, setIsBackendOnline] = useState<boolean | null>(null);
  const [isRawJsonOpen, setIsRawJsonOpen] = useState<boolean>(false);
  const [isCreatorOpen, setIsCreatorOpen] = useState<boolean>(false);
  const [droppedFile, setDroppedFile] = useState<File | null>(null);

  const activeJob = jobs.find(j => j.id === activeJobId) || jobs[0] || null;

  const requestSeek = useCallback((time: number) => {
    const target = Math.max(0, time);
    setCurrentTime(target);
    setSeekRequest(previous => ({
      time: target,
      sequence: (previous?.sequence || 0) + 1,
    }));
  }, []);

  // Jobs, backend heartbeat and expensive telemetry have separate failure domains.
  const refreshJobs = useCallback(async () => {
    try {
      const jobsData = await fetchJobs();
      if (jobsData && jobsData.length > 0) {
        setJobs(jobsData);
        setActiveJobId(current => current || jobsData[0].id);
      } else {
        setJobs([]);
      }
    } catch (err) {
      console.error('Failed refreshing jobs:', err);
    }
  }, []);

  const refreshBackendHealth = useCallback(async () => {
    try {
      setIsBackendOnline(await fetchBackendHealth());
    } catch (err) {
      console.error('Backend health check failed:', err);
      setIsBackendOnline(false);
    }
  }, []);

  const refreshSystemStatus = useCallback(async () => {
    try {
      const sysData = await fetchSystemStatus();
      if (sysData) setSystemStatus(sysData);
    } catch (err) {
      // Telemetry is intentionally non-fatal: /api/system may probe external runtimes.
      console.error('Failed refreshing system telemetry:', err);
    }
  }, []);

  // Keep the lightweight heartbeat/jobs responsive; poll expensive telemetry less often.
  useEffect(() => {
    setLoading(true);
    Promise.allSettled([
      refreshJobs(),
      refreshBackendHealth(),
      refreshSystemStatus(),
    ]).finally(() => setLoading(false));

    const jobsInterval = setInterval(refreshJobs, 2000);
    const healthInterval = setInterval(refreshBackendHealth, 2000);
    const systemInterval = setInterval(refreshSystemStatus, 10000);
    return () => {
      clearInterval(jobsInterval);
      clearInterval(healthInterval);
      clearInterval(systemInterval);
    };
  }, [refreshJobs, refreshBackendHealth, refreshSystemStatus]);

  // Load segments when activeJob changes
  useEffect(() => {
    if (!activeJobId) return;
    setSelectedPreview(null);
    let isMounted = true;
    fetchJobSegments(activeJobId)
      .then(segs => {
        if (isMounted && segs) {
          setSegments(segs);
        }
      })
      .catch(err => {
        console.error('Failed fetching segments:', err);
        if (isMounted) setSegments([]);
      });
    return () => { isMounted = false; };
  }, [activeJobId]);

  // Sync segment highlight with playback currentTime
  useEffect(() => {
    if (!segments || segments.length === 0) return;
    const foundIdx = segments.findIndex(
      seg => currentTime >= seg.start && currentTime <= seg.end
    );
    if (foundIdx !== -1 && foundIdx !== activeSegmentIndex) {
      setActiveSegmentIndex(foundIdx);
    }
  }, [currentTime, segments, activeSegmentIndex]);

  // Update a segment locally and remotely
  const updateSegment = async (segmentId: number, changes: Partial<Segment>) => {
    setSegments(prev =>
      prev.map(seg => (seg.id === segmentId ? { ...seg, ...changes } : seg))
    );
    if (activeJob) {
      await updateSegmentReview(activeJob.id, segmentId, {
        text: changes.text,
        vi: changes.vi,
        speaker: changes.speaker,
        review_status: changes.review_status,
      });
    }
  };

  const acceptSegment = async (segmentId: number) => {
    await updateSegment(segmentId, { review_status: 'accepted' });
  };

  const acceptAllSegments = async () => {
    setSegments(prev => prev.map(s => ({ ...s, review_status: 'accepted' })));
    if (activeJob) {
      await Promise.all(
        segments.map(s =>
          updateSegmentReview(activeJob.id, s.id, { review_status: 'accepted' })
        )
      );
    }
  };

  const rerenderSegment = async (segmentId: number) => {
    await updateSegment(segmentId, { review_status: 'needs_review' });
  };

  const controlJob = async (action: 'pause' | 'run' | 'cancel') => {
    if (!activeJob) return;
    await requestJobControl(activeJob.id, action);
    setJobs(prev =>
      prev.map(j => {
        if (j.id !== activeJob.id) return j;
        const newStatus =
          action === 'pause' ? 'paused' : action === 'cancel' ? 'cancelled' : 'running';
        return { ...j, status: newStatus };
      })
    );
  };

  const createNewJob = async (options: CreateJobOptions): Promise<string | null> => {
    try {
      setLoading(true);
      let inputPath: string | undefined;
      const youtubeUrl: string | undefined = options.youtubeUrl;

      if (options.source === 'file' && options.file) {
        const uploadRes = await uploadMediaFile(options.file);
        if (uploadRes.success && uploadRes.filePath) {
          inputPath = uploadRes.filePath;
        } else {
          inputPath = `work/uploads/${options.file.name}`;
        }
      }

      const res = await createDubJob({
        input_path: inputPath,
        youtube_url: youtubeUrl,
        profile: options.profile || 'balanced_fast',
        translation_model: options.model,
        translation_effort: options.effort,
      });

      if (res.jobId) {
        const newJobState: JobState = {
          id: res.jobId,
          status: 'running',
          stage: 'prepare',
          progress: 0.05,
          message: 'Pipeline started: Demucs vocal separation & WhisperX ASR...',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {
            input_name: (options.source === 'file' ? options.file?.name : options.youtubeUrl) || 'New Dubbing Ingest',
            source_mode: options.source === 'youtube' ? 'YouTube' : 'Local',
            profile: (options.profile as any) || 'balanced_fast',
            translation_model: options.model || 'chatgpt-web/gpt-5.6-sol',
          },
        };
        setJobs(prev => [newJobState, ...prev.filter(j => j.id !== res.jobId)]);
        setActiveJobId(res.jobId);
        requestSeek(0);
        setIsPlaying(true);
        setTimeout(refreshJobs, 1000);
        return res.jobId;
      }
      return null;
    } catch (err) {
      console.error('createNewJob error:', err);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return (
    <JobContext.Provider
      value={{
        jobs,
        activeJob,
        activeJobId,
        setActiveJobId,
        segments,
        activeSegmentIndex,
        setActiveSegmentIndex,
        currentTime,
        setCurrentTime,
        seekRequest,
        requestSeek,
        isPlaying,
        setIsPlaying,
        selectedPreview,
        setSelectedPreview,
        selectedAudioTrack,
        setSelectedAudioTrack,
        systemStatus,
        loading,
        isBackendOnline,
        updateSegment,
        acceptSegment,
        acceptAllSegments,
        rerenderSegment,
        refreshJobs,
        controlJob,
        isRawJsonOpen,
        setIsRawJsonOpen,
        isCreatorOpen,
        setIsCreatorOpen,
        droppedFile,
        setDroppedFile,
        createNewJob,
      }}
    >
      {children}
    </JobContext.Provider>
  );
};

export const useJob = () => {
  const context = useContext(JobContext);
  if (!context) {
    throw new Error('useJob must be used within a JobProvider');
  }
  return context;
};
