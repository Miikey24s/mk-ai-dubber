param(
    [string] $OutputPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputPath) {
    $OutputPath = Join-Path $repoRoot "work\benchmarks\p14-synthetic-acceptance\fixture-ground-truth.json"
}
elseif (-not [IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $repoRoot $OutputPath
}

$ffmpeg = Get-ChildItem (Join-Path $repoRoot "tools\ffmpeg") -Recurse -Filter ffmpeg.exe |
    Select-Object -First 1 -ExpandProperty FullName
$ffprobe = Get-ChildItem (Join-Path $repoRoot "tools\ffmpeg") -Recurse -Filter ffprobe.exe |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $ffmpeg -or -not $ffprobe) {
    throw "Project FFmpeg/ffprobe not found"
}

Add-Type -AssemblyName System.Speech

function Invoke-FFmpeg([string[]] $Arguments) {
    & $ffmpeg -hide_banner -loglevel error -y @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "ffmpeg failed with exit code $LASTEXITCODE"
    }
}

function Get-Duration([string] $Path) {
    $value = & $ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $Path
    if ($LASTEXITCODE -ne 0) {
        throw "ffprobe failed for $Path"
    }
    return [double]::Parse($value.Trim(), [Globalization.CultureInfo]::InvariantCulture)
}

function Write-Speech(
    [string] $Path,
    [string] $Voice,
    [string] $Text
) {
    $speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
    try {
        $speaker.SelectVoice($Voice)
        $speaker.SetOutputToWaveFile($Path)
        $speaker.Speak($Text)
    }
    finally {
        $speaker.Dispose()
    }
}

