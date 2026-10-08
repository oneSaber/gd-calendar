<#
.SYNOPSIS
    停止由启动器拉起的本地服务。

.DESCRIPTION
    只按端口找监听进程，并**核对命令行**确认是本项目的 uvicorn 才停，
    避免误杀其它程序。
#>

[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir

Write-Host ""
Write-Host "广东地下演出日历 · 停止服务（端口 $Port）" -ForegroundColor Cyan

# 找监听该端口的进程
$pids = @()
try {
    $pids = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique)
} catch {
    $pids = (netstat -ano | Select-String -Pattern ":$Port\s+.*LISTENING") -replace '.*\s(\d+)\s*$', '$1' |
            Select-Object -Unique
}

if (-not $pids) {
    Write-Host "  端口 $Port 没有监听进程 —— 服务可能已经停了。" -ForegroundColor Yellow
    exit 0
}

$stopped = 0
foreach ($procId in $pids) {
    $procId = "$procId".Trim()
    if (-not $procId) { continue }

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if (-not $proc) { continue }

    # 安全校验：只停本项目的 uvicorn
    $cmd = "$($proc.CommandLine)"
    if ($cmd -notmatch 'uvicorn' -or $cmd -notmatch 'app\.api\.main') {
        Write-Host "  跳过 PID $procId（不是本项目的服务）：$($proc.Name)" -ForegroundColor Yellow
        continue
    }

    try {
        Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop
        Write-Host "  已停止 PID $procId" -ForegroundColor Green
        $stopped++
    } catch {
        Write-Host "  停止 PID $procId 失败: $($_.Exception.Message)" -ForegroundColor Red
    }
}

if ($stopped -eq 0) {
    Write-Host "  没有停止任何进程。" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "  完成（停止 $stopped 个进程）。" -ForegroundColor Green
}
