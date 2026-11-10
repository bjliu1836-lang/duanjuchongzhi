param([switch]$InstallTask)
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$credentialPath = Join-Path $PSScriptRoot '.deploy-credentials.json'
$logDir = Join-Path $env:LOCALAPPDATA 'VideoRemakeWorkshop\logs'
$logFile = Join-Path $logDir 'supervisor.log'
$backendOut = Join-Path $logDir 'backend.log'
$backendErr = Join-Path $logDir 'backend-error.log'
$tunnelOut = Join-Path $logDir 'named-tunnel.log'
$tunnelErr = Join-Path $logDir 'named-tunnel-error.log'
$tunnelName = 'workshop-api'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log([string]$Message) {
    Add-Content -LiteralPath $logFile -Value "$(Get-Date -Format o) $Message"
}

function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (!$connect.AsyncWaitHandle.WaitOne(800)) { return $false }
        $client.EndConnect($connect)
        return $true
    } catch { return $false }
    finally { $client.Close() }
}

function Test-Health {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8003/api/health' -TimeoutSec 6
        return [bool]$health.ok
    } catch { return $false }
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$tunnelHealthUrl = 'https://api.0po3eu1.com/api/health'
$script:tunnelFailCount = 0
$script:tunnelGraceUntil = [DateTime]::MinValue

function Test-TunnelHealth {
    # Probe via the public hostname on purpose: only an edge round-trip reveals
    # a half-open tunnel (process alive, connection dead). Checking 127.0.0.1
    # would always look healthy and prove nothing.
    try {
        $health = Invoke-RestMethod -Uri $tunnelHealthUrl -TimeoutSec 10
        return [bool]$health.ok
    } catch { return $false }
}

function Start-NamedTunnel {
    Write-Log 'starting named tunnel'
    Start-Process -FilePath $cloudflared `
        -ArgumentList @('tunnel', '--no-autoupdate', 'run', $tunnelName) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $tunnelOut -RedirectStandardError $tunnelErr | Out-Null
    Start-Sleep -Seconds 8
    $script:tunnelFailCount = 0
    $script:tunnelGraceUntil = (Get-Date).AddSeconds(90)
}

function Resolve-Python {
    if (Test-Path -LiteralPath $credentialPath) {
        try {
            $cred = Get-Content -LiteralPath $credentialPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $candidate = [string]$cred.pythonExecutable
            if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
        } catch { Write-Log "credential read failed: $($_.Exception.Message)" }
    }
    $fallback = (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    if ($fallback) { return $fallback }
    throw 'Python interpreter not found.'
}

function Resolve-Cloudflared {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\cloudflared\cloudflared.exe'),
        'C:\Program Files (x86)\cloudflared\cloudflared.exe',
        'C:\Program Files\cloudflared\cloudflared.exe'
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw 'cloudflared.exe not found.'
}

if ($InstallTask) {
    $taskName = 'VideoRemakeWorkshop-Bridge'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description 'Keeps the local backend (8003) and the named Cloudflare tunnel alive.' -Force | Out-Null
    Write-Log 'scheduled task registered'
    Start-ScheduledTask -TaskName $taskName
    return
}

Write-Log "supervisor started (pid $PID)"

$python = Resolve-Python
$cloudflared = Resolve-Cloudflared
Write-Log "python=$python cloudflared=$cloudflared"

while ($true) {
    try {
        if (-not (Test-Health)) {
            if (Test-Port 8003) {
                Write-Log 'port 8003 occupied but unhealthy; leaving it alone'
            } else {
                Write-Log 'backend is down; starting'
                Start-Process -FilePath $python `
                    -ArgumentList '-m uvicorn backend.app.main:app --host 127.0.0.1 --port 8003' `
                    -WorkingDirectory $projectRoot -WindowStyle Hidden `
                    -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr | Out-Null
                $healthy = $false
                for ($i = 0; $i -lt 30; $i++) {
                    Start-Sleep -Seconds 2
                    if (Test-Health) { $healthy = $true; break }
                }
                Write-Log "backend health after start: $healthy"
            }
        }

        $tunnel = Get-Process cloudflared -ErrorAction SilentlyContinue
        if (-not $tunnel) {
            Write-Log 'cloudflared is down'
            Start-NamedTunnel
        } elseif ((Get-Date) -ge $script:tunnelGraceUntil) {
            # Only probe the tunnel when the local backend is healthy: with the
            # backend down the public probe fails too, and restarting the tunnel
            # would just mask the backend outage.
            if (-not (Test-Health)) {
                if ($script:tunnelFailCount -gt 0) { Write-Log 'backend unhealthy; tunnel probe skipped, counter reset'; $script:tunnelFailCount = 0 }
            } elseif (Test-TunnelHealth) {
                if ($script:tunnelFailCount -gt 0) { Write-Log "tunnel healthy again after $script:tunnelFailCount failed probe(s)"; $script:tunnelFailCount = 0 }
            } else {
                $script:tunnelFailCount += 1
                Write-Log "tunnel probe via public hostname failed ($script:tunnelFailCount/3)"
                if ($script:tunnelFailCount -ge 3) {
                    Write-Log 'tunnel process alive but public hostname unreachable; restarting cloudflared (half-open connection)'
                    Stop-Process -Name cloudflared -Force -Confirm:$false
                    Start-Sleep -Seconds 3
                    Start-NamedTunnel
                }
            }
        }
    } catch {
        Write-Log "loop error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 30
}
