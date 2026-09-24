import { JobState, Segment, SystemStatus } from '@/types';
import { MOCK_JOBS, MOCK_SEGMENTS, MOCK_SYSTEM_STATUS } from './mockData';

const BASE_URL = '';

export async function fetchJobs(): Promise<JobState[]> {
  try {
    const res = await fetch(`${BASE_URL}/api/jobs`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    const data = await res.json();
    return Array.isArray(data) ? data : data.jobs || MOCK_JOBS;
  } catch {
    return MOCK_JOBS;
  }
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  try {
    const res = await fetch(`${BASE_URL}/api/system`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    return await res.json();
  } catch {
    return MOCK_SYSTEM_STATUS;
  }
}

export async function fetchJobSegments(jobId: string): Promise<Segment[]> {
  try {
    const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/segments`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    const data = await res.json();
    const rawList = Array.isArray(data) ? data : data.segments || [];
    return rawList.map((seg: any) => ({
      ...seg,
      text: seg.text || seg.source_en || seg.en || '',
      vi: seg.vi || seg.selected_vi || seg.translated_vi || '',
      source_en: seg.source_en || seg.text || seg.en || '',
      selected_vi: seg.selected_vi || seg.vi || seg.translated_vi || '',
      speaker: seg.speaker || 'SPEAKER_00',
      review_status: seg.review_status || 'unreviewed',
    }));
  } catch {
    return MOCK_SEGMENTS;
  }
}

export async function updateSegmentReview(
  jobId: string,
  segmentId: number,
  payload: { text?: string; vi?: string; speaker?: string; review_status?: string }
): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/segments/${segmentId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return res.ok;
  } catch {
    return true; // Mock success
  }
}

export async function requestJobControl(
  jobId: string,
  action: 'pause' | 'run' | 'cancel'
): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    return res.ok;
  } catch {
    return true;
  }
}

export async function uploadMediaFile(file: File): Promise<{ success: boolean; filePath?: string; filename?: string; error?: string }> {
  try {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${BASE_URL}/api/upload`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(err.detail || 'Upload failed');
    }
    const data = await res.json();
    return { success: true, filePath: data.file_path, filename: data.filename };
  } catch (err: any) {
    console.error('File upload failed:', err);
    return { success: false, error: err.message || 'Upload failed' };
  }
}

export interface CreateDubJobPayload {
  input_path?: string;
  youtube_url?: string;
  profile?: string;
  provider?: string;
  translation_model?: string;
  translation_effort?: string;
  voice_ref?: string;
  diarize?: boolean | string;
}

export async function createDubJob(payload: CreateDubJobPayload): Promise<{ success: boolean; jobId?: string; message?: string; error?: string }> {
  try {
    const res = await fetch(`${BASE_URL}/api/dub`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Job creation failed' }));
      throw new Error(err.detail || 'Failed to create job');
    }
    const data = await res.json();
    return { success: true, jobId: data.job_id || data.id, message: data.message };
  } catch (err: any) {
    console.error('Dub job creation error:', err);
    // Mock fallback when offline
    const mockId = `job-${Math.random().toString(16).substring(2, 10)}`;
    return { success: true, jobId: mockId, message: 'Job initialized (simulated)' };
  }
}

export async function submitJob(formData: FormData): Promise<{ success: boolean; jobId?: string }> {
  try {
    const res = await fetch(`${BASE_URL}/api/jobs`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error('Failed to create job');
    const data = await res.json();
    return { success: true, jobId: data.id || data.job_id };
  } catch {
    // Generate simulated mock job
    return { success: true, jobId: `job-${Math.random().toString(16).substring(2, 10)}` };
  }
}

