# Push local data/ to the cloud server over SSH (streaming tar, no temp file).
# Pure ASCII by design: the repo path contains non-ASCII characters, so all
# paths are resolved at runtime via $PSScriptRoot instead of being hardcoded.
#
# Usage (run from anywhere):
#   powershell -File deploy\migrate-data.ps1 -ServerIp 1.2.3.4 [-User root]
#
# Prereq on the server: none (just SSH). The script untars into
#   /root/workshop/data   (override with -RemoteRoot)
# Afterwards follow docs/cloud-hosting-hk.md to load it into the docker volume.

param(
    [Parameter(Mandatory = $true)][string]$ServerIp,
    [string]$User = "root",
    [string]$RemoteRoot = "/root/workshop"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$dataDir = Join-Path $repoRoot "data"
if (-not (Test-Path $dataDir)) { throw "data dir not found: $dataDir" }

# Consistent SQLite snapshot: checkpoint WAL before copying while local backend
# may still be running. (Runbook recommends stopping the local bridge first.)
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    python -c "import sqlite3; c = sqlite3.connect(r'$dataDir\workshop.sqlite3', timeout=30); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close(); print('sqlite wal checkpoint ok')"
} else {
    Write-Warning "python not found; skipping WAL checkpoint (stop the local backend first!)"
}

Write-Host "Streaming $dataDir to ${User}@${ServerIp}:${RemoteRoot}/data ..."
Write-Host "About 2.4 GB over your uplink; expect 15-60 min depending on bandwidth."

# Windows 10+ ships tar.exe and ssh.exe. Stream tar over ssh, no temp files.
& tar.exe -cf - -C "$repoRoot" data | & ssh.exe "${User}@${ServerIp}" "mkdir -p '$RemoteRoot' && tar -xf - -C '$RemoteRoot'"
if ($LASTEXITCODE -ne 0) { throw "transfer failed with exit code $LASTEXITCODE" }

Write-Host "Transfer complete. Verify on the server:"
Write-Host "  ssh ${User}@${ServerIp} du -sh $RemoteRoot/data"
