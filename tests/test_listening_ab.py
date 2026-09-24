import importlib.util
import json
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "listening_ab.py"

_SPEC = importlib.util.spec_from_file_location("vi_dubber_listening_ab", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(TOOL)


def _write_media(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _spec() -> dict:
    return {
        "schema_version": 1,
        "study_id": "study-1",
        "minimum_completed_votes": 1,
        "trials": [
            {
                "id": "clip-1",
                "fixture_id": "fixture-1",
                "reference": {"path": "media/reference.wav", "provenance": {"kind": "source"}},
                "candidates": [
                    {"id": "baseline", "path": "media/baseline.wav"},
                    {"id": "candidate", "path": "media/candidate.wav"},
                ],
            }
        ],
    }


def _create(tmp_path: Path, output_name: str = "packet", seed: int = 17) -> Path:
    _write_media(tmp_path / "media" / "reference.wav", b"reference")
    _write_media(tmp_path / "media" / "baseline.wav", b"baseline")
    _write_media(tmp_path / "media" / "candidate.wav", b"candidate")
    output = tmp_path / output_name
    TOOL.create_packet(_spec(), root=tmp_path, output_dir=output, seed=seed)
    return output


def _completed_ballot(output: Path, reviewer_id: str = "listener-1") -> dict:
    ballot = json.loads((output / "ballot-template.json").read_text(encoding="utf-8"))
    ballot["reviewer_id"] = reviewer_id
    ballot["blinding_confirmed"] = True
    for trial in ballot["trials"]:
        trial["overall_preference"] = "tie"
        for scores in trial["ratings"].values():
            scores["A"] = 4
            scores["B"] = 4
    return ballot


def test_create_packet_is_deterministic_and_hides_source_mapping(tmp_path: Path) -> None:
    first = _create(tmp_path, "first", seed=90210)
    second = _create(tmp_path, "second", seed=90210)

    first_packet = (first / "packet.json").read_bytes()
    second_packet = (second / "packet.json").read_bytes()
    assert first_packet == second_packet
    assert b'"baseline"' not in first_packet
    assert b'"candidate"' not in first_packet
    assert b"media/baseline.wav" not in first_packet
    assert b"media/candidate.wav" not in first_packet

    receipt = json.loads((first / "organizer-receipt.json").read_text(encoding="utf-8"))
    mapping = receipt["trials"][0]["candidate_mapping"]
    assert {item["candidate_id"] for item in mapping} == {"baseline", "candidate"}
    assert receipt["blinding"]["seed"] == 90210
    assert receipt["blinding"]["media_copied"] is False
    for item in mapping:
        assert os.path.samefile(first / item["alias_path"], tmp_path / item["source_path"])


def test_different_seed_changes_candidate_mapping(tmp_path: Path) -> None:
    first = _create(tmp_path, "first", seed=1)
    second = _create(tmp_path, "second", seed=5)
    first_receipt = json.loads((first / "organizer-receipt.json").read_text(encoding="utf-8"))
    second_receipt = json.loads((second / "organizer-receipt.json").read_text(encoding="utf-8"))
    first_mapping = [
        item["candidate_id"] for item in first_receipt["trials"][0]["candidate_mapping"]
    ]
    second_mapping = [
        item["candidate_id"] for item in second_receipt["trials"][0]["candidate_mapping"]
    ]
    assert first_mapping != second_mapping


def test_evaluate_without_votes_fails_closed(tmp_path: Path) -> None:
    output = _create(tmp_path)
    result = TOOL.evaluate_ballots(packet_path=output / "packet.json", ballot_paths=[])

    assert result["status"] == "pending_human_votes"
    assert result["valid_ballots"] == 0
    assert result["gate_passed"] is False
    assert result["automatic_winner_selected"] is False


def test_incomplete_ballot_is_invalid_and_cannot_pass(tmp_path: Path) -> None:
    output = _create(tmp_path)
    ballot = json.loads((output / "ballot-template.json").read_text(encoding="utf-8"))
    ballot["reviewer_id"] = "listener-1"
    ballot["blinding_confirmed"] = True
    ballot_path = output / "incomplete.json"
    ballot_path.write_text(json.dumps(ballot), encoding="utf-8")

    result = TOOL.evaluate_ballots(
        packet_path=output / "packet.json",
        ballot_paths=[ballot_path],
    )

    assert result["status"] == "pending_human_votes"
    assert result["valid_ballots"] == 0
    assert result["invalid_ballots"]
    assert result["gate_passed"] is False


def test_valid_votes_still_require_explicit_human_decision(tmp_path: Path) -> None:
    output = _create(tmp_path)
    ballot_path = output / "listener-1.json"
    ballot_path.write_text(json.dumps(_completed_ballot(output)), encoding="utf-8")

    result = TOOL.evaluate_ballots(
        packet_path=output / "packet.json",
        ballot_paths=[ballot_path],
    )

    assert result["valid_ballots"] == 1
    assert result["status"] == "awaiting_human_decision"
    assert result["gate_passed"] is False
    assert result["trials"][0]["preference_counts"] == {"A": 0, "B": 0, "tie": 1}


def test_human_attested_pass_is_required_for_gate_pass(tmp_path: Path) -> None:
    output = _create(tmp_path)
    ballot_path = output / "listener-1.json"
    ballot_path.write_text(json.dumps(_completed_ballot(output)), encoding="utf-8")
    decision = {
        "schema_version": 1,
        "study_id": "study-1",
        "decision": "pass",
        "decided_by": "human-review-owner",
        "rationale": "Reviewed the completed blind ballot and accepted quality parity.",
        "human_attestation": True,
        "reviewed_valid_ballots": 1,
    }
    decision_path = output / "decision.json"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")

    result = TOOL.evaluate_ballots(
        packet_path=output / "packet.json",
        ballot_paths=[ballot_path],
        decision_path=decision_path,
    )

    assert result["status"] == "human_accepted"
    assert result["gate_passed"] is True
    assert result["automatic_winner_selected"] is False


def test_tampered_media_invalidates_gate_even_with_human_decision(tmp_path: Path) -> None:
    output = _create(tmp_path)
    ballot_path = output / "listener-1.json"
    ballot_path.write_text(json.dumps(_completed_ballot(output)), encoding="utf-8")
    decision_path = output / "decision.json"
    decision_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "study_id": "study-1",
                "decision": "pass",
                "decided_by": "human-review-owner",
                "rationale": "Blind review completed.",
                "human_attestation": True,
                "reviewed_valid_ballots": 1,
            }
        ),
        encoding="utf-8",
    )
    packet = json.loads((output / "packet.json").read_text(encoding="utf-8"))
    media_path = output / packet["trials"][0]["candidates"][0]["media_path"]
    media_path.unlink()
    media_path.write_bytes(b"tampered")

    result = TOOL.evaluate_ballots(
        packet_path=output / "packet.json",
        ballot_paths=[ballot_path],
        decision_path=decision_path,
    )

    assert result["status"] == "invalid_media_integrity"
    assert result["gate_passed"] is False
    assert result["media_integrity"]["issues"]


