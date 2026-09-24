import json
from pathlib import Path

import pytest

from vi_dubber.metrics import MetricsRecorder, cuda_memory_snapshot


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_metrics_records_stage_counters_and_run_metadata(tmp_path: Path) -> None:
    clock = FakeClock()
    metrics = MetricsRecorder(
        run_kind="resume",
        cache={"resume": True, "manifest": tmp_path / "stage.json"},
        metadata={"fixture": "short", "attempt": 2},
        enable_cuda=False,
        clock=clock,
    )

    with metrics.stage("translation"):
        clock.advance(1.25)
    with metrics.stage("translation"):
        clock.advance(0.75)
    metrics.increment("translation_calls", 2)
    metrics.increment("cache_hits")
    metrics.set_counter("source_segments", 17)
    clock.advance(0.5)
    metrics.finish()

    snapshot = metrics.snapshot()
    assert snapshot["schema_version"] == 1
    assert snapshot["run"]["kind"] == "resume"
    assert snapshot["run"]["cache"]["manifest"] == str(tmp_path / "stage.json")
    assert snapshot["stages"]["translation"] == {
        "wall_seconds": 2.0,
        "calls": 2,
        "failed_calls": 0,
    }
    assert snapshot["counters"]["translation_calls"] == 2
    assert snapshot["counters"]["cache_hits"] == 1
    assert snapshot["counters"]["source_segments"] == 17
    assert snapshot["total_wall_seconds"] == 2.5
    assert snapshot["resources"]["torch_cuda_allocator"] == {
        "available": False,
        "reason": "disabled",
    }

    json.dumps(snapshot, allow_nan=False)


def test_failed_stage_is_recorded_without_swallowing_error() -> None:
    clock = FakeClock()
    metrics = MetricsRecorder(enable_cuda=False, clock=clock)

    with pytest.raises(RuntimeError, match="boom"):
        with metrics.stage("tts_pass_1"):
            clock.advance(0.4)
            raise RuntimeError("boom")

    item = metrics.snapshot()["stages"]["tts_pass_1"]
    assert item["wall_seconds"] == pytest.approx(0.4)
    assert item["calls"] == 1
    assert item["failed_calls"] == 1


def test_finish_freezes_total_elapsed_time() -> None:
    clock = FakeClock()
    metrics = MetricsRecorder(enable_cuda=False, clock=clock)
    clock.advance(3.0)
    metrics.finish()
    clock.advance(20.0)

    assert metrics.snapshot()["total_wall_seconds"] == 3.0
    with pytest.raises(RuntimeError, match="already finished"):
        with metrics.stage("late_stage"):
            pass

    with pytest.raises(RuntimeError, match="already finished"):
        metrics.increment("cache_hits")
    with pytest.raises(RuntimeError, match="already finished"):
        metrics.set_counter("source_segments", 1)


def test_finish_rejects_active_stage() -> None:
    clock = FakeClock()
    metrics = MetricsRecorder(enable_cuda=False, clock=clock)

    with metrics.stage("translation"):
        clock.advance(1.0)
        with pytest.raises(RuntimeError, match="stage is active"):
            metrics.finish()
        clock.advance(1.0)

    metrics.finish()
    snapshot = metrics.snapshot()
    assert snapshot["total_wall_seconds"] == 2.0
    assert snapshot["stages"]["translation"]["wall_seconds"] == 2.0


def test_finished_snapshots_are_stable() -> None:
    clock = FakeClock()
    metrics = MetricsRecorder(enable_cuda=False, clock=clock)
    clock.advance(2.0)
    metrics.finish()

    first = metrics.snapshot()
    clock.advance(50.0)
    second = metrics.snapshot()
    assert second == first


def test_counter_accepts_custom_names_but_rejects_non_integer_values() -> None:
    metrics = MetricsRecorder(enable_cuda=False)
    metrics.increment("custom_counter", 3)
    assert metrics.snapshot()["counters"]["custom_counter"] == 3

    with pytest.raises(TypeError):
        metrics.increment("custom_counter", 1.5)  # type: ignore[arg-type]


def test_unknown_standard_counters_are_not_reported_as_zero() -> None:
    metrics = MetricsRecorder(enable_cuda=False)
    snapshot = metrics.snapshot()
    assert "tts_inferences" in snapshot["counter_schema"]
    assert "tts_inferences" not in snapshot["counters"]


def test_failed_stage_persists_failure_snapshot(tmp_path: Path) -> None:
    clock = FakeClock()
    target = tmp_path / "metrics.json"
    metrics = MetricsRecorder(enable_cuda=False, clock=clock, failure_path=target)

    with pytest.raises(RuntimeError, match="boom"):
        with metrics.stage("asr"):
            clock.advance(0.5)
            raise RuntimeError("boom")

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["stages"]["asr"]["failed_calls"] == 1
    assert saved["total_wall_seconds"] == 0.5


def test_cuda_probe_disabled_is_safe_and_import_free(monkeypatch: pytest.MonkeyPatch) -> None:
    import vi_dubber.metrics as metrics_module

    def unexpected_import(name: str):
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(metrics_module.importlib, "import_module", unexpected_import)
    assert cuda_memory_snapshot(enabled=False) == {"available": False, "reason": "disabled"}


def test_cuda_probe_handles_missing_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    import vi_dubber.metrics as metrics_module

    def missing_torch(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(metrics_module.importlib, "import_module", missing_torch)
    assert cuda_memory_snapshot(enabled=True) == {
        "available": False,
        "reason": "torch_unavailable:ModuleNotFoundError",
    }


def test_invalid_run_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported run_kind"):
        MetricsRecorder(run_kind="other")  # type: ignore[arg-type]


def test_snapshot_write_preserves_previous_file_and_cleans_temp_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vi_dubber.metrics as metrics_module

    target = tmp_path / "metrics.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    metrics = MetricsRecorder(enable_cuda=False)

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(metrics_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        metrics.write_snapshot(target)

    assert target.read_text(encoding="utf-8") == '{"old": true}\n'
    assert list(tmp_path.glob(".metrics.json.*.tmp")) == []
