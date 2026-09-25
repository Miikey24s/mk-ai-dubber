from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from vi_dubber.media import build_mix_filter_graph, measure_mix_metrics, mux_dubbed_video
from vi_dubber.runtime import PROJECT_ROOT, ffmpeg_exe, ffprobe_exe


DEFAULT_VIDEO = "work/benchmarks/CP2-short-smart-source.mp4"
DEFAULT_BACKGROUND = (
    "work/job-4c8236a32c685a11/stems/"
    "original_(Instrumental)_model_bs_roformer_ep_317_sdr_12.wav"
)
DEFAULT_VOICE = "work/job-4c8236a32c685a11/voice_vi.wav"
DEFAULT_OUTPUT = "work/benchmarks/p11-blind-ab-20260925-v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _project_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes project root: {value}")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _build_baseline_graph(
    *,
    background_gain_db: float,
    voice_gain_db: float,
    final_lufs: float,
    true_peak_db: float,
) -> str:
    graph = build_mix_filter_graph(
        background_gain_db=background_gain_db,
        voice_gain_db=voice_gain_db,
        final_lufs=final_lufs,
        true_peak_db=true_peak_db,
        duck_background=False,
        loudnorm_measurements=None,
        output_peak_ceiling_db=None,
    )
    if not graph.endswith("[outa]"):
        raise RuntimeError("unexpected mix graph shape")
    return graph.removesuffix("[outa]") + ",aresample=48000[outa]"