def test_create_rejects_media_outside_project_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _write_media(tmp_path / "outside.wav", b"outside")
    _write_media(root / "media" / "baseline.wav", b"baseline")
    _write_media(root / "media" / "candidate.wav", b"candidate")
    spec = _spec()
    spec["trials"][0]["reference"]["path"] = "../outside.wav"

    with pytest.raises(TOOL.ListeningABError, match="escapes --root"):
        TOOL.create_packet(spec, root=root, output_dir=root / "packet", seed=1)


def test_retained_sample_is_blinded_and_explicitly_pending_human_votes() -> None:
    sample_dir = REPO_ROOT / "work" / "benchmarks" / "listening-ab"
    packet_path = sample_dir / "packet.json"
    packet_text = packet_path.read_text(encoding="utf-8")
    receipt = json.loads((sample_dir / "organizer-receipt.json").read_text(encoding="utf-8"))
    pending = json.loads((sample_dir / "evaluation-no-votes.json").read_text(encoding="utf-8"))

    assert "retained-legacy" not in packet_text
    assert "retained-smart" not in packet_text
    assert "before-legacy.mp4" not in packet_text
    assert "after-smart.mp4" not in packet_text
    assert receipt["blinding"]["seed"] == 20260923
    assert receipt["blinding"]["media_copied"] is False
    assert {question["id"] for question in receipt["question_rubric"]} == {
        "voice_similarity_reference_stability",
        "naturalness",
        "timing",
        "mix",
    }
    assert TOOL._verify_packet_media(
        json.loads(packet_text),
        receipt,
        packet_path,
        TOOL._sha256(packet_path),
    ) == []
    assert pending["status"] == "pending_human_votes"
    assert pending["valid_ballots"] == 0
    assert pending["gate_passed"] is False
