# Request a production build of the current origin/main through the Telegram APK workflow.
# Resolves the exact commit for you, shows it, asks once, then dispatches. Nothing else.
param(
    [switch]$Yes   # skip the confirmation prompt
)
$ErrorActionPreference = 'Stop'
$repository = Split-Path $PSScriptRoot -Parent
Set-Location $repository

git fetch --quiet origin main
$sha = (git rev-parse origin/main).Trim()
if ($sha -notmatch '^[0-9a-f]{40}$') { throw "Could not resolve origin/main to a commit" }
$subject = git log -1 --format=%s $sha
Write-Host "Requesting build of origin/main:`n  $sha`n  $subject"
if (-not $Yes) {
    $answer = Read-Host 'Dispatch Telegram APK for this commit? [y/N]'
    if ($answer -notin @('y', 'Y')) { Write-Host 'Cancelled.'; exit 1 }
}
gh workflow run telegram-apk.yml --ref main -f "source_sha=$sha"
Write-Host 'Dispatched. The owner will be asked to approve once the unsigned build completes.'
