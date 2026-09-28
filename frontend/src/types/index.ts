export type JobStatus =
  | 'queued'
  | 'running'
  | 'paused'
  | 'failed'
  | 'cancelled'
  | 'completed';

export type ProfileType = 'fast' | 'balanced_fast' | 'balanced_best' | 'max_quality';

export type StageId =
  | 'prepare'
  | 'separation'
  | 'asr'
  | 'translation'
  | 'tts'
  | 'mix_mux'
  | 'qa';

export interface StageDefinition {
  id: StageId;
  index: number;
  labelEn: string;
  labelVi: string;
  descriptionEn: string;
  descriptionVi: string;
  internalStageNames: string[];
}

export interface WordToken {
  text: string;
  start?: number | null;
  end?: number | null;
  confidence?: number | null;
  speaker?: string | null;
  overlap?: boolean;
}

export type ReviewStatus = 'unreviewed' | 'needs_review' | 'reviewed' | 'accepted';

export interface Segment {
  id: number;
  start: number;
  end: number;
  text: string; // English source
  source_en?: string;
  en?: string;
  vi: string; // Vietnamese dub
  selected_vi?: string;
  translated_vi?: string;
  speaker: string;
  words?: WordToken[];
  review_status: ReviewStatus;
  rewritten?: boolean;
  overflow?: boolean;
  semantic_flag?: boolean;
  speaker_overlap?: boolean;
  multi_speaker?: boolean;
  flagged?: boolean;
  avg_logprob?: number;
  target_duration?: number;
  actual_duration?: number;
  source_audio_url?: string;
  dub_audio_url?: string;
}

export interface JobMetadata {
  input_name: string;
  input_path?: string;
  youtube_url?: string;
  output?: string;
  profile: ProfileType;
  translation_model: string;
  translation_model_display_name?: string;
  translation_effort?: string;
  translation_provider?: string;
  voice_ref?: string;
  source_mode: 'YouTube' | 'Local' | string;
  source_sha256?: string;
  diarization?: string | boolean;
  config_path?: string;
  longform?: LongformMetadata;
}

export interface LongformMetadata {
  enabled?: boolean;
  phase?: string;
  current_chunk?: string;
  completed_chunks?: number;
  completed_tts_chunks?: number;
  total_chunks?: number;
  eta_seconds?: number | null;
  throughput_media_seconds_per_wall_second?: number;
  queue?: {
    asr_prefetch_max_pending?: number;
    gpu_asr_concurrency?: number;
    [key: string]: number | undefined;
  };
  preview_ready_chunks?: string[];
  preview_stale_chunks?: string[];
  preview_blocked_chunks?: string[];
  preview_total_chunks?: number;
  preview_policy_version?: number;
}

export interface PreviewArtifact {
  version: number;
  kind: 'preview';
  label: 'PREVIEW';
  final: false;
  stale: false;
  chunk_id: string;
  index: number;
  start: number;
  end: number;
  duration: number;
  qa_status?: string;
  artifact: string;
  manifest_fingerprint: string;
  play_url: string;
  download_url: string;
  download_available: boolean;
}

export interface MixTelemetry {
  integrated_lufs: number;
  true_peak_db: number;
  loudness_delta_lu: number;
  loudness_range_lu: number;
  passes_loudness: boolean;
  passes_true_peak: boolean;
  target_lufs: number;
  target_true_peak_db: number;
  clipping_risk?: boolean;
}

export interface QaTelemetry {
  similarity: number;
  threshold: number;
  passed: boolean;
  global_passed?: boolean | null;
  mode?: string;
  full_track_skipped?: boolean;
  segment_summary?: {
    segments_checked: number;
    initial_failed: number;
    repairs_attempted: number;
    repairs_completed: number;
    final_failed: number;
    passed: boolean;
  };
}

export interface SemanticQaTelemetry {
  mode: string;
  translated_status: string;
  rewritten_status: string;
  translated_checked: number;
  translated_needs_review: number;
}

export interface JobResult {
  duration_seconds: number;
  elapsed_seconds: number;
  real_time_factor: number;
  segments: number;
  speaker_count: number;
  speakers: string[];
  overflow_segments: number;
  rewritten_segments: number;
  cloned_segments: number;
  average_tempo: number;
  max_tempo: number;
  output: string;
  subtitle?: string;
  mix: MixTelemetry;
  qa: QaTelemetry;
  semantic_qa: SemanticQaTelemetry;
  previews?: PreviewArtifact[];
  voice_track?: {
    peak: number;
    peak_dbfs: number;
    clipping_ratio: number;
  };
  translation?: {
    model: string;
    provider: string;
    prefit_rewrite_calls?: number;
    rewrite_calls?: number;
    webgpt_attempts?: number;
  };
}

export interface StageTelemetry {
  wall_seconds: number;
  calls: number;
  failed_calls: number;
}

export interface JobMetrics {
  schema_version?: number;
  status?: string;
  total_wall_seconds: number;
  stages: Record<string, StageTelemetry>;
  counters: {
    source_segments?: number;
    speech_turns?: number;
    translation_calls?: number;
    webgpt_batches?: number;
    rewrite_calls?: number;
    typesafe_requests?: number;
    typesafe_tokens?: number;
    tts_inferences?: number;
    tts_batches?: number;
    cache_hits?: number;
    cache_misses?: number;
    qa_repairs?: number;
    webgpt_attempts?: number;
    webgpt_failures?: number;
  };
  resources: {
    torch_cuda_allocator?: {
      available: boolean;
      device_name: string;
      device_index?: number;
      allocated_bytes: number;
      reserved_bytes: number;
      peak_allocated_bytes: number;
      peak_reserved_bytes: number;
    };
  };
}

export interface JobState {
  id: string;
  created_at: string;
  updated_at: string;
  status: JobStatus;
  stage: string;
  progress: number; // 0.0 to 1.0
  message: string;
  metadata: JobMetadata;
  result?: JobResult;
  metrics?: JobMetrics;
  error?: Record<string, any>;
}

export type CatalogAvailability = 'available' | 'missing' | 'stale' | 'failed' | 'pending' | 'unknown';

export interface CatalogItem {
  item_id: string;
  title: string;
  availability: CatalogAvailability;
  source: { fingerprint: string; ref: string };
  lineage: { revision: string; segment_count: number };
  job: {
    source_name?: string;
    job_status?: string;
    job_stage?: string;
    progress?: number;
    source_size_bytes?: number;
  };
  review: {
    revision: string;
    review_state: string;
    watch_position_seconds: number;
    bookmarks: Array<{ segment_index: number; position_seconds: number }>;
  };
}

export interface CatalogResponse {
  format: string;
  schema_version: number;
  catalog_status: 'ready' | 'unavailable' | 'unsupported' | string;
  query: string;
  availability_filter: CatalogAvailability | null;
  pagination: { limit: number; offset: number; returned: number };
  items: CatalogItem[];
  counts: { returned: number; availability: Record<string, number> };
  metadata_only: true;
}

export interface SystemStatus {
  gpu_name: string;
  gpu_vram_used_bytes: number;
  gpu_vram_total_bytes: number;
  gpu_utilization_pct: number;
  cpu_utilization_pct: number;
  active_jobs_count: number;
  webgpt_connected: boolean;
  webgpt_model: string;
  webgpt_port: number;
  server_uptime_seconds: number;
  websocket_connected: boolean;
}

export type AppMode = 'standard' | 'engineer';
export type AppTheme = 'dark' | 'light';
export type Language = 'en' | 'vi';
