import { CatalogAvailability, CatalogResponse, CatalogStatusResponse, JobState, PreviewArtifact, Segment, SystemStatus } from '@/types';

const BASE_URL = '';
// A completed long-form job can return a multi-megabyte normalized review
// payload.  Two seconds is shorter than the local JSON serialization and
// browser parse time for real jobs (for example Job12), so keep this request
// timeout separate from the lightweight jobs/health polling budget.
const JOB_SEGMENTS_TIMEOUT_MS = 15_000;

type CatalogCacheEntry = { etag: string; data: CatalogResponse };
const catalogCache = new Map<string, CatalogCacheEntry>();
const MAX_CATALOG_CACHE_ENTRIES = 32;

export async function fetchBackendHealth(): Promise<boolean> {
  const res = await fetch(`${BASE_URL}/api/health`, { signal: AbortSignal.timeout(1500) });
  return res.ok;
}

export async function fetchJobs(): Promise<JobState[]> {
  const res = await fetch(`${BASE_URL}/api/jobs`, { signal: AbortSignal.timeout(2000) });
  if (!res.ok) throw new Error(`HTTP error ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : data.jobs || [];
}

export async function fetchCatalog(
  query = '',
  availability?: CatalogAvailability,
  limit = 100,
): Promise<CatalogResponse> {
  const params = new URLSearchParams({ query, limit: String(limit), offset: '0' });
  if (availability) params.set('availability', availability);
  const cacheKey = params.toString();
  const cached = catalogCache.get(cacheKey);
  const res = await fetch(`${BASE_URL}/api/catalog?${params.toString()}`, {
    headers: cached ? { 'If-None-Match': cached.etag } : undefined,
    signal: AbortSignal.timeout(3000),
  });
  if (res.status === 304) {
    if (!cached) throw new Error('Catalog returned 304 without a cached response');
    return cached.data;
  }
  if (!res.ok) throw new Error(`HTTP error ${res.status}`);
  const data = await res.json() as CatalogResponse;
  const etag = res.headers.get('ETag');
  if (etag) {
    catalogCache.delete(cacheKey);
    catalogCache.set(cacheKey, { etag, data });
    while (catalogCache.size > MAX_CATALOG_CACHE_ENTRIES) {
      const oldest = catalogCache.keys().next().value;
      if (oldest === undefined) break;
      catalogCache.delete(oldest);
    }
  }
  return data;
}

export async function fetchCatalogStatus(): Promise<CatalogStatusResponse> {
  const res = await fetch(`${BASE_URL}/api/catalog/status`, {
    signal: AbortSignal.timeout(3000),
  });
  if (!res.ok) throw new Error(`HTTP error ${res.status}`);
  return await res.json() as CatalogStatusResponse;
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const res = await fetch(`${BASE_URL}/api/system`, { signal: AbortSignal.timeout(6000) });
  if (!res.ok) throw new Error(`HTTP error ${res.status}`);
  return await res.json();
}

export async function fetchJobSegments(jobId: string): Promise<Segment[]> {
  const res = await fetch(`${BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/segments`, {
    signal: AbortSignal.timeout(JOB_SEGMENTS_TIMEOUT_MS),
  });
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
}

export async function fetchJobPreviews(jobId: string): Promise<PreviewArtifact[]> {
  const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/previews`, {
    signal: AbortSignal.timeout(3000),
  });
  if (!res.ok) throw new Error(`HTTP error ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : [];
}

export async function rerenderJob(jobId: string): Promise<boolean> {
  const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/rerender`, { method: 'POST' });
  if (!res.ok) throw new Error(`HTTP error ${res.status}`);
  return res.ok;
}

export async function updateSegmentReview(
  jobId: string,
  segmentId: number,
  payload: { text?: string; vi?: string; speaker?: string; review_status?: string }
): Promise<boolean> {
  const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/segments/${segmentId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`HTTP error ${res.status}`);
  return res.ok;
}

export async function requestJobControl(
  jobId: string,
  action: 'pause' | 'run' | 'cancel'
): Promise<boolean> {
  const res = await fetch(`${BASE_URL}/api/jobs/${jobId}/control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  if (!res.ok) throw new Error(`HTTP error ${res.status}`);
  return res.ok;
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
    throw err;
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
    throw err;
  }
}

export async function submitJob(formData: FormData): Promise<{ success: boolean; jobId?: string }> {
  const res = await fetch(`${BASE_URL}/api/jobs`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) throw new Error('Failed to create job');
  const data = await res.json();
  return { success: true, jobId: data.id || data.job_id };
}

