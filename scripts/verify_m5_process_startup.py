"""Offline PREP_ONLY process-startup smoke for the VI Dubber M5 boundary.

This rehearsal starts three fresh Python interpreters against an isolated
temporary workspace:

* ``boot`` projects two synthetic job directories and persists review state;
* ``restart`` imports the real FastAPI application entrypoint, exercises only
  its local health route, then rebuilds the catalog from the persisted files;
* ``lineage_change`` rebuilds after a manifest fingerprint change and verifies
  that review state does not cross the new revision.

The child processes never call a provider, open media, or use the repository's
real ``work/`` directory.  This is a deterministic process-boundary smoke,
not M5 product acceptance.  It deliberately does not claim real media,
browser/UI, cold/warm SLO, or external-service behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


# The child intentionally receives this code as a single, small protocol.  A
# fresh interpreter is the important part: reopening CatalogStore in the
# parent process would not exercise import/startup/restart boundaries.
_CHILD_CODE = r'''
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
phase = sys.argv[2]
work_dir = root / "work"
media_root = root / "media"
db_path = root / "catalog.sqlite3"
job_id = "job-m5-process-0001"

# Importing the API must not create a provider session or mutate the real
# project runtime during this synthetic smoke.  Patch only the runtime paths
# and setup hook before importing vi_dubber.api; no production code is changed
# by this child process.
from vi_dubber import runtime

runtime.WORK_DIR = work_dir
runtime.MODELS_DIR = root / "models"
runtime.configure_runtime = lambda: None

from vi_dubber.artifacts import atomic_write_json
from vi_dubber.catalog_projection import rebuild_from_work_dir
from vi_dubber.catalog_store import CatalogStore, UserState


def _catalog_report() -> tuple[CatalogStore, dict[str, object]]:
    store = CatalogStore(db_path)
    report = rebuild_from_work_dir(store, work_dir, source_root=media_root)
    return store, {
        "indexed": report.indexed,
        "skipped": [issue.job_id for issue in report.skipped],
    }


if phase == "boot":
    store, report = _catalog_report()
    item = store.get_item(job_id)
    if item is None:
        raise AssertionError("boot did not project the synthetic job")
    store.set_user_state(
        UserState(
            item_id=job_id,
            revision=item.revision,
            bookmarks=({"segment_index": 1, "position_seconds": 1.25},),
            review_state="in_review",
            watch_position_seconds=1.5,
        )
    )
    payload = {
        "phase": phase,
        "report": report,
        "state_persisted": store.get_user_state(job_id) is not None,
        "revision": item.revision,
    }
elif phase == "restart":
    # Importing the actual app entrypoint is part of this process boundary.
    # The health route is local and deterministic; it does not touch provider
    # transport, catalog/network routes, media decode, or the real work tree.
    import vi_dubber.api as api
    from fastapi.testclient import TestClient

    with TestClient(api.api_app) as client:
        health = client.get("/api/health")
    if health.status_code != 200:
        raise AssertionError(f"health route failed: {health.status_code}")
    health_payload = health.json()
    if health_payload.get("status") != "ok" or health_payload.get("service") != "vi-dubber":
        raise AssertionError(f"unexpected health payload: {health_payload!r}")

    store, report = _catalog_report()
    state = store.get_user_state(job_id)
    payload = {
        "phase": phase,
        "report": report,
        "app_entrypoint_imported": True,
        "health_status": health.status_code,
        "health_service": health_payload.get("service"),
        "state_retained": state is not None and state.review_state == "in_review" and state.watch_position_seconds == 1.5,
    }
elif phase == "lineage_change":
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
    store, report = _catalog_report()
    payload = {
        "phase": phase,
        "report": report,
        "state_invalidated": store.get_user_state(job_id) is None,
    }
else:
    raise SystemExit(f"unsupported smoke phase: {phase}")

print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
'''


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_job(work_dir: Path, media_root: Path, *, job_id: str, title: str, manifest_fingerprint: str) -> None:
    source_dir = media_root / job_id
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "source.mp4"
    source.write_bytes(f"synthetic source for {job_id}\n".encode("utf-8"))

    job_dir = work_dir / job_id
    manifests = job_dir / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    from vi_dubber.artifacts import atomic_write_json

    atomic_write_json(
        job_dir / "job.json",
        {
            "version": 1,
            "source": {"sha256": _sha256(source), "size_bytes": source.stat().st_size},
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


def _write_invalid_job(work_dir: Path) -> None:
    invalid = work_dir / "job-invalid"
    invalid.mkdir(parents=True, exist_ok=True)
    (invalid / "job.json").write_text("{not-json", encoding="utf-8")


def _run_child(root: Path, phase: str, *, env: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_CODE, str(root), phase],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "child produced no diagnostics").strip()
        raise RuntimeError(f"{phase} child failed with exit {completed.returncode}: {detail}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{phase} child returned non-JSON output: {completed.stdout!r}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{phase} child returned a non-object receipt")
    return value


def run_smoke() -> dict[str, Any]:
    """Run the isolated three-process startup/restart rehearsal."""
    with tempfile.TemporaryDirectory(prefix="vi-dubber-m5-process-") as raw_root:
        root = Path(raw_root)
        work_dir = root / "work"
        media_root = root / "media"
        work_dir.mkdir()
        media_root.mkdir()
        job_id = "job-m5-process-0001"
        _write_job(
            work_dir,
            media_root,
            job_id=job_id,
            title="Process recovery fixture.mp4",
            manifest_fingerprint="a" * 64,
        )
        _write_job(
            work_dir,
            media_root,
            job_id="job-m5-process-0002",
            title="Process recovery fixture two.mp4",
            manifest_fingerprint="b" * 64,
        )
        _write_invalid_job(work_dir)

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(SRC_ROOT), env.get("PYTHONPATH", "")) if value
        )
        # Keep the smoke provider-free even if the parent shell has optional
        # credentials configured.  No provider route is called in the child.
        for key in ("TYPESAFE_API_KEY", "HUGGINGFACE_TOKEN", "OPENAI_API_KEY"):
            env.pop(key, None)
        env["PYTHONWARNINGS"] = "ignore"

        boot = _run_child(root, "boot", env=env)
        restart = _run_child(root, "restart", env=env)
        lineage_change = _run_child(root, "lineage_change", env=env)

        assert boot["report"] == {"indexed": 2, "skipped": ["job-invalid"]}
        assert boot["state_persisted"] is True
        assert restart["report"] == {"indexed": 2, "skipped": ["job-invalid"]}
        assert restart["app_entrypoint_imported"] is True
        assert restart["health_status"] == 200
        assert restart["health_service"] == "vi-dubber"
        assert restart["state_retained"] is True
        assert lineage_change["report"] == {"indexed": 2, "skipped": ["job-invalid"]}
        assert lineage_change["state_invalidated"] is True

        return {
            "status": "PREP_ONLY",
            "smoke": "m5-process-startup-restart-v1",
            "scope": {
                "fresh_python_processes": 3,
                "jobs": 2,
                "segments_per_job": 2,
                "media": "synthetic local bytes only",
                "provider": False,
                "real_media": False,
                "real_work_dir": False,
                "job12_touched": False,
                "api_route": "/api/health only",
            },
            "observations": {
                "boot_indexed": boot["report"]["indexed"],
                "boot_skipped": boot["report"]["skipped"],
                "restart_indexed": restart["report"]["indexed"],
                "restart_app_entrypoint_imported": restart["app_entrypoint_imported"],
                "restart_health_status": restart["health_status"],
                "restart_state_retained_same_revision": restart["state_retained"],
                "lineage_change_invalidated_state": lineage_change["state_invalidated"],
            },
            "claims_excluded": [
                "M0 baseline acceptance",
                "M5 product/UI acceptance",
                "real authorized media or playback",
                "application server network/listening or browser acceptance",
                "provider/OAuth/Drive behavior",
                "cold/warm p95 product SLO",
                "Job12 completion or media quality",
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON receipt path")
    args = parser.parse_args()
    receipt = run_smoke()
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
