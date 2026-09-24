from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path
from statistics import fmean
from typing import Any


SCHEMA_VERSION = 1
MEDIA_SUFFIXES = frozenset({".flac", ".m4a", ".mp3", ".mp4", ".wav", ".webm"})
RATING_SCALE = {"minimum": 1, "maximum": 5}
QUESTION_RUBRIC = (
    {
        "id": "voice_similarity_reference_stability",
        "prompt": (
            "How similar is the dubbed voice to the reference speaker, and how stable "
            "does that identity remain from the beginning to the end?"
        ),
        "low_anchor": "Different or drifting voice identity",
        "high_anchor": "Close to the reference and stable throughout",
    },
    {
        "id": "naturalness",
        "prompt": "How natural and comfortable does the Vietnamese speech sound?",
        "low_anchor": "Robotic, awkward, or tiring",
        "high_anchor": "Natural and easy to listen to",
    },
    {
        "id": "timing",
        "prompt": "How well do speech pace, pauses, and turn boundaries fit the video?",
        "low_anchor": "Distracting timing or rushed/stretched speech",
        "high_anchor": "Well-paced and aligned",
    },
    {
        "id": "mix",
        "prompt": (
            "How clear is dialogue while background audio stays continuous and free of "
            "obvious pumping, clipping, or level jumps?"
        ),
        "low_anchor": "Hard to understand or audibly damaged mix",
        "high_anchor": "Clear dialogue and natural background continuity",
    },
)


