import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { JobState, Segment, SystemStatus } from '@/types';
import { fetchJobs, fetchSystemStatus, fetchJobSegments, updateSegmentReview, requestJobControl } from '@/lib/api';
import { MOCK_JOBS, MOCK_SEGMENTS, MOCK_SYSTEM_STATUS } from '@/lib/mockData';

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
  isPlaying: boolean;
  setIsPlaying: (playing: boolean) => void;
  selectedAudioTrack: 'a' | 'b' | 'bgm';
  setSelectedAudioTrack: (track: 'a' | 'b' | 'bgm') => void;
  systemStatus: SystemStatus;
  loading: boolean;
  updateSegment: (segmentId: number, changes: Partial<Segment>) => Promise<void>;
  acceptSegment: (segmentId: number) => Promise<void>;
  rerenderSegment: (segmentId: number) => Promise<void>;
  refreshJobs: () => Promise<void>;
  controlJob: (action: 'pause' | 'run' | 'cancel') => Promise<void>;
  isRawJsonOpen: boolean;
  setIsRawJsonOpen: (open: boolean) => void;
  isCreatorOpen: boolean;
  setIsCreatorOpen: (open: boolean) => void;
}

const JobContext = createContext<JobContextType | undefined>(undefined);

export const JobProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [jobs, setJobs] = useState<JobState[]>(MOCK_JOBS);
  const [activeJobId, setActiveJobId] = useState<string>(MOCK_JOBS[0].id);
  const [segments, setSegments] = useState<Segment[]>(MOCK_SEGMENTS);
  const [activeSegmentIndex, setActiveSegmentIndex] = useState<number>(0);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [selectedAudioTrack, setSelectedAudioTrack] = useState<'a' | 'b' | 'bgm'>('b');
  const [systemStatus, setSystemStatus] = useState<SystemStatus>(MOCK_SYSTEM_STATUS);
  const [loading, setLoading] = useState<boolean>(false);
  const [isRawJsonOpen, setIsRawJsonOpen] = useState<boolean>(false);
  const [isCreatorOpen, setIsCreatorOpen] = useState<boolean>(false);

  const activeJob = jobs.find(j => j.id === activeJobId) || jobs[0] || null;

  // Poll jobs & system status
  const refreshJobs = useCallback(async () => {
    try {
      const [jobsData, sysData] = await Promise.all([
        fetchJobs(),
        fetchSystemStatus(),
      ]);
      if (jobsData && jobsData.length > 0) {
        setJobs(jobsData);
      }
      if (sysData) {
        setSystemStatus(sysData);
      }
    } catch (err) {
      console.error('Failed refreshing jobs:', err);
    }
  }, []);

  // Initial and recurring poll
  useEffect(() => {
    setLoading(true);
    refreshJobs().finally(() => setLoading(false));
    const interval = setInterval(refreshJobs, 2000);
    return () => clearInterval(interval);
  }, [refreshJobs]);

  // Load segments when activeJob changes
  useEffect(() => {
    if (!activeJobId) return;
    let isMounted = true;
    fetchJobSegments(activeJobId).then(segs => {
      if (isMounted && segs) {
        setSegments(segs);
      }
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
        isPlaying,
        setIsPlaying,
        selectedAudioTrack,
        setSelectedAudioTrack,
        systemStatus,
        loading,
        updateSegment,
        acceptSegment,
        rerenderSegment,
        refreshJobs,
        controlJob,
        isRawJsonOpen,
        setIsRawJsonOpen,
        isCreatorOpen,
        setIsCreatorOpen,
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
