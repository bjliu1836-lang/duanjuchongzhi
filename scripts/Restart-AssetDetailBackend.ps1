param(
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonExe = 'D:\YOUKU\python.exe'
$Port = 8007
$ExpectedCommandLine = '"D:\YOUKU\python.exe" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8007'
$ExpectedDb = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'data\workshop.sqlite3'))
$MainFile = Join-Path $ProjectRoot 'backend\app\main.py'
$PromptFiles = @(
    (Join-Path $ProjectRoot 'config\agents\asset_extraction_localization.md'),
    (Join-Path $ProjectRoot 'config\agents\asset_source_extraction.md'),
    (Join-Path $ProjectRoot 'config\agents\asset_target_localization.md')
)
$CodeAndPromptFiles = @($MainFile) + $PromptFiles
$LogDirectory = Join-Path $env:LOCALAPPDATA '短剧重制-项目\backend-8007'

function Get-PortListeners {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Test-ExpectedCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $quotedExe = '"' + $PythonExe + '"'
    $unquotedExe = $PythonExe
    if ($CommandLine.StartsWith($quotedExe, [System.StringComparison]::OrdinalIgnoreCase)) {
        $arguments = $CommandLine.Substring($quotedExe.Length).Trim()
    } elseif ($CommandLine.StartsWith($unquotedExe, [System.StringComparison]::OrdinalIgnoreCase)) {
        $arguments = $CommandLine.Substring($unquotedExe.Length).Trim()
    } else {
        return $false
    }
    $expectedArguments = $ExpectedCommandLine.Substring(('"' + $PythonExe + '"').Length).Trim()
    return [string]::Equals($arguments, $expectedArguments, [System.StringComparison]::Ordinal)
}

function Get-ValidatedListenerProcess {
    $listeners = Get-PortListeners
    if ($listeners.Count -gt 1) {
        throw "端口 $Port 存在多个监听项，已停止检查。"
    }
    if ($listeners.Count -eq 0) {
        return $null
    }
    $listener = $listeners[0]
    if ($listener.LocalAddress -ne '127.0.0.1') {
        throw "端口 $Port 由非预期地址 $($listener.LocalAddress) 监听，未作更改。"
    }
    $processId = [int]$listener.OwningProcess
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
    $started = (Get-Process -Id $processId -ErrorAction Stop).StartTime
    $identityMatches = $process -and [string]::Equals([string]$process.ExecutablePath, $PythonExe, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-ExpectedCommandLine -CommandLine ([string]$process.CommandLine))
    if (-not $identityMatches) {
        throw "端口 $Port 的进程身份与预期不符（PID $processId），未作更改。"
    }
    return [pscustomobject]@{
        Pid = $processId
        Started = $started
        CommandLine = $process.CommandLine
    }
}

function Assert-NoActiveDatabaseJobs {
    $pythonAudit = @'
import json, sqlite3, sys
db = sys.argv[1]
terminal = {"success", "succeeded", "failed", "failure", "error", "completed", "cancelled", "canceled", "stale", "\u6210\u529f", "\u5931\u8d25", "\u5df2\u5b8c\u6210"}
tables = ("tasks", "asset_generation_jobs", "composition_exports", "video_generations")
try:
    conn = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True, timeout=3)
    result = {}
    for table in tables:
        rows = conn.execute("SELECT status, COUNT(*) FROM \"" + table + "\" GROUP BY status").fetchall()
        result[table] = {str(status): int(count) for status, count in rows}
    conn.close()
    active = {table: {status: count for status, count in values.items() if status.strip().lower() not in terminal} for table, values in result.items()}
    active = {table: values for table, values in active.items() if values}
    print(json.dumps({"ok": not active, "active": active}, ensure_ascii=True))
    sys.exit(0 if not active else 3)
except Exception as exc:
    print(json.dumps({"ok": False, "error": type(exc).__name__}, ensure_ascii=True))
    sys.exit(2)
'@
    $auditFile = Join-Path ([System.IO.Path]::GetTempPath()) ("shortdrama-db-audit-{0}-{1}.py" -f $PID, ([guid]::NewGuid().ToString('N')))
    try {
        [System.IO.File]::WriteAllText($auditFile, $pythonAudit, (New-Object System.Text.UTF8Encoding($false)))
        $auditOutput = & $PythonExe $auditFile $ExpectedDb
        $auditExitCode = $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $auditFile -Force -ErrorAction SilentlyContinue
    }
    if ($auditExitCode -ne 0) {
        throw "数据库任务检查未通过；输出：$($auditOutput -join ' ')"
    }
}