class ListeningABError(ValueError):
    pass


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ListeningABError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ListeningABError(f"invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(data), encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ListeningABError(f"{field} must be a non-empty string")
    return value.strip()


def _project_media(root: Path, raw_path: Any, field: str) -> tuple[Path, str]:
    relative = Path(_require_string(raw_path, field))
    if relative.is_absolute():
        raise ListeningABError(f"{field} must be relative to --root")
    root = root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ListeningABError(f"{field} escapes --root: {relative}")
    if not resolved.is_file():
        raise ListeningABError(f"{field} does not exist: {relative.as_posix()}")
    if resolved.suffix.lower() not in MEDIA_SUFFIXES:
        raise ListeningABError(f"{field} is not a supported media file: {relative.as_posix()}")
    return resolved, relative.as_posix()


def _media_record(
    *,
    root: Path,
    item: dict[str, Any],
    field: str,
    alias_path: Path,
    output_dir: Path,
    force: bool,
) -> dict[str, Any]:
    source, relative_source = _project_media(root, item.get("path"), f"{field}.path")
    expected_hash = item.get("sha256")
    source_hash = _sha256(source)
    if expected_hash is not None:
        expected_hash = _require_string(expected_hash, f"{field}.sha256").lower()
        if expected_hash != source_hash:
            raise ListeningABError(
                f"{field}.sha256 mismatch: expected {expected_hash}, got {source_hash}"
            )

    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if alias_path.exists():
        if not force:
            raise ListeningABError(f"output media alias already exists: {alias_path}")
        alias_path.unlink()
    try:
        os.link(source, alias_path)
    except OSError as exc:
        raise ListeningABError(
            f"cannot create zero-copy hardlink {alias_path} -> {source}: {exc}"
        ) from exc

    provenance = item.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ListeningABError(f"{field}.provenance must be an object")
    return {
        "alias_path": alias_path.relative_to(output_dir).as_posix(),
        "bytes": source.stat().st_size,
        "link_mode": "hardlink",
        "provenance": provenance,
        "sha256": source_hash,
        "source_path": relative_source,
    }


def _validate_spec(spec: Any) -> tuple[str, list[dict[str, Any]], int]:
    if not isinstance(spec, dict):
        raise ListeningABError("study spec must be a JSON object")
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise ListeningABError(f"study spec schema_version must be {SCHEMA_VERSION}")
    study_id = _require_string(spec.get("study_id"), "study_id")
    trials = spec.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ListeningABError("trials must be a non-empty array")
    seen: set[str] = set()
    for index, trial in enumerate(trials):
        if not isinstance(trial, dict):
            raise ListeningABError(f"trials[{index}] must be an object")
        trial_id = _require_string(trial.get("id"), f"trials[{index}].id")
        if trial_id in seen:
            raise ListeningABError(f"duplicate trial id: {trial_id}")
        seen.add(trial_id)
        if not isinstance(trial.get("reference"), dict):
            raise ListeningABError(f"trials[{index}].reference must be an object")
        candidates = trial.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 2:
            raise ListeningABError(f"trials[{index}].candidates must contain exactly two items")
        candidate_ids = []
        for candidate_index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                raise ListeningABError(
                    f"trials[{index}].candidates[{candidate_index}] must be an object"
                )
            candidate_ids.append(
                _require_string(
                    candidate.get("id"),
                    f"trials[{index}].candidates[{candidate_index}].id",
                )
            )
        if len(set(candidate_ids)) != 2:
            raise ListeningABError(f"trials[{index}] candidate ids must be unique")

    minimum_votes = spec.get("minimum_completed_votes", 3)
    if isinstance(minimum_votes, bool) or not isinstance(minimum_votes, int) or minimum_votes < 1:
        raise ListeningABError("minimum_completed_votes must be an integer >= 1")
    return study_id, trials, minimum_votes


def create_packet(
    spec: dict[str, Any],
    *,
    root: Path,
    output_dir: Path,
    seed: int,
    force: bool = False,
) -> dict[str, Path]:
    study_id, source_trials, minimum_votes = _validate_spec(spec)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ListeningABError("seed must be an integer")
    root = root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "packet": output_dir / "packet.json",
        "receipt": output_dir / "organizer-receipt.json",
        "ballot": output_dir / "ballot-template.json",
        "decision": output_dir / "decision-template.json",
    }
    for path in outputs.values():
        if path.exists() and not force:
            raise ListeningABError(f"output already exists: {path}")

    rng = random.Random(seed)
    indexed_trials = list(enumerate(source_trials))
    rng.shuffle(indexed_trials)
    packet_trials: list[dict[str, Any]] = []
    receipt_trials: list[dict[str, Any]] = []

    for display_index, (source_index, trial) in enumerate(indexed_trials, start=1):
        trial_id = _require_string(trial.get("id"), f"trials[{source_index}].id")
        alias_stem = f"trial-{display_index:03d}"
        reference_item = trial["reference"]
        reference_source = Path(str(reference_item["path"]))
        reference_record = _media_record(
            root=root,
            item=reference_item,
            field=f"trials[{source_index}].reference",
            alias_path=output_dir / "media" / f"{alias_stem}-reference{reference_source.suffix.lower()}",
            output_dir=output_dir,
            force=force,
        )

        shuffled_candidates = list(trial["candidates"])
        rng.shuffle(shuffled_candidates)
        packet_candidates: list[dict[str, Any]] = []
        receipt_candidates: list[dict[str, Any]] = []
        for label, candidate in zip(("A", "B"), shuffled_candidates, strict=True):
            source_path = Path(str(candidate["path"]))
            record = _media_record(
                root=root,
                item=candidate,
                field=f"trials[{source_index}].candidate[{candidate['id']}]",
                alias_path=output_dir / "media" / f"{alias_stem}-{label}{source_path.suffix.lower()}",
                output_dir=output_dir,
                force=force,
            )
            packet_candidates.append(
                {
                    "label": label,
                    "media_path": record["alias_path"],
                }
            )
            receipt_candidates.append(
                {
                    **record,
                    "candidate_id": candidate["id"],
                    "label": label,
                }
            )

        packet_trials.append(
            {
                "candidates": packet_candidates,
                "reference": {
                    "media_path": reference_record["alias_path"],
                },
                "trial_id": trial_id,
            }
        )
        receipt_trials.append(
            {
                "candidate_mapping": receipt_candidates,
                "display_index": display_index,
                "fixture_id": trial.get("fixture_id"),
                "limitations": trial.get("limitations", []),
                "reference": reference_record,
                "trial_id": trial_id,
            }
        )

    rubric = [
        {**question, "response": "integer", "scale": RATING_SCALE}
        for question in QUESTION_RUBRIC
    ]
    packet = {
        "instructions": [
            "Use the same playback device, volume, and listening environment for both candidates.",
            "Listen to the reference first, then compare A and B in either order.",
            "Replay as needed; do not inspect organizer-receipt.json until the ballot is final.",
            "Rate each candidate independently before selecting A, B, or tie.",
        ],
        "minimum_completed_votes": minimum_votes,
        "preference_choices": ["A", "B", "tie"],
        "question_rubric": rubric,
        "schema_version": SCHEMA_VERSION,
        "study_id": study_id,
        "trials": packet_trials,
    }
    _write_json(outputs["packet"], packet)
    packet_hash = _sha256(outputs["packet"])

    receipt = {
        "blinding": {
            "candidate_labels": ["A", "B"],
            "mapping_private_until_ballots_final": True,
            "media_alias_mode": "hardlink",
            "media_copied": False,
            "seed": seed,
        },
        "packet_sha256": packet_hash,
        "project_root": root.as_posix(),
        "question_rubric": rubric,
        "schema_version": SCHEMA_VERSION,
        "spec_sha256": hashlib.sha256(_canonical_json(spec).encode("utf-8")).hexdigest(),
        "study_id": study_id,
        "trials": receipt_trials,
    }
    _write_json(outputs["receipt"], receipt)

    ratings = {question["id"]: {"A": None, "B": None} for question in QUESTION_RUBRIC}
    ballot = {
        "blinding_confirmed": None,
        "packet_sha256": packet_hash,
        "reviewer_id": "",
        "schema_version": SCHEMA_VERSION,
        "study_id": study_id,
        "trials": [
            {
                "notes": "",
                "overall_preference": None,
                "ratings": ratings,
                "trial_id": trial["trial_id"],
            }
            for trial in packet_trials
        ],
    }
    _write_json(outputs["ballot"], ballot)

    decision_template = {
        "decided_by": "",
        "decision": None,
        "human_attestation": None,
        "rationale": "",
        "reviewed_valid_ballots": None,
        "schema_version": SCHEMA_VERSION,
        "study_id": study_id,
    }
    _write_json(outputs["decision"], decision_template)
    return outputs


