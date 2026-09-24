from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
from statistics import median
import time
from typing import Any

import numpy as np
import soundfile as sf


SCHEMA_VERSION = 1
SAMPLE_RATE = 48_000
DEFAULT_TEXTS = (
    "Đây là một cây nến Bullish Engulfing đang phá lên khỏi vùng kháng cự.",
    "Dời stop loss về hòa vốn sau khi giá xác nhận cấu trúc tăng.",
    "Nếu FVG chưa được lấp, đừng vội đuổi theo breakout.",
    "RSI đang ở mức 68 phần trăm, nhưng tín hiệu này chưa đủ để vào lệnh.",
    "OpenAI công bố bản cập nhật API mới, còn GPU vẫn xử lý phần suy luận cục bộ.",
    "Bạn có thể chờ pullback về order block rồi mới đánh giá lại market structure.",
    "Không được tăng rủi ro chỉ vì lệnh trước vừa thắng.",
    "Nếu giá quét liquidity rồi đóng cửa trở lại trong vùng, hãy chờ thêm xác nhận.",
    "Take profit đầu tiên nằm ở vùng đỉnh cũ, phần còn lại có thể giữ theo xu hướng.",
    "BOS cho thấy cấu trúc tiếp diễn, còn CHoCH thường được dùng để chú ý khả năng đổi hướng.",
    "Video này dài hơn ba mươi phút, nên nhịp nói phải tự nhiên chứ không được gấp giả.",
    "Prompt, token và context window là ba khái niệm khác nhau trong một hệ thống AI.",
    "Mức giá là 86.400 đô la và xác suất được nêu trong ví dụ là 99,8 phần trăm.",
    "Câu này có nhiều mệnh đề, vì vậy dấu phẩy cần tạo khoảng nghỉ vừa đủ, không bị đứt ý.",
    "Đây là câu hỏi ngắn: tại sao giá lại phản ứng mạnh ở vùng này?",
    "Hãy nhớ rằng một setup đẹp vẫn có thể thua, vì không có chiến lược nào chắc chắn một trăm phần trăm.",
)
LISTENING_SAMPLE_COUNT = 6


class BenchmarkError(RuntimeError):
    pass


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(data), encoding="utf-8", newline="\n")


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BenchmarkError(f"expected JSON object: {path}")
    return raw


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise BenchmarkError(f"artifact must live under project root: {resolved}") from exc


def _audio_metrics(audio: np.ndarray) -> dict[str, float | int | bool]:
    values = np.asarray(audio, dtype=np.float32).reshape(-1)
    if values.size == 0:
        raise BenchmarkError("VieNeu returned empty audio")
    finite = bool(np.isfinite(values).all())
    if not finite:
        raise BenchmarkError("VieNeu returned NaN/Inf audio")
    rms = float(np.sqrt(np.mean(np.square(values, dtype=np.float64))))
    peak = float(np.max(np.abs(values)))
    return {
        "samples": int(values.size),
        "duration_s": values.size / SAMPLE_RATE,
        "rms": rms,
        "peak": peak,
        "finite": finite,
    }


def _set_seed(seed: int) -> None:
    import torch

    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _sync_cuda() -> None:
    import torch

    if torch.cuda.is_available():
        torch.cuda.synchronize()


def _run_batch(
    engine: Any,
    texts: list[str],
    *,
    reference: Path,
    batch_size: int,
    seed: int,
) -> tuple[float, list[np.ndarray], float, float]:
    import torch

    _set_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    _sync_cuda()
    started = time.perf_counter()
    outputs = engine.infer_batch(
        texts,
        ref_audio=str(reference),
        batch_size=batch_size,
    )
    _sync_cuda()
    wall_s = time.perf_counter() - started
    if len(outputs) != len(texts):
        raise BenchmarkError(
            f"infer_batch returned {len(outputs)} outputs for {len(texts)} texts"
        )
    peak_allocated_mib = (
        float(torch.cuda.max_memory_allocated()) / (1024 * 1024)
        if torch.cuda.is_available()
        else 0.0
    )
    peak_reserved_mib = (
        float(torch.cuda.max_memory_reserved()) / (1024 * 1024)
        if torch.cuda.is_available()
        else 0.0
    )
    return wall_s, [np.asarray(item) for item in outputs], peak_allocated_mib, peak_reserved_mib


