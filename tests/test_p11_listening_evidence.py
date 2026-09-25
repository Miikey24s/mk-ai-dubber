import importlib.util
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "p11_listening_evidence.py"

_SPEC = importlib.util.spec_from_file_location("p11_listening_evidence", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(TOOL)


def test_baseline_graph_is_single_pass_without_limiter_and_forces_48k() -> None:
    graph = TOOL._build_baseline_graph(
        background_gain_db=0.0,
        voice_gain_db=1.5,
        final_lufs=-14.0,
        true_peak_db=-1.5,
    )

    assert "loudnorm=I=-14.0:TP=-1.5:LRA=11" in graph
    assert "measured_I=" not in graph
    assert "alimiter=" not in graph
    assert graph.endswith(",aresample=48000[outa]")


def test_audio_sample_metrics_counts_zero_dbfs_and_over_samples(tmp_path: Path) -> None:
    sample = tmp_path / "float.wav"
    values = np.array([0.0, 0.25, -0.5, 0.999, 1.0, -1.1], dtype=np.float32)
    sf.write(sample, values, 48_000, subtype="FLOAT")

    metrics = TOOL._audio_sample_metrics(sample)

    assert metrics["decoded_samples"] == len(values)
    assert metrics["clipping_samples_ge_0dbfs"] == 2
    assert metrics["passes_zero_sample_clipping"] is False
    assert metrics["sample_peak_linear"] == pytest.approx(1.1, rel=1e-5)