def _verify_packet_media(
    packet: dict[str, Any],
    receipt: dict[str, Any],
    packet_path: Path,
    packet_hash: str,
) -> list[str]:
    issues: list[str] = []
    if receipt.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"receipt.schema_version must be {SCHEMA_VERSION}")
    if receipt.get("study_id") != packet.get("study_id"):
        issues.append("receipt.study_id does not match packet")
    if receipt.get("packet_sha256") != packet_hash:
        issues.append("receipt.packet_sha256 does not match packet")
    packet_trials = packet.get("trials")
    if not isinstance(packet_trials, list):
        return ["packet.trials must be an array"]
    receipt_trials = receipt.get("trials")
    if not isinstance(receipt_trials, list):
        return issues + ["receipt.trials must be an array"]

    packet_aliases: set[str] = set()
    for trial_index, trial in enumerate(packet_trials):
        if not isinstance(trial, dict) or not isinstance(trial.get("reference"), dict):
            issues.append(f"packet.trials[{trial_index}] has invalid media entries")
            continue
        reference_path = trial["reference"].get("media_path")
        if isinstance(reference_path, str):
            packet_aliases.add(reference_path)
        candidates = trial.get("candidates")
        if not isinstance(candidates, list):
            issues.append(f"packet.trials[{trial_index}].candidates must be an array")
            continue
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("media_path"), str):
                packet_aliases.add(candidate["media_path"])

    receipt_aliases: set[str] = set()
    for trial_index, trial in enumerate(receipt_trials):
        if not isinstance(trial, dict):
            issues.append(f"receipt.trials[{trial_index}] must be an object")
            continue
        media_items = [("reference", trial.get("reference"))]
        candidates = trial.get("candidate_mapping")
        if isinstance(candidates, list):
            media_items.extend((f"candidate[{index}]", item) for index, item in enumerate(candidates))
        else:
            issues.append(f"receipt.trials[{trial_index}].candidate_mapping must be an array")
        for name, item in media_items:
            if not isinstance(item, dict):
                issues.append(f"receipt.trials[{trial_index}].{name} must be an object")
                continue
            raw_path = item.get("alias_path")
            expected_hash = item.get("sha256")
            if not isinstance(raw_path, str) or not isinstance(expected_hash, str):
                issues.append(f"receipt.trials[{trial_index}].{name} lacks alias_path/sha256")
                continue
            receipt_aliases.add(raw_path)
            media_path = (packet_path.parent / raw_path).resolve()
            if not media_path.is_relative_to(packet_path.parent.resolve()):
                issues.append(f"receipt.trials[{trial_index}].{name} escapes packet directory")
            elif not media_path.is_file():
                issues.append(f"receipt.trials[{trial_index}].{name} media missing: {raw_path}")
            elif _sha256(media_path) != expected_hash.lower():
                issues.append(f"receipt.trials[{trial_index}].{name} media hash mismatch")
    if packet_aliases != receipt_aliases:
        issues.append("packet and receipt media alias sets do not match")
    return issues


