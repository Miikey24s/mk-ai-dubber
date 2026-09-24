from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from vi_dubber.runtime import MODELS_DIR, configure_runtime


REQUIRED_FIXTURE_CATEGORIES = frozenset(
    {
        "clean-single-speaker-talking-head",
        "long-monologue",
        "fast-english-speech",
        "short-fragmented-subtitle-style-speech",
        "music-under-dialogue",
        "noisy-speech",
        "two-speakers",
        "overlapping-speech",
        "names-numbers-technical-terms",
        "emotional-prosody-stress",
    }
)


DEFAULT_MANIFEST = Path("work/benchmarks/fixtures.json")
DEFAULT_OUTPUT = Path("work/benchmarks/p13-whisperx-full-p17.json")
DEFAULT_LONG_MONOLOGUE_FALLBACK = Path(
    "work/Trading_Strategies_That_Work__full_training_-7a8ee35de0/original.wav"
)


@dataclass(frozen=True)
class FixtureInput:
    fixture_id: str
    categories: tuple[str, ...]
    path: Path
    source_kind: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _segment_signature(result: dict[str, Any]) -> list[dict[str, Any]]:
    signature: list[dict[str, Any]] = []
    for item in result.get("segments", []):
        if not isinstance(item, dict):
            continue
        signature.append(
            {
                "start": round(float(item.get("start", 0.0)), 3),
                "end": round(float(item.get("end", 0.0)), 3),
                "text": _normalize_text(str(item.get("text", ""))),
            }
        )
    return signature


def _aligned_word_signature(result: dict[str, Any]) -> list[dict[str, Any]]:
    signature: list[dict[str, Any]] = []
    for segment in result.get("segments", []):
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            text = str(word.get("word", word.get("text", ""))).strip()
            if not text:
                continue
            start = word.get("start")
            end = word.get("end")
            signature.append(
                {
                    "start": None if start is None else round(float(start), 3),
                    "end": None if end is None else round(float(end), 3),
                    "text": _normalize_text(text),
                }
            )
    return signature


def _signatures_match(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    *,
    tolerance_seconds: float,
) -> bool:
    if len(baseline) != len(candidate):
        return False
    for left, right in zip(baseline, candidate, strict=True):
        if left["text"] != right["text"]:
            return False
        for key in ("start", "end"):
            left_value = left[key]
            right_value = right[key]
            if left_value is None or right_value is None:
                if left_value != right_value:
                    return False
                continue
            if math.fabs(float(left_value) - float(right_value)) > tolerance_seconds:
                return False
    return True


def _parse_category_overrides(values: Iterable[str], root: Path) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for raw in values:
        category, separator, path_text = raw.partition("=")
        if not separator or not category.strip() or not path_text.strip():
            raise ValueError(f"invalid --category-source value: {raw!r}")
        path = Path(path_text.strip())
        overrides[category.strip()] = path if path.is_absolute() else root / path
    return overrides


def select_fixture_inputs(
    manifest: dict[str, Any],
    *,
    root: Path,
    category_overrides: dict[str, Path] | None = None,
) -> tuple[list[FixtureInput], list[str]]:
    overrides = category_overrides or {}
    required = set(REQUIRED_FIXTURE_CATEGORIES)
    selected: dict[Path, FixtureInput] = {}
    covered: set[str] = set()

    for fixture in manifest.get("fixtures", []):
        if not isinstance(fixture, dict) or fixture.get("status") != "available":
            continue
        categories = tuple(
            sorted(required & {str(item) for item in fixture.get("categories", [])})
        )
        if not categories:
            continue
        source = fixture.get("source") or {}
        raw_path = source.get("path") if isinstance(source, dict) else None
        if not raw_path or source.get("status") != "available":
            continue
        path = Path(str(raw_path))
        path = path if path.is_absolute() else root / path
        if not path.exists():
            continue
        existing = selected.get(path)
        merged_categories = set(categories)
        if existing:
            merged_categories.update(existing.categories)
        selected[path] = FixtureInput(
            fixture_id=str(fixture.get("id") or path.stem),
            categories=tuple(sorted(merged_categories)),
            path=path.resolve(),
            source_kind="manifest-source",
        )
        covered.update(categories)

    for category, path in overrides.items():
        if category not in required:
            raise ValueError(f"unknown P17 category override: {category}")
        if not path.exists():
            raise FileNotFoundError(path)
        resolved = path.resolve()
        existing = selected.get(resolved)
        categories = set(existing.categories if existing else ())
        categories.add(category)
        selected[resolved] = FixtureInput(
            fixture_id=(existing.fixture_id if existing else f"override-{category}"),
            categories=tuple(sorted(categories)),
            path=resolved,
            source_kind="explicit-override",
        )
        covered.add(category)

    missing = sorted(required - covered)
    return sorted(selected.values(), key=lambda item: item.fixture_id), missing


