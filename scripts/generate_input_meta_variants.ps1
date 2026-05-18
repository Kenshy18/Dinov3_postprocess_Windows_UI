param(
    [string]$SourceDir = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "input"),
    [string]$OutputDir = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "input_meta_debug"),
    [switch]$Run,
    [switch]$AllVideos,
    [switch]$ShortTricky,
    [switch]$IncludeHuge,
    [switch]$Verify,
    [string]$EncoderPreset = "ultrafast"
)

$ErrorActionPreference = "Stop"

$VideoExts = @(".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")

function Invoke-Checked {
    param(
        [string]$Exe,
        [string[]]$ArgumentList
    )
    Write-Host ("[cmd] {0} {1}" -f $Exe, (($ArgumentList | ForEach-Object { if ($_ -match "\s") { '"' + $_ + '"' } else { $_ } }) -join " "))
    if (-not $Run) {
        return
    }
    & $Exe @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Exe failed with exit code $LASTEXITCODE"
    }
}

function Get-VideoProbe {
    param(
        [string]$Path,
        [switch]$CountFrames
    )
    $args = @(
        "-v", "error",
        "-select_streams", "v:0"
    )
    if ($CountFrames) {
        $args += "-count_frames"
    }
    $args += @(
        "-show_entries", "stream=codec_name,pix_fmt,width,height,avg_frame_rate,r_frame_rate,nb_frames,nb_read_frames,duration,field_order,sample_aspect_ratio",
        "-show_entries", "format=format_name,duration",
        "-of", "json",
        $Path
    )
    $json = & ffprobe @args
    if ($LASTEXITCODE -ne 0) {
        throw "ffprobe failed: $Path"
    }
    $data = ($json -join "`n") | ConvertFrom-Json
    if (-not $data.streams -or $data.streams.Count -lt 1) {
        throw "No video stream found: $Path"
    }
    return $data
}

function Convert-RateToDouble {
    param([string]$Rate)
    if ([string]::IsNullOrWhiteSpace($Rate) -or $Rate -eq "0/0") {
        return 30.0
    }
    if ($Rate.Contains("/")) {
        $parts = $Rate.Split("/", 2)
        $num = [double]$parts[0]
        $den = [double]$parts[1]
        if ($den -eq 0) {
            return 30.0
        }
        return $num / $den
    }
    return [double]$Rate
}

function Get-FrameCount {
    param([object]$Probe)
    $stream = $Probe.streams[0]
    if ($stream.nb_read_frames) {
        return [int64]$stream.nb_read_frames
    }
    if ($stream.nb_frames) {
        return [int64]$stream.nb_frames
    }
    $durationValue = $stream.duration
    if (-not $durationValue) {
        $durationValue = $Probe.format.duration
    }
    $rateValue = $stream.avg_frame_rate
    if (-not $rateValue) {
        $rateValue = $stream.r_frame_rate
    }
    $duration = [double]$durationValue
    $fps = Convert-RateToDouble ([string]$rateValue)
    return [int64][Math]::Round($duration * $fps)
}

function New-SafeStem {
    param(
        [int]$Index,
        [string]$Variant,
        [string]$SourcePath
    )
    $hash = (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA1).Hash.Substring(0, 8).ToLowerInvariant()
    return "{0:00}_{1}_{2}" -f $Index, $Variant, $hash
}

function Add-Job {
    param(
        [System.Collections.Generic.List[object]]$Jobs,
        [int]$Index,
        [string]$Variant,
        [string]$Source,
        [string]$Ext,
        [string[]]$FfmpegArgs,
        [string]$Purpose
    )
    $stem = New-SafeStem -Index $Index -Variant $Variant -SourcePath $Source
    $out = Join-Path $OutputDir ($stem + $Ext)
    $Jobs.Add([pscustomobject]@{
        source = $Source
        variant = $Variant
        output = $out
        purpose = $Purpose
        ffmpeg_args = $FfmpegArgs
    }) | Out-Null
}