def _validate_ballot(
    ballot: Any,
    *,
    packet: dict[str, Any],
    packet_hash: str,
) -> tuple[str | None, list[str]]:
    issues: list[str] = []
    if not isinstance(ballot, dict):
        return None, ["ballot must be an object"]
    reviewer_id = ballot.get("reviewer_id")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        issues.append("reviewer_id must be a non-empty string")
        normalized_reviewer = None
    else:
        normalized_reviewer = reviewer_id.strip()
    if ballot.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"schema_version must be {SCHEMA_VERSION}")
    if ballot.get("study_id") != packet.get("study_id"):
        issues.append("study_id does not match packet")
    if ballot.get("packet_sha256") != packet_hash:
        issues.append("packet_sha256 does not match packet")
    if ballot.get("blinding_confirmed") is not True:
        issues.append("blinding_confirmed must be true")

    packet_trials = packet.get("trials") if isinstance(packet.get("trials"), list) else []
    ballot_trials = ballot.get("trials")
    if not isinstance(ballot_trials, list):
        issues.append("trials must be an array")
        return normalized_reviewer, issues
    packet_ids = [trial.get("trial_id") for trial in packet_trials if isinstance(trial, dict)]
    ballot_by_id: dict[Any, Any] = {}
    for trial in ballot_trials:
        if isinstance(trial, dict):
            trial_id = trial.get("trial_id")
            if trial_id in ballot_by_id:
                issues.append(f"duplicate ballot trial_id: {trial_id}")
            ballot_by_id[trial_id] = trial
        else:
            issues.append("each ballot trial must be an object")
    if set(ballot_by_id) != set(packet_ids):
        issues.append("ballot trial ids do not exactly match packet")

    question_ids = [question["id"] for question in QUESTION_RUBRIC]
    for trial_id in packet_ids:
        trial = ballot_by_id.get(trial_id)
        if not isinstance(trial, dict):
            continue
        ratings = trial.get("ratings")
        if not isinstance(ratings, dict) or set(ratings) != set(question_ids):
            issues.append(f"trial {trial_id} ratings must exactly match rubric")
            continue
        for question_id in question_ids:
            scores = ratings.get(question_id)
            if not isinstance(scores, dict) or set(scores) != {"A", "B"}:
                issues.append(f"trial {trial_id} rating {question_id} must contain A and B")
                continue
            for label in ("A", "B"):
                score = scores[label]
                if (
                    isinstance(score, bool)
                    or not isinstance(score, int)
                    or not RATING_SCALE["minimum"] <= score <= RATING_SCALE["maximum"]
                ):
                    issues.append(
                        f"trial {trial_id} rating {question_id}.{label} must be integer 1-5"
                    )
        if trial.get("overall_preference") not in packet.get("preference_choices", []):
            issues.append(f"trial {trial_id} overall_preference must be A, B, or tie")
    return normalized_reviewer, issues


def _parse_decision(
    decision: Any,
    *,
    study_id: Any,
    valid_ballots: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    if decision is None:
        return None, []
    issues: list[str] = []
    if not isinstance(decision, dict):
        return None, ["decision must be an object"]
    if decision.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"decision.schema_version must be {SCHEMA_VERSION}")
    if decision.get("study_id") != study_id:
        issues.append("decision.study_id does not match packet")
    if decision.get("decision") not in {"pass", "fail"}:
        issues.append("decision.decision must be pass or fail")
    for field in ("decided_by", "rationale"):
        if not isinstance(decision.get(field), str) or not decision[field].strip():
            issues.append(f"decision.{field} must be a non-empty string")
    if decision.get("human_attestation") is not True:
        issues.append("decision.human_attestation must be true")
    if decision.get("reviewed_valid_ballots") != valid_ballots:
        issues.append("decision.reviewed_valid_ballots must equal parsed valid ballot count")
    return decision if not issues else None, issues


