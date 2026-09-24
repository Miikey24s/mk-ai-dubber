$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$ffmpeg = Get-ChildItem (Join-Path $repoRoot "tools\ffmpeg") -Recurse -Filter ffmpeg.exe |
    Select-Object -First 1 -ExpandProperty FullName
$ffprobe = Get-ChildItem (Join-Path $repoRoot "tools\ffmpeg") -Recurse -Filter ffprobe.exe |
    Select-Object -First 1 -ExpandProperty FullName

if (-not $ffmpeg -or -not $ffprobe) {
    throw "Project FFmpeg/ffprobe not found"
}

Add-Type -AssemblyName System.Speech

function Invoke-FFmpeg([string[]] $ffArgs) {
    & $ffmpeg -hide_banner -loglevel error -y @ffArgs
    if ($LASTEXITCODE -ne 0) {
        throw "ffmpeg failed with exit code $LASTEXITCODE"
    }
}

function Get-Duration([string] $path) {
    $value = & $ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $path
    if ($LASTEXITCODE -ne 0) {
        throw "ffprobe failed for $path"
    }
    return [double]::Parse($value.Trim(), [Globalization.CultureInfo]::InvariantCulture)
}

function Write-Speech(
    [string] $path,
    [string] $voice,
    [string] $text,
    [int] $rate = 0,
    [int] $volume = 100
) {
    $speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
    try {
        $speaker.SelectVoice($voice)
        $speaker.Rate = $rate
        $speaker.Volume = $volume
        $speaker.SetOutputToWaveFile($path)
        $speaker.Speak($text)
    }
    finally {
        $speaker.Dispose()
    }
}

function Wrap-Video([string] $wavPath, [string] $videoPath) {
    $duration = Get-Duration $wavPath
    $durationText = $duration.ToString("0.000", [Globalization.CultureInfo]::InvariantCulture)
    Invoke-FFmpeg @(
        "-f", "lavfi", "-i", "color=c=0x202020:s=640x360:r=30:d=$durationText",
        "-i", $wavPath,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", $videoPath
    )
}

function Write-Receipt(
    [string] $directory,
    [string] $fixtureClass,
    [string] $sourcePath,
    [hashtable] $probe,
    [string] $limitation
) {
    $source = Get-Item $sourcePath
    $receipt = [ordered]@{
        schema_version = 1
        fixture_class = $fixtureClass
        generation_kind = "synthetic"
        source_file = $source.Name
        source_sha256 = (Get-FileHash $source.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        duration_seconds = [math]::Round((Get-Duration $source.FullName), 3)
        generator = "work/benchmarks/p17-synthetic-missing-classes/generate.ps1"
        voices = @("Microsoft David Desktop", "Microsoft Zira Desktop")
        behavior_probe = $probe
        limitation = $limitation
    }
    $receipt | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $directory "verification.json") -Encoding utf8
}

$availableVoices = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $voiceNames = @($availableVoices.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
}
finally {
    $availableVoices.Dispose()
}

$requiredVoices = @("Microsoft David Desktop", "Microsoft Zira Desktop")
foreach ($voice in $requiredVoices) {
    if ($voice -notin $voiceNames) {
        throw "Required Windows speech voice is missing: $voice"
    }
}

$scratch = Join-Path $PSScriptRoot "scratch"
New-Item -ItemType Directory -Force $scratch | Out-Null

$davidA = Join-Path $scratch "david-a.wav"
$davidB = Join-Path $scratch "david-b.wav"
$ziraA = Join-Path $scratch "zira-a.wav"
$ziraB = Join-Path $scratch "zira-b.wav"
$prosodySlow = Join-Path $scratch "prosody-slow.wav"
$prosodyNeutral = Join-Path $scratch "prosody-neutral.wav"
$prosodyFast = Join-Path $scratch "prosody-fast.wav"

Write-Speech $davidA "Microsoft David Desktop" "When the market opens, confirm the trend before entering a position. Keep the risk small and write down the reason for every trade."
Write-Speech $davidB "Microsoft David Desktop" "I would wait for the price to close above resistance before changing the plan."
Write-Speech $ziraA "Microsoft Zira Desktop" "I agree with the risk limit, but I want to see stronger volume before I enter."
Write-Speech $ziraB "Microsoft Zira Desktop" "If the breakout fails, I will step aside and review the setup again."
Write-Speech $prosodySlow "Microsoft Zira Desktop" "This is the moment where everything slows down, and every word matters." -rate -4 -volume 92
Write-Speech $prosodyNeutral "Microsoft Zira Desktop" "Now the situation changes. I am calm, but the decision is urgent." -rate 0 -volume 80
Write-Speech $prosodyFast "Microsoft Zira Desktop" "Move now, protect the downside, and do not hesitate when the signal disappears." -rate 4 -volume 100

# Music under dialogue: speech plus a deterministic two-tone bed at low gain.
$musicDir = Join-Path $PSScriptRoot "music-under-dialogue"
New-Item -ItemType Directory -Force $musicDir | Out-Null
$musicWav = Join-Path $musicDir "source.wav"
$speechDuration = Get-Duration $davidA
$speechDurationText = $speechDuration.ToString("0.000", [Globalization.CultureInfo]::InvariantCulture)
Invoke-FFmpeg @(
    "-i", $davidA,
    "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=$speechDurationText",
    "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=$speechDurationText",
    "-filter_complex", "[1:a]volume=0.10[m1];[2:a]volume=0.07[m2];[m1][m2]amix=inputs=2:normalize=0[music];[0:a]aresample=48000[speech];[speech][music]amix=inputs=2:normalize=0[a]",
    "-map", "[a]", "-c:a", "pcm_s16le", $musicWav
)
$musicVideo = Join-Path $musicDir "source.mp4"
Wrap-Video $musicWav $musicVideo
Write-Receipt $musicDir "music-under-dialogue" $musicVideo ([ordered]@{
    speech_voice = "Microsoft David Desktop"
    music_frequencies_hz = @(220, 330)
    music_gains = @(0.10, 0.07)
    simultaneous_background = $true
}) "Synthetic tonal bed validates separator/ASR behavior with deterministic music-like interference; it is not human-performed music."

