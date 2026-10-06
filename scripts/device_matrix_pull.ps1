# Pulls the private capture evidence for one AN-01 device-matrix cell.
#
# After a capture finishes in the app, run:
#   .\scripts\device_matrix_pull.ps1 -Provider youtube -Condition speaker
# Output goes to .local\device-matrix\ (Git-ignored): capture.json, one sampled frame,
# and a row appended to matrix.csv. Only the device model and Android version are
# recorded, never the serial.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('youtube', 'tiktok', 'instagram', 'facebook', 'control')]
    [string]$Provider,
    [Parameter(Mandatory = $true)]
    [string]$Condition,
    [string]$Notes = '',
    [string]$Package = 'app.ovrly'
)

$ErrorActionPreference = 'Stop'
$adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
if (-not (Test-Path $adb)) { throw "adb not found at $adb" }

$serials = & $adb devices | Select-String 'device$' | ForEach-Object { $_.Line.Split("`t")[0] }
if (-not $serials) { throw 'No device connected (adb devices is empty).' }
$serial = $serials | Select-Object -First 1

$model = (& $adb -s $serial shell getprop ro.product.model).Trim()
$android = (& $adb -s $serial shell getprop ro.build.version.release).Trim()

$root = Join-Path (Split-Path $PSScriptRoot -Parent) '.local\device-matrix'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$cell = Join-Path $root "$Provider-$Condition-$stamp"
New-Item -ItemType Directory -Path $cell -Force | Out-Null

$remote = 'no_backup/capture'
$json = & $adb -s $serial shell "run-as $Package cat $remote/capture.json" 2>$null
if (-not $json) { throw "No capture.json in $Package private storage. Did the capture finish?" }
[IO.File]::WriteAllText((Join-Path $cell 'capture.json'), ($json -join "`n"), (New-Object Text.UTF8Encoding $false))
$meta = ($json -join "`n") | ConvertFrom-Json

$frames = & $adb -s $serial shell "run-as $Package ls $remote/frames" 2>$null | Where-Object { $_ -match '^frame-\d+ms\.jpg$' } | Sort-Object { [int]($_ -replace '\D', '') }
$frameName = $null
if ($frames) {
    $frameName = $frames[[math]::Floor($frames.Count / 2)]
    # run-as cannot write to shared storage on this device; stream the frame as base64 instead.
    $b64 = (& $adb -s $serial exec-out "run-as $Package base64 $remote/frames/$frameName") -join ''
    [IO.File]::WriteAllBytes((Join-Path $cell $frameName), [Convert]::FromBase64String($b64))
}

$csv = Join-Path $root 'matrix.csv'
if (-not (Test-Path $csv)) {
    'timestamp,model,android,provider,condition,duration_ms,frames,playback_signal,stop_reason,frame_file,notes' | Set-Content $csv -Encoding UTF8
}
$row = @(
    $stamp, $model, $android, $Provider, $Condition,
    $meta.durationMs, $meta.frames, $meta.playbackSignalDetected,
    ('"' + ($meta.stopReason -replace '"', '""') + '"'),
    $frameName, ('"' + ($Notes -replace '"', '""') + '"')
) -join ','
Add-Content $csv $row -Encoding UTF8

Write-Host "Cell $Provider/$Condition on $model Android $android"
Write-Host "  duration $($meta.durationMs) ms, frames $($meta.frames), playback signal $($meta.playbackSignalDetected)"
Write-Host "  stop: $($meta.stopReason)"
if ($frameName) { Write-Host "  frame: $(Join-Path $cell $frameName) (open it and check it is not black)" } else { Write-Host '  frame: none captured' }
Write-Host "  row appended to $csv"