def _render_baseline(
    *,
    video: Path,
    background: Path,
    voice: Path,
    output: Path,
    background_gain_db: float,
    voice_gain_db: float,
    final_lufs: float,
    true_peak_db: float,
) -> str:
    graph = _build_baseline_graph(
        background_gain_db=background_gain_db,
        voice_gain_db=voice_gain_db,
        final_lufs=final_lufs,
        true_peak_db=true_peak_db,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(ffmpeg_exe()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(background),
            "-i",
            str(voice),
            "-filter_complex",
            graph,
            "-map",
            "0:v:0",
            "-map",
            "[outa]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"baseline FFmpeg render failed:\n{result.stderr.strip()}")
    return graph


def _audio_stream_info(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(ffprobe_exe()),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,channel_layout,duration,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{result.stderr.strip()}")
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    if len(streams) != 1:
        raise RuntimeError(f"expected exactly one audio stream in {path}")
    return streams[0]


def _audio_sample_metrics(path: Path) -> dict[str, float | int | bool]:
    process = subprocess.Popen(
        [
            str(ffmpeg_exe()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vn",
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("failed to open FFmpeg pipes")

    decoded_samples = 0
    clipping_samples = 0
    peak_linear = 0.0
    while True:
        raw = process.stdout.read(4 * 262_144)
        if not raw:
            break
        aligned_size = len(raw) - (len(raw) % 4)
        samples = np.frombuffer(raw[:aligned_size], dtype="<f4")
        if samples.size == 0:
            continue
        absolute = np.abs(samples)
        peak_linear = max(peak_linear, float(absolute.max()))
        clipping_samples += int(np.count_nonzero(absolute >= 1.0))
        decoded_samples += int(samples.size)

    stderr = process.stderr.read().decode("utf-8", errors="replace")
    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(f"FFmpeg PCM decode failed for {path}:\n{stderr.strip()}")
    peak_dbfs = 20.0 * math.log10(peak_linear) if peak_linear > 0 else float("-inf")
    return {
        "decoded_samples": decoded_samples,
        "clipping_samples_ge_0dbfs": clipping_samples,
        "passes_zero_sample_clipping": clipping_samples == 0,
        "sample_peak_linear": peak_linear,
        "sample_peak_dbfs": peak_dbfs,
    }


def _load_listening_ab():
    tool_path = Path(__file__).resolve().with_name("listening_ab.py")
    spec = importlib.util.spec_from_file_location("vi_dubber_listening_ab", tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load listening tool: {tool_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_evidence(
    *,
    root: Path,
    video_rel: str,
    background_rel: str,
    voice_rel: str,
    output_rel: str,
    seed: int,
    background_gain_db: float,
    voice_gain_db: float,
    target_lufs: float,
    target_true_peak_db: float,
    force: bool,
) -> dict[str, Path]:
    root = root.resolve()
    video = _project_path(root, video_rel)
    background = _project_path(root, background_rel)
    voice = _project_path(root, voice_rel)
    output_dir = (root / output_rel).resolve()
    if not output_dir.is_relative_to(root):
        raise ValueError(f"output escapes project root: {output_rel}")
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline = output_dir / "baseline-single-pass-48k.mp4"
    candidate = output_dir / "candidate-two-pass-limiter.mp4"
    protected = [
        baseline,
        candidate,
        output_dir / "pair-provenance.json",
        output_dir / "study-spec.json",
    ]
    if not force:
        existing = [path for path in protected if path.exists()]
        if existing:
            raise FileExistsError(f"evidence outputs already exist: {existing[0]}")

    baseline_graph = _render_baseline(
        video=video,
        background=background,
        voice=voice,
        output=baseline,
        background_gain_db=background_gain_db,
        voice_gain_db=voice_gain_db,
        final_lufs=target_lufs,
        true_peak_db=target_true_peak_db,
    )
    mux_dubbed_video(
        video,
        background,
        voice,
        candidate,
        background_gain_db=background_gain_db,
        voice_gain_db=voice_gain_db,
        final_lufs=target_lufs,
        true_peak_db=target_true_peak_db,
        duck_background=False,
    )

    baseline_stream = _audio_stream_info(baseline)
    candidate_stream = _audio_stream_info(candidate)
    for field in ("codec_name", "sample_rate", "channels", "channel_layout"):
        if baseline_stream.get(field) != candidate_stream.get(field):
            raise RuntimeError(
                f"A/B audio format mismatch for {field}: "
                f"{baseline_stream.get(field)!r} != {candidate_stream.get(field)!r}"
            )

    baseline_metrics = {
        **measure_mix_metrics(
            baseline,
            target_lufs=target_lufs,
            target_true_peak_db=target_true_peak_db,
        ),
        **_audio_sample_metrics(baseline),
    }
    candidate_metrics = {
        **measure_mix_metrics(
            candidate,
            target_lufs=target_lufs,
            target_true_peak_db=target_true_peak_db,
        ),
        **_audio_sample_metrics(candidate),
    }

    provenance = {
        "schema_version": 2,
        "purpose": "P11 controlled blind A/B; no subjective winner selected automatically",
        "fixture": {
            "id": "CP2-short-smart-source",
            "source_path": video_rel,
            "source_sha256": _sha256(video),
        },
        "shared_inputs": {
            "background_path": background_rel,
            "background_sha256": _sha256(background),
            "voice_path": voice_rel,
            "voice_sha256": _sha256(voice),
            "background_gain_db": background_gain_db,
            "voice_gain_db": voice_gain_db,
            "target_lufs": target_lufs,
            "target_true_peak_db": target_true_peak_db,
            "duck_background": False,
            "audio_codec": "aac",
            "audio_bitrate": "192k",
            "audio_sample_rate_hz": int(baseline_stream["sample_rate"]),
            "audio_channels": int(baseline_stream["channels"]),
        },
        "baseline": {
            "id": "single-pass-loudnorm-no-limiter-48k",
            "path": baseline.relative_to(root).as_posix(),
            "sha256": _sha256(baseline),
            "policy": "same mix/gains, single-pass loudnorm, 48 kHz output, no limiter",
            "filter_graph": baseline_graph,
            "stream": baseline_stream,
            "metrics": baseline_metrics,
        },
        "candidate": {
            "id": "current-two-pass-loudnorm-limiter",
            "path": candidate.relative_to(root).as_posix(),
            "sha256": _sha256(candidate),
            "policy": (
                "current mux_dubbed_video two-pass loudnorm plus AAC true-peak "
                "headroom limiter; no ducking"
            ),
            "stream": candidate_stream,
            "metrics": candidate_metrics,
        },
        "controlled_difference": (
            "Both candidates share the exact source video, background stem, voice track, "
            "gains, codec, bitrate, 48 kHz stereo format, loudness target and no-ducking "
            "setting. The tested difference is single-pass/no-limiter versus current "
            "two-pass loudnorm plus limiter policy."
        ),
        "objective_gate": {
            "candidate_passes_loudness": bool(candidate_metrics["passes_loudness"]),
            "candidate_passes_true_peak": bool(candidate_metrics["passes_true_peak"]),
            "candidate_zero_sample_clipping": bool(
                candidate_metrics["passes_zero_sample_clipping"]
            ),
        },
        "limitations": [
            "This pair closes only P11 listening evidence; it does not isolate P07 or P12.",
            "Production duck_background=false for this fixture, so sidechain-ducking benefit remains untested.",
            "One real 226-second fixture is representative of the retained production job but is not broad genre coverage.",
        ],
    }
    provenance_path = output_dir / "pair-provenance.json"
    _write_json(provenance_path, provenance)

    spec = {
        "schema_version": 1,
        "study_id": "p11-controlled-mix-master-20260925-v2",
        "minimum_completed_votes": 3,
        "trials": [
            {
                "id": "p11-single-pass-vs-two-pass-limiter-48k",
                "fixture_id": "CP2-short-smart-source",
                "reference": {
                    "path": video_rel,
                    "sha256": _sha256(video),
                    "provenance": {
                        "role": "original English speaker/reference video",
                        "fixture": "CP2-short-smart-source",
                    },
                },
                "candidates": [
                    {
                        "id": provenance["baseline"]["id"],
                        "path": provenance["baseline"]["path"],
                        "sha256": provenance["baseline"]["sha256"],
                        "provenance": {
                            "gate": "P11",
                            "policy": provenance["baseline"]["policy"],
                            "pair_provenance": provenance_path.relative_to(root).as_posix(),
                        },
                    },
                    {
                        "id": provenance["candidate"]["id"],
                        "path": provenance["candidate"]["path"],
                        "sha256": provenance["candidate"]["sha256"],
                        "provenance": {
                            "gate": "P11",
                            "policy": provenance["candidate"]["policy"],
                            "pair_provenance": provenance_path.relative_to(root).as_posix(),
                        },
                    },
                ],
                "limitations": provenance["limitations"],
            }
        ],
    }
    spec_path = output_dir / "study-spec.json"
    _write_json(spec_path, spec)

    listening = _load_listening_ab()
    listening.create_packet(
        spec,
        root=root,
        output_dir=output_dir,
        seed=seed,
        force=force,
    )
    evaluation = listening.evaluate_ballots(
        packet_path=output_dir / "packet.json",
        ballot_paths=[],
    )
    evaluation_path = output_dir / "evaluation-no-votes.json"
    _write_json(evaluation_path, evaluation)
    return {
        "baseline": baseline,
        "candidate": candidate,
        "provenance": provenance_path,
        "spec": spec_path,
        "packet": output_dir / "packet.json",
        "ballot": output_dir / "ballot-template.json",
        "evaluation": evaluation_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build controlled P11 blind listening evidence")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--video", default=DEFAULT_VIDEO)
    parser.add_argument("--background", default=DEFAULT_BACKGROUND)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--background-gain-db", type=float, default=0.0)
    parser.add_argument("--voice-gain-db", type=float, default=1.5)
    parser.add_argument("--target-lufs", type=float, default=-14.0)
    parser.add_argument("--target-true-peak-db", type=float, default=-1.5)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    outputs = build_evidence(
        root=args.root,
        video_rel=args.video,
        background_rel=args.background,
        voice_rel=args.voice,
        output_rel=args.output_dir,
        seed=args.seed,
        background_gain_db=args.background_gain_db,
        voice_gain_db=args.voice_gain_db,
        target_lufs=args.target_lufs,
        target_true_peak_db=args.target_true_peak_db,
        force=args.force,
    )
    for name, path in outputs.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
