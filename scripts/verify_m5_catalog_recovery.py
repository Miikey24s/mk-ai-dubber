"""Offline PREP_ONLY rehearsal for the M5 catalog recovery boundary.

The rehearsal uses small synthetic job metadata and source fixtures.  It proves
that the local catalog can be rebuilt after a process restart, preserve review
state for the same manifest lineage, drop that state when the lineage changes,
and round-trip a metadata-only backup into a fresh SQLite file.  It deliberately
does not start the app, touch a real job, open media playback, call a provider,
or claim M5 product acceptance.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from vi_dubber.artifacts import atomic_write_json
from vi_dubber.catalog_projection import rebuild_from_work_dir
from vi_dubber.catalog_store import CatalogIntegrityError, CatalogStore, UserState


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_job(work_dir: Path, media_root: Path, *, job_id: str, title: str, manifest_fingerprint: str) -> Path:
    """Write only the metadata shape consumed by the M5 projection."""
    source_dir = media_root / job_id
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "source.mp4"
    source.write_bytes(f"synthetic source for {job_id}\n".encode("utf-8"))

    job_dir = work_dir / job_id
    manifests = job_dir / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        job_dir / "job.json",
        {
            "version": 1,
            "source": {"sha256": _sha256(source), "size_bytes": source.stat().st_size},
            # Absolute paths are source-job metadata only.  The projection must
            # replace them with a portable source_ref before persistence.
            "source_path": str(source.resolve()),
            "source_name": title,
            "translation": {
                "provider": "offline-fixture",
                "model_id": "fixture-model",
                "effort": "medium",
                "catalog_revision": "fixture-catalog-r1",
            },
        },
    )
    atomic_write_json(
        job_dir / "state.json",
        {
            "version": 1,
            "status": "completed",
            "stage": "complete",
            "progress": 1.0,
            "metadata": {"input_name": title, "input_path": str(source.resolve())},
        },
    )
    atomic_write_json(
        manifests / "final.json",
        {
            "version": 1,
            "stage": "final",
            "status": "complete",
            "fingerprint": manifest_fingerprint,
            "artifacts": [],
        },
    )
    atomic_write_json(
        job_dir / "segments_source.json",
        [{"id": 0, "start": 0.0, "end": 1.0}, {"id": 1, "start": 1.0, "end": 2.0}],
    )
    return job_dir


def _write_invalid_job(work_dir: Path) -> None:
    invalid = work_dir / "job-invalid"
    invalid.mkdir(parents=True, exist_ok=True)
    # A malformed/incomplete job is intentionally ignored by the projection.
    (invalid / "job.json").write_text("{not-json", encoding="utf-8")


def run_rehearsal() -> dict[str, Any]:
    """Run the local restart/restore rehearsal in an isolated temp directory."""
    with tempfile.TemporaryDirectory(prefix="vi-dubber-m5-recovery-") as raw_root:
        root = Path(raw_root)
        work_dir = root / "work"
        media_root = root / "media"
        work_dir.mkdir()
        media_root.mkdir()
        job_id = "job-m5-recovery-0001"
        second_job_id = "job-m5-recovery-0002"
        _write_job(
            work_dir,
            media_root,
            job_id=job_id,
            title="Recovery fixture one.mp4",
            manifest_fingerprint="a" * 64,
        )
        _write_job(
            work_dir,
            media_root,
            job_id=second_job_id,
            title="Recovery fixture two.mp4",
            manifest_fingerprint="b" * 64,
        )
        _write_invalid_job(work_dir)

        db_path = root / "catalog.sqlite3"
        backup_path = root / "catalog-backup.json"
        restored_db_path = root / "restored.sqlite3"

        first_store = CatalogStore(db_path)
        first_report = rebuild_from_work_dir(first_store, work_dir, source_root=media_root)
        first_item = first_store.get_item(job_id)
        assert first_item is not None
        assert first_item.availability == "available"
        assert first_item.source_ref == f"{job_id}/source.mp4"
        assert first_item.segment_count == 2
        first_store.set_user_state(
            UserState(
                item_id=job_id,
                revision=first_item.revision,
                bookmarks=({"segment_index": 1, "position_seconds": 1.25},),
                review_state="in_review",
                watch_position_seconds=1.5,
            )
        )

        # Simulate app restart: reopen the same SQLite file and rebuild from
        # immutable job/manifests.  The exact lineage must retain user state.
        restarted_store = CatalogStore(db_path)
        restarted_report = rebuild_from_work_dir(restarted_store, work_dir, source_root=media_root)
        retained_state = restarted_store.get_user_state(job_id)
        assert retained_state is not None
        assert retained_state.review_state == "in_review"
        assert retained_state.watch_position_seconds == 1.5
        restart_snapshot = restarted_store.export_metadata()

        # A second restart against the same immutable job metadata must be
        # idempotent: no duplicate rows, no revision drift and no loss of
        # review state. ``export_metadata`` intentionally omits volatile
        # SQLite timestamps, so this is a stable content comparison.
        second_restart_report = rebuild_from_work_dir(restarted_store, work_dir, source_root=media_root)
        assert second_restart_report == restarted_report
        assert restarted_store.export_metadata() == restart_snapshot
        assert restarted_store.get_user_state(job_id) == retained_state
        restart_idempotent = second_restart_report == restarted_report and restarted_store.export_metadata() == restart_snapshot

        backup_payload = restarted_store.backup_to(backup_path)
        restored_store = CatalogStore(restored_db_path)
        restored_count = restored_store.restore_from(backup_path)
        assert restored_count == 2
        assert restored_store.export_metadata() == backup_payload
        # Restoring the same verified snapshot twice is also idempotent. This
        # models a crash after commit but before the caller records completion.
        assert restored_store.restore_from(backup_path) == restored_count
        assert restored_store.export_metadata() == backup_payload
        restore_idempotent = restored_store.export_metadata() == backup_payload

        # Digest-valid structural corruption must fail before SQLite mutation.
        # Keep a baseline so the no-data-loss invariant is checked directly on
        # the already-populated target rather than only on a fresh database.
        before_tamper = restored_store.export_metadata()
        tampered = copy.deepcopy(backup_payload)
        tampered["catalog"][0]["title"] = "tampered after backup"
        try:
            restored_store.restore_metadata(tampered)
        except CatalogIntegrityError:
            pass
        else:  # pragma: no cover - assertion documents the acceptance gate
            raise AssertionError("tampered backup unexpectedly restored")
        assert restored_store.export_metadata() == before_tamper
        tampered_backup_rejected_without_data_loss = restored_store.export_metadata() == before_tamper

        # A temporarily unavailable work root must never be interpreted as an
        # empty catalog. Existing metadata and review state stay available so
        # the next startup can retry after the mount/process recovers.
        unavailable_report = rebuild_from_work_dir(
            restarted_store,
            root / "temporarily-unavailable-work",
            source_root=media_root,
        )
        assert unavailable_report.rebuild_applied is False
        assert unavailable_report.preserved_existing is True
        assert restarted_store.export_metadata() == restart_snapshot
        assert restarted_store.get_user_state(job_id) == retained_state
        unavailable_work_preserved_existing = (
            unavailable_report.preserved_existing and restarted_store.export_metadata() == restart_snapshot
        )

        # A changed stage manifest creates a new lineage revision.  Review
        # state must not cross that boundary on the next rebuild.
        atomic_write_json(
            work_dir / job_id / "manifests" / "final.json",
            {
                "version": 1,
                "stage": "final",
                "status": "complete",
                "fingerprint": "c" * 64,
                "artifacts": [],
            },
        )
        invalidated_report = rebuild_from_work_dir(restarted_store, work_dir, source_root=media_root)
        assert restarted_store.get_user_state(job_id) is None
        revised_item = restarted_store.get_item(job_id)
        assert revised_item is not None and revised_item.revision != first_item.revision

        receipt = {
            "status": "PREP_ONLY",
            "rehearsal": "m5-catalog-startup-restart-restore-v2",
            "scope": {
                "jobs": 2,
                "segments_per_job": 2,
                "media": "synthetic local bytes only",
                "provider": False,
                "app_server": False,
                "job12_touched": False,
                "real_media": False,
            },
            "observations": {
                "initial_indexed": first_report.indexed,
                "initial_skipped": [issue.job_id for issue in first_report.skipped],
                "restart_indexed": restarted_report.indexed,
                "restart_idempotent": restart_idempotent,
                "restart_state_retained_same_revision": retained_state is not None,
                "backup_restore_count": restored_count,
                "backup_round_trip_equal": restored_store.export_metadata() == backup_payload,
                "restore_idempotent": restore_idempotent,
                "tampered_backup_rejected_without_data_loss": tampered_backup_rejected_without_data_loss,
                "unavailable_work_preserved_existing": unavailable_work_preserved_existing,
                "changed_lineage_invalidated_state": restarted_store.get_user_state(job_id) is None,
                "changed_lineage_rebuild_indexed": invalidated_report.indexed,
                "portable_source_ref": first_item.source_ref,
            },
            "claims_excluded": [
                "M0 baseline acceptance",
                "M5 product/UI acceptance",
                "real authorized media or playback",
                "multi-process/application startup integration",
                "provider/OAuth/Drive behavior",
                "Job12 completion or media quality",
                "cold/warm p95 product SLO",
            ],
        }
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON receipt path")
    args = parser.parse_args()
    receipt = run_rehearsal()
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