function Test-VariantFrameCount {
    param(
        [string]$Source,
        [string]$Output
    )
    $sourceProbe = Get-VideoProbe $Source -CountFrames
    $outputProbe = Get-VideoProbe $Output -CountFrames
    $sourceFrames = Get-FrameCount $sourceProbe
    $outputFrames = Get-FrameCount $outputProbe
    if ($sourceFrames -ne $outputFrames) {
        throw "Frame count mismatch: $Output source=$sourceFrames output=$outputFrames"
    }
    Write-Host "[verify] frame_count OK: $Output ($outputFrames)"
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg was not found on PATH."
}
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "ffprobe was not found on PATH."
}

$sourceRoot = Resolve-Path $SourceDir
$videos = Get-ChildItem -LiteralPath $sourceRoot -File |
    Where-Object { $VideoExts -contains $_.Extension.ToLowerInvariant() } |
    Sort-Object Name

if (-not $videos) {
    throw "No videos found: $sourceRoot"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$outRoot = Resolve-Path $OutputDir

$probed = foreach ($video in $videos) {
    $probe = Get-VideoProbe $video.FullName
    $durationValue = $probe.streams[0].duration
    if (-not $durationValue) {
        $durationValue = $probe.format.duration
    }
    $rateValue = $probe.streams[0].avg_frame_rate
    if (-not $rateValue) {
        $rateValue = $probe.streams[0].r_frame_rate
    }
    [pscustomobject]@{
        file = $video
        probe = $probe
        frames = Get-FrameCount $probe
        duration = [double]$durationValue
        fps = Convert-RateToDouble ([string]$rateValue)
    }
}

$shortest = $probed | Sort-Object duration | Select-Object -First 1
$jobs = [System.Collections.Generic.List[object]]::new()
$targets = if ($AllVideos) { $probed } else { @($shortest) }

$i = 0
foreach ($item in $targets) {
    $i += 1
    $src = $item.file.FullName
    $frameLimit = [string]$item.frames
    Add-Job $jobs $i "h265_mp4" $src ".mp4" @(
        "-y", "-hide_banner", "-i", $src,
        "-map", "0:v:0", "-an", "-frames:v", $frameLimit, "-fps_mode", "passthrough",
        "-c:v", "libx265", "-preset", $EncoderPreset, "-crf", "24",
        "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-movflags", "+faststart"
    ) "H.265/HEVC MP4 input path"

    Add-Job $jobs $i "mpeg4_avi" $src ".avi" @(
        "-y", "-hide_banner", "-i", $src,
        "-map", "0:v:0", "-an", "-frames:v", $frameLimit, "-fps_mode", "passthrough",
        "-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p"
    ) "AVI container with non-H.264 MPEG-4 video"

    Add-Job $jobs $i "h264_mkv" $src ".mkv" @(
        "-y", "-hide_banner", "-i", $src,
        "-map", "0:v:0", "-an", "-frames:v", $frameLimit, "-fps_mode", "passthrough",
        "-c:v", "libx264", "-preset", $EncoderPreset, "-crf", "18",
        "-pix_fmt", "yuv420p"
    ) "Matroska container with H.264"

    Add-Job $jobs $i "h264_interlaced_tff" $src ".mp4" @(
        "-y", "-hide_banner", "-i", $src,
        "-map", "0:v:0", "-an", "-frames:v", $frameLimit, "-fps_mode", "passthrough",
        "-c:v", "libx264", "-preset", $EncoderPreset, "-crf", "18",
        "-flags", "+ildct+ilme", "-x264-params", "tff=1",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart"
    ) "Interlaced H.264 coding metadata without dropping frames"
}

if ($ShortTricky) {
    $src = $shortest.file.FullName
    $i = 99
    $frameLimit = [string]$shortest.frames
    $fpsExpr = ([string]::Format([System.Globalization.CultureInfo]::InvariantCulture, "{0:R}", [double]$shortest.fps))
    Add-Job $jobs $i "vfr_mkv" $src ".mkv" @(
        "-y", "-hide_banner", "-i", $src,
        "-map", "0:v:0", "-an", "-frames:v", $frameLimit, "-fps_mode", "passthrough",
        "-vf", "setpts=N/($fpsExpr*TB)+(0.004*sin(N/7))/TB",
        "-c:v", "libx264", "-preset", $EncoderPreset, "-crf", "18",
        "-pix_fmt", "yuv420p"
    ) "Variable frame timestamps while keeping the same frame count"

    Add-Job $jobs $i "rot90_metadata_mp4" $src ".mp4" @(
        "-y", "-hide_banner", "-i", $src,
        "-map", "0:v:0", "-an",
        "-vf", "transpose=2",
        "-frames:v", $frameLimit, "-fps_mode", "passthrough",
        "-c:v", "libx264", "-preset", $EncoderPreset, "-crf", "18",
        "-pix_fmt", "yuv420p", "-metadata:s:v:0", "rotate=90",
        "-movflags", "+faststart"
    ) "Rotation metadata path; pixels are pre-rotated to keep displayed orientation"

    Add-Job $jobs $i "sar_4_3_mp4" $src ".mp4" @(
        "-y", "-hide_banner", "-i", $src,
        "-map", "0:v:0", "-an",
        "-vf", "scale=960:720,setsar=4/3",
        "-frames:v", $frameLimit, "-fps_mode", "passthrough",
        "-c:v", "libx264", "-preset", $EncoderPreset, "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart"
    ) "Non-square SAR while preserving displayed 16:9 geometry"

    Add-Job $jobs $i "hevc_10bit_422_mp4" $src ".mp4" @(
        "-y", "-hide_banner", "-i", $src,
        "-map", "0:v:0", "-an", "-frames:v", $frameLimit, "-fps_mode", "passthrough",
        "-c:v", "libx265", "-preset", $EncoderPreset, "-crf", "24",
        "-pix_fmt", "yuv422p10le", "-tag:v", "hvc1", "-movflags", "+faststart"
    ) "10-bit 4:2:2 HEVC pixel format normalization path"

    if ($IncludeHuge) {
        Add-Job $jobs $i "prores_422_mov" $src ".mov" @(
            "-y", "-hide_banner", "-i", $src,
            "-map", "0:v:0", "-an", "-frames:v", $frameLimit, "-fps_mode", "passthrough",
            "-c:v", "prores_ks", "-profile:v", "3",
            "-pix_fmt", "yuv422p10le"
        ) "ProRes 422 MOV; very large output"
    }
}

$manifest = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    source_dir = "$sourceRoot"
    output_dir = "$outRoot"
    run = [bool]$Run
    all_videos = [bool]$AllVideos
    short_tricky = [bool]$ShortTricky
    include_huge = [bool]$IncludeHuge
    encoder_preset = $EncoderPreset
    shortest_source = $shortest.file.FullName
    sources = $probed | ForEach-Object {
        [pscustomobject]@{
            path = $_.file.FullName
            frames = $_.frames
            duration_sec = $_.duration
            fps = $_.fps
            codec = $_.probe.streams[0].codec_name
            pix_fmt = $_.probe.streams[0].pix_fmt
            field_order = $_.probe.streams[0].field_order
            sar = $_.probe.streams[0].sample_aspect_ratio
        }
    }
    jobs = $jobs
}

$manifestPath = Join-Path $outRoot "manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "[plan] videos=$($videos.Count) jobs=$($jobs.Count) output=$outRoot"
Write-Host "[plan] shortest=$($shortest.file.Name) frames=$($shortest.frames) duration=$([Math]::Round($shortest.duration, 3))s"
Write-Host "[plan] manifest=$manifestPath"
if (-not $Run) {
    Write-Host "[plan] dry-run only. Add -Run to create files."
}

foreach ($job in $jobs) {
    $ffmpegArgs = @($job.ffmpeg_args) + @($job.output)
    Invoke-Checked "ffmpeg" $ffmpegArgs
    if ($Run -and $Verify) {
        Test-VariantFrameCount -Source $job.source -Output $job.output
    }
}

if ($Run) {
    Write-Host "[done] generated $($jobs.Count) files in $outRoot"
}