def _release_cuda(*objects: object) -> None:
    for obj in objects:
        del obj
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _cuda_metrics() -> dict[str, float | None]:
    try:
        import torch

        if not torch.cuda.is_available():
            return {"peak_allocated_mib": None, "peak_reserved_mib": None}
        return {
            "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / (1024**2), 2),
            "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / (1024**2), 2),
        }
    except Exception:
        return {"peak_allocated_mib": None, "peak_reserved_mib": None}


def _reset_cuda_peak() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def _audio_duration_seconds(audio: Any) -> float:
    try:
        return float(len(audio)) / 16000.0
    except Exception:
        return 0.0


def run_benchmark(
    fixtures: list[FixtureInput],
    *,
    batches: list[int],
    model_name: str,
    device: str,
    compute_type: str,
    language: str | None,
    alignment_tolerance_seconds: float,
) -> dict[str, Any]:
    configure_runtime()
    import whisperx

    cache_dir = MODELS_DIR / "whisperx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []

    for batch_size in batches:
        model = None
        align_models: dict[str, tuple[Any, Any]] = {}
        try:
            model = whisperx.load_model(
                model_name,
                device,
                compute_type=compute_type,
                language=language,
                download_root=str(cache_dir),
            )
            for fixture in fixtures:
                audio = whisperx.load_audio(str(fixture.path))
                _reset_cuda_peak()
                started = time.perf_counter()
                error: str | None = None
                transcribed: dict[str, Any] | None = None
                aligned: dict[str, Any] | None = None
                try:
                    transcribed = model.transcribe(audio, batch_size=batch_size)
                    language_code = str(transcribed.get("language") or language or "en")
                    if language_code not in align_models:
                        align_models[language_code] = whisperx.load_align_model(
                            language_code=language_code,
                            device=device,
                        )
                    align_model, metadata = align_models[language_code]
                    aligned = whisperx.align(
                        transcribed.get("segments", []),
                        align_model,
                        metadata,
                        audio,
                        device,
                        return_char_alignments=False,
                    )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                wall_seconds = time.perf_counter() - started
                cuda = _cuda_metrics()
                transcript = ""
                segment_signature: list[dict[str, Any]] = []
                aligned_word_signature: list[dict[str, Any]] = []
                detected_language = None
                if transcribed is not None:
                    detected_language = transcribed.get("language")
                    transcript = " ".join(
                        str(item.get("text", "")).strip()
                        for item in transcribed.get("segments", [])
                        if isinstance(item, dict)
                    ).strip()
                    segment_signature = _segment_signature(transcribed)
                if aligned is not None:
                    aligned_word_signature = _aligned_word_signature(aligned)
                duration = _audio_duration_seconds(audio)
                runs.append(
                    {
                        "fixture_id": fixture.fixture_id,
                        "categories": list(fixture.categories),
                        "source_kind": fixture.source_kind,
                        "path": str(fixture.path),
                        "sha256": _sha256(fixture.path),
                        "batch_size": batch_size,
                        "audio_seconds": round(duration, 3),
                        "wall_seconds": round(wall_seconds, 4),
                        "rtf": round(wall_seconds / duration, 6) if duration > 0 else None,
                        "detected_language": detected_language,
                        "normalized_transcript": _normalize_text(transcript),
                        "segment_signature": segment_signature,
                        "aligned_word_signature": aligned_word_signature,
                        "error": error,
                        **cuda,
                    }
                )
        finally:
            _release_cuda(model, *(item[0] for item in align_models.values()))

    baseline_batch = batches[0]
    by_fixture: dict[str, dict[int, dict[str, Any]]] = {}
    for run in runs:
        by_fixture.setdefault(run["fixture_id"], {})[int(run["batch_size"])] = run

    comparisons: list[dict[str, Any]] = []
    for fixture in fixtures:
        fixture_runs = by_fixture.get(fixture.fixture_id, {})
        baseline = fixture_runs.get(baseline_batch)
        for batch_size in batches[1:]:
            candidate = fixture_runs.get(batch_size)
            passed = bool(
                baseline
                and candidate
                and not baseline["error"]
                and not candidate["error"]
                and baseline["normalized_transcript"] == candidate["normalized_transcript"]
                and _signatures_match(
                    baseline["segment_signature"],
                    candidate["segment_signature"],
                    tolerance_seconds=alignment_tolerance_seconds,
                )
                and _signatures_match(
                    baseline["aligned_word_signature"],
                    candidate["aligned_word_signature"],
                    tolerance_seconds=alignment_tolerance_seconds,
                )
            )
            speedup = None
            if baseline and candidate and candidate["wall_seconds"]:
                speedup = round(baseline["wall_seconds"] / candidate["wall_seconds"], 4)
            comparisons.append(
                {
                    "fixture_id": fixture.fixture_id,
                    "candidate_batch": batch_size,
                    "transcript_equal": bool(
                        baseline
                        and candidate
                        and baseline["normalized_transcript"]
                        == candidate["normalized_transcript"]
                    ),
                    "segment_timing_equal": bool(
                        baseline
                        and candidate
                        and _signatures_match(
                            baseline["segment_signature"],
                            candidate["segment_signature"],
                            tolerance_seconds=alignment_tolerance_seconds,
                        )
                    ),
                    "aligned_words_equal": bool(
                        baseline
                        and candidate
                        and _signatures_match(
                            baseline["aligned_word_signature"],
                            candidate["aligned_word_signature"],
                            tolerance_seconds=alignment_tolerance_seconds,
                        )
                    ),
                    "speedup_vs_baseline": speedup,
                    "passed": passed,
                }
            )

    aggregate: dict[str, Any] = {}
    for batch_size in batches:
        batch_runs = [item for item in runs if item["batch_size"] == batch_size]
        wall = sum(float(item["wall_seconds"]) for item in batch_runs)
        audio = sum(float(item["audio_seconds"]) for item in batch_runs)
        aggregate[str(batch_size)] = {
            "fixtures": len(batch_runs),
            "errors": sum(bool(item["error"]) for item in batch_runs),
            "wall_seconds": round(wall, 4),
            "audio_seconds": round(audio, 3),
            "rtf": round(wall / audio, 6) if audio > 0 else None,
            "max_peak_allocated_mib": max(
                (float(item["peak_allocated_mib"]) for item in batch_runs if item["peak_allocated_mib"] is not None),
                default=None,
            ),
            "max_peak_reserved_mib": max(
                (float(item["peak_reserved_mib"]) for item in batch_runs if item["peak_reserved_mib"] is not None),
                default=None,
            ),
        }

    all_runs_ok = all(not item["error"] for item in runs)
    all_comparisons_ok = all(item["passed"] for item in comparisons)
    return {
        "schema": "vi-dubber-p13-whisperx-full-p17-v1",
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "language": language,
        "baseline_batch": baseline_batch,
        "candidate_batches": batches[1:],
        "alignment_tolerance_seconds": alignment_tolerance_seconds,
        "fixtures": [
            {
                "fixture_id": fixture.fixture_id,
                "categories": list(fixture.categories),
                "path": str(fixture.path),
                "source_kind": fixture.source_kind,
            }
            for fixture in fixtures
        ],
        "runs": runs,
        "comparisons": comparisons,
        "aggregate": aggregate,
        "passed": all_runs_ok and all_comparisons_ok,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark WhisperX batch parity across P17 fixtures")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch", type=int, action="append", default=[])
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--language", default="en")
    parser.add_argument("--alignment-tolerance-seconds", type=float, default=0.03)
    parser.add_argument(
        "--category-source",
        action="append",
        default=[],
        help="Explicit category=path override for a P17 category whose manifest source is unresolved",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    root = args.root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    output_path = args.output if args.output.is_absolute() else root / args.output
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    overrides = _parse_category_overrides(args.category_source, root)
    fixtures, missing = select_fixture_inputs(
        manifest,
        root=root,
        category_overrides=overrides,
    )
    if missing:
        raise SystemExit(
            "Missing P17 source coverage for categories: " + ", ".join(missing)
        )
    batches = args.batch or [4, 6]
    if len(batches) < 2 or batches[0] <= 0 or any(item <= 0 for item in batches):
        raise SystemExit("Provide at least two positive --batch values; first is baseline")
    report = run_benchmark(
        fixtures,
        batches=batches,
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=(args.language or None),
        alignment_tolerance_seconds=args.alignment_tolerance_seconds,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "passed": report["passed"], "aggregate": report["aggregate"]}, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
