from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import random
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from vi_dubber.reference import ReferenceScore, rank_reference_candidates
from vi_dubber.types import Segment


SCHEMA_VERSION = 1
SAMPLE_RATE = 48_000
DEFAULT_SEED = 20260925
DEFAULT_BATCH_SIZE = 4


class P07ABError(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_path(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _vocals_path(job_dir: Path) -> Path:
    matches = sorted((job_dir / "stems").glob("*Vocals*.wav"))
    if not matches:
        raise P07ABError(f"no retained vocals stem found in {job_dir}")
    return matches[0]


def _segments(path: Path) -> list[Segment]:
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise P07ABError(f"expected segment array: {path}")
    return [Segment.from_dict(item) for item in raw if isinstance(item, dict)]


def _eligible(segments: list[Segment], speaker: str) -> list[Segment]:
    return [
        item
        for item in segments
        if item.speaker == speaker and 3.0 <= item.duration <= 8.0 and not item.overlap
    ]


def _score_map(ranked: list[ReferenceScore]) -> dict[int, ReferenceScore]:
    return {item.segment_id: item for item in ranked}


def _selection(job_dir: Path, speaker: str) -> dict[str, Any]:
    turns = _segments(job_dir / "segments_turns.json")
    candidates = _eligible(turns, speaker)
    if len(candidates) < 3:
        raise P07ABError(f"{job_dir.name}/{speaker} has fewer than three eligible candidates")
    vocals = _vocals_path(job_dir)
    ranked = rank_reference_candidates(vocals, candidates)
    by_id = {item.id: item for item in candidates}
    scores = _score_map(ranked)
    legacy = candidates[0]
    smart = by_id[ranked[0].segment_id]
    holdout = next((by_id[row.segment_id] for row in ranked if row.segment_id not in {legacy.id, smart.id}), None)
    if holdout is None:
        raise P07ABError(f"{job_dir.name}/{speaker} has no independent holdout reference")
    return {
        "vocals": vocals,
        "legacy": legacy,
        "smart": smart,
        "holdout": holdout,
        "scores": scores,
        "ranked": ranked,
        "candidate_count": len(candidates),
    }


def _job_source(job_dir: Path) -> dict[str, Any]:
    job_json = job_dir / "job.json"
    if not job_json.is_file():
        return {}
    payload = _read_json(job_json)
    return payload if isinstance(payload, dict) else {}


def discover(project_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job_dir in sorted((project_root / "work").glob("job-*")):
        turns_path = job_dir / "segments_turns.json"
        if not turns_path.is_file() or not (job_dir / "stems").is_dir():
            continue
        try:
            turns = _segments(turns_path)
            speakers = sorted({item.speaker for item in turns})
            source = _job_source(job_dir)
            for speaker in speakers:
                selection = _selection(job_dir, speaker)
                legacy = selection["legacy"]
                smart = selection["smart"]
                scores = selection["scores"]
                rows.append(
                    {
                        "job_id": job_dir.name,
                        "speaker": speaker,
                        "source_name": source.get("source_name"),
                        "source_sha256": (source.get("source") or {}).get("sha256") if isinstance(source.get("source"), dict) else None,
                        "candidate_count": selection["candidate_count"],
                        "legacy_segment_id": legacy.id,
                        "smart_segment_id": smart.id,
                        "legacy_score": scores[legacy.id].score,
                        "smart_score": scores[smart.id].score,
                        "score_gain": scores[smart.id].score - scores[legacy.id].score,
                        "selection_differs": legacy.id != smart.id,
                    }
                )
        except Exception as exc:
            rows.append({"job_id": job_dir.name, "error": str(exc)})
    rows.sort(key=lambda item: float(item.get("score_gain", -999.0)), reverse=True)
    return rows


def _trim_reference(vocals: Path, segment: Segment, target: Path) -> dict[str, Any]:
    duration = min(8.0, max(3.0, segment.duration - 0.2))
    start = segment.start + max(0.0, (segment.duration - duration) / 2.0)
    with sf.SoundFile(str(vocals)) as handle:
        sample_rate = int(handle.samplerate)
        handle.seek(max(0, int(round(start * sample_rate))))
        frames = max(1, int(round(duration * sample_rate)))
        audio = handle.read(frames, dtype="float32", always_2d=True)
    if audio.size == 0:
        raise P07ABError(f"empty reference crop for segment {segment.id}")
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, audio, sample_rate, subtype="PCM_16")
    return {
        "segment_id": segment.id,
        "start": start,
        "duration": duration,
        "sample_rate": sample_rate,
        "sha256": _sha256(target),
    }


def _pick_texts(job_dir: Path, speaker: str, count: int) -> list[dict[str, Any]]:
    source_path = job_dir / "segments_vi.json"
    if not source_path.is_file():
        source_path = job_dir / "segments_translated.json"
    segments = [
        item
        for item in _segments(source_path)
        if item.speaker == speaker and 1.0 <= item.duration <= 8.0 and 18 <= len((item.vi or item.text).strip()) <= 180
    ]
    if len(segments) < count:
        raise P07ABError(f"{job_dir.name}/{speaker} has only {len(segments)} usable listening texts")
    if count == 1:
        chosen = [segments[len(segments) // 2]]
    else:
        chosen = []
        last_index = len(segments) - 1
        for index in range(count):
            position = round(index * last_index / (count - 1))
            chosen.append(segments[position])
    return [
        {
            "segment_id": item.id,
            "timeline_start": item.start,
            "text": (item.vi or item.text).strip(),
        }
        for item in chosen
    ]


def _set_seed(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _render_candidate(engine: Any, texts: list[str], reference: Path, target: Path, *, seed: int, batch_size: int) -> dict[str, Any]:
    _set_seed(seed)
    outputs = engine.infer_batch(texts, ref_audio=str(reference), batch_size=batch_size)
    if len(outputs) != len(texts):
        raise P07ABError(f"infer_batch returned {len(outputs)} outputs for {len(texts)} texts")
    silence = np.zeros(int(round(SAMPLE_RATE * 0.4)), dtype=np.float32)
    parts: list[np.ndarray] = []
    utterances: list[dict[str, Any]] = []
    for index, output in enumerate(outputs):
        audio = np.asarray(output, dtype=np.float32).reshape(-1)
        if audio.size == 0 or not np.isfinite(audio).all():
            raise P07ABError(f"invalid TTS audio at utterance {index}")
        if index:
            parts.append(silence)
        parts.append(audio)
        utterances.append(
            {
                "index": index,
                "duration_s": audio.size / SAMPLE_RATE,
                "rms": float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))),
                "peak": float(np.max(np.abs(audio))),
            }
        )
    combined = np.concatenate(parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, combined, SAMPLE_RATE, subtype="PCM_16")
    return {
        "path": target,
        "sha256": _sha256(target),
        "duration_s": combined.size / SAMPLE_RATE,
        "utterances": utterances,
    }


def build(project_root: Path, jobs: list[str], output_dir: Path, *, seed: int, batch_size: int) -> dict[str, Any]:
    import torch
    from vieneu import Vieneu

    if not torch.cuda.is_available():
        raise P07ABError("CUDA is required for the P07 isolated listening build")
    if batch_size < 1:
        raise P07ABError("batch size must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = Vieneu(
        mode="v3turbo",
        backend="pytorch",
        device="cuda",
        precision="fp16",
        dtype="float16",
        max_batch_size=batch_size,
    )
    trials: list[dict[str, Any]] = []
    receipt_trials: list[dict[str, Any]] = []
    try:
        for job_index, job_id in enumerate(jobs):
            job_dir = project_root / "work" / job_id
            if not job_dir.is_dir():
                raise P07ABError(f"job not found: {job_id}")
            turns = _segments(job_dir / "segments_turns.json")
            speakers = sorted({item.speaker for item in turns})
            source = _job_source(job_dir)
            for speaker_index, speaker in enumerate(speakers):
                selection = _selection(job_dir, speaker)
                legacy = selection["legacy"]
                smart = selection["smart"]
                if legacy.id == smart.id:
                    raise P07ABError(f"{job_id}/{speaker}: legacy and smart choose the same segment {smart.id}")
                trial_seed = seed + job_index * 100 + speaker_index
                trial_id = f"{job_id}-{speaker}"
                trial_dir = output_dir / "source-media" / trial_id
                legacy_ref = trial_dir / "legacy-reference.wav"
                smart_ref = trial_dir / "smart-reference.wav"
                holdout_ref = trial_dir / "holdout-reference.wav"
                legacy_crop = _trim_reference(selection["vocals"], legacy, legacy_ref)
                smart_crop = _trim_reference(selection["vocals"], smart, smart_ref)
                holdout_crop = _trim_reference(selection["vocals"], selection["holdout"], holdout_ref)
                text_rows = _pick_texts(job_dir, speaker, batch_size)
                texts = [row["text"] for row in text_rows]
                legacy_audio = _render_candidate(
                    engine,
                    texts,
                    legacy_ref,
                    trial_dir / "legacy-candidate.wav",
                    seed=trial_seed,
                    batch_size=batch_size,
                )
                smart_audio = _render_candidate(
                    engine,
                    texts,
                    smart_ref,
                    trial_dir / "smart-candidate.wav",
                    seed=trial_seed,
                    batch_size=batch_size,
                )
                scores = selection["scores"]
                trial_provenance = {
                    "job_id": job_id,
                    "speaker": speaker,
                    "source_name": source.get("source_name"),
                    "source_sha256": (source.get("source") or {}).get("sha256") if isinstance(source.get("source"), dict) else None,
                    "seed": trial_seed,
                    "texts": text_rows,
                    "legacy": {**legacy_crop, "score": scores[legacy.id].to_dict()},
                    "smart": {**smart_crop, "score": scores[smart.id].to_dict()},
                    "holdout": {**holdout_crop, "score": scores[selection["holdout"].id].to_dict()},
                    "legacy_audio": {key: value for key, value in legacy_audio.items() if key != "path"},
                    "smart_audio": {key: value for key, value in smart_audio.items() if key != "path"},
                }
                trials.append(
                    {
                        "id": trial_id,
                        "fixture_id": source.get("source_name") or job_id,
                        "reference": {
                            "path": _project_path(holdout_ref, project_root),
                            "sha256": _sha256(holdout_ref),
                            "provenance": {"role": "independent-held-out-source-reference"},
                        },
                        "candidates": [
                            {
                                "id": "legacy-first-length-valid",
                                "path": _project_path(legacy_audio["path"], project_root),
                                "sha256": legacy_audio["sha256"],
                                "provenance": {"selector": "legacy-first-length-valid"},
                            },
                            {
                                "id": "smart-acoustic-delivery",
                                "path": _project_path(smart_audio["path"], project_root),
                                "sha256": smart_audio["sha256"],
                                "provenance": {"selector": "current-smart-acoustic-delivery"},
                            },
                        ],
                        "limitations": [
                            "Single-speaker retained fixture; listener judgment is still required.",
                            "Candidates differ only by source reference clip; VieNeu version, texts, seed, backend, device, precision and batch size are held constant.",
                        ],
                    }
                )
                receipt_trials.append(trial_provenance)
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()

    study_spec = {
        "schema_version": 1,
        "study_id": "p07-reference-selector-20260925",
        "minimum_completed_votes": 3,
        "trials": trials,
    }
    study_spec_path = output_dir / "study-spec.json"
    _write_json(study_spec_path, study_spec)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "p07-reference-selector-isolated-ab",
        "date": "2026-09-25",
        "baseline": "legacy-first-length-valid-ref",
        "candidate": "current-smart-acoustic-delivery-ref",
        "vieneu_version": importlib.metadata.version("vieneu"),
        "torch_version": torch.__version__,
        "gpu": torch.cuda.get_device_name(0),
        "backend": "pytorch",
        "device": "cuda",
        "precision": "fp16",
        "batch_size": batch_size,
        "seed": seed,
        "study_spec": _project_path(study_spec_path, project_root),
        "study_spec_sha256": _sha256(study_spec_path),
        "trials": receipt_trials,
        "human_gate_status": "pending_human_votes",
        "automatic_winner_selected": False,
    }
    _write_json(output_dir / "generation-receipt.json", receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build isolated P07 smart-reference blind A/B evidence")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    discover_parser = sub.add_parser("discover", help="compare legacy first-valid and current smart selectors")
    discover_parser.add_argument("--output", type=Path)
    build_parser = sub.add_parser("build", help="render isolated blind-listening source media")
    build_parser.add_argument("--job", action="append", required=True, help="retained work/job-* directory name")
    build_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    build_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    try:
        if args.command == "discover":
            rows = discover(project_root)
            payload = {"schema_version": SCHEMA_VERSION, "rows": rows}
            if args.output:
                _write_json(args.output.resolve(), payload)
            print(json.dumps(payload, ensure_ascii=True, indent=2))
            return 0
        output_dir = args.output_dir.resolve()
        receipt = build(project_root, args.job, output_dir, seed=args.seed, batch_size=args.batch_size)
        print(json.dumps(receipt, ensure_ascii=True, indent=2))
        return 0
    except Exception as exc:
        print(f"P07 A/B error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
