from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vi_dubber.catalog_projection import catalog_item_from_job, rebuild_from_work_dir
from vi_dubber.catalog_store import CatalogItem, CatalogStore, UserState


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_job(work_dir: Path, source: Path, *, job_id: str = "job-fixture-0001") -> Path:
    job_dir = work_dir / job_id
    (job_dir / "manifests").mkdir(parents=True)
    identity = {"sha256": _sha256(source), "size_bytes": source.stat().st_size}
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source": identity,
                "source_path": str(source.resolve()),
                "source_name": source.name,
                "translation": {
                    "provider": "local",
                    "model_id": "fixture-model",
                    "effort": "medium",
                    "catalog_revision": "fixture-catalog-r1",
                },
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "status": "completed",
                "stage": "publish",
                "progress": 1.0,
                "updated_at": "2026-09-27T00:00:00+00:00",
                "metadata": {"input_name": source.name, "input_path": str(source.resolve())},
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "manifests" / "final.json").write_text(
        json.dumps(
            {
                "version": 1,
                "stage": "final",
                "status": "complete",
                "fingerprint": "b" * 64,
                "artifacts": [{"path": "final.mp4", "sha256": "c" * 64, "size_bytes": 10}],
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "segments_source.json").write_text(json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8")
    return job_dir


def test_projection_preserves_source_identity_and_excludes_absolute_paths(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    source_root = tmp_path / "media"
    source_root.mkdir()
    source = source_root / "fixture.mp4"
    source.write_bytes(b"fixture source")
    _write_job(work_dir, source)

    item = catalog_item_from_job(work_dir / "job-fixture-0001", source_root=source_root)
    assert item is not None
    assert item.source_fingerprint == _sha256(source)
    assert item.source_ref == "fixture.mp4"
    assert item.availability == "available"
    assert item.segment_count == 2
    serialized = json.dumps(item.metadata, ensure_ascii=False)
    assert str(source.resolve()) not in serialized
    assert "source_path" not in serialized


def test_rebuild_reopen_search_and_missing_stale_mapping_are_deterministic(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    source_root = tmp_path / "media"
    source_root.mkdir()
    source = source_root / "fixture.mp4"
    source.write_bytes(b"fixture source")
    job_dir = _write_job(work_dir, source)
    invalid = work_dir / "job-invalid"
    invalid.mkdir()

    store = CatalogStore(tmp_path / "catalog.sqlite3")
    report = rebuild_from_work_dir(store, work_dir, source_root=source_root)
    assert report.indexed == 1
    assert report.skipped[0].job_id == "job-invalid"
    first = store.get_item("job-fixture-0001")
    assert first is not None
    assert store.search("FIXTURE") == [first]

    reopened = CatalogStore(tmp_path / "catalog.sqlite3")
    assert reopened.get_item(first.item_id) == first

    source.unlink()
    rebuild_from_work_dir(reopened, work_dir, source_root=source_root)
    assert reopened.get_item(first.item_id).availability == "missing"  # type: ignore[union-attr]

    source.write_bytes(b"changed source")
    rebuild_from_work_dir(reopened, work_dir, source_root=source_root)
    assert reopened.get_item(first.item_id).availability == "stale"  # type: ignore[union-attr]


def test_rebuild_skips_non_finite_job_state_instead_of_poisoning_catalog(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    source = tmp_path / "fixture.mp4"
    source.write_bytes(b"fixture source")
    job_dir = _write_job(work_dir, source)
    state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    state["progress"] = "NaN"
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    store = CatalogStore(tmp_path / "catalog.sqlite3")
    report = rebuild_from_work_dir(store, work_dir, source_root=tmp_path)
    assert report.indexed == 0
    assert report.skipped[0].job_id == job_dir.name


def test_rebuild_preserves_existing_projection_when_all_jobs_are_invalid(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    invalid = work_dir / "job-invalid"
    invalid.mkdir()
    (invalid / "job.json").write_text("{not-json", encoding="utf-8")

    store = CatalogStore(tmp_path / "catalog.sqlite3")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    seeded = CatalogStore(tmp_path / "catalog.sqlite3")
    seeded.rebuild(
        [
            # A direct catalog seed mirrors the already-projected durable row;
            # this test intentionally does not rely on a valid job fixture.
            CatalogItem(
                item_id="job-existing",
                title="Existing",
                source_fingerprint=_sha256(source),
                revision="r1",
                availability="available",
                segment_count=1,
                source_ref="sources/job-existing/source.mp4",
            )
        ]
    )
    seeded.set_user_state(UserState("job-existing", "r1", (), "in_review", 2.0))

    report = rebuild_from_work_dir(seeded, work_dir, source_root=tmp_path)

    assert report.rebuild_applied is False
    assert report.preserved_existing is True
    assert report.reason == "all_jobs_invalid"
    assert seeded.get_item("job-existing") is not None
    assert seeded.get_user_state("job-existing") is not None


def test_rebuild_preserves_existing_projection_when_work_root_is_empty(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    store.rebuild(
        [
            CatalogItem(
                item_id="job-existing",
                title="Existing",
                source_fingerprint=_sha256(source),
                revision="r1",
                availability="available",
                segment_count=1,
                source_ref="sources/job-existing/source.mp4",
            )
        ]
    )
    store.set_user_state(UserState("job-existing", "r1", (), "in_review", 2.0))

    report = rebuild_from_work_dir(store, work_dir, source_root=tmp_path)

    assert report.rebuild_applied is False
    assert report.preserved_existing is True
    assert report.reason == "empty_work_dir"
    assert store.get_item("job-existing") is not None
    assert store.get_user_state("job-existing") is not None


def test_rebuild_preserves_known_invalid_sibling_and_review_state(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    source_root = tmp_path / "media"
    source_root.mkdir()
    valid_source = source_root / "valid.mp4"
    valid_source.write_bytes(b"valid source")
    _write_job(work_dir, valid_source, job_id="job-valid")
    invalid_dir = work_dir / "job-invalid"
    invalid_dir.mkdir()
    (invalid_dir / "job.json").write_text("{not-json", encoding="utf-8")

    store = CatalogStore(tmp_path / "catalog.sqlite3")
    preserved = CatalogItem(
        item_id="job-invalid",
        title="Previously indexed",
        source_fingerprint="a" * 64,
        revision="invalid-lineage-r1",
        availability="available",
        segment_count=2,
        source_ref="sources/job-invalid/source.mp4",
    )
    store.rebuild([preserved])
    store.set_user_state(UserState("job-invalid", preserved.revision, (), "in_review", 4.0))

    report = rebuild_from_work_dir(store, work_dir, source_root=source_root)

    assert report.rebuild_applied is True
    assert report.preserved_existing is True
    assert report.reason == "rebuilt_with_preserved_invalid"
    assert report.indexed == 2
    assert store.get_item("job-valid") is not None
    invalid_item = store.get_item("job-invalid")
    assert invalid_item is not None
    assert invalid_item.availability == "unknown"
    assert invalid_item.metadata["projection_warning"]
    assert store.get_user_state("job-invalid") == UserState("job-invalid", preserved.revision, (), "in_review", 4.0)


def test_rebuild_preserves_existing_projection_when_work_root_is_unavailable(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    store.rebuild(
        [
            CatalogItem(
                item_id="job-existing",
                title="Existing",
                source_fingerprint=_sha256(source),
                revision="r1",
                availability="available",
                segment_count=1,
                source_ref="sources/job-existing/source.mp4",
            )
        ]
    )
    store.set_user_state(UserState("job-existing", "r1", (), "in_review", 2.0))

    report = rebuild_from_work_dir(store, tmp_path / "missing-work", source_root=tmp_path)

    assert report.rebuild_applied is False
    assert report.preserved_existing is True
    assert report.reason == "work_dir_unavailable"
    assert store.get_item("job-existing") is not None
    assert store.get_user_state("job-existing") is not None

