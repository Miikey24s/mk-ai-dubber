param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$KeepSandbox
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required for the P20 fresh-install smoke test."
}

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$sandbox = Join-Path ([IO.Path]::GetTempPath()) ("vi-dubber-p20-fresh-" + [guid]::NewGuid().ToString("N"))
$ffmpegFixture = Get-ChildItem (Join-Path $ProjectRoot "tools\downloads") -Filter "*FFmpeg*zip" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $ffmpegFixture) {
    throw "A cached FFmpeg portable zip under tools/downloads is required for an offline setup smoke."
}

$mainVenvConfig = Join-Path $ProjectRoot ".venv\pyvenv.cfg"
$mainVenvHashBefore = if (Test-Path $mainVenvConfig) {
    (Get-FileHash -Algorithm SHA256 $mainVenvConfig).Hash
} else {
    $null
}
$oldPath = $env:PATH
$oldUvOffline = $env:UV_OFFLINE
$oldUvLinkMode = $env:UV_LINK_MODE
$oldFixture = $env:P20_FFMPEG_FIXTURE

New-Item -ItemType Directory -Path $sandbox | Out-Null

try {
    foreach ($name in @("pyproject.toml", "uv.lock", "config.yaml", "glossary.yaml", "setup.ps1")) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $name) -Destination $sandbox
    }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "src") -Destination $sandbox -Recurse

    $shimDir = Join-Path $sandbox "shim"
    New-Item -ItemType Directory -Path $shimDir | Out-Null
    $wingetShim = @'
@echo off
setlocal EnableExtensions
set "dest="
:next
if "%~1"=="" goto copy
if /I "%~1"=="--download-directory" (
  set "dest=%~2"
  shift
)
shift
goto next
:copy
if not defined dest exit /b 2
copy /Y "%P20_FFMPEG_FIXTURE%" "%dest%\P20-FFmpeg-fixture.zip" >nul
exit /b %ERRORLEVEL%
'@
    Set-Content -LiteralPath (Join-Path $shimDir "winget.cmd") -Value $wingetShim -Encoding Ascii

    $env:P20_FFMPEG_FIXTURE = $ffmpegFixture.FullName
    $env:UV_OFFLINE = "1"
    $env:UV_LINK_MODE = "copy"
    $env:PATH = "$shimDir;$oldPath"

    Push-Location $sandbox
    try {
        & (Join-Path $sandbox "setup.ps1")
        if ($LASTEXITCODE -ne 0) {
            throw "setup.ps1 failed with exit code $LASTEXITCODE"
        }

        $cli = Join-Path $sandbox ".venv\Scripts\vi-dubber.exe"
        & $cli --help | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "vi-dubber --help failed with exit code $LASTEXITCODE"
        }

        $modelTarget = Join-Path $sandbox "models\llm\Qwen--Qwen3-14B-GGUF\Qwen3-14B-Q4_K_M.gguf"
        if (Test-Path -LiteralPath $modelTarget) {
            throw "Default setup unexpectedly provisioned the opt-in local model."
        }

        $modelFixture = Join-Path $sandbox "Qwen3-14B-Q4_K_M.fixture.gguf"
        $fixtureStream = [IO.File]::Open($modelFixture, [IO.FileMode]::CreateNew)
        try {
            $fixtureStream.SetLength(1000001)
        }
        finally {
            $fixtureStream.Dispose()
        }
        $fixtureHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $modelFixture).Hash.ToLowerInvariant()

        $checksumRejected = $false
        try {
            & (Join-Path $sandbox "setup.ps1") -ProvisionLocalModel `
                -LocalModelSource $modelFixture `
                -LocalModelSha256 ("0" * 64)
        }
        catch {
            $checksumRejected = $_.Exception.Message -match "provisioning failed"
        }
        if (-not $checksumRejected) {
            throw "Local-model opt-in did not reject an incorrect SHA-256."
        }
        if ((Test-Path -LiteralPath $modelTarget) -or (Test-Path -LiteralPath ($modelTarget + ".partial"))) {
            throw "Checksum rejection left a target or partial model behind."
        }

        & (Join-Path $sandbox "setup.ps1") -ProvisionLocalModel `
            -LocalModelSource $modelFixture `
            -LocalModelSha256 $fixtureHash
        if ($LASTEXITCODE -ne 0) {
            throw "setup.ps1 local-model opt-in failed with exit code $LASTEXITCODE"
        }
        if (-not (Test-Path -LiteralPath $modelTarget -PathType Leaf)) {
            throw "Local-model opt-in did not create the configured target."
        }
        $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $modelTarget).Hash.ToLowerInvariant()
        if ($targetHash -ne $fixtureHash) {
            throw "Provisioned local-model checksum does not match the fixture."
        }
    }
    finally {
        Pop-Location
    }

    $mainVenvHashAfter = if (Test-Path $mainVenvConfig) {
        (Get-FileHash -Algorithm SHA256 $mainVenvConfig).Hash
    } else {
        $null
    }
    if ($mainVenvHashBefore -ne $mainVenvHashAfter) {
        throw "Main project .venv changed during disposable fresh-install smoke."
    }

    [pscustomobject]@{
        status = "PASS"
        sandbox = $sandbox
        setup = ".\\setup.ps1 with UV_OFFLINE=1 and a temp-only winget shim backed by the cached FFmpeg archive"
        ffmpeg_fixture = $ffmpegFixture.Name
        cli = ".venv\\Scripts\\vi-dubber.exe --help"
        default_model_download = "skipped"
        local_model_opt_in = "PASS from a local 1,000,001-byte fixture with SHA-256 verification"
        checksum_mismatch = "rejected without a target or partial file"
        local_model_target = "models\\llm\\Qwen--Qwen3-14B-GGUF\\Qwen3-14B-Q4_K_M.gguf"
        main_venv_unchanged = $true
        note = "Exercises the real default setup and explicit local-model provisioning without network or system configuration changes."
    } | ConvertTo-Json -Depth 3
}
finally {
    $env:PATH = $oldPath
    $env:UV_OFFLINE = $oldUvOffline
    $env:UV_LINK_MODE = $oldUvLinkMode
    $env:P20_FFMPEG_FIXTURE = $oldFixture
    if (-not $KeepSandbox -and (Test-Path $sandbox)) {
        Remove-Item -LiteralPath $sandbox -Recurse -Force
    }
}
