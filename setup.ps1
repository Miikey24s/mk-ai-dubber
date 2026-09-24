param(
    [switch]$ProvisionLocalModel,
    [string]$LocalModelSource,
    [ValidatePattern("^[0-9a-fA-F]{64}$")]
    [string]$LocalModelSha256
)

$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Tools = Join-Path $Root "tools"
$Downloads = Join-Path $Tools "downloads"
New-Item -ItemType Directory -Force -Path $Downloads | Out-Null

function Ensure-FFmpeg {
    $existing = Get-ChildItem (Join-Path $Tools "ffmpeg") -Recurse -Filter "ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existing) { return }

    winget download --id BtbN.FFmpeg.GPL.Shared.7.1 --source winget --exact `
        --download-directory $Downloads --accept-package-agreements --accept-source-agreements --disable-interactivity
    $zip = Get-ChildItem $Downloads -Filter "*FFmpeg*zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $zip) { throw "FFmpeg portable archive was not downloaded." }
    Expand-Archive -LiteralPath $zip.FullName -DestinationPath (Join-Path $Tools "ffmpeg") -Force
}

function Install-LocalModel {
    $provisionScript = @'
import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import yaml
from huggingface_hub import get_hf_file_metadata, hf_hub_download, hf_hub_url


MIN_MODEL_BYTES = 1_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument("--root", required=True)
parser.add_argument("--source")
parser.add_argument("--sha256")
args = parser.parse_args()

root = Path(args.root).resolve()
with (root / "config.yaml").open("r", encoding="utf-8") as handle:
    config = yaml.safe_load(handle) or {}
if not isinstance(config, dict):
    raise SystemExit("config.yaml root must be a mapping")
translation = config.get("translation", {})
if not isinstance(translation, dict):
    raise SystemExit("config.yaml translation must be a mapping")

repo = str(translation.get("repo") or "").strip()
filename = str(translation.get("filename") or "").strip()
repo_parts = repo.split("/")
if not repo or any(
    not part or part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
    for part in repo_parts
):
    raise SystemExit(f"translation.repo is not a safe Hugging Face repo id: {repo!r}")
if not filename or Path(filename).name != filename or filename in {".", ".."}:
    raise SystemExit(f"translation.filename is not a safe filename: {filename!r}")

model_dir = root / "models" / "llm" / repo.replace("/", "--")
model_dir.mkdir(parents=True, exist_ok=True)
target = model_dir / filename
expected_sha = (args.sha256 or "").lower() or None

if target.is_file() and target.stat().st_size > MIN_MODEL_BYTES:
    existing_sha = sha256(target) if expected_sha else None
    if (not args.source and not expected_sha) or (expected_sha and existing_sha == expected_sha):
        print(json.dumps({
            "status": "reused",
            "repo": repo,
            "filename": filename,
            "path": str(target),
            "bytes": target.stat().st_size,
            "sha256": existing_sha,
        }))
        raise SystemExit(0)

if args.source:
    source = Path(args.source).resolve()
    if not source.is_file():
        raise SystemExit(f"Local model source does not exist: {source}")
    partial = target.with_name(target.name + ".partial")
    try:
        shutil.copyfile(source, partial)
        actual_sha = sha256(partial)
        if partial.stat().st_size <= MIN_MODEL_BYTES:
            raise RuntimeError(
                f"Local model fixture/file is too small ({partial.stat().st_size} bytes); "
                f"expected more than {MIN_MODEL_BYTES} bytes"
            )
        if expected_sha and actual_sha != expected_sha:
            raise RuntimeError(
                f"Local model SHA-256 mismatch: expected {expected_sha}, got {actual_sha}"
            )
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    validation = "explicit SHA-256" if expected_sha else "existence and minimum size"
else:
    downloaded = Path(hf_hub_download(repo_id=repo, filename=filename, local_dir=str(model_dir)))
    if downloaded.resolve() != target.resolve():
        raise RuntimeError(f"Unexpected Hugging Face target: {downloaded}")
    metadata = get_hf_file_metadata(hf_hub_url(repo_id=repo, filename=filename))
    upstream_etag = str(metadata.etag or "").strip('"').lower()
    if re.fullmatch(r"[0-9a-f]{64}", upstream_etag):
        expected_sha = upstream_etag
    actual_sha = sha256(target)
    if expected_sha and actual_sha != expected_sha:
        raise RuntimeError(
            f"Downloaded local model SHA-256 mismatch: expected {expected_sha}, got {actual_sha}"
        )
    validation = "upstream LFS SHA-256" if expected_sha else "Hugging Face cache metadata and minimum size"

if not target.is_file() or target.stat().st_size <= MIN_MODEL_BYTES:
    raise RuntimeError(f"Local model provisioning did not produce a valid file: {target}")

print(json.dumps({
    "status": "provisioned",
    "repo": repo,
    "filename": filename,
    "path": str(target),
    "bytes": target.stat().st_size,
    "sha256": actual_sha,
    "validation": validation,
}))
'@

    $tempScript = Join-Path ([IO.Path]::GetTempPath()) ("vi-dubber-provision-" + [guid]::NewGuid().ToString("N") + ".py")
    [IO.File]::WriteAllText($tempScript, $provisionScript, [Text.UTF8Encoding]::new($false))
    try {
        $arguments = @("run", "python", $tempScript, "--root", $Root)
        if ($LocalModelSource) {
            $resolvedSource = (Resolve-Path -LiteralPath $LocalModelSource).Path
            $arguments += @("--source", $resolvedSource)
        }
        if ($LocalModelSha256) {
            $arguments += @("--sha256", $LocalModelSha256.ToLowerInvariant())
        }

        & uv @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Local Qwen model provisioning failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Remove-Item -LiteralPath $tempScript -Force -ErrorAction SilentlyContinue
    }
}

if (($LocalModelSource -or $LocalModelSha256) -and -not $ProvisionLocalModel) {
    throw "-LocalModelSource and -LocalModelSha256 require -ProvisionLocalModel."
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install uv first, then rerun setup.ps1."
}
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "WinGet is required to bootstrap portable FFmpeg."
}

Ensure-FFmpeg

Push-Location $Root
try {
    uv sync --dev
    uv run python -m compileall -q src
    if ($ProvisionLocalModel) {
        Install-LocalModel
    }
    uv run vi-dubber doctor
}
finally {
    Pop-Location
}
