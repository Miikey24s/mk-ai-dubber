from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from rich.console import Console

from .artifacts import (
    atomic_write_json,
    build_stage_manifest,
    fingerprint_file,
    load_stage_manifest,
    relative_artifact_path,
    resolve_artifact_path,
    stage_fingerprint,
    write_stage_manifest,
)
from .asr import transcribe_and_align, transcribe_text, transcribe_text_files
from .audio import evaluate_reference_clarity, resolve_loudness_profile
from .jobs import (
    PipelineControl,
    check_control,
    claim_job,
    clear_control,
    load_job_state,
    reconcile_job_state,
    update_job_state,
)
from .media import (
    assemble_voice_track,
    extract_audio,
    measure_mix_metrics,
    media_duration,
    mux_dubbed_video,
    voice_track_metrics,
    write_srt,
)
from .metrics import MetricsRecorder
from .pronunciation import normalize_pronunciation
from .profiles import resolve_profile, validate_config
from .qa import (
    SEGMENT_QA_POLICY_VERSION,
    SegmentQAObservation,
    run_segment_qa_cycle,
    transcript_similarity,
)
from .review import load_review_overrides
from .runtime import MODELS_DIR, WORK_DIR, configure_runtime
from .segmentation import SEGMENTATION_POLICY_VERSION, build_speech_turns
from .semantic_qa import (
    SEMANTIC_QA_QUESTION_VERSION,
    run_semantic_qa_stage,
    semantic_qa_stage_cache_path,
    semantic_qa_summary,
)
from .separation import separate_dialogue
from .timing import TIMING_POLICY_VERSION, allocate_timing_windows
from .translate import (
    DEFAULT_WEBGPT_MODEL,
    TRANSLATION_POLICY_VERSION,
    build_translator,
    duration_fit_hint,
    load_glossary,
    normalize_translation_provider,
    webgpt_model_catalog,
)
from .tts import TTSStat, build_reference_clips, synthesize_segments
from .types import SEGMENT_SCHEMA_VERSION, Segment
from .versions import runtime_versions


console = Console()
ProgressCallback = Callable[[float, str], None]


def _semantic_rewrite_accepted(result: dict[str, Any], profile: str) -> bool:
    """Route one pre-TTS rewrite candidate using the profile's semantic policy."""
    if result.get("status") != "ok":
        # Balanced may degrade when the verifier is unavailable; Max Quality
        # fails closed instead of silently accepting an unverified rewrite.
        return profile != "max_quality"
    items = result.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return profile != "max_quality"
    return str(items[0].get("decision") or "review") == "pass"


def _notify(callback: ProgressCallback | None, fraction: float, message: str) -> None:
    if callback is not None:
        callback(max(0.0, min(1.0, fraction)), message)


def load_config(path: Path, profile: str | None = None) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"File cấu hình không hợp lệ: {path}")
    resolved = resolve_profile(data, profile)
    validate_config(resolved)
    return resolved


def _config_origin_base(config: dict[str, Any], config_path: Path) -> Path:
    origin = config.get("_config_origin")
    if isinstance(origin, dict):
        base_dir = origin.get("base_dir")
        if isinstance(base_dir, str) and base_dir.strip():
            return Path(base_dir).resolve()

    # Legacy resolved snapshots kept paths relative to the original config but
    # did not record that config's directory. Recover the common work/config
    # layout only when the direct interpretation is missing and the parent one
    # actually resolves the glossary.
    glossary_value = config.get("translation", {}).get("glossary")
    if (
        config_path.name == "resolved_config.json"
        and config_path.parent.name.startswith("job-")
        and isinstance(glossary_value, str)
        and glossary_value
        and not Path(glossary_value).is_absolute()
    ):
        direct = (config_path.parent / glossary_value).resolve()
        legacy_base = config_path.parent.parent
        legacy = (legacy_base / glossary_value).resolve()
        if not direct.is_file() and legacy.is_file():
            return legacy_base.resolve()
    return config_path.parent.resolve()