# Noisy speech: speech plus deterministic seeded-like lavfi pink noise at a fixed gain.
$noiseDir = Join-Path $PSScriptRoot "noisy-speech"
New-Item -ItemType Directory -Force $noiseDir | Out-Null
$noiseWav = Join-Path $noiseDir "source.wav"
Invoke-FFmpeg @(
    "-i", $davidA,
    "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.09:r=48000:d=${speechDurationText}:seed=1701",
    "-filter_complex", "[0:a]aresample=48000[speech];[speech][1:a]amix=inputs=2:normalize=0[a]",
    "-map", "[a]", "-c:a", "pcm_s16le", $noiseWav
)
$noiseVideo = Join-Path $noiseDir "source.mp4"
Wrap-Video $noiseWav $noiseVideo
Write-Receipt $noiseDir "noisy-speech" $noiseVideo ([ordered]@{
    speech_voice = "Microsoft David Desktop"
    noise_kind = "pink"
    noise_amplitude = 0.09
    noise_seed = 1701
    simultaneous_background = $true
}) "Synthetic pink noise is a deterministic stressor for ASR/separation; it does not model every real room or microphone noise profile."

# Two speakers: alternating non-overlapping turns from two installed Windows voices.
$twoDir = Join-Path $PSScriptRoot "two-speakers"
New-Item -ItemType Directory -Force $twoDir | Out-Null
$twoWav = Join-Path $twoDir "source.wav"
Invoke-FFmpeg @(
    "-i", $davidB, "-i", $ziraA, "-i", $davidA, "-i", $ziraB,
    "-filter_complex", "[0:a]aresample=48000[a0];[1:a]aresample=48000[a1];[2:a]aresample=48000[a2];[3:a]aresample=48000[a3];[a0][a1][a2][a3]concat=n=4:v=0:a=1[a]",
    "-map", "[a]", "-c:a", "pcm_s16le", $twoWav
)
$twoVideo = Join-Path $twoDir "source.mp4"
Wrap-Video $twoWav $twoVideo
Write-Receipt $twoDir "two-speakers" $twoVideo ([ordered]@{
    distinct_synthetic_speakers = 2
    speaker_voices = @("Microsoft David Desktop", "Microsoft Zira Desktop")
    alternating_turns = 4
    designed_overlap_seconds = 0.0
}) "Two distinct synthetic voices exercise speaker-turn handling, but this is not evidence of real-human diarization quality."

# Overlapping speech: the two voices start 1.2 seconds apart so their utterances overlap measurably.
$overlapDir = Join-Path $PSScriptRoot "overlapping-speech"
New-Item -ItemType Directory -Force $overlapDir | Out-Null
$overlapWav = Join-Path $overlapDir "source.wav"
$overlapDelayMs = 1200
Invoke-FFmpeg @(
    "-i", $davidA, "-i", $ziraA,
    "-filter_complex", "[0:a]aresample=48000[a0];[1:a]aresample=48000,adelay=${overlapDelayMs}|${overlapDelayMs}[a1];[a0][a1]amix=inputs=2:normalize=0:duration=longest[a]",
    "-map", "[a]", "-c:a", "pcm_s16le", $overlapWav
)
$overlapVideo = Join-Path $overlapDir "source.mp4"
Wrap-Video $overlapWav $overlapVideo
$davidDuration = Get-Duration $davidA
$ziraDuration = Get-Duration $ziraA
$overlapSeconds = [math]::Max(0.0, [math]::Min($davidDuration, 1.2 + $ziraDuration) - 1.2)
Write-Receipt $overlapDir "overlapping-speech" $overlapVideo ([ordered]@{
    distinct_synthetic_speakers = 2
    speaker_voices = @("Microsoft David Desktop", "Microsoft Zira Desktop")
    second_speaker_delay_seconds = 1.2
    designed_overlap_seconds = [math]::Round($overlapSeconds, 3)
}) "Synthetic two-voice overlap validates overlap visibility and downstream handling; it is not evidence of real conversational crosstalk performance."

# Prosody stress: the same voice switches from slow to neutral to fast with deliberate volume changes.
$prosodyDir = Join-Path $PSScriptRoot "emotional-prosody-stress"
New-Item -ItemType Directory -Force $prosodyDir | Out-Null
$prosodyWav = Join-Path $prosodyDir "source.wav"
Invoke-FFmpeg @(
    "-i", $prosodySlow, "-i", $prosodyNeutral, "-i", $prosodyFast,
    "-filter_complex", "[0:a]aresample=48000[a0];[1:a]aresample=48000[a1];[2:a]aresample=48000[a2];[a0][a1][a2]concat=n=3:v=0:a=1[a]",
    "-map", "[a]", "-c:a", "pcm_s16le", $prosodyWav
)
$prosodyVideo = Join-Path $prosodyDir "source.mp4"
Wrap-Video $prosodyWav $prosodyVideo
Write-Receipt $prosodyDir "emotional-prosody-stress" $prosodyVideo ([ordered]@{
    speaker_voice = "Microsoft Zira Desktop"
    rate_sequence = @(-4, 0, 4)
    volume_sequence = @(92, 80, 100)
    prosody_sections = 3
}) "This fixture stresses rate/volume transitions only. It is synthetic prosody coverage and must not be described as real human emotion coverage."

Remove-Item $scratch -Recurse -Force