def evaluate_ballots(
    *,
    packet_path: Path,
    ballot_paths: list[Path],
    decision_path: Path | None = None,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    packet = _read_json(packet_path)
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA_VERSION:
        raise ListeningABError(f"packet schema_version must be {SCHEMA_VERSION}")
    packet_hash = _sha256(packet_path)
    receipt_path = receipt_path or packet_path.parent / "organizer-receipt.json"
    receipt = _read_json(receipt_path)
    if not isinstance(receipt, dict):
        raise ListeningABError("organizer receipt must be a JSON object")
    media_issues = _verify_packet_media(packet, receipt, packet_path, packet_hash)
    valid: list[tuple[Path, dict[str, Any], str]] = []
    invalid: list[dict[str, Any]] = []
    reviewers: set[str] = set()
    for path in ballot_paths:
        try:
            ballot = _read_json(path)
            reviewer_id, issues = _validate_ballot(
                ballot,
                packet=packet,
                packet_hash=packet_hash,
            )
        except ListeningABError as exc:
            ballot = None
            reviewer_id = None
            issues = [str(exc)]
        if reviewer_id is not None and reviewer_id in reviewers:
            issues.append(f"duplicate reviewer_id: {reviewer_id}")
        if issues:
            invalid.append({"issues": issues, "path": str(path)})
        else:
            assert isinstance(ballot, dict) and reviewer_id is not None
            reviewers.add(reviewer_id)
            valid.append((path, ballot, reviewer_id))

    trial_summaries: list[dict[str, Any]] = []
    packet_trials = packet.get("trials") if isinstance(packet.get("trials"), list) else []
    for packet_trial in packet_trials:
        trial_id = packet_trial["trial_id"]
        ballots_by_trial = {
            reviewer_id: next(
                trial for trial in ballot["trials"] if trial["trial_id"] == trial_id
            )
            for _, ballot, reviewer_id in valid
        }
        rating_means: dict[str, dict[str, float | None]] = {}
        for question in QUESTION_RUBRIC:
            question_id = question["id"]
            rating_means[question_id] = {}
            for label in ("A", "B"):
                scores = [
                    trial["ratings"][question_id][label]
                    for trial in ballots_by_trial.values()
                ]
                rating_means[question_id][label] = fmean(scores) if scores else None
        preferences = Counter(
            trial["overall_preference"] for trial in ballots_by_trial.values()
        )
        trial_summaries.append(
            {
                "completed_votes": len(ballots_by_trial),
                "preference_counts": {
                    choice: preferences.get(choice, 0) for choice in ("A", "B", "tie")
                },
                "rating_means": rating_means,
                "trial_id": trial_id,
            }
        )

    minimum_votes = packet.get("minimum_completed_votes")
    if isinstance(minimum_votes, bool) or not isinstance(minimum_votes, int) or minimum_votes < 1:
        raise ListeningABError("packet.minimum_completed_votes must be an integer >= 1")
    enough_votes = len(valid) >= minimum_votes
    decision_data = _read_json(decision_path) if decision_path is not None else None
    decision, decision_issues = _parse_decision(
        decision_data,
        study_id=packet.get("study_id"),
        valid_ballots=len(valid),
    )
    integrity_ok = not media_issues
    gate_passed = bool(
        integrity_ok
        and enough_votes
        and decision is not None
        and decision.get("decision") == "pass"
    )
    if not integrity_ok:
        status = "invalid_media_integrity"
    elif not valid:
        status = "pending_human_votes"
    elif not enough_votes:
        status = "insufficient_human_votes"
    elif decision_issues:
        status = "invalid_human_decision"
    elif decision is None:
        status = "awaiting_human_decision"
    elif decision.get("decision") == "fail":
        status = "human_rejected"
    else:
        status = "human_accepted"

    return {
        "automatic_winner_selected": False,
        "decision": decision,
        "decision_issues": decision_issues,
        "descriptive_statistics_only": True,
        "gate_passed": gate_passed,
        "invalid_ballots": invalid,
        "media_integrity": {"issues": media_issues, "passed": integrity_ok},
        "minimum_completed_votes": minimum_votes,
        "packet_sha256": packet_hash,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "study_id": packet.get("study_id"),
        "trials": trial_summaries,
        "valid_ballots": len(valid),
    }


def _create_command(args: argparse.Namespace) -> int:
    spec = _read_json(args.spec)
    if not isinstance(spec, dict):
        raise ListeningABError("study spec must be a JSON object")
    paths = create_packet(
        spec,
        root=args.root,
        output_dir=args.output_dir,
        seed=args.seed,
        force=args.force,
    )
    print(_canonical_json({name: str(path) for name, path in paths.items()}), end="")
    return 0


def _evaluate_command(args: argparse.Namespace) -> int:
    result = evaluate_ballots(
        packet_path=args.packet,
        ballot_paths=args.ballots or [],
        decision_path=args.decision,
        receipt_path=args.receipt,
    )
    if args.output:
        _write_json(args.output, result)
    print(_canonical_json(result), end="")
    return 0 if result["gate_passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and parse deterministic, blinded human listening A/B packets."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create blinded media aliases and ballot files.")
    create.add_argument("--spec", type=Path, required=True)
    create.add_argument("--root", type=Path, default=Path.cwd())
    create.add_argument("--output-dir", type=Path, required=True)
    create.add_argument("--seed", type=int, required=True)
    create.add_argument("--force", action="store_true")
    create.set_defaults(handler=_create_command)

    evaluate = subparsers.add_parser("evaluate", help="Validate ballots and human gate decision.")
    evaluate.add_argument("--packet", type=Path, required=True)
    evaluate.add_argument("--ballots", type=Path, nargs="*")
    evaluate.add_argument("--decision", type=Path)
    evaluate.add_argument("--receipt", type=Path)
    evaluate.add_argument("--output", type=Path)
    evaluate.set_defaults(handler=_evaluate_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ListeningABError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
