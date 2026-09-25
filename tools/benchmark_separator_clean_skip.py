from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from difflib import SequenceMatcher
from importlib import metadata
from pathlib import Path
from typing import Any

import soundfile as sf
import yaml

from vi_dubber.asr import transcribe_and_align
from vi_dubber.media import extract_audio
from vi_dubber.runtime import configure_runtime
from vi_dubber.separation import separate_dialogue


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "work" / "benchmarks" / "fixtures.json"
DEFAULT_OUTPUT = ROOT / "work" / "benchmarks" / "p13-separator-clean-skip-20260925.json"
DEFAULT_WORK_DIR = ROOT / "work" / "benchmarks" / "p13-separator-clean-skip-20260925"
DEFAULT_FIXTURE_ID = "golden-clean-single-speaker-talking-head"
TIMING_TOLERANCE_SECONDS = 0.03
TEXT_SIMILARITY_GATE = 0.995
SPEED_GAIN_GATE = 0.10


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _fixture(manifest_path: Path, fixture_id: str) -> dict[str, Any]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixtures = data.get("fixtures") or []
    for fixture in fixtures:
        if fixture.get("id") == fixture_id:
            return fixture
    raise KeyError(f"Fixture not found: {fixture_id}")


def _write_silence_like(source: Path, target: Path) -> None:
    info = sf.info(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    zero_frame = [0.0] * info.channels if info.channels > 1 else 0.0
    chunk_frames = min(48_000, max(1, info.frames))
    with sf.SoundFile(
        target,
        mode="w",
        samplerate=info.samplerate,
        channels=info.channels,
        subtype="PCM_16",
        format="WAV",
    ) as output:
        remaining = info.frames
        while remaining > 0:
            count = min(chunk_frames, remaining)
            if info.channels == 1:
                output.write([zero_frame] * count)
            else:
                output.write([zero_frame] * count)
            remaining -= count


def _audio_info(path: Path) -> dict[str, Any]:
    info = sf.info(path)
    peak = 0.0
    rms_sum = 0.0
    sample_count = 0
    with sf.SoundFile(path) as handle:
        for block in handle.blocks(blocksize=48_000, dtype="float32", always_2d=True):
            if block.size == 0:
                continue
            block_abs = abs(block)
            peak = max(peak, float(block_abs.max()))
            rms_sum += float((block * block).sum())
            sample_count += int(block.size)
    rms = (rms_sum / sample_count) ** 0.5 if sample_count else 0.0
    return {
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "frames": info.frames,
        "duration_seconds": round(info.duration, 6),
        "peak": round(peak, 9),
        "rms": round(rms, 9),
        "sha256": _sha256(path),
    }


def _segment_summary(segments: list[Any]) -> dict[str, Any]:
    normalized_transcript = _normalize_text(" ".join(segment.text for segment in segments))
    words: list[dict[str, Any]] = []
    for segment in segments:
        for word in segment.words:
            if not word.text:
                continue
            words.append(
                {
                    "text": _normalize_text(word.text),
                    "start": word.start,
                    "end": word.end,
                }
            )
    return {
        "segment_count": len(segments),
        "word_count": len(words),
        "normalized_transcript": normalized_transcript,
        "words": words,
    }


def _compare_asr(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    transcript_similarity = SequenceMatcher(
        None,
        baseline["normalized_transcript"],
        candidate["normalized_transcript"],
    ).ratio()
    baseline_word_text = " ".join(word["text"] for word in baseline["words"])
    candidate_word_text = " ".join(word["text"] for word in candidate["words"])
    word_text_similarity = SequenceMatcher(None, baseline_word_text, candidate_word_text).ratio()

    exact_word_sequence = [word["text"] for word in baseline["words"]] == [
        word["text"] for word in candidate["words"]
    ]
    timing_deltas: list[float] = []
    if exact_word_sequence:
        for base_word, candidate_word in zip(baseline["words"], candidate["words"], strict=True):
            for key in ("start", "end"):
                base_value = base_word[key]
                candidate_value = candidate_word[key]
                if base_value is not None and candidate_value is not None:
                    timing_deltas.append(abs(float(base_value) - float(candidate_value)))
    max_timing_delta = max(timing_deltas, default=None)
    timing_gate = bool(
        exact_word_sequence
        and max_timing_delta is not None
        and max_timing_delta <= TIMING_TOLERANCE_SECONDS
    )
    return {
        "transcript_similarity": round(transcript_similarity, 9),
        "word_text_similarity": round(word_text_similarity, 9),
        "exact_word_sequence": exact_word_sequence,
        "max_aligned_word_timing_delta_seconds": (
            round(max_timing_delta, 6) if max_timing_delta is not None else None
        ),
        "timing_tolerance_seconds": TIMING_TOLERANCE_SECONDS,
        "timing_gate_pass": timing_gate,
        "quality_gate_pass": bool(
            transcript_similarity >= TEXT_SIMILARITY_GATE
            and word_text_similarity >= TEXT_SIMILARITY_GATE
            and timing_gate
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    configure_runtime()
    manifest_path = args.manifest.resolve()
    fixture = _fixture(manifest_path, args.fixture_id)
    categories = [str(value) for value in fixture.get("categories") or []]
    if not any("clean" in value.lower() for value in categories):
        raise RuntimeError(
            "Clean-speech skip benchmark requires an explicitly clean fixture category; "
            f"got {categories!r}"
        )

    source = fixture.get("source") or {}
    source_path = (ROOT / str(source.get("path"))).resolve()
    expected_source_sha = str(source.get("sha256") or "")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    actual_source_sha = _sha256(source_path)
    if expected_source_sha and actual_source_sha != expected_source_sha:
        raise RuntimeError(
            f"Fixture SHA mismatch: expected {expected_source_sha}, got {actual_source_sha}"
        )

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    separation_config = dict(config.get("separation") or {})
    asr_config = dict(config.get("asr") or {})
    model_filename = str(separation_config.get("model") or "")
    if not model_filename:
        raise RuntimeError("config.yaml separation.model is missing")

    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    original_audio = work_dir / "original.wav"
    extract_audio(source_path, original_audio, sample_rate=48_000)

    baseline_dir = work_dir / "baseline-bs-roformer"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        torch = None  # type: ignore[assignment]

    baseline_started = time.perf_counter()
    baseline_vocals, baseline_background = separate_dialogue(
        original_audio,
        baseline_dir,
        ROOT / "models" / "separator",
        model_filename,
    )
    baseline_separator_seconds = time.perf_counter() - baseline_started

    peak_allocated = None
    peak_reserved = None
    if torch is not None and torch.cuda.is_available():
        peak_allocated = torch.cuda.max_memory_allocated() / (1024 * 1024)
        peak_reserved = torch.cuda.max_memory_reserved() / (1024 * 1024)

    candidate_background = work_dir / "candidate-skip-background.wav"
    candidate_started = time.perf_counter()
    _write_silence_like(original_audio, candidate_background)
    candidate_prep_seconds = time.perf_counter() - candidate_started
    candidate_vocals = original_audio

    baseline_asr_started = time.perf_counter()
    baseline_segments = transcribe_and_align(baseline_vocals, asr_config)
    baseline_asr_seconds = time.perf_counter() - baseline_asr_started

    candidate_asr_started = time.perf_counter()
    candidate_segments = transcribe_and_align(candidate_vocals, asr_config)
    candidate_asr_seconds = time.perf_counter() - candidate_asr_started

    baseline_summary = _segment_summary(baseline_segments)
    candidate_summary = _segment_summary(candidate_segments)
    comparison = _compare_asr(baseline_summary, candidate_summary)

    speed_gain_fraction = (
        (baseline_separator_seconds - candidate_prep_seconds) / baseline_separator_seconds
        if baseline_separator_seconds > 0
        else 0.0
    )
    speed_gate_pass = speed_gain_fraction >= SPEED_GAIN_GATE
    candidate_audio = _audio_info(candidate_vocals)
    original_audio_info = _audio_info(original_audio)
    candidate_background_info = _audio_info(candidate_background)
    identity_gate_pass = candidate_audio["sha256"] == original_audio_info["sha256"]
    silence_gate_pass = candidate_background_info["peak"] == 0.0

    benchmark_gate_pass = bool(
        comparison["quality_gate_pass"]
        and speed_gate_pass
        and identity_gate_pass
        and silence_gate_pass
    )

    model_path = ROOT / "models" / "separator" / model_filename
    return {
        "schema": "vi-dubber-p13-separator-clean-skip-v1",
        "fixture": {
            "id": args.fixture_id,
            "categories": categories,
            "source_path": str(source_path),
            "source_sha256": actual_source_sha,
        },
        "production_baseline": {
            "package": "audio-separator",
            "package_version": metadata.version("audio-separator"),
            "model": model_filename,
            "model_sha256": _sha256(model_path),
            "separator_wall_seconds": round(baseline_separator_seconds, 6),
            "peak_cuda_allocated_mib": round(peak_allocated, 3) if peak_allocated is not None else None,
            "peak_cuda_reserved_mib": round(peak_reserved, 3) if peak_reserved is not None else None,
            "vocals": _audio_info(baseline_vocals),
            "background": _audio_info(baseline_background),
            "asr_wall_seconds": round(baseline_asr_seconds, 6),
            "asr": baseline_summary,
        },
        "candidate_oracle_clean_skip": {
            "implementation": "reuse extracted original.wav as vocals and synthesize silent background",
            "prep_wall_seconds": round(candidate_prep_seconds, 6),
            "vocals": candidate_audio,
            "background": candidate_background_info,
            "asr_wall_seconds": round(candidate_asr_seconds, 6),
            "asr": candidate_summary,
        },
        "gates": {
            "text_similarity_min": TEXT_SIMILARITY_GATE,
            "timing_tolerance_seconds": TIMING_TOLERANCE_SECONDS,
            "speed_gain_min_fraction": SPEED_GAIN_GATE,
            "asr_comparison": comparison,
            "speed_gain_fraction": round(speed_gain_fraction, 9),
            "speed_gate_pass": speed_gate_pass,
            "candidate_vocals_identity_gate_pass": identity_gate_pass,
            "candidate_background_silence_gate_pass": silence_gate_pass,
            "benchmark_gate_pass": benchmark_gate_pass,
            "detector_false_positive_gate_pass": False,
            "production_default_change_allowed": False,
        },
        "decision": (
            "PROMISING_BENCHMARK_ONLY"
            if benchmark_gate_pass
            else "REJECT_ORACLE_SKIP_ON_CURRENT_GATE"
        ),
        "limitations": [
            "This is an oracle-label clean-speech benchmark; it does not implement or validate an automatic detector.",
            "The clean fixture is one real P17 source. It is not representative evidence for music, SFX, reverb, male/female voice diversity, or dialogue overlap.",
            "No production config or separator default is changed by this benchmark.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P13 BS-RoFormer vs clean-speech skip A/B")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--fixture-id", default=DEFAULT_FIXTURE_ID)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["gates"], ensure_ascii=False, indent=2))
    print(f"decision={result['decision']}")
    print(f"receipt={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