function Get-Sha256([string] $Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function New-Turn(
    [int] $Index,
    [string] $Speaker,
    [string] $Voice,
    [string] $Text,
    [double] $Start,
    [double] $End
) {
    return [ordered]@{
        index = $Index
        speaker = $Speaker
        voice = $Voice
        text = $Text
        start = [math]::Round($Start, 6)
        end = [math]::Round($End, 6)
        duration = [math]::Round(($End - $Start), 6)
        overlap = $false
    }
}

$voices = @("Microsoft David Desktop", "Microsoft Zira Desktop")
$voiceProbe = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $installedVoices = @($voiceProbe.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
}
finally {
    $voiceProbe.Dispose()
}
foreach ($voice in $voices) {
    if ($voice -notin $installedVoices) {
        throw "Required Windows speech voice is missing: $voice"
    }
}

$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$scratch = Join-Path $temporaryRoot ("vi-dubber-p14-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scratch | Out-Null

try {
    $utterances = @(
        [ordered]@{
            key = "david-b"
            speaker = "SPEAKER_00"
            voice = "Microsoft David Desktop"
            text = "I would wait for the price to close above resistance before changing the plan."
        },
        [ordered]@{
            key = "zira-a"
            speaker = "SPEAKER_01"
            voice = "Microsoft Zira Desktop"
            text = "I agree with the risk limit, but I want to see stronger volume before I enter."
        },
        [ordered]@{
            key = "david-a"
            speaker = "SPEAKER_00"
            voice = "Microsoft David Desktop"
            text = "When the market opens, confirm the trend before entering a position. Keep the risk small and write down the reason for every trade."
        },
        [ordered]@{
            key = "zira-b"
            speaker = "SPEAKER_01"
            voice = "Microsoft Zira Desktop"
            text = "If the breakout fails, I will step aside and review the setup again."
        }
    )

    foreach ($utterance in $utterances) {
        $utterance.path = Join-Path $scratch ($utterance.key + ".wav")
        Write-Speech $utterance.path $utterance.voice $utterance.text
        $utterance.duration = Get-Duration $utterance.path
        $utterance.sha256 = Get-Sha256 $utterance.path
    }

    $reconstructedTwo = Join-Path $scratch "two-speakers.wav"
    Invoke-FFmpeg @(
        "-i", $utterances[0].path,
        "-i", $utterances[1].path,
        "-i", $utterances[2].path,
        "-i", $utterances[3].path,
        "-filter_complex", "[0:a]aresample=48000[a0];[1:a]aresample=48000[a1];[2:a]aresample=48000[a2];[3:a]aresample=48000[a3];[a0][a1][a2][a3]concat=n=4:v=0:a=1[a]",
        "-map", "[a]", "-c:a", "pcm_s16le", $reconstructedTwo
    )

    $delaySeconds = 1.2
    $delayMs = [int]($delaySeconds * 1000)
    $reconstructedOverlap = Join-Path $scratch "overlapping-speech.wav"
    Invoke-FFmpeg @(
        "-i", $utterances[2].path,
        "-i", $utterances[1].path,
        "-filter_complex", "[0:a]aresample=48000[a0];[1:a]aresample=48000,adelay=${delayMs}|${delayMs}[a1];[a0][a1]amix=inputs=2:normalize=0:duration=longest[a]",
        "-map", "[a]", "-c:a", "pcm_s16le", $reconstructedOverlap
    )

    $fixtureRoot = Join-Path $repoRoot "work\benchmarks\p17-synthetic-missing-classes"
    $retainedTwo = Join-Path $fixtureRoot "two-speakers\source.wav"
    $retainedOverlap = Join-Path $fixtureRoot "overlapping-speech\source.wav"
    $twoRetainedHash = Get-Sha256 $retainedTwo
    $twoReconstructedHash = Get-Sha256 $reconstructedTwo
    $overlapRetainedHash = Get-Sha256 $retainedOverlap
    $overlapReconstructedHash = Get-Sha256 $reconstructedOverlap
    if ($twoRetainedHash -ne $twoReconstructedHash) {
        throw "Reconstructed two-speaker WAV does not match the retained fixture"
    }
    if ($overlapRetainedHash -ne $overlapReconstructedHash) {
        throw "Reconstructed overlap WAV does not match the retained fixture"
    }

    $turns = @()
    $cursor = 0.0
    for ($index = 0; $index -lt $utterances.Count; $index++) {
        $item = $utterances[$index]
        $end = $cursor + [double]$item.duration
        $turns += New-Turn $index $item.speaker $item.voice $item.text $cursor $end
        $cursor = $end
    }

    $davidEnd = [double]$utterances[2].duration
    $ziraStart = $delaySeconds
    $ziraEnd = $ziraStart + [double]$utterances[1].duration
    $overlapStart = [math]::Max(0.0, $ziraStart)
    $overlapEnd = [math]::Min($davidEnd, $ziraEnd)

    $receipt = [ordered]@{
        schema_version = 1
        evidence_scope = "deterministic synthetic fixture ground truth only"
        generator = "tools/p14_fixture_ground_truth.ps1"
        two_speakers = [ordered]@{
            retained_wav = "work/benchmarks/p17-synthetic-missing-classes/two-speakers/source.wav"
            retained_wav_sha256 = $twoRetainedHash
            reconstructed_wav_sha256 = $twoReconstructedHash
            byte_identical = $true
            duration_seconds = [math]::Round((Get-Duration $retainedTwo), 6)
            distinct_speakers = 2
            alternating_turns = 4
            designed_overlap_seconds = 0.0
            turns = $turns
        }
        overlapping_speech = [ordered]@{
            retained_wav = "work/benchmarks/p17-synthetic-missing-classes/overlapping-speech/source.wav"
            retained_wav_sha256 = $overlapRetainedHash
            reconstructed_wav_sha256 = $overlapReconstructedHash
            byte_identical = $true
            duration_seconds = [math]::Round((Get-Duration $retainedOverlap), 6)
            distinct_speakers = 2
            second_speaker_delay_seconds = $delaySeconds
            designed_overlap_seconds = [math]::Round(($overlapEnd - $overlapStart), 3)
            speaker_spans = @(
                [ordered]@{ speaker = "SPEAKER_00"; start = 0.0; end = [math]::Round($davidEnd, 6) },
                [ordered]@{ speaker = "SPEAKER_01"; start = $ziraStart; end = [math]::Round($ziraEnd, 6) }
            )
            overlap_span = [ordered]@{
                start = [math]::Round($overlapStart, 6)
                end = [math]::Round($overlapEnd, 6)
            }
        }
        limitation = "Byte-identical reconstruction proves the synthetic timeline and voice assignment, not real-human diarization or listening quality."
    }

    $parent = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutputPath -Encoding utf8
    Write-Output $OutputPath
}
finally {
    $resolvedScratch = [IO.Path]::GetFullPath($scratch)
    if (-not $resolvedScratch.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove scratch path outside the temporary directory: $resolvedScratch"
    }
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force -ErrorAction SilentlyContinue
}
