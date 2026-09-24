import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { StageDefinition } from '@/types';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatSeconds(seconds: number): string {
  if (isNaN(seconds) || seconds < 0) return '00:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 10);
  if (mins >= 60) {
    const hours = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hours}:${remMins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}.${ms}`;
}

export function formatDurationCompact(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

export function formatBytes(bytes: number, decimals = 1): string {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

export function formatRtf(rtf: number): string {
  if (!rtf || isNaN(rtf)) return '0.00x';
  return `${rtf.toFixed(2)}x`;
}

export const PIPELINE_STAGES: StageDefinition[] = [
  {
    id: 'prepare',
    index: 0,
    labelEn: 'Prepare & Audio Extract',
    labelVi: 'Chuẩn bị & Tách Audio',
    descriptionEn: 'Audio demuxing, sample rate normalization (24kHz), preflight safety gates',
    descriptionVi: 'Trích xuất audio, chuẩn hóa 24kHz, kiểm tra preflight an toàn',
    internalStageNames: ['prepare', 'preflight', 'input_extract', 'reference_selection'],
  },
  {
    id: 'separation',
    index: 1,
    labelEn: 'Vocal Separation',
    labelVi: 'Tách âm Vocals/BGM',
    descriptionEn: 'Demucs BS-RoFormer v2 separating vocals from background music & SFX',
    descriptionVi: 'Tách lời thoại khỏi nhạc nền & hiệu ứng âm thanh với BS-RoFormer',
    internalStageNames: ['separation', 'demucs'],
  },
  {
    id: 'asr',
    index: 2,
    labelEn: 'ASR & Diarization',
    labelVi: 'Nhận dạng giọng nói (ASR)',
    descriptionEn: 'WhisperX word-level timestamps & PyAnnote speaker clustering',
    descriptionVi: 'Nhận dạng mốc thời gian từng từ với WhisperX & phân tách người nói',
    internalStageNames: ['asr', 'diarization', 'segmentation'],
  },
  {
    id: 'translation',
    index: 3,
    labelEn: 'Neural Translation',
    labelVi: 'Dịch thuật Neural AI',
    descriptionEn: 'GPT-5.6 Sol / Claude with domain glossary & syllable length pre-fitting',
    descriptionVi: 'Dịch ngữ cảnh bằng GPT-5.6 Sol kèm glossary tài chính & khớp âm tiết',
    internalStageNames: ['translation', 'translation_prefit', 'rewrite_generation'],
  },
  {
    id: 'tts',
    index: 4,
    labelEn: 'TTS Voice Synthesis',
    labelVi: 'Tổng hợp giọng nói (TTS)',
    descriptionEn: 'Vietnamese neural voice synthesis with Kokoro / zero-shot voice cloning',
    descriptionVi: 'Tổng hợp giọng đọc tiếng Việt bằng Kokoro & voice clone',
    internalStageNames: ['tts', 'tts_pass_1', 'tts_rewrite'],
  },
  {
    id: 'mix_mux',
    index: 5,
    labelEn: 'Audio Mix & Ducking',
    labelVi: 'Hòa âm & Timing Mix',
    descriptionEn: 'Sidechain ducking, ITU-R BS.1770 -14 LUFS loudness mastering & mux',
    descriptionVi: 'Sidechain ducking nhạc nền, cân chỉnh âm lượng -14 LUFS & ghép video',
    internalStageNames: ['mix_mux', 'timing_assembly', 'timing_fit'],
  },
  {
    id: 'qa',
    index: 6,
    labelEn: 'QA & Acoustic Verification',
    labelVi: 'Kiểm định chất lượng (QA)',
    descriptionEn: 'TypeSafe semantic faithfulness, hallucination gate & acoustic SNR audit',
    descriptionVi: 'Xác thực ngữ nghĩa TypeSafe, chặn hallucination & kiểm tra SNR âm học',
    internalStageNames: ['qa', 'acoustic_qa', 'segment_qa', 'semantic_qa', 'semantic_qa_translated'],
  },
];

export function resolveActiveStageIndex(currentStageName: string, progress: number): number {
  if (!currentStageName) {
    return Math.min(6, Math.floor(progress * 7));
  }
  const name = currentStageName.toLowerCase();
  for (let i = 0; i < PIPELINE_STAGES.length; i++) {
    const stg = PIPELINE_STAGES[i];
    if (stg.id === name || stg.internalStageNames.some(sub => name.includes(sub))) {
      return i;
    }
  }
  if (name.includes('chu?n b?') || name.includes('chuan bi')) return 0;
  if (name.includes('tch') || name.includes('tach')) return 1;
  if (name.includes('asr') || name.includes('whisper')) return 2;
  if (name.includes('d?ch') || name.includes('dich')) return 3;
  if (name.includes('tts') || name.includes('synth')) return 4;
  if (name.includes('mix') || name.includes('timing')) return 5;
  if (name.includes('qa') || name.includes('complete') || name.includes('hoan tat')) return 6;
  return Math.min(6, Math.floor(progress * 7));
}