def _resolve_config_path(config: dict[str, Any], config_path: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (_config_origin_base(config, config_path) / candidate).resolve()


def _job_dir(input_path: Path, source_identity: dict[str, Any] | None = None) -> Path:
    identity = source_identity or fingerprint_file(input_path)
    sha256 = str(identity.get("sha256") or "")
    if len(sha256) != 64:
        raise ValueError("source identity is missing a valid sha256")
    return WORK_DIR / f"job-{sha256[:16]}"


def _manifest_path(job_dir: Path, stage: str) -> Path:
    return job_dir / "manifests" / f"{stage}.json"


def _stage_cache_hit(
    job_dir: Path,
    stage: str,
    fingerprint: str,
    *,
    resume: bool,
) -> dict[str, Any] | None:
    if not resume:
        return None
    return load_stage_manifest(
        _manifest_path(job_dir, stage),
        job_dir=job_dir,
        expected_stage=stage,
        expected_fingerprint=fingerprint,
    )


def _commit_stage(
    job_dir: Path,
    stage: str,
    *,
    inputs: Any,
    artifacts: list[Path],
    upstream: Any = None,
    config: Any = None,
    model: Any = None,
    prompt: Any = None,
    versions: Any = None,
) -> dict[str, Any]:
    manifest = build_stage_manifest(
        job_dir,
        stage,
        inputs=inputs,
        artifacts=artifacts,
        upstream=upstream,
        config=config,
        model=model,
        prompt=prompt,
        versions=versions,
    )
    write_stage_manifest(_manifest_path(job_dir, stage), manifest)
    return manifest


def _load_stems(job_dir: Path, stems_meta: Path) -> tuple[Path | None, Path | None]:
    try:
        stem_data = json.loads(stems_meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(stem_data, dict):
        return None, None

    if stem_data.get("version") == 2:
        try:
            vocals = resolve_artifact_path(job_dir, str(stem_data["vocals"]))
            background = resolve_artifact_path(job_dir, str(stem_data["background"]))
        except (KeyError, ValueError):
            return None, None
    else:
        # Legacy absolute-path metadata is accepted only when it still resolves
        # inside the current content-addressed job directory.
        try:
            vocals = Path(str(stem_data["vocals"])).resolve()
            background = Path(str(stem_data["background"])).resolve()
            vocals.relative_to(job_dir.resolve())
            background.relative_to(job_dir.resolve())
        except (KeyError, ValueError):
            return None, None

    if not vocals.is_file() or not background.is_file():
        return None, None
    return vocals, background


def _save_segments(
    path: Path,
    segments: list[Segment],
    *,
    review_statuses: dict[int, str] | None = None,
) -> None:
    payload: list[dict[str, Any]] = []
    for item in segments:
        data = item.to_dict()
        if review_statuses is not None and item.id in review_statuses:
            data["review_status"] = review_statuses[item.id]
        payload.append(data)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_segments(path: Path) -> list[Segment]:
    return [Segment.from_dict(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def _load_json_dict(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_reference_map(job_dir: Path, metadata_path: Path) -> dict[str, Path] | None:
    data = _load_json_dict(metadata_path)
    raw_references = data.get("references")
    if not isinstance(raw_references, dict):
        return None
    references: dict[str, Path] = {}
    try:
        for speaker, stored_path in raw_references.items():
            path = resolve_artifact_path(job_dir, str(stored_path))
            if not path.is_file():
                return None
            references[str(speaker)] = path
    except (OSError, ValueError):
        return None
    return references


def _load_tts_manifest(
    job_dir: Path,
    request_fingerprint: str,
    final_text_path: Path,
    *,
    resume: bool,
) -> dict[str, Any] | None:
    if not resume:
        return None
    manifest = load_stage_manifest(
        _manifest_path(job_dir, "tts"),
        job_dir=job_dir,
        expected_stage="tts",
    )
    if manifest is None:
        return None
    identity = manifest.get("identity")
    inputs = identity.get("inputs") if isinstance(identity, dict) else None
    if not isinstance(inputs, dict) or inputs.get("request_fingerprint") != request_fingerprint:
        return None
    if not final_text_path.is_file():
        return None
    final_text = inputs.get("final_text")
    if not isinstance(final_text, dict) or final_text != fingerprint_file(final_text_path):
        return None
    return manifest


def _prepare_tts_partial_cache(
    output_dir: Path,
    request_fingerprint: str,
    *,
    resume: bool,
    preserve_raw_on_mismatch: bool = False,
) -> tuple[Path, bool]:
    """Keep incomplete TTS work only when it belongs to the exact same request."""
    receipt_path = output_dir / "request.json"
    receipt = _load_json_dict(receipt_path)
    matches = (
        resume
        and receipt.get("version") == 1
        and receipt.get("fingerprint") == request_fingerprint
    )
    if not matches:
        if resume and preserve_raw_on_mismatch and output_dir.is_dir():
            for child in output_dir.iterdir():
                if child.name.endswith("_raw.wav") or child.name.endswith("_raw.meta.json"):
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        else:
            shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        receipt_path,
        {"version": 1, "fingerprint": request_fingerprint},
    )
    return receipt_path, matches


def _apply_review_overrides(
    segments: list[Segment],
    overrides: dict[int, dict[str, str]],
) -> dict[int, str]:
    """Overlay durable human edits after generated text and before reference/TTS stages."""
    if not overrides:
        return {}
    by_id = {segment.id: segment for segment in segments}
    unknown = sorted(set(overrides) - set(by_id))
    if unknown:
        raise ValueError(f"Manual review overrides reference unknown segment ids: {unknown}")
    statuses: dict[int, str] = {}
    for segment_id, override in overrides.items():
        segment = by_id[segment_id]
        segment.vi = str(override["text"]).strip()
        segment.speaker = str(override["speaker"]).strip()
        statuses[segment_id] = str(override["review_status"])
    return statuses


def _segment_qa_spoken_contract(
    segment: Segment,
    pronunciation_map: dict[str, str],
) -> tuple[str, tuple[str, ...]]:
    """Return the exact TTS target plus critical spoken forms introduced by normalization."""
    normalized = normalize_pronunciation(
        segment.vi,
        pronunciation_map,
        normalize_numbers=True,
    )
    critical_terms = tuple(
        sorted(
            {
                replacement.spoken.strip()
                for replacement in normalized.replacements
                if replacement.spoken.strip()
            }
        )
    )
    return normalized.tts_text, critical_terms


def _record_typesafe_metrics(metrics: MetricsRecorder, result: dict[str, Any]) -> None:
    if result.get("status") != "ok" or bool(result.get("_runtime_cache_hit")):
        return
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return
    metrics.increment("typesafe_requests", int(usage.get("requests") or 0))
    metrics.increment(
        "typesafe_tokens",
        int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0),
    )


def _register_semantic_qa_artifact(
    job_dir: Path,
    cache_path: Path,
    stage: str,
    result: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """Register one self-verified raw TypeSafe stage as a normal pipeline artifact."""
    if result.get("status") != "ok":
        return None
    artifact = semantic_qa_stage_cache_path(cache_path, stage)
    if not artifact.is_file():
        return None
    fingerprint = str(result.get("fingerprint") or "")
    if not fingerprint:
        return None
    return _commit_stage(
        job_dir,
        f"semantic_qa_{stage}",
        inputs={"raw_inference_fingerprint": fingerprint},
        artifacts=[artifact],
        model={"name": config.get("model", "jev-latest")},
        prompt={"question_policy": f"semantic-qa-v{SEMANTIC_QA_QUESTION_VERSION}"},
        versions={"policy": SEMANTIC_QA_QUESTION_VERSION},
    )


def _diarization_enabled(config: dict[str, Any], hf_token: str | None, override: bool | None) -> bool:
    if override is not None:
        return override
    value = config.get("diarization", {}).get("enabled", "auto")
    if isinstance(value, bool):
        return value
    return str(value).lower() == "auto" and bool(hf_token)


def _run_pipeline_impl(
    input_path: Path,
    output_path: Path,
    config_path: Path,
    voice_ref: Path | None = None,
    hf_token: str | None = None,
    diarize_override: bool | None = None,
    translation_provider: str | None = None,
    translation_model: str | None = None,
    translation_effort: str | None = None,
    translation_display_name: str | None = None,
    translation_catalog_revision: str | None = None,
    translation_catalog_timestamp: str | None = None,
    profile: str | None = None,
    resume: bool = True,
    progress_callback: ProgressCallback | None = None,
    _source_identity: dict[str, Any] | None = None,
    _job_existed: bool | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    configure_runtime()
    _notify(progress_callback, 0.01, "Đang chuẩn bị tác vụ")
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    config_path = config_path.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if voice_ref is not None and not voice_ref.exists():
        raise FileNotFoundError(voice_ref)

    config = load_config(config_path, profile)
    config_origin = _config_origin_base(config, config_path)
    config["_config_origin"] = {"base_dir": str(config_origin)}
    source_identity = _source_identity or fingerprint_file(input_path)
    job_dir = _job_dir(input_path, source_identity)
    job_existed = job_dir.exists() if _job_existed is None else _job_existed
    job_dir.mkdir(parents=True, exist_ok=True)
    clear_control(job_dir)
    selected_profile = str(config["profile"])

    translation_config = dict(config.get("translation", {}) or {})
    requested_provider = normalize_translation_provider(
        translation_provider or str(translation_config.get("provider", "webgpt"))
    )
    persisted_state = reconcile_job_state(job_dir) if job_existed else {}
    persisted_metadata = (
        dict(persisted_state.get("metadata") or {})
        if isinstance(persisted_state, dict)
        else {}
    )

    def selected_value(
        explicit: str | None,
        metadata_key: str,
        *config_keys: str,
    ) -> str:
        persisted = str(persisted_metadata.get(metadata_key) or "").strip()
        requested = str(explicit or "").strip()
        if resume and persisted:
            if requested and requested != persisted:
                raise ValueError(
                    f"Resume giữ nguyên {metadata_key}={persisted!r}; "
                    f"giá trị mới {requested!r} cần chạy --fresh."
                )
            return persisted
        if requested:
            return requested
        for key in config_keys:
            value = str(translation_config.get(key) or "").strip()
            if value:
                return value
        return ""

    persisted_provider = str(persisted_metadata.get("translation_provider") or "").strip()
    if resume and persisted_provider:
        normalized_persisted_provider = normalize_translation_provider(persisted_provider)
        if translation_provider and requested_provider != normalized_persisted_provider:
            raise ValueError(
                f"Resume giữ nguyên translation_provider={normalized_persisted_provider!r}; "
                f"provider mới {requested_provider!r} cần chạy --fresh."
            )
        requested_provider = normalized_persisted_provider

    if requested_provider == "aurora":
        selected_model = selected_value(
            translation_model,
            "translation_model",
            "aurora_model",
            "model_id",
            "model",
        )
        if not selected_model:
            raise ValueError("Aurora cần model đã chọn từ live catalog trước khi chạy.")
        selected_effort = selected_value(
            translation_effort,
            "translation_effort",
            "aurora_effort",
            "reasoning_effort",
            "effort",
        )
        selected_display_name = selected_value(
            translation_display_name,
            "translation_model_display_name",
        ) or selected_model
        selected_catalog_revision = selected_value(
            translation_catalog_revision,
            "translation_catalog_revision",
        )
        selected_catalog_timestamp = selected_value(
            translation_catalog_timestamp,
            "translation_catalog_timestamp",
        )
    elif requested_provider == "webgpt":
        selected_model = selected_value(
            translation_model,
            "translation_model",
            "webgpt_model",
            "codex_model",
        ) or DEFAULT_WEBGPT_MODEL
        selected_effort = selected_value(
            translation_effort,
            "translation_effort",
        )
        selected_display_name = selected_value(
            translation_display_name,
            "translation_model_display_name",
        ) or selected_model
        selected_catalog_revision = selected_value(
            translation_catalog_revision,
            "translation_catalog_revision",
        )
        selected_catalog_timestamp = selected_value(
            translation_catalog_timestamp,
            "translation_catalog_timestamp",
        )
        if not selected_catalog_revision:
            live_catalog = webgpt_model_catalog(translation_config)
            live_model = next(
                (
                    item
                    for item in live_catalog.get("models", [])
                    if isinstance(item, dict) and item.get("id") == selected_model
                ),
                None,
            )
            if live_model is None:
                raise ValueError(
                    f"Model WebGPT đã chọn {selected_model!r} không có trong live catalog instance 2."
                )
            selected_catalog_revision = str(live_catalog.get("revision") or "").strip()
            selected_catalog_timestamp = str(live_catalog.get("updated_at") or "").strip()
            if not translation_display_name:
                selected_display_name = str(live_model.get("display_name") or selected_model).strip()
            advertised_efforts = [
                str(value).strip()
                for value in live_model.get("supported_efforts", [])
                if str(value).strip()
            ]
            if not selected_effort:
                selected_effort = str(
                    live_model.get("default_effort")
                    or live_catalog.get("default_effort")
                    or ""
                ).strip()
            if selected_effort and advertised_efforts and selected_effort not in advertised_efforts:
                raise ValueError(
                    f"Model WebGPT {selected_model!r} không advertise effort {selected_effort!r}; "
                    f"supported={advertised_efforts}."
                )
    elif requested_provider == "local":
        selected_model = str(
            translation_config.get("local_model")
            or translation_config.get("model")
            or translation_config.get("repo")
            or ""
        ).strip()
        selected_effort = ""
        selected_display_name = selected_model
        selected_catalog_revision = ""
        selected_catalog_timestamp = ""
    else:
        selected_model = str(
            translation_config.get("webgpt_model")
            or translation_config.get("codex_model")
            or "chatgpt-web/gpt-5.6-sol"
        ).strip()
        selected_effort = ""
        selected_display_name = selected_model
        selected_catalog_revision = ""
        selected_catalog_timestamp = ""

    translation_selection = {
        "provider": requested_provider,
        "model_id": selected_model,
        "display_name": selected_display_name,
        "effort": selected_effort,
        "catalog_revision": selected_catalog_revision,
        "catalog_timestamp": selected_catalog_timestamp,
    }
    config["_translation_selection"] = translation_selection
    translation_config["provider"] = requested_provider
    if requested_provider == "aurora":
        translation_config["aurora_model"] = selected_model
        if selected_effort:
            translation_config["aurora_effort"] = selected_effort
    elif requested_provider == "webgpt":
        translation_config["webgpt_model"] = selected_model
    config["translation"] = translation_config
    atomic_write_json(job_dir / "resolved_config.json", config)
    update_job_state(
        job_dir,
        status="running",
        stage="preflight",
        progress=0.01,
        message="Đang chuẩn bị tác vụ",
        metadata={
            "input_path": str(input_path),
            "input_name": input_path.name,
            "source_sha256": source_identity["sha256"],
            "profile": selected_profile,
            "output": str(output_path),
            "config_path": str(config_path),
            "translation_provider": requested_provider,
            "translation_model": selected_model,
            "translation_model_display_name": selected_display_name,
            "translation_effort": selected_effort,
            "translation_catalog_revision": selected_catalog_revision,
            "translation_catalog_timestamp": selected_catalog_timestamp,
        },
    )

    def progress(value: float, message: str, *, stage: str | None = None) -> None:
        check_control(job_dir)
        update_job_state(
            job_dir,
            status="running",
            stage=stage,
            progress=value,
            message=message,
        )
        _notify(progress_callback, value, message)

    metrics_path = job_dir / "metrics.json"
    metrics = MetricsRecorder(
        run_kind=("resume" if resume else "warm") if job_existed else "cold",
        metadata={"input_name": input_path.name, "source_sha256": source_identity["sha256"]},
        enable_cuda=True,
        started_at=started_at,
        failure_path=metrics_path,
    )
    atomic_write_json(
        job_dir / "job.json",
        {
            "version": 1,
            "source": source_identity,
            "source_path": str(input_path),
            "source_name": input_path.name,
            "profile": selected_profile,
            "translation": translation_selection,
        },
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    check_control(job_dir)
    with metrics.stage("input_extract"):
        total_duration = media_duration(input_path)

        console.rule("1/6 Âm thanh")
        progress(0.04, "Đang tách âm thanh khỏi video", stage="input_extract")
        original_audio = job_dir / "original.wav"
        extract_inputs = {"source": source_identity}
        extract_fingerprint = stage_fingerprint(
            "extract_audio",
            inputs=extract_inputs,
            versions={"policy": 1},
        )
        if _stage_cache_hit(job_dir, "extract_audio", extract_fingerprint, resume=resume):
            metrics.increment("cache_hits")
        else:
            metrics.increment("cache_misses")
            extract_audio(input_path, original_audio)
            _commit_stage(
                job_dir,
                "extract_audio",
                inputs=extract_inputs,
                artifacts=[original_audio],
                versions={"policy": 1},
            )

    stems_meta = job_dir / "stems.json"
    vocals: Path | None = None
    background: Path | None = None
    original_identity = fingerprint_file(original_audio)
    separation_config = dict(config.get("separation", {}) or {})
    separation_inputs = {"original_audio": original_identity}
    separation_fingerprint = stage_fingerprint(
        "separation",
        inputs=separation_inputs,
        config=separation_config,
        model={"name": separation_config.get("model")},
        versions={"policy": 1, **runtime_versions("audio_separator", "torch")},
    )
    with metrics.stage("separation"):
        if _stage_cache_hit(job_dir, "separation", separation_fingerprint, resume=resume):
            vocals, background = _load_stems(job_dir, stems_meta)

        if vocals is None or background is None:
            metrics.increment("cache_misses")
            if not bool(config.get("separation", {}).get("enabled", True)):
                raise RuntimeError("Tách nguồn âm thanh đang bị tắt; pipeline cần background stem để lồng tiếng sạch.")
            console.print("Đang tách lời thoại khỏi nhạc/SFX bằng BS-Roformer...")
            progress(0.08, "Đang tách lời thoại khỏi nhạc và SFX", stage="separation")
            vocals, background = separate_dialogue(
                original_audio,
                job_dir / "stems",
                MODELS_DIR / "separator",
                str(config["separation"]["model"]),
            )
            atomic_write_json(
                stems_meta,
                {
                    "version": 2,
                    "vocals": relative_artifact_path(job_dir, vocals),
                    "background": relative_artifact_path(job_dir, background),
                },
            )
            _commit_stage(
                job_dir,
                "separation",
                inputs=separation_inputs,
                artifacts=[vocals, background, stems_meta],
                config=separation_config,
                model={"name": separation_config.get("model")},
                versions={"policy": 1, **runtime_versions("audio_separator", "torch")},
            )
        else:
            metrics.increment("cache_hits")

    console.rule("2/6 Nhận dạng giọng nói + căn thời gian")
    progress(0.20, "Đang nhận dạng và căn thời gian lời thoại", stage="asr")
    source_json = job_dir / "segments_source.json"
    diarize = _diarization_enabled(config, hf_token, diarize_override)
    asr_config = dict(config.get("asr", {}) or {})
    diarization_config = dict(config.get("diarization", {}) or {})
    diarization_hints = {
        key: diarization_config.get(key)
        for key in ("num_speakers", "min_speakers", "max_speakers")
        if diarization_config.get(key) is not None
    }
    asr_inputs = {
        "vocals": fingerprint_file(vocals),
        "diarization": diarize,
        "diarization_hints": diarization_hints if diarize else {},
    }
    asr_fingerprint = stage_fingerprint(
        "asr",
        inputs=asr_inputs,
        config=asr_config,
        model={"name": asr_config.get("model")},
        versions={
            "segment_schema": SEGMENT_SCHEMA_VERSION,
            "policy": 1,
            **runtime_versions("whisperx", "torch"),
        },
    )
    with metrics.stage("asr"):
        if _stage_cache_hit(job_dir, "asr", asr_fingerprint, resume=resume):
            metrics.increment("cache_hits")
            segments = _load_segments(source_json)
        else:
            metrics.increment("cache_misses")
            segments = transcribe_and_align(
                vocals,
                config["asr"],
                hf_token=hf_token,
                diarize=diarize,
                diarization_config=diarization_config,
            )
            if not segments:
                raise RuntimeError("WhisperX không nhận diện được đoạn thoại nào.")
            _save_segments(source_json, segments)
            source_srt = job_dir / "source_en.srt"
            write_srt(segments, source_srt, translated=False)
            _commit_stage(
                job_dir,
                "asr",
                inputs=asr_inputs,
                artifacts=[source_json, source_srt],
                config=asr_config,
                model={"name": asr_config.get("model")},
                versions={
                    "segment_schema": SEGMENT_SCHEMA_VERSION,
                    "policy": 1,
                    **runtime_versions("whisperx", "torch"),
                },
            )
    metrics.set_counter("source_segments", len(segments))

    segmentation_config = dict(config.get("segmentation", {}) or {})
    segmentation_mode = str(segmentation_config.get("mode", "legacy")).strip().lower()
    translation_source_json = source_json
    if segmentation_mode not in {"legacy", "smart"}:
        raise ValueError("segmentation.mode must be 'legacy' or 'smart'")
    if segmentation_mode == "smart":
        turns_json = job_dir / "segments_turns.json"
        turns_srt = job_dir / "source_turns.srt"
        segmentation_inputs = {"source_segments": fingerprint_file(source_json)}
        segmentation_fingerprint = stage_fingerprint(
            "segmentation",
            inputs=segmentation_inputs,
            config=segmentation_config,
            versions={"policy": SEGMENTATION_POLICY_VERSION},
        )
        with metrics.stage("segmentation"):
            if _stage_cache_hit(
                job_dir,
                "segmentation",
                segmentation_fingerprint,
                resume=resume,
            ):
                metrics.increment("cache_hits")
                segments = _load_segments(turns_json)
            else:
                metrics.increment("cache_misses")
                segments = build_speech_turns(segments, segmentation_config)
                _save_segments(turns_json, segments)
                write_srt(segments, turns_srt, translated=False)
                _commit_stage(
                    job_dir,
                    "segmentation",
                    inputs=segmentation_inputs,
                    artifacts=[turns_json, turns_srt],
                    config=segmentation_config,
                    versions={"policy": SEGMENTATION_POLICY_VERSION},
                )
        translation_source_json = turns_json
    metrics.set_counter("speech_turns", len(segments))
    console.print(f"Đã nhận diện {len(segments)} đoạn thoại; diarization={'bật' if diarize else 'tắt'}.")

    glossary_path = _resolve_config_path(
        config,
        config_path,
        str(config["translation"].get("glossary", "glossary.yaml")),
    )
    glossary = load_glossary(glossary_path)

    translated_baseline_json = job_dir / "segments_translated.json"
    translated_json = job_dir / "segments_vi.json"
    translation_meta_path = job_dir / "translation_meta.json"
    semantic_qa_path = job_dir / "semantic_qa.json"
    console.rule("3/6 Dịch + tối ưu câu lồng tiếng")
    translation_retry_budget = int(config.get("reliability", {}).get("retry_budget", 3))
    prefit_config_keys = {
        "prefit_enabled",
        "duration_chars_per_second",
        "duration_uncertainty_ratio",
        "duration_overflow_ratio",
    }
    translation_stage_config = {
        key: value for key, value in translation_config.items() if key not in prefit_config_keys
    }
    translation_inputs = {
        "source_segments": fingerprint_file(translation_source_json),
        "glossary": fingerprint_file(glossary_path),
        "provider": requested_provider,
    }
    translation_model_provenance = dict(translation_selection)
    if requested_provider == "hybrid":
        translation_model_provenance["fallback_model"] = (
            translation_config.get("local_model")
            or translation_config.get("model")
            or translation_config.get("repo")
        )
    translation_fingerprint = stage_fingerprint(
        "translation",
        inputs=translation_inputs,
        config=translation_stage_config,
        model=translation_model_provenance,
        prompt={"policy": f"translation-v{TRANSLATION_POLICY_VERSION}"},
        versions={"policy": TRANSLATION_POLICY_VERSION},
    )
    provider_label = {
        "aurora": f"Aurora · {selected_model}" + (f" · {selected_effort}" if selected_effort else ""),
        "webgpt": f"Codex WebGPT instance 2 · {selected_model}",
        "local": "LLM local only · Qwen",
        "hybrid": "LLM fallback · WebGPT → local khi cần",
    }[requested_provider]
    current_translation_stats: dict[str, Any] = {}
    with metrics.stage("translation"):
        translation_manifest = _stage_cache_hit(
            job_dir,
            "translation",
            translation_fingerprint,
            resume=resume,
        )
        if translation_manifest is not None:
            translation_meta = _load_json_dict(translation_meta_path)
            if not isinstance(translation_meta.get("stats"), dict):
                translation_manifest = None
        if translation_manifest is not None:
            metrics.increment("cache_hits")
            segments = _load_segments(translated_baseline_json)
            translation_stats = dict(translation_meta["stats"])
        else:
            metrics.increment("cache_misses")
            # The translator's legacy ID-only cache is unsafe once any
            # translation fingerprint input changes.
            (job_dir / "translations_cache.json").unlink(missing_ok=True)
            progress(0.34, f"Đang dịch bằng {provider_label}", stage="translation")
            translator = build_translator(
                config["translation"],
                job_dir,
                requested_provider,
                retry_budget=translation_retry_budget,
                model_override=selected_model,
                effort_override=selected_effort,
            )
            with translator.running():
                segments = translator.translate_segments(
                    segments,
                    glossary,
                    progress_callback=lambda value, message: progress(
                        0.34 + value * 0.16,
                        message,
                        stage="translation",
                    ),
                )
                current_translation_stats = translator.stats()
            translation_stats = dict(current_translation_stats)
            _save_segments(translated_baseline_json, segments)
            atomic_write_json(
                translation_meta_path,
                {
                    "requested_provider": requested_provider,
                    "selection": translation_selection,
                    "stats": translation_stats,
                },
            )
            translation_manifest = _commit_stage(
                job_dir,
                "translation",
                inputs=translation_inputs,
                artifacts=[translated_baseline_json, translation_meta_path],
                config=translation_stage_config,
                model=translation_model_provenance,
                prompt={"policy": f"translation-v{TRANSLATION_POLICY_VERSION}"},
                versions={"policy": TRANSLATION_POLICY_VERSION},
            )

    semantic_config = dict(config.get("qa", {}).get("semantic", {}) or {})
    with metrics.stage("semantic_qa_translated"):
        translated_semantic_qa = run_semantic_qa_stage(
            segments,
            semantic_config,
            stage="translated",
            cache_path=semantic_qa_path,
            resume=resume,
        )
        _register_semantic_qa_artifact(
            job_dir,
            semantic_qa_path,
            "translated",
            translated_semantic_qa,
            semantic_config,
        )
    metrics.increment(
        "cache_hits" if bool(translated_semantic_qa.get("_runtime_cache_hit")) else "cache_misses"
    )
    _record_typesafe_metrics(metrics, translated_semantic_qa)
    if translated_semantic_qa.get("status") == "ok":
        review_count = int(translated_semantic_qa.get("needs_review") or 0)
        console.print(
            f"Semantic QA bản dịch (shadow): {review_count}/{len(segments)} đoạn cần xem lại."
        )

    semantic_gate_receipts: list[dict[str, Any]] = []

    def verify_semantic_candidate(segment: Segment, candidate_text: str, stage_prefix: str) -> bool:
        if not bool(semantic_config.get("enabled", False)) or selected_profile == "fast":
            return True
        candidate = Segment.from_dict(segment.to_dict())
        candidate.vi = str(candidate_text).strip()
        stage = f"{stage_prefix}_{segment.id:05d}"
        with metrics.stage("semantic_qa_rewrite_gate"):
            gate = run_semantic_qa_stage(
                [candidate],
                semantic_config,
                stage=stage,
                cache_path=semantic_qa_path,
                resume=resume,
            )
        metrics.increment("cache_hits" if bool(gate.get("_runtime_cache_hit")) else "cache_misses")
        _record_typesafe_metrics(metrics, gate)
        accepted = _semantic_rewrite_accepted(gate, selected_profile)
        items = gate.get("items")
        first_item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        semantic_gate_receipts.append(
            {
                "stage": stage_prefix,
                "segment_id": segment.id,
                "status": gate.get("status"),
                "decision": first_item.get("decision"),
                "accepted": accepted,
                "fingerprint": gate.get("fingerprint"),
            }
        )
        return accepted

    # Deterministic duration prediction can avoid an expensive TTS-then-rewrite
    # cycle. Fast skips this extra model call; Balanced/Max prefit only candidates
    # that are predicted to overflow and verify the rewrite before it reaches TTS.
    prefit_enabled = bool(translation_config.get("prefit_enabled", True)) and bool(
        config.get("profile_policy", {}).get("translation_prefit", selected_profile != "fast")
    )
    prefit_config = {
        "enabled": prefit_enabled,
        "chars_per_second": float(translation_config.get("duration_chars_per_second", 13.0)),
        "uncertainty_ratio": float(translation_config.get("duration_uncertainty_ratio", 0.20)),
        "overflow_ratio": float(translation_config.get("duration_overflow_ratio", 1.10)),
    }
    tts_text_source_json = translated_baseline_json
    current_prefit_stats: dict[str, Any] = {}
    if prefit_enabled:
        prefit_json = job_dir / "segments_prefit.json"
        prefit_meta_path = job_dir / "prefit_meta.json"
        prefit_inputs = {
            "translated": fingerprint_file(translated_baseline_json),
            "translation_stage": str((translation_manifest or {}).get("fingerprint") or translation_fingerprint),
            "glossary": fingerprint_file(glossary_path),
            "provider": requested_provider,
            "profile": selected_profile,
            "semantic": semantic_config if bool(semantic_config.get("enabled", False)) else {"enabled": False},
        }
        prefit_fingerprint = stage_fingerprint(
            "translation_prefit",
            inputs=prefit_inputs,
            config=prefit_config,
            model=translation_model_provenance,
            prompt={"policy": "duration-prefit-v2"},
            versions={"policy": 2, "translation_policy": TRANSLATION_POLICY_VERSION},
        )
        with metrics.stage("translation_prefit"):
            prefit_manifest = _stage_cache_hit(
                job_dir,
                "translation_prefit",
                prefit_fingerprint,
                resume=resume,
            )
            if prefit_manifest is not None and prefit_meta_path.is_file():
                metrics.increment("cache_hits")
                segments = _load_segments(prefit_json)
            else:
                metrics.increment("cache_misses")
                rewrite_items: list[tuple[Segment, float, float, float, int]] = []
                fit_receipts: list[dict[str, Any]] = []
                for segment in segments:
                    hint = duration_fit_hint(
                        segment.vi,
                        segment.duration,
                        chars_per_second=prefit_config["chars_per_second"],
                        uncertainty_ratio=prefit_config["uncertainty_ratio"],
                        overflow_ratio=prefit_config["overflow_ratio"],
                    )
                    fit_receipts.append({"segment_id": segment.id, **hint})
                    if hint.get("fit") != "likely_overflow":
                        continue
                    measured = max(0.1, float(hint.get("expected_seconds") or segment.duration))
                    target = max(0.25, segment.duration)
                    ratio = measured / target
                    target_chars = max(8, int(len(segment.vi) * (target / measured) * 0.95))
                    rewrite_items.append((segment, target, measured, ratio, target_chars))

                accepted_ids: list[int] = []
                if rewrite_items:
                    prefit_translator = build_translator(
                        config["translation"],
                        job_dir,
                        requested_provider,
                        retry_budget=translation_retry_budget,
                        model_override=selected_model,
                        effort_override=selected_effort,
                    )
                    with prefit_translator.running():
                        rewritten = prefit_translator.rewrite_batch(rewrite_items, glossary)
                        for segment, _target, _measured, _ratio, _target_chars in rewrite_items:
                            candidate_text = str(rewritten.get(segment.id) or "").strip()
                            if not candidate_text or candidate_text == segment.vi:
                                continue
                            if verify_semantic_candidate(segment, candidate_text, "prefit_candidate"):
                                segment.vi = candidate_text
                                accepted_ids.append(segment.id)
                        current_prefit_stats = prefit_translator.stats()

                _save_segments(prefit_json, segments)
                atomic_write_json(
                    prefit_meta_path,
                    {
                        "version": 1,
                        "profile": selected_profile,
                        "candidates": len(rewrite_items),
                        "accepted_ids": accepted_ids,
                        "fit": fit_receipts,
                        "translator": current_prefit_stats,
                    },
                )
                _commit_stage(
                    job_dir,
                    "translation_prefit",
                    inputs=prefit_inputs,
                    artifacts=[prefit_json, prefit_meta_path],
                    config=prefit_config,
                    model=translation_model_provenance,
                    prompt={"policy": "duration-prefit-v2"},
                    versions={"policy": 2, "translation_policy": TRANSLATION_POLICY_VERSION},
                )
        tts_text_source_json = prefit_json

    review_overrides = load_review_overrides(job_dir)
    review_statuses = _apply_review_overrides(segments, review_overrides)
    manual_review_ids = set(review_overrides)

    console.rule("4/6 TTS tiếng Việt + khớp thời lượng")
    progress(0.52, "Đang chuẩn bị giọng tiếng Việt", stage="reference_selection")
    reference_meta_path = job_dir / "references.json"
    reference_inputs = {
        "vocals": fingerprint_file(vocals),
        "source_segments": fingerprint_file(translation_source_json),
        "voice_ref": fingerprint_file(voice_ref.resolve()) if voice_ref is not None else None,
        "review_speakers": {
            str(segment_id): state["speaker"]
            for segment_id, state in sorted(review_overrides.items())
        },
    }
    reference_config = {
        "clone_original_voice": bool(config["tts"].get("clone_original_voice", True)),
    }
    reference_fingerprint = stage_fingerprint(
        "reference_selection",
        inputs=reference_inputs,
        config=reference_config,
        versions={"policy": 2},
    )
    references: dict[str, Path] = {}
    reference_selection_receipts: dict[str, Any] = {}
    with metrics.stage("reference_selection"):
        reference_manifest = _stage_cache_hit(
            job_dir,
            "reference_selection",
            reference_fingerprint,
            resume=resume,
        )
        if reference_manifest is not None:
            loaded_references = _load_reference_map(job_dir, reference_meta_path)
            if loaded_references is None:
                reference_manifest = None
        if reference_manifest is not None:
            metrics.increment("cache_hits")
            references = loaded_references or {}
        else:
            metrics.increment("cache_misses")
            if voice_ref is None and reference_config["clone_original_voice"]:
                references = build_reference_clips(
                    vocals,
                    segments,
                    job_dir / "references",
                    selection_receipts=reference_selection_receipts,
                )
                for speaker, ref_clip in references.items():
                    if ref_clip.is_file():
                        try:
                            clarity = evaluate_reference_clarity(ref_clip)
                            if speaker in reference_selection_receipts:
                                reference_selection_receipts[speaker]["clarity"] = clarity
                        except Exception:
                            pass
            elif voice_ref is not None and voice_ref.is_file():
                try:
                    clarity = evaluate_reference_clarity(voice_ref)
                    reference_selection_receipts["override"] = {
                        "source": str(voice_ref),
                        "clarity": clarity,
                    }
                except Exception:
                    pass
            atomic_write_json(
                reference_meta_path,
                {
                    "version": 1,
                    "selection": reference_selection_receipts,
                    "references": {
                        speaker: relative_artifact_path(job_dir, path)
                        for speaker, path in sorted(references.items())
                    },
                },
            )
            reference_manifest = _commit_stage(
                job_dir,
                "reference_selection",
                inputs=reference_inputs,
                artifacts=[reference_meta_path, *references.values()],
                config=reference_config,
                versions={"policy": 2},
            )

    reference_identity = {
        speaker: fingerprint_file(path)
        for speaker, path in sorted(references.items())
    }
    if voice_ref is not None:
        reference_identity = {"override": fingerprint_file(voice_ref.resolve())}

    tts_output_dir = job_dir / "tts"
    tts_request_receipt_path = tts_output_dir / "request.json"
    tts_request_inputs = {
        "translated": fingerprint_file(tts_text_source_json),
        "translation_stage": str((translation_manifest or {}).get("fingerprint") or translation_fingerprint),
        "glossary": fingerprint_file(glossary_path),
        "references": reference_identity,
        "reference_stage": str((reference_manifest or {}).get("fingerprint") or reference_fingerprint),
        "provider": requested_provider,
        "semantic_rewrite_gate": (
            semantic_config
            if bool(semantic_config.get("enabled", False)) and selected_profile != "fast"
            else {"enabled": False}
        ),
        "review_overrides": {
            str(segment_id): {
                "text": state["text"],
                "speaker": state["speaker"],
            }
            for segment_id, state in sorted(review_overrides.items())
        },
    }
    tts_config = dict(config.get("tts", {}) or {})
    timing_config = dict(config.get("timing", {}) or {})
    timing_windows = allocate_timing_windows(
        segments,
        pad_seconds=max(0.0, float(timing_config.get("segment_pad_ms", 35))) / 1000.0,
        max_borrow_seconds=max(0.0, float(timing_config.get("max_borrow_seconds", 0.35))),
    )

    def placement_segment(segment: Segment) -> Segment:
        window = timing_windows.get(segment.id)
        if window is None:
            return segment
        placed = Segment.from_dict(segment.to_dict())
        placed.start = window.start
        placed.end = window.end
        return placed

    tts_model = {
        "name": "VieNeu-v3turbo",
        "backend": tts_config.get("backend"),
        "device": tts_config.get("device"),
        "precision": tts_config.get("precision"),
        "rewrite_model": translation_model_provenance,
    }
    tts_versions = {
        "policy": 5,
        "timing_policy": TIMING_POLICY_VERSION,
        "translation_policy": TRANSLATION_POLICY_VERSION,
        "semantic_question_policy": SEMANTIC_QA_QUESTION_VERSION,
        "pronunciation_policy": 1,
        "semantic_rewrite_gate_policy": 1,
        **runtime_versions("vieneu", "torch"),
    }
    tts_request_fingerprint = stage_fingerprint(
        "tts_request",
        inputs=tts_request_inputs,
        config={"tts": tts_config, "timing": timing_config},
        model=tts_model,
        prompt={"rewrite_policy": "duration-rewrite-v1"},
        versions=tts_versions,
    )
    tts_stats_path = job_dir / "tts_stats.json"
    tts_translation_meta_path = job_dir / "tts_translation_meta.json"
    tts_manifest = _load_tts_manifest(
        job_dir,
        tts_request_fingerprint,
        translated_json,
        resume=resume,
    )
    current_rewrite_stats: dict[str, Any] = {}
    pronunciation_map = dict(tts_config.get("pronunciation_map", {}) or {})

    def verify_rewrite(segment: Segment, candidate_text: str) -> bool:
        return verify_semantic_candidate(segment, candidate_text, "rewrite_candidate")

    def tts_text(segment: Segment) -> str:
        return normalize_pronunciation(
            segment.vi,
            pronunciation_map,
            normalize_numbers=True,
        ).tts_text

    if tts_manifest is not None:
        rewrite_meta = _load_json_dict(tts_translation_meta_path)
        if not isinstance(rewrite_meta.get("stats"), dict):
            tts_manifest = None
    if tts_manifest is not None:
        metrics.increment("cache_hits")
        metrics.set_counter("tts_inferences", 0)
        metrics.set_counter("tts_batches", 0)
        with metrics.stage("tts_cache_load"):
            segments = _load_segments(translated_json)
            stats_data = json.loads(tts_stats_path.read_text(encoding="utf-8"))
            tts_stats = [TTSStat(**item) for item in stats_data]
            rendered = [
                (placement_segment(segment), tts_output_dir / f"{segment.id:05d}.wav")
                for segment in segments
            ]
            rewrite_stats = dict(rewrite_meta["stats"])
    else:
        metrics.increment("cache_misses")
        tts_request_receipt_path, _partial_request_matches = _prepare_tts_partial_cache(
            tts_output_dir,
            tts_request_fingerprint,
            resume=resume,
            preserve_raw_on_mismatch=bool(review_overrides),
        )
        translator = build_translator(
            config["translation"],
            job_dir,
            requested_provider,
            retry_budget=translation_retry_budget,
            model_override=selected_model,
            effort_override=selected_effort,
        )

        with translator.running():
            rendered, tts_stats = synthesize_segments(
                segments,
                tts_output_dir,
                config["tts"],
                config["timing"],
                translator,
                glossary,
                references,
                voice_ref=voice_ref,
                progress_callback=lambda value, message: progress(
                    0.54 + value * 0.26,
                    message,
                    stage="tts",
                ),
                metrics=metrics,
                rewrite_verifier=verify_rewrite,
                tts_text_mapper=tts_text,
                timing_windows=timing_windows,
                locked_segment_ids=manual_review_ids,
            )
            current_rewrite_stats = translator.stats()
        rewrite_stats = dict(current_rewrite_stats)
        _save_segments(translated_json, segments, review_statuses=review_statuses)
        tts_stats_path.write_text(
            json.dumps([item.to_dict() for item in tts_stats], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        atomic_write_json(
            tts_translation_meta_path,
            {
                "requested_provider": requested_provider,
                "selection": translation_selection,
                "stats": rewrite_stats,
                "rewrite_gate": semantic_gate_receipts,
            },
        )
        tts_manifest = _commit_stage(
            job_dir,
            "tts",
            inputs={
                "request_fingerprint": tts_request_fingerprint,
                "final_text": fingerprint_file(translated_json),
            },
            artifacts=[
                translated_json,
                tts_stats_path,
                tts_translation_meta_path,
                tts_request_receipt_path,
                *[path for _, path in rendered],
            ],
            config={"tts": tts_config, "timing": timing_config},
            model=tts_model,
            prompt={"rewrite_policy": "duration-rewrite-v1"},
            versions=tts_versions,
        )

    rewritten_ids = {item.segment_id for item in tts_stats if item.rewrites > 0}
    rewritten_segments_for_qa = [item for item in segments if item.id in rewritten_ids]
    with metrics.stage("semantic_qa_rewritten"):
        rewritten_semantic_qa = run_semantic_qa_stage(
            rewritten_segments_for_qa,
            semantic_config,
            stage="rewritten",
            cache_path=semantic_qa_path,
            resume=resume,
        )
        _register_semantic_qa_artifact(
            job_dir,
            semantic_qa_path,
            "rewritten",
            rewritten_semantic_qa,
            semantic_config,
        )
    metrics.increment(
        "cache_hits" if bool(rewritten_semantic_qa.get("_runtime_cache_hit")) else "cache_misses"
    )
    _record_typesafe_metrics(metrics, rewritten_semantic_qa)
    if rewritten_semantic_qa.get("status") == "ok":
        review_count = int(rewritten_semantic_qa.get("needs_review") or 0)
        console.print(
            "Semantic QA sau rút gọn (shadow): "
            f"{review_count}/{len(rewritten_segments_for_qa)} đoạn cần xem lại."
        )

    translation_stats = dict(translation_stats)
    translation_stats["prefit_rewrite_calls"] = int(current_prefit_stats.get("rewrite_calls") or 0)
    translation_stats["rewrite_calls"] = int(translation_stats.get("rewrite_calls") or 0) + int(
        current_prefit_stats.get("rewrite_calls") or 0
    ) + int(
        rewrite_stats.get("rewrite_calls") or 0
    )
    if bool(rewrite_stats.get("fallback_used")):
        translation_stats["fallback_used"] = True
        translation_stats["fallback_reason"] = rewrite_stats.get("fallback_reason")
    translation_stats["rewrite_provider"] = rewrite_stats.get("used") or rewrite_stats.get("requested")
    translation_stats["retry_budget"] = translation_retry_budget
    for retry_counter in (
        "webgpt_attempts",
        "webgpt_retry_attempts",
        "webgpt_failures",
        "webgpt_retries_exhausted",
    ):
        translation_stats[retry_counter] = int(translation_stats.get(retry_counter) or 0) + int(
            current_prefit_stats.get(retry_counter) or 0
        ) + int(current_rewrite_stats.get(retry_counter) or 0)

    # Metrics describe work performed by this invocation, not historical
    # statistics reloaded from successful cache artifacts.
    translation_batches = int(current_translation_stats.get("translation_batches") or 0)
    metrics.set_counter("translation_calls", translation_batches)
    metrics.set_counter(
        "webgpt_batches",
        int(
            current_translation_stats.get("webgpt_batches")
            or (translation_batches if requested_provider == "webgpt" else 0)
        ),
    )
    metrics.set_counter(
        "rewrite_calls",
        int(current_prefit_stats.get("rewrite_calls") or 0)
        + int(current_rewrite_stats.get("rewrite_calls") or 0),
    )
    for retry_counter in (
        "webgpt_attempts",
        "webgpt_retry_attempts",
        "webgpt_failures",
        "webgpt_retries_exhausted",
    ):
        metrics.set_counter(
            retry_counter,
            int(current_translation_stats.get(retry_counter) or 0)
            + int(current_prefit_stats.get(retry_counter) or 0)
            + int(current_rewrite_stats.get(retry_counter) or 0),
        )

    _save_segments(translated_json, segments)
    tts_stats_path.write_text(
        json.dumps([item.to_dict() for item in tts_stats], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    segment_qa_report: dict[str, Any] | None = None
    qa_config = dict(config.get("qa", {}) or {})
    if bool(qa_config.get("enabled", True)) and bool(qa_config.get("segment_enabled", True)):
        metrics.set_counter("qa_repairs", 0)
        segment_qa_path = job_dir / "segment_qa.json"

        def segment_qa_inputs() -> dict[str, Any]:
            return {
                "segments": fingerprint_file(translated_json),
                "audio": [
                    {
                        "segment_id": segment.id,
                        "artifact": fingerprint_file(path),
                    }
                    for segment, path in rendered
                ],
            }

        segment_qa_model = {
            "language": "vi",
            "name": config["asr"].get("model"),
        }
        segment_qa_stage_config = {
            "policy_version": SEGMENT_QA_POLICY_VERSION,
            "asr": dict(config.get("asr", {}) or {}),
            "min_similarity": float(qa_config.get("min_similarity", 0.78)),
            "severe_similarity": float(qa_config.get("severe_similarity", 0.55)),
            "max_timing_ratio": float(qa_config.get("max_timing_ratio", 1.03)),
            "repair_actions": list(
                qa_config.get(
                    "repair_actions",
                    ["pronunciation_retry", "timing_rewrite"],
                )
            ),
            "max_repairs": int(
                qa_config.get(
                    "max_segment_repairs",
                    config.get("reliability", {}).get("retry_budget", 3),
                )
            ),
        }
        segment_qa_fingerprint = stage_fingerprint(
            "segment_qa",
            inputs=segment_qa_inputs(),
            config=segment_qa_stage_config,
            model=segment_qa_model,
            versions={
                "policy": SEGMENT_QA_POLICY_VERSION,
                "pronunciation_policy": 1,
            },
        )
        progress(0.80, "Đang kiểm tra từng đoạn thoại", stage="segment_qa")
        with metrics.stage("segment_qa"):
            if _stage_cache_hit(
                job_dir,
                "segment_qa",
                segment_qa_fingerprint,
                resume=resume,
            ):
                metrics.increment("cache_hits")
                segment_qa_report = _load_json_dict(segment_qa_path)
            else:
                metrics.increment("cache_misses")
                rendered_by_id = {segment.id: (segment, path) for segment, path in rendered}
                stats_by_id = {item.segment_id: item for item in tts_stats}
                actual_texts = transcribe_text_files(
                    [rendered_by_id[segment.id][1] for segment in segments],
                    config["asr"],
                    language="vi",
                )
                spoken_qa_contracts = {
                    segment.id: _segment_qa_spoken_contract(segment, pronunciation_map)
                    for segment in segments
                }
                observations = [
                    SegmentQAObservation(
                        segment_id=segment.id,
                        expected=spoken_qa_contracts[segment.id][0],
                        actual=actual,
                        target_duration=stats_by_id[segment.id].target_duration,
                        actual_duration=stats_by_id[segment.id].final_duration,
                        critical_terms=spoken_qa_contracts[segment.id][1],
                    )
                    for segment, actual in zip(segments, actual_texts, strict=True)
                ]

                def repair_segments(
                    repair_plan: list[dict[str, Any]],
                ) -> list[SegmentQAObservation]:
                    repair_ids = {int(item["segment_id"]) for item in repair_plan}
                    selected = [segment for segment in segments if segment.id in repair_ids]
                    for segment in selected:
                        raw_path = tts_output_dir / f"{segment.id:05d}_raw.wav"
                        raw_path.with_name(f"{raw_path.stem}.meta.json").unlink(missing_ok=True)

                    repair_translator = build_translator(
                        config["translation"],
                        job_dir,
                        requested_provider,
                        retry_budget=translation_retry_budget,
                        model_override=selected_model,
                        effort_override=selected_effort,
                    )
                    with repair_translator.running():
                        repaired_rendered, repaired_stats = synthesize_segments(
                            selected,
                            tts_output_dir,
                            config["tts"],
                            config["timing"],
                            repair_translator,
                            glossary,
                            references,
                            voice_ref=voice_ref,
                            metrics=metrics,
                            rewrite_verifier=verify_rewrite,
                            tts_text_mapper=tts_text,
                            timing_windows=timing_windows,
                            locked_segment_ids=manual_review_ids,
                        )
                    repair_stats = repair_translator.stats()
                    repair_rewrites = int(repair_stats.get("rewrite_calls") or 0)
                    if repair_rewrites:
                        translation_stats["rewrite_calls"] = int(
                            translation_stats.get("rewrite_calls") or 0
                        ) + repair_rewrites
                        metrics.increment("rewrite_calls", repair_rewrites)
                    for retry_counter in (
                        "webgpt_attempts",
                        "webgpt_retry_attempts",
                        "webgpt_failures",
                        "webgpt_retries_exhausted",
                    ):
                        retry_value = int(repair_stats.get(retry_counter) or 0)
                        if retry_value:
                            translation_stats[retry_counter] = int(
                                translation_stats.get(retry_counter) or 0
                            ) + retry_value
                            metrics.increment(retry_counter, retry_value)

                    for segment, path in repaired_rendered:
                        rendered_by_id[segment.id] = (segment, path)
                    for item in repaired_stats:
                        stats_by_id[item.segment_id] = item

                    repaired_actuals = transcribe_text_files(
                        [rendered_by_id[segment.id][1] for segment in selected],
                        config["asr"],
                        language="vi",
                    )
                    repaired_observations: list[SegmentQAObservation] = []
                    for segment, actual in zip(selected, repaired_actuals, strict=True):
                        expected, critical_terms = _segment_qa_spoken_contract(
                            segment,
                            pronunciation_map,
                        )
                        repaired_observations.append(
                            SegmentQAObservation(
                                segment_id=segment.id,
                                expected=expected,
                                actual=actual,
                                target_duration=stats_by_id[segment.id].target_duration,
                                actual_duration=stats_by_id[segment.id].final_duration,
                                critical_terms=critical_terms,
                            )
                        )
                    return repaired_observations

                segment_qa_report = run_segment_qa_cycle(
                    observations,
                    glossary_terms=glossary.values(),
                    min_similarity=segment_qa_stage_config["min_similarity"],
                    severe_similarity=segment_qa_stage_config["severe_similarity"],
                    max_timing_ratio=segment_qa_stage_config["max_timing_ratio"],
                    repair=repair_segments,
                    repair_actions=segment_qa_stage_config["repair_actions"],
                    max_repairs=segment_qa_stage_config["max_repairs"],
                )
                completed_repairs = int(
                    segment_qa_report.get("summary", {}).get("repairs_completed") or 0
                )
                metrics.increment("qa_repairs", completed_repairs)

                if completed_repairs:
                    rendered = [rendered_by_id[segment.id] for segment in segments]
                    tts_stats = [stats_by_id[segment.id] for segment in segments]
                    _save_segments(translated_json, segments, review_statuses=review_statuses)
                    tts_stats_path.write_text(
                        json.dumps(
                            [item.to_dict() for item in tts_stats],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    tts_manifest = _commit_stage(
                        job_dir,
                        "tts",
                        inputs={
                            "request_fingerprint": tts_request_fingerprint,
                            "final_text": fingerprint_file(translated_json),
                        },
                        artifacts=[
                            translated_json,
                            tts_stats_path,
                            tts_translation_meta_path,
                            tts_request_receipt_path,
                            *[path for _, path in rendered],
                        ],
                        config={"tts": tts_config, "timing": timing_config},
                        model=tts_model,
                        prompt={"rewrite_policy": "duration-rewrite-v1"},
                        versions=tts_versions,
                    )

                atomic_write_json(segment_qa_path, segment_qa_report)
                final_segment_qa_inputs = segment_qa_inputs()
                _commit_stage(
                    job_dir,
                    "segment_qa",
                    inputs=final_segment_qa_inputs,
                    artifacts=[segment_qa_path],
                    config=segment_qa_stage_config,
                    model=segment_qa_model,
                    versions={
                        "policy": SEGMENT_QA_POLICY_VERSION,
                        "pronunciation_policy": 1,
                    },
                )

        segment_summary = segment_qa_report.get("summary", {}) if segment_qa_report else {}
        console.print(
            "QA theo đoạn: "
            f"{int(segment_summary.get('final_failed') or 0)}/{len(segments)} đoạn còn bị flag; "
            f"đã repair {int(segment_summary.get('repairs_completed') or 0)} đoạn."
        )

    write_srt(segments, output_path.with_suffix(".vi.srt"), translated=True)

    overflow = [item for item in tts_stats if item.final_duration > item.target_duration * 1.03]
    if overflow:
        console.print(
            f"[yellow]Cảnh báo thời lượng:[/] {len(overflow)} đoạn vẫn dài hơn khung sau khi rút gọn câu và giới hạn tempo."
        )

    voice_track = job_dir / "voice_vi.wav"
    assembly_config = {
        "sample_rate": int(config["tts"].get("sample_rate", 48000)),
        "fade_ms": max(0.0, float(timing_config.get("boundary_fade_ms", 8.0))),
        "overlap_policy": str(timing_config.get("overlap_policy", "equal_power")),
    }
    assembly_inputs = {
        "tts": str((tts_manifest or {}).get("fingerprint") or tts_request_fingerprint),
        "rendered_audio": [
            {
                "segment_id": segment.id,
                "artifact": fingerprint_file(path),
            }
            for segment, path in rendered
        ],
        "total_duration": total_duration,
    }
    assembly_fingerprint = stage_fingerprint(
        "timing_assembly",
        inputs=assembly_inputs,
        config=assembly_config,
        versions={"policy": 3},
    )
    with metrics.stage("timing_assembly"):
        if _stage_cache_hit(job_dir, "timing_assembly", assembly_fingerprint, resume=resume):
            metrics.increment("cache_hits")
        else:
            metrics.increment("cache_misses")
            voice_track = assemble_voice_track(
                rendered,
                voice_track,
                total_duration=total_duration,
                sample_rate=assembly_config["sample_rate"],
                fade_ms=assembly_config["fade_ms"],
                overlap_policy=assembly_config["overlap_policy"],
            )
            _commit_stage(
                job_dir,
                "timing_assembly",
                inputs=assembly_inputs,
                artifacts=[voice_track],
                config=assembly_config,
                versions={"policy": 3},
            )

    voice_metrics = voice_track_metrics(voice_track)
    console.rule("5/6 Mix + ghép video")
    progress(0.84, "Đang mix âm thanh và ghép video cuối", stage="mix_mux")
    mix = dict(config["mix"])
    cached_video = job_dir / "dubbed.mp4"
    mix_metrics_path = job_dir / "mix_metrics.json"
    mix_inputs = {
        "source": source_identity,
        "background": fingerprint_file(background),
        "voice_track": fingerprint_file(voice_track),
    }
    mix_fingerprint = stage_fingerprint(
        "mix_mux",
        inputs=mix_inputs,
        config=mix,
        versions={"policy": 2},
    )
    with metrics.stage("mix_mux"):
        if _stage_cache_hit(job_dir, "mix_mux", mix_fingerprint, resume=resume):
            metrics.increment("cache_hits")
        else:
            metrics.increment("cache_misses")
            target_lufs, target_true_peak = resolve_loudness_profile(
                profile=str(mix.get("loudness_profile") or config.get("profile") or "youtube"),
                custom_lufs=mix.get("final_lufs"),
                custom_true_peak=mix.get("final_true_peak_db"),
            )
            mux_dubbed_video(
                input_path,
                background,
                voice_track,
                cached_video,
                background_gain_db=float(mix.get("background_gain_db", 0.0)),
                voice_gain_db=float(mix.get("voice_gain_db", 1.5)),
                final_lufs=target_lufs,
                true_peak_db=target_true_peak,
                duck_background=bool(mix.get("duck_background", False)),
            )
            atomic_write_json(
                mix_metrics_path,
                measure_mix_metrics(
                    cached_video,
                    target_lufs=target_lufs,
                    target_true_peak_db=target_true_peak,
                ),
            )
            _commit_stage(
                job_dir,
                "mix_mux",
                inputs=mix_inputs,
                artifacts=[cached_video, mix_metrics_path],
                config=mix,
                versions={"policy": 2},
            )
        if cached_video.resolve() != output_path.resolve():
            shutil.copy2(cached_video, output_path)

    mix_metrics = _load_json_dict(mix_metrics_path)

    qa_result: dict[str, Any] | None = None
    if bool(config.get("qa", {}).get("enabled", True)):
        console.rule("6/6 Kiểm tra lại bằng ASR")
        progress(0.91, "Đang kiểm tra lại track tiếng Việt bằng ASR", stage="acoustic_qa")
        expected = " ".join(item.vi for item in segments)
        qa_raw_path = job_dir / "qa_raw.json"
        qa_inputs = {
            "voice_track": fingerprint_file(voice_track),
            "expected": expected,
        }
        qa_inference_config = dict(config.get("asr", {}) or {})
        qa_fingerprint = stage_fingerprint(
            "acoustic_qa",
            inputs=qa_inputs,
            config=qa_inference_config,
            model={"language": "vi", "name": qa_inference_config.get("model")},
            versions={"policy": 1},
        )
        with metrics.stage("acoustic_qa"):
            if _stage_cache_hit(job_dir, "acoustic_qa", qa_fingerprint, resume=resume):
                metrics.increment("cache_hits")
                raw_qa = json.loads(qa_raw_path.read_text(encoding="utf-8"))
            else:
                metrics.increment("cache_misses")
                actual = transcribe_text(voice_track, config["asr"], language="vi")
                raw_qa = {
                    "similarity": transcript_similarity(expected, actual),
                    "expected": expected,
                    "actual": actual,
                }
                atomic_write_json(qa_raw_path, raw_qa)
                _commit_stage(
                    job_dir,
                    "acoustic_qa",
                    inputs=qa_inputs,
                    artifacts=[qa_raw_path],
                    config=qa_inference_config,
                    model={"language": "vi", "name": qa_inference_config.get("model")},
                    versions={"policy": 1},
                )
            similarity = float(raw_qa["similarity"])
            threshold = float(config["qa"].get("min_similarity", 0.78))
            global_passed = similarity >= threshold
            segment_summary = (
                segment_qa_report.get("summary", {}) if segment_qa_report else {}
            )
            segment_passed = bool(segment_summary.get("passed", True))
            qa_result = {
                **raw_qa,
                "threshold": threshold,
                "global_passed": global_passed,
                "segment_summary": segment_summary,
                "segment_artifact": (
                    str(job_dir / "segment_qa.json") if segment_qa_report else None
                ),
                "passed": global_passed and segment_passed,
            }
            atomic_write_json(job_dir / "qa.json", qa_result)
        status = "green" if qa_result["passed"] else "yellow"
        console.print(f"Độ tương đồng QA: [{status}]{similarity:.1%}[/] (mục tiêu {threshold:.0%})")
    else:
        console.rule("6/6 Bỏ qua QA")

    elapsed_seconds = time.perf_counter() - started_at
    rewritten_segments = sum(1 for item in tts_stats if item.rewrites > 0)
    cloned_segments = sum(1 for item in tts_stats if item.used_clone)
    avg_tempo = sum(item.tempo for item in tts_stats) / max(1, len(tts_stats))
    max_tempo = max((item.tempo for item in tts_stats), default=1.0)
    metrics.finish()
    metrics_snapshot = metrics.snapshot()
    metrics.write_snapshot(metrics_path)
    result = {
        "output": str(output_path),
        "subtitle": str(output_path.with_suffix(".vi.srt")),
        "work_dir": str(job_dir),
        "duration_seconds": total_duration,
        "elapsed_seconds": elapsed_seconds,
        "real_time_factor": elapsed_seconds / max(total_duration, 0.001),
        "segments": len(segments),
        "speakers": sorted({item.speaker for item in segments}),
        "speaker_count": len({item.speaker for item in segments}),
        "diarization": diarize,
        "overflow_segments": len(overflow),
        "rewritten_segments": rewritten_segments,
        "cloned_segments": cloned_segments,
        "average_tempo": avg_tempo,
        "max_tempo": max_tempo,
        "voice_track": voice_metrics,
        "mix": mix_metrics,
        "translation": translation_stats,
        "semantic_qa": semantic_qa_summary(semantic_qa_path),
        "qa": qa_result,
        "metrics": {
            "artifact": str(metrics_path),
            "schema_version": metrics_snapshot["schema_version"],
        },
        "profile": selected_profile,
        "profile_policy": config.get("profile_policy", {}),
    }
    atomic_write_json(job_dir / "result.json", result)
    update_job_state(
        job_dir,
        status="completed",
        stage="complete",
        progress=1.0,
        message="Hoàn tất",
        result=result,
    )
    _notify(progress_callback, 1.0, "Hoàn tất")
    return result


def run_pipeline(
    input_path: Path,
    output_path: Path,
    config_path: Path,
    voice_ref: Path | None = None,
    hf_token: str | None = None,
    diarize_override: bool | None = None,
    translation_provider: str | None = None,
    translation_model: str | None = None,
    translation_effort: str | None = None,
    translation_display_name: str | None = None,
    translation_catalog_revision: str | None = None,
    translation_catalog_timestamp: str | None = None,
    profile: str | None = None,
    resume: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the pipeline and leave a durable failure receipt when possible."""
    invocation_started = time.time()
    resolved_input = input_path.resolve()
    pre_source_identity: dict[str, Any] | None = None
    pre_job_dir: Path | None = None
    pre_job_existed = False
    if resolved_input.is_file():
        pre_source_identity = fingerprint_file(resolved_input)
        pre_job_dir = _job_dir(resolved_input, pre_source_identity)
        pre_job_existed = pre_job_dir.exists()
        if pre_job_existed and resume:
            reconcile_job_state(pre_job_dir)
    try:
        if pre_job_dir is None:
            return _run_pipeline_impl(
                input_path,
                output_path,
                config_path,
                voice_ref=voice_ref,
                hf_token=hf_token,
                diarize_override=diarize_override,
                translation_provider=translation_provider,
                translation_model=translation_model,
                translation_effort=translation_effort,
                translation_display_name=translation_display_name,
                translation_catalog_revision=translation_catalog_revision,
                translation_catalog_timestamp=translation_catalog_timestamp,
                profile=profile,
                resume=resume,
                progress_callback=progress_callback,
                _source_identity=pre_source_identity,
                _job_existed=pre_job_existed,
            )
        with claim_job(pre_job_dir):
            return _run_pipeline_impl(
                input_path,
                output_path,
                config_path,
                voice_ref=voice_ref,
                hf_token=hf_token,
                diarize_override=diarize_override,
                translation_provider=translation_provider,
                translation_model=translation_model,
                translation_effort=translation_effort,
                translation_display_name=translation_display_name,
                translation_catalog_revision=translation_catalog_revision,
                translation_catalog_timestamp=translation_catalog_timestamp,
                profile=profile,
                resume=resume,
                progress_callback=progress_callback,
                _source_identity=pre_source_identity,
                _job_existed=pre_job_existed,
            )
    except PipelineControl as exc:
        if pre_job_dir is not None:
            update_job_state(
                pre_job_dir,
                status=exc.status,
                message=str(exc),
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        raise
    except Exception as exc:
        try:
            if resolved_input.is_file():
                source_identity = pre_source_identity or fingerprint_file(resolved_input)
                job_dir = pre_job_dir or _job_dir(resolved_input, source_identity)
                update_job_state(
                    job_dir,
                    status="failed",
                    message=str(exc),
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                failure_path = job_dir / "metrics.json"
                current_failure = False
                if failure_path.exists() and failure_path.stat().st_mtime >= invocation_started - 0.01:
                    current_failure = _load_json_dict(failure_path).get("status") == "failed"
                if not current_failure:
                    job_dir.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(
                        failure_path,
                        {
                            "schema_version": 1,
                            "status": "failed",
                            "run": {
                                "kind": (
                                    "cold"
                                    if not pre_job_existed
                                    else ("resume" if resume else "warm")
                                ),
                                "metadata": {
                                    "input_name": resolved_input.name,
                                    "source_sha256": source_identity["sha256"],
                                },
                            },
                            "error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            },
                        },
                    )
        except Exception:
            pass
        raise