def run_benchmark(args: argparse.Namespace) -> int:
    import torch
    from vieneu import Vieneu

    project_root = args.project_root.resolve()
    reference = args.reference.resolve()
    if not reference.is_file():
        raise BenchmarkError(f"reference not found: {reference}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_sizes = sorted(set(args.batch_size))
    if not batch_sizes or batch_sizes[0] < 1:
        raise BenchmarkError("batch sizes must be positive")
    if batch_sizes[-1] > len(DEFAULT_TEXTS):
        raise BenchmarkError(
            f"largest batch {batch_sizes[-1]} exceeds built-in text count {len(DEFAULT_TEXTS)}"
        )
    if not torch.cuda.is_available():
        raise BenchmarkError("CUDA is required for this isolated VieNeu GPU benchmark")

    version = importlib.metadata.version("vieneu")
    engine = Vieneu(
        mode="v3turbo",
        backend="pytorch",
        device="cuda",
        precision="fp16",
        dtype="float16",
        max_batch_size=max(batch_sizes),
    )

    results: list[dict[str, Any]] = []
    for batch_size in batch_sizes:
        texts = list(DEFAULT_TEXTS[:batch_size])
        for warmup_index in range(args.warmups):
            _run_batch(
                engine,
                texts,
                reference=reference,
                batch_size=batch_size,
                seed=args.seed + 10_000 + warmup_index,
            )
        repetitions: list[dict[str, Any]] = []
        for repetition in range(args.repetitions):
            wall_s, outputs, peak_allocated_mib, peak_reserved_mib = _run_batch(
                engine,
                texts,
                reference=reference,
                batch_size=batch_size,
                seed=args.seed + repetition,
            )
            output_metrics = [_audio_metrics(item) for item in outputs]
            audio_s = sum(float(item["duration_s"]) for item in output_metrics)
            repetitions.append(
                {
                    "repetition": repetition,
                    "wall_s": wall_s,
                    "audio_s": audio_s,
                    "rtf": wall_s / audio_s if audio_s > 0 else math.inf,
                    "peak_cuda_allocated_mib": peak_allocated_mib,
                    "peak_cuda_reserved_mib": peak_reserved_mib,
                    "outputs": output_metrics,
                }
            )
        results.append(
            {
                "batch_size": batch_size,
                "median_wall_s": median(float(row["wall_s"]) for row in repetitions),
                "median_rtf": median(float(row["rtf"]) for row in repetitions),
                "max_peak_cuda_allocated_mib": max(
                    float(row["peak_cuda_allocated_mib"]) for row in repetitions
                ),
                "max_peak_cuda_reserved_mib": max(
                    float(row["peak_cuda_reserved_mib"]) for row in repetitions
                ),
                "repetitions": repetitions,
            }
        )

    listening_texts = list(DEFAULT_TEXTS[:LISTENING_SAMPLE_COUNT])
    _, listening_outputs, _, _ = _run_batch(
        engine,
        listening_texts,
        reference=reference,
        batch_size=min(4, LISTENING_SAMPLE_COUNT),
        seed=args.seed + 50_000,
    )
    listening_dir = output_dir / "listening"
    listening_dir.mkdir(parents=True, exist_ok=True)
    listening_samples: list[dict[str, Any]] = []
    for index, (text, audio) in enumerate(zip(listening_texts, listening_outputs, strict=True), start=1):
        target = listening_dir / f"sample-{index:02d}.wav"
        sf.write(target, np.asarray(audio, dtype=np.float32), SAMPLE_RATE, subtype="PCM_16")
        listening_samples.append(
            {
                "id": f"sample-{index:02d}",
                "text": text,
                "path": _project_path(target, project_root),
                "sha256": _sha256(target),
                "metrics": _audio_metrics(audio),
            }
        )

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "vieneu-version-benchmark",
        "label": args.label,
        "vieneu_version": version,
        "torch_version": torch.__version__,
        "gpu": torch.cuda.get_device_name(0),
        "gpu_total_gib": torch.cuda.get_device_properties(0).total_memory / (1024**3),
        "backend": "pytorch",
        "device": "cuda",
        "dtype": "float16",
        "precision": "fp16",
        "seed": args.seed,
        "warmups": args.warmups,
        "repetitions": args.repetitions,
        "reference": {
            "path": _project_path(reference, project_root),
            "sha256": _sha256(reference),
        },
        "text_set_sha256": hashlib.sha256(
            "\n".join(DEFAULT_TEXTS).encode("utf-8")
        ).hexdigest(),
        "batch_results": results,
        "listening_samples": listening_samples,
    }
    receipt_path = output_dir / "receipt.json"
    _write_json(receipt_path, receipt)
    print(_canonical_json({"receipt": _project_path(receipt_path, project_root), "version": version}), end="")
    return 0