function Get-Health {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 4
    $bodyStream = New-Object System.IO.MemoryStream
    $response.RawContentStream.CopyTo($bodyStream)
    $body = [System.Text.Encoding]::UTF8.GetString($bodyStream.ToArray())
    $bodyStream.Dispose()
    $health = $body | ConvertFrom-Json
    if (-not $health.ok -or -not $health.db) {
        throw '健康接口返回内容无效。'
    }
    return $health
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "未找到预期 Python 解释器：$PythonExe"
}
if (@($CodeAndPromptFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }).Count -gt 0) {
    throw '预期的后端或本地化提示词文件缺失，未作更改。'
}
$LatestPromptWrite = ($CodeAndPromptFiles | ForEach-Object { (Get-Item -LiteralPath $_).LastWriteTime } | Sort-Object -Descending | Select-Object -First 1)

$oldProcess = Get-ValidatedListenerProcess
if ($oldProcess) {
    $health = Get-Health
    $healthDb = [System.IO.Path]::GetFullPath([string]$health.db)
    if (-not [string]::Equals($healthDb, $ExpectedDb, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw '当前服务健康接口指向的数据库与本项目数据库不一致，未作更改。'
    }
}

Assert-NoActiveDatabaseJobs

if ($CheckOnly) {
    if ($oldProcess) {
        Write-Output "检查通过：PID $($oldProcess.Pid) 是本项目预期的 8007 服务；数据库无活动任务。计划以用户本地终端重启该单一进程。"
    } else {
        Write-Output "检查通过：8007 空闲；数据库无活动任务。计划按固定配置启动本项目后端。"
    }
    exit 0
}

if ($oldProcess -and $oldProcess.Started -gt $LatestPromptWrite) {
    Write-Output '当前 8007 服务已在提示词文件最后修改之后启动，无需重启。'
    exit 0
}

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutLog = Join-Path $LogDirectory "backend-$stamp.stdout.log"
$stderrLog = Join-Path $LogDirectory "backend-$stamp.stderr.log"

if ($oldProcess) {
    Stop-Process -Id $oldProcess.Pid
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Process -Id $oldProcess.Pid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $oldProcess.Pid -ErrorAction SilentlyContinue) {
        throw "预期服务 PID $($oldProcess.Pid) 未能正常退出；未启动替代进程。"
    }
    if ((Get-PortListeners).Count -gt 0) {
        throw "停止预期服务后端口 $Port 仍被占用；未终止其他进程。"
    }
}

try {
    $startedProcess = Start-Process -FilePath $PythonExe -ArgumentList @('-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', '8007') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
    $deadline = (Get-Date).AddSeconds(30)
    $ready = $false
    $lastVerificationFailure = '服务尚未开始监听。'
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $startedProcess.Refresh()
        if ($startedProcess.HasExited) {
            $lastVerificationFailure = "新进程 PID $($startedProcess.Id) 已退出。"
            break
        }
        try {
            $health = Get-Health
        } catch {
            $lastVerificationFailure = "健康接口检查失败：$($_.Exception.Message)"
            continue
        }
        $healthDb = [System.IO.Path]::GetFullPath([string]$health.db)
        if (-not [string]::Equals($healthDb, $ExpectedDb, [System.StringComparison]::OrdinalIgnoreCase)) {
            $lastVerificationFailure = "健康接口数据库不匹配：$healthDb"
            continue
        }
        $newListeners = Get-PortListeners
        if ($newListeners.Count -ne 1) {
            $lastVerificationFailure = "端口 $Port 监听数为 $($newListeners.Count)，预期 1。"
            continue
        }
        if ($newListeners[0].LocalAddress -ne '127.0.0.1') {
            $lastVerificationFailure = "端口监听地址为 $($newListeners[0].LocalAddress)，预期 127.0.0.1。"
            continue
        }
        if ([int]$newListeners[0].OwningProcess -ne $startedProcess.Id) {
            $lastVerificationFailure = "端口监听 PID $($newListeners[0].OwningProcess) 与本次启动 PID $($startedProcess.Id) 不一致。"
            continue
        }
        if ($startedProcess.StartTime -le $LatestPromptWrite) {
            $lastVerificationFailure = '新进程启动时间未晚于当前提示词文件修改时间。'
            continue
        }
        if ($startedProcess.HasExited) {
            $lastVerificationFailure = "新进程 PID $($startedProcess.Id) 在健康检查期间退出。"
            continue
        }
        $ready = $true
        break
    }
    if (-not $ready) {
        throw "30 秒内未能完成启动验证：$lastVerificationFailure 日志：$stdoutLog 和 $stderrLog"
    }
    Write-Output "后端已就绪：PID $($startedProcess.Id)，数据库为本项目数据库。日志：$stdoutLog 和 $stderrLog"
} catch {
    Write-Error ("启动或验证失败：{0} 日志：{1} 和 {2}" -f $_.Exception.Message, $stdoutLog, $stderrLog)
    exit 1
}
