from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from vi_dubber.asr import (
    _diarization_overlap_ranges,
    _diarization_speaker_hints,
    _mark_diarization_overlaps,
    _segment_from_aligned,
    transcribe_and_align,
)
from vi_dubber.media import assemble_voice_track
from vi_dubber.segmentation import build_speech_turns
from vi_dubber.tts import build_reference_clips
from vi_dubber.types import Segment


REQUIRED_CATEGORIES = ("two-speakers", "overlapping-speech")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def _relative(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _record(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: Any) -> None:
    checks.append({"id": check_id, "passed": bool(passed), "detail": detail})


def _expect_error(callable_: Any, message: str) -> str:
    try:
        callable_()
    except Exception as exc:  # The receipt records the exact fail-closed boundary.
        if message not in str(exc):
            raise AssertionError(f"Expected error containing {message!r}, got {exc!r}") from exc
        return str(exc)
    raise AssertionError(f"Expected an error containing {message!r}")


def _fixture_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for fixture in manifest.get("fixtures", []):
        if not isinstance(fixture, dict):
            continue
        categories = fixture.get("categories") or []
        for category in REQUIRED_CATEGORIES:
            if category in categories:
                entries[category] = fixture
    return entries


def _write_constant(path: Path, value: float, seconds: float, sample_rate: int) -> Path:
    sf.write(path, np.full(int(seconds * sample_rate), value, dtype=np.float32), sample_rate)
    return path


def _probe_equal_power_overlap(directory: Path) -> dict[str, float]:
    sample_rate = 8000
    first = _write_constant(directory / "first.wav", 0.4, 0.75, sample_rate)
    second = _write_constant(directory / "second.wav", 0.4, 0.75, sample_rate)
    output = assemble_voice_track(
        [
            (Segment(id=1, start=0.0, end=0.75, text="one", speaker="SPEAKER_00"), first),
            (
                Segment(
                    id=2,
                    start=0.25,
                    end=1.0,
                    text="two",
                    speaker="SPEAKER_01",
                    overlap=True,
                ),
                second,
            ),
        ],
        output=directory / "voice.wav",
        total_duration=1.0,
        sample_rate=sample_rate,
        overlap_policy="equal_power",
    )
    rendered, rendered_rate = sf.read(output, dtype="float32")
    single_value = float(rendered[int(0.1 * sample_rate)])
    overlap_value = float(rendered[int(0.5 * sample_rate)])
    expected_overlap = 0.8 / math.sqrt(2.0)
    if rendered_rate != sample_rate or not math.isclose(single_value, 0.4, abs_tol=2e-4):
        raise AssertionError("Non-overlap timeline sample changed during assembly")
    if not math.isclose(overlap_value, expected_overlap, abs_tol=2e-4):
        raise AssertionError("Equal-power overlap sample does not match the production contract")
    return {
        "sample_rate": rendered_rate,
        "single_speaker_sample": round(single_value, 6),
        "overlap_sample": round(overlap_value, 6),
        "expected_overlap_sample": round(expected_overlap, 6),
    }


def run_acceptance(
    root: Path,
    *,
    manifest_path: Path | None = None,
    ground_truth_path: Path | None = None,
    hf_token: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = (manifest_path or root / "work/benchmarks/fixtures.json").resolve()
    ground_truth_path = (
        ground_truth_path
        or root / "work/benchmarks/p14-synthetic-acceptance/fixture-ground-truth.json"
    ).resolve()
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        manifest = _read_json(manifest_path)
        ground_truth = _read_json(ground_truth_path)
        fixtures = _fixture_entries(manifest)
        _record(
            checks,
            "fixtures.present",
            set(fixtures) == set(REQUIRED_CATEGORIES),
            {"found": sorted(fixtures), "required": list(REQUIRED_CATEGORIES)},
        )

        receipts: dict[str, dict[str, Any]] = {}
        for category in REQUIRED_CATEGORIES:
            fixture = fixtures[category]
            source = fixture["source"]
            verification = fixture["verification"]
            source_path = _relative(root, str(source["path"]))
            verification_path = _relative(root, str(verification["path"]))
            receipt = _read_json(verification_path)
            receipts[category] = receipt
            source_hash = _sha256(source_path)
            verification_hash = _sha256(verification_path)
            passed = (
                fixture.get("status") in {"source-ready", "available"}
                and source_hash == source.get("sha256")
                and verification_hash == verification.get("sha256")
                and receipt.get("source_sha256") == source_hash
                and receipt.get("fixture_class") == category
                and receipt.get("generation_kind") == "synthetic"
            )
            _record(
                checks,
                f"fixture.{category}.provenance",
                passed,
                {
                    "status": fixture.get("status"),
                    "source_sha256": source_hash,
                    "verification_sha256": verification_hash,
                },
            )

        two_gt = ground_truth["two_speakers"]
        overlap_gt = ground_truth["overlapping_speech"]
        two_wav = _relative(root, str(two_gt["retained_wav"]))
        overlap_wav = _relative(root, str(overlap_gt["retained_wav"]))
        gt_hashes_pass = (
            bool(two_gt.get("byte_identical"))
            and bool(overlap_gt.get("byte_identical"))
            and _sha256(two_wav) == two_gt.get("retained_wav_sha256")
            and two_gt.get("retained_wav_sha256") == two_gt.get("reconstructed_wav_sha256")
            and _sha256(overlap_wav) == overlap_gt.get("retained_wav_sha256")
            and overlap_gt.get("retained_wav_sha256")
            == overlap_gt.get("reconstructed_wav_sha256")
        )
        _record(
            checks,
            "ground_truth.byte_identical_reconstruction",
            gt_hashes_pass,
            {
                "two_speakers_wav_sha256": _sha256(two_wav),
                "overlapping_speech_wav_sha256": _sha256(overlap_wav),
            },
        )

        two_probe = receipts["two-speakers"]["behavior_probe"]
        overlap_probe = receipts["overlapping-speech"]["behavior_probe"]
        gt_receipts_pass = (
            two_gt.get("distinct_speakers") == two_probe.get("distinct_synthetic_speakers") == 2
            and two_gt.get("alternating_turns") == two_probe.get("alternating_turns") == 4
            and two_gt.get("designed_overlap_seconds")
            == two_probe.get("designed_overlap_seconds")
            == 0
            and overlap_gt.get("distinct_speakers")
            == overlap_probe.get("distinct_synthetic_speakers")
            == 2
            and math.isclose(
                float(overlap_gt.get("second_speaker_delay_seconds")),
                float(overlap_probe.get("second_speaker_delay_seconds")),
                abs_tol=1e-9,
            )
            and math.isclose(
                float(overlap_gt.get("designed_overlap_seconds")),
                float(overlap_probe.get("designed_overlap_seconds")),
                abs_tol=0.001,
            )
        )
        _record(
            checks,
            "ground_truth.matches_p17_receipts",
            gt_receipts_pass,
            {
                "two_speaker_turns": two_gt.get("alternating_turns"),
                "designed_overlap_seconds": overlap_gt.get("designed_overlap_seconds"),
            },
        )

        turns = [
            Segment(
                id=int(turn["index"]),
                start=float(turn["start"]),
                end=float(turn["end"]),
                text=str(turn["text"]),
                speaker=str(turn["speaker"]),
                overlap=False,
            )
            for turn in two_gt["turns"]
        ]
        hints = _diarization_speaker_hints({"num_speakers": 2})
        diarized_turns = [
            {"start": item.start, "end": item.end, "speaker": item.speaker} for item in turns
        ]
        built_turns = build_speech_turns(
            turns,
            {"min_duration": 0.0, "target_duration": 60.0, "max_duration": 60.0},
        )
        expected_speakers = ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00", "SPEAKER_01"]
        turn_contract_pass = (
            hints == {"num_speakers": 2}
            and _diarization_overlap_ranges(diarized_turns) == []
            and [item.speaker for item in built_turns] == expected_speakers
            and len(built_turns) == 4
        )
        _record(
            checks,
            "two_speakers.identity_and_no_silent_merge",
            turn_contract_pass,
            {
                "speaker_hints": hints,
                "speaker_sequence": [item.speaker for item in built_turns],
                "turn_count": len(built_turns),
            },
        )

        with tempfile.TemporaryDirectory(prefix="vi-dubber-p14-refs-") as temp_name:
            selection: dict[str, Any] = {}
            references = build_reference_clips(
                two_wav,
                turns,
                Path(temp_name),
                selection_receipts=selection,
            )
            turns_by_id = {item.id: item for item in turns}
            canonical_pass = set(references) == {"SPEAKER_00", "SPEAKER_01"}
            selected: dict[str, Any] = {}
            for speaker, reference_path in sorted(references.items()):
                receipt = selection[speaker]
                selected_turn = turns_by_id[int(receipt["selected_segment_id"])]
                selected[speaker] = {
                    "segment_id": selected_turn.id,
                    "source_speaker": selected_turn.speaker,
                    "selected_start": round(float(receipt["selected_start"]), 6),
                    "selected_duration": round(float(receipt["selected_duration"]), 6),
                    "sha256": _sha256(reference_path),
                }
                canonical_pass = (
                    canonical_pass
                    and selected_turn.speaker == speaker
                    and not selected_turn.overlap
                    and reference_path.is_file()
                )
            _record(
                checks,
                "two_speakers.canonical_reference_isolation",
                canonical_pass,
                {"references": selected},
            )

        spans = [dict(item) for item in overlap_gt["speaker_spans"]]
        overlap_ranges = _diarization_overlap_ranges(spans)
        expected_span = overlap_gt["overlap_span"]
        overlap_range_pass = (
            len(overlap_ranges) == 1
            and math.isclose(overlap_ranges[0][0], float(expected_span["start"]), abs_tol=1e-6)
            and math.isclose(overlap_ranges[0][1], float(expected_span["end"]), abs_tol=1e-6)
        )
        _record(
            checks,
            "overlap.distinct_speaker_range",
            overlap_range_pass,
            {"ranges": overlap_ranges, "expected": expected_span},
        )

        overlap_start = float(expected_span["start"])
        overlap_end = float(expected_span["end"])
        aligned = {
            "start": 0.0,
            "end": float(overlap_gt["duration_seconds"]),
            "text": "before shared after",
            "speaker": "SPEAKER_00",
            "words": [
                {"word": "before", "start": 0.1, "end": max(0.2, overlap_start - 0.1), "speaker": "SPEAKER_00"},
                {"word": "shared", "start": overlap_start + 0.1, "end": overlap_end - 0.1, "speaker": "SPEAKER_01"},
                {"word": "after", "start": overlap_end + 0.1, "end": float(overlap_gt["duration_seconds"]) - 0.1, "speaker": "SPEAKER_01"},
            ],
        }
        result = {"segments": [aligned]}
        _mark_diarization_overlaps(result, spans)
        segment = _segment_from_aligned(aligned, 0)
        visibility = segment.speaker_visibility()
        protected_turns = build_speech_turns(
            [segment],
            {"min_duration": 0.0, "target_duration": 60.0, "max_duration": 60.0},
        )
        visible_pass = (
            aligned.get("overlap") is True
            and aligned["words"][0].get("overlap") is not True
            and aligned["words"][1].get("overlap") is True
            and visibility["multi_speaker"] is True
            and visibility["overlap"] is True
            and visibility["needs_review"] is True
            and len(protected_turns) >= 2
            and any(item.overlap for item in protected_turns)
        )
        _record(
            checks,
            "overlap.visibility_and_review_contract",
            visible_pass,
            {
                "speaker_visibility": visibility,
                "protected_turns": [
                    {"speaker": item.speaker, "overlap": item.overlap, "text": item.text}
                    for item in protected_turns
                ],
            },
        )

        with tempfile.TemporaryDirectory(prefix="vi-dubber-p14-mix-") as temp_name:
            assembly = _probe_equal_power_overlap(Path(temp_name))
        _record(checks, "overlap.equal_power_assembly", True, assembly)

        missing_token_error = _expect_error(
            lambda: transcribe_and_align(
                root / "does-not-need-to-exist.wav",
                {"model": "large-v3"},
                hf_token=None,
                diarize=True,
            ),
            "HUGGINGFACE_TOKEN is missing",
        )
        invalid_hints_error = _expect_error(
            lambda: _diarization_speaker_hints({"num_speakers": 2, "max_speakers": 3}),
            "cannot be combined",
        )
        _record(
            checks,
            "diarization.fail_closed",
            True,
            {
                "missing_token": missing_token_error,
                "invalid_hints": invalid_hints_error,
            },
        )
    except Exception as exc:
        errors.append(f"{exc.__class__.__name__}: {exc}")

    effective_token = hf_token if hf_token is not None else os.getenv("HUGGINGFACE_TOKEN")
    pyannote_cache = root / "models/pyannote"
    cached_files = sorted(path for path in pyannote_cache.rglob("*") if path.is_file()) if pyannote_cache.exists() else []
    runtime_probe = {
        "whisperx_importable": importlib.util.find_spec("whisperx") is not None,
        "pyannote_audio_importable": importlib.util.find_spec("pyannote.audio") is not None,
        "huggingface_token_present": bool((effective_token or "").strip()),
        "project_pyannote_cache_files": len(cached_files),
        "live_diarization_attempted": False,
        "reason": (
            "Live diarization intentionally not invoked: HUGGINGFACE_TOKEN is absent."
            if not (effective_token or "").strip()
            else "Live diarization is outside this deterministic contract harness; use a separate authorized model probe."
        ),
    }
    passed = not errors and bool(checks) and all(item["passed"] for item in checks)
    return {
        "schema_version": 1,
        "task": "P14-SYNTH-ACCEPT",
        "passed": passed,
        "acceptance_scope": "machine-verifiable synthetic contract",
        "real_human_quality_claimed": False,
        "checks": checks,
        "errors": errors,
        "runtime_probe": runtime_probe,
        "remaining_acceptance": [
            "Run diarization on a consented real two-speaker interview with accepted pyannote terms and an authorized HF read token.",
            "Run a real conversational crosstalk sample and listen for identity continuity, cross-reference contamination, and natural overlap rendering.",
            "Complete human listening review; synthetic contract evidence cannot establish voice quality or conversational naturalness.",
        ],
        "provenance": {
            "manifest": _display_path(root, manifest_path),
            "ground_truth": _display_path(root, ground_truth_path),
            "tool": "tools/p14_synthetic_acceptance.py",
            "tool_sha256": _sha256(root / "tools/p14_synthetic_acceptance.py"),
            "ground_truth_generator": "tools/p14_fixture_ground_truth.ps1",
            "ground_truth_generator_sha256": _sha256(
                root / "tools/p14_fixture_ground_truth.ps1"
            ),
            "production_contract_sha256": {
                path: _sha256(root / path)
                for path in (
                    "src/vi_dubber/asr.py",
                    "src/vi_dubber/media.py",
                    "src/vi_dubber/segmentation.py",
                    "src/vi_dubber/tts.py",
                    "src/vi_dubber/types.py",
                )
            },
        },
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate P14 synthetic multi-speaker contracts")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ground-truth", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/benchmarks/p14-synthetic-acceptance/acceptance-report.json"),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest = args.manifest
    if manifest is not None and not manifest.is_absolute():
        manifest = root / manifest
    ground_truth = args.ground_truth
    if ground_truth is not None and not ground_truth.is_absolute():
        ground_truth = root / ground_truth
    output = args.output if args.output.is_absolute() else root / args.output
    report = run_acceptance(
        root,
        manifest_path=manifest,
        ground_truth_path=ground_truth,
    )
    _write_report(output, report)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