def compare_benchmarks(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    baseline = _read_json(args.baseline)
    candidate = _read_json(args.candidate)
    for field in ("reference", "text_set_sha256"):
        if baseline.get(field) != candidate.get(field):
            raise BenchmarkError(f"baseline/candidate {field} mismatch")

    baseline_batches = {
        int(row["batch_size"]): row for row in baseline.get("batch_results", [])
    }
    candidate_batches = {
        int(row["batch_size"]): row for row in candidate.get("batch_results", [])
    }
    common_batches = sorted(set(baseline_batches) & set(candidate_batches))
    if not common_batches:
        raise BenchmarkError("no common batch sizes")

    batch_comparison: list[dict[str, Any]] = []
    for batch_size in common_batches:
        old = baseline_batches[batch_size]
        new = candidate_batches[batch_size]
        old_wall = float(old["median_wall_s"])
        new_wall = float(new["median_wall_s"])
        old_rtf = float(old["median_rtf"])
        new_rtf = float(new["median_rtf"])
        batch_comparison.append(
            {
                "batch_size": batch_size,
                "baseline_wall_s": old_wall,
                "candidate_wall_s": new_wall,
                "wall_delta_pct": ((new_wall / old_wall) - 1.0) * 100.0,
                "baseline_rtf": old_rtf,
                "candidate_rtf": new_rtf,
                "rtf_delta_pct": ((new_rtf / old_rtf) - 1.0) * 100.0,
                "baseline_peak_allocated_mib": old["max_peak_cuda_allocated_mib"],
                "candidate_peak_allocated_mib": new["max_peak_cuda_allocated_mib"],
                "baseline_peak_reserved_mib": old["max_peak_cuda_reserved_mib"],
                "candidate_peak_reserved_mib": new["max_peak_cuda_reserved_mib"],
            }
        )

    old_samples = {item["id"]: item for item in baseline.get("listening_samples", [])}
    new_samples = {item["id"]: item for item in candidate.get("listening_samples", [])}
    if set(old_samples) != set(new_samples) or not old_samples:
        raise BenchmarkError("baseline/candidate listening sample ids mismatch")

    trials: list[dict[str, Any]] = []
    metric_pairs: list[dict[str, Any]] = []
    reference = baseline["reference"]
    for sample_id in sorted(old_samples):
        old = old_samples[sample_id]
        new = new_samples[sample_id]
        if old.get("text") != new.get("text"):
            raise BenchmarkError(f"text mismatch for {sample_id}")
        old_metrics = old["metrics"]
        new_metrics = new["metrics"]
        metric_pairs.append(
            {
                "id": sample_id,
                "baseline_duration_s": old_metrics["duration_s"],
                "candidate_duration_s": new_metrics["duration_s"],
                "duration_delta_pct": (
                    (float(new_metrics["duration_s"]) / float(old_metrics["duration_s"])) - 1.0
                )
                * 100.0,
                "baseline_rms": old_metrics["rms"],
                "candidate_rms": new_metrics["rms"],
                "baseline_peak": old_metrics["peak"],
                "candidate_peak": new_metrics["peak"],
            }
        )
        trials.append(
            {
                "id": sample_id,
                "fixture_id": "vieneu-3.8.3-version-ab",
                "reference": {
                    "path": reference["path"],
                    "sha256": reference["sha256"],
                    "provenance": {"role": "voice-reference"},
                },
                "candidates": [
                    {
                        "id": str(baseline["label"]),
                        "path": old["path"],
                        "sha256": old["sha256"],
                        "provenance": {
                            "vieneu_version": baseline["vieneu_version"],
                            "text": old["text"],
                        },
                    },
                    {
                        "id": str(candidate["label"]),
                        "path": new["path"],
                        "sha256": new["sha256"],
                        "provenance": {
                            "vieneu_version": candidate["vieneu_version"],
                            "text": new["text"],
                        },
                    },
                ],
                "limitations": [
                    "Synthetic TTS sample; human listening is required for naturalness and identity parity."
                ],
            }
        )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison = {
        "schema_version": SCHEMA_VERSION,
        "kind": "vieneu-version-comparison",
        "baseline": {
            "label": baseline["label"],
            "version": baseline["vieneu_version"],
        },
        "candidate": {
            "label": candidate["label"],
            "version": candidate["vieneu_version"],
        },
        "batch_comparison": batch_comparison,
        "listening_metric_pairs": metric_pairs,
        "automatic_quality_winner_selected": False,
        "quality_gate": "pending_blind_human_listening",
    }
    comparison_path = output_dir / "comparison.json"
    _write_json(comparison_path, comparison)
    study_spec = {
        "schema_version": 1,
        "study_id": args.study_id,
        "minimum_completed_votes": args.minimum_votes,
        "trials": trials,
    }
    study_path = output_dir / "study-spec.json"
    _write_json(study_path, study_spec)
    print(
        _canonical_json(
            {
                "comparison": _project_path(comparison_path, project_root),
                "study_spec": _project_path(study_path, project_root),
            }
        ),
        end="",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Isolated VieNeu version benchmark and blind A/B study-spec builder."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Benchmark the VieNeu version in the current environment.")
    run.add_argument("--project-root", type=Path, default=Path.cwd())
    run.add_argument("--reference", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--label", required=True)
    run.add_argument("--batch-size", type=int, action="append", default=[])
    run.add_argument("--warmups", type=int, default=1)
    run.add_argument("--repetitions", type=int, default=3)
    run.add_argument("--seed", type=int, default=20260925)
    run.set_defaults(handler=run_benchmark)

    compare = subparsers.add_parser("compare", help="Compare two receipts and build a blind A/B spec.")
    compare.add_argument("--project-root", type=Path, default=Path.cwd())
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output-dir", type=Path, required=True)
    compare.add_argument("--study-id", default="vieneu-3.7.1-vs-3.8.3-20260925")
    compare.add_argument("--minimum-votes", type=int, default=3)
    compare.set_defaults(handler=compare_benchmarks)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run" and not args.batch_size:
        args.batch_size = [4, 8, 16]
    try:
        return int(args.handler(args))
    except (BenchmarkError, OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
