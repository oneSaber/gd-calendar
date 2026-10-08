<#
.SYNOPSIS
    广东地下演出日历 —— 一键启动本地服务与前端页面。

.DESCRIPTION
    这个脚本负责「稳」的部分，批处理入口只负责调用它：
      1. 选一个能用的 Python（优先 .venv；都没有时现场建 .venv 装依赖）
      2. 首次运行自动建库（并自动补齐缺失的列）
      3. 启动 uvicorn，轮询 /api/health 直到真正可用
      4. 用默认浏览器打开前端页面
      5. Ctrl+C 优雅退出

    依赖处理 / Python 探测 / 原生命令调用等公共逻辑在 scripts\_common.ps1，
    与 update.ps1 共用一份实现。

    设计取舍：
      * 不写死任何绝对路径，换机器也能跑。
      * 用健康轮询而不是 sleep —— 端口起来 ≠ 服务可用（建表可能还在跑）。
      * 端口若被占用：先探测是不是本项目已在运行，是就直接开页面，不重复起。

.PARAMETER Port
        服务端口，默认 8000。

.PARAMETER BindAddress
        绑定地址，默认 127.0.0.1（不要用 -Host，那与 PowerShell 内置变量冲突）。

.PARAMETER City
        采集城市，逗号分隔；留空用配置里的默认城市。

.PARAMETER NoBrowser
        不自动打开浏览器。

.PARAMETER AutoInstall
        缺依赖时不询问，直接装。

.PARAMETER SkipFetch
        完全不采集（即使库里没数据）。

.PARAMETER Fetch
        强制采集一轮（即使库里已有数据）。

.PARAMETER Sources
        采集源，逗号分隔，默认 showstart,douban（不含浏览器源，启动快）。

.EXAMPLE
    .\scripts\start.ps1
    .\scripts\start.ps1 -Port 8080 -City 广州,深圳
    .\scripts\start.ps1 -Fetch -Sources showstart,douban,bilibili,weibo
#>

[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [string]$BindAddress = '127.0.0.1',

    [string]$City = '',

    [switch]$NoBrowser,
    [switch]$AutoInstall,
    [switch]$SkipFetch,
    [switch]$Fetch,

    [string]$Sources = 'showstart,douban'
)

$ErrorActionPreference = 'Stop'
$script:StepNo = 0

# 公共函数（Invoke-Native / Get-ApiJson / Python 探测与依赖安装）都在 _common.ps1，
# 与 update.ps1 共用一份实现 —— 否则改一处忘一处，两个脚本行为会漂移。
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir '_common.ps1')

$Root = Split-Path -Parent $ScriptDir

# --------------------------------------------------------------------------- #
# 1. 挑一个可用的 Python（探测 + 按需建 .venv 装依赖）
# --------------------------------------------------------------------------- #
Step '检查 Python 环境'
$Python = Initialize-ProjectPython -Root $Root -AutoInstall:$AutoInstall
if (-not $Python) { exit 1 }
Ok "Python: $Python"

# .env：不存在就从示例复制一份（可选，不影响默认运行）
$EnvFile = Join-Path $Root '.env'
$EnvSample = Join-Path $Root '.env.example'
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvSample)) {
    Copy-Item $EnvSample $EnvFile
    Info "已从 .env.example 生成 .env（可自行修改端口/数据库等）"
}


# --------------------------------------------------------------------------- #
# 3. 端口占用检查（可能本项目已在跑）
# --------------------------------------------------------------------------- #
Step "检查端口 $Port"

$BaseUrl = "http://${BindAddress}:${Port}"
$InUse = $false
try {
    $InUse = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
} catch {
    # 老系统没有 Get-NetTCPConnection，退回 netstat
    $InUse = [bool](netstat -ano | Select-String -Pattern ":$Port\s+.*LISTENING")
}

if ($InUse) {
    Warn "端口 $Port 已被占用。"
    $health = $null
    try {
        $health = Get-ApiJson -Uri "$BaseUrl/api/health" -TimeoutSec 4
    } catch { }

    if ($health -and $health.app) {
        Ok "检测到本项目已在运行（$($health.app) v$($health.version)）"
        if (-not $NoBrowser) {
            Info "直接打开页面，不重复启动服务。"
            Start-Process $BaseUrl | Out-Null
        }
        exit 0
    }

    Fail "该端口被其它程序占用，且不是本项目。"
    Write-Host "      换个端口重试，例如：" -ForegroundColor Yellow
    Write-Host "        .\启动.bat -Port 8010" -ForegroundColor Yellow
    Write-Host "      或查占用进程：" -ForegroundColor Yellow
    Write-Host "        Get-NetTCPConnection -LocalPort $Port | Select OwningProcess" -ForegroundColor Yellow
    exit 1
}
Ok "端口空闲"


# --------------------------------------------------------------------------- #
# 4. 建库 + 判断是否需要采集
# --------------------------------------------------------------------------- #
Step '准备数据库'

$rc = Invoke-Native -Exe $Python -Arguments @('-m', 'app.cli', 'initdb')
if ($rc -ne 0) {
    Fail "建库失败（退出码 $rc），请检查上面的报错。"
    exit 1
}
Ok "数据库就绪"

# 是否需要采集，留到服务起来后用 /api/stats 判断（见第 7 步）


# --------------------------------------------------------------------------- #
# 5. 启动服务
# --------------------------------------------------------------------------- #
Step "启动服务 $BaseUrl"

$UvicornArgs = @('-m', 'uvicorn', 'app.api.main:app', '--host', $BindAddress, '--port', "$Port", '--log-level', 'info')

# 用同一窗口的独立进程启动，输出直接显示在本窗口，便于看日志与 Ctrl+C
$Server = Start-Process -FilePath $Python -ArgumentList $UvicornArgs `
    -WorkingDirectory $Root -PassThru -NoNewWindow

if (-not $Server) {
    Fail "启动服务进程失败。"
    exit 1
}
Info "服务进程 PID = $($Server.Id)"


# --------------------------------------------------------------------------- #
# 6. 健康轮询（端口起来 ≠ 服务可用，建表可能还在跑）
# --------------------------------------------------------------------------- #
Step '等待服务就绪'

$Ready = $false
$Deadline = (Get-Date).AddSeconds(45)
$Health = $null
while ((Get-Date) -lt $Deadline) {
    if ($Server.HasExited) {
        Fail "服务进程已退出（退出码 $($Server.ExitCode)），请看上面的日志。"
        exit 1
    }
    try {
        $Health = Get-ApiJson -Uri "$BaseUrl/api/health" -TimeoutSec 3
        if ($Health -and $Health.status -eq 'ok') { $Ready = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
    Write-Host "." -NoNewline -ForegroundColor DarkGray
}
Write-Host ""

if (-not $Ready) {
    Fail "等待 45 秒后服务仍未就绪。"
    try { Stop-Process -Id $Server.Id -Force } catch { }
    exit 1
}
Ok "服务已就绪（$($Health.app) v$($Health.version)，数据库 $($Health.database)）"


# --------------------------------------------------------------------------- #
# 7. 数据检查：库里没数据时，问是否跑一轮采集
# --------------------------------------------------------------------------- #
Step '检查数据'

$Stats = $null
$Stats = Get-ApiJson -Uri "$BaseUrl/api/stats" -TimeoutSec 10
$OccCount = if ($Stats) { [int]$Stats.occurrences } else { 0 }
Info "当前库内场次: $OccCount"

$DoFetch = $false
if ($Fetch) {
    $DoFetch = $true
    Info "已指定 -Fetch，强制采集。"
} elseif (-not $SkipFetch -and $OccCount -lt 5) {
    Warn "库里几乎没有数据，日历会是空的。"
    Write-Host ""
    Write-Host "    是否现在采集一轮？（源: $Sources；约需 1-3 分钟）" -ForegroundColor White
    Write-Host "      · 只抓公开的演出事实信息，遵守站点限速" -ForegroundColor Gray
    Write-Host "      · 采集期间服务保持运行，页面会自动有数据" -ForegroundColor Gray
    $ans = Read-Host "    输入 Y 采集，其它键跳过 (Y/n)"
    $DoFetch = ($ans -eq '' -or $ans -match '^[Yy]')
}

if ($DoFetch) {
    Write-Host ""
    $FetchArgs = @('-m', 'app.cli', 'fetch', '--sources', $Sources, '--no-browser')
    if ($City) { $FetchArgs += @('--cities', $City) }
    Info "开始采集：python $($FetchArgs -join ' ')"
    Write-Host ""
    # 采集日志直接显示在本窗口（信息量大，便于观察进度）
    $rc = Invoke-Native -Exe $Python -Arguments $FetchArgs -PassThruOutput
    if ($rc -eq 0) {
        $Stats = Get-ApiJson -Uri "$BaseUrl/api/stats" -TimeoutSec 10
        Ok "采集完成，当前场次: $($Stats.occurrences)"
    } else {
        Warn "采集过程有报错（退出码 $rc，服务不受影响，可稍后重试）。"
    }
} elseif ($OccCount -lt 5) {
    Info "已跳过采集。页面会显示空状态，可随时执行：start.bat -Fetch"
}


# --------------------------------------------------------------------------- #
# 8. 打开页面 + 保持前台
# --------------------------------------------------------------------------- #
Step '打开页面'

$DemoUrl = "$BaseUrl/"
if (-not $NoBrowser) {
    Start-Process $BaseUrl | Out-Null
    Ok "已用默认浏览器打开: $BaseUrl"
} else {
    Info "已跳过打开浏览器（-NoBrowser）"
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor DarkCyan
Write-Host "  服务运行中 —— 按 Ctrl+C 停止" -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor DarkCyan
Write-Host "  前端页面    $BaseUrl" -ForegroundColor Gray
Write-Host "  接口文档    $BaseUrl/docs" -ForegroundColor Gray
Write-Host "  内置演示    ${BaseUrl}/index.html?demo=1   (不依赖后端数据)" -ForegroundColor Gray
Write-Host "  日历订阅    $BaseUrl/api/ics?city=广州" -ForegroundColor Gray
Write-Host ""
Write-Host "  示例：只看地偶    $BaseUrl/?is_idol=1" -ForegroundColor DarkGray
Write-Host "        月历视图    $BaseUrl/?view=month" -ForegroundColor DarkGray
Write-Host ""

try {
    # 前台等待：服务退出或用户 Ctrl+C
    while (-not $Server.HasExited) {
        Start-Sleep -Seconds 1
    }
    Write-Host ""
    Warn "服务进程已退出（退出码 $($Server.ExitCode)）。"
} finally {
    Write-Host ""
    Info "正在停止服务 ..."
    try {
        if (-not $Server.HasExited) { Stop-Process -Id $Server.Id -Force }
        Ok "已停止。"
    } catch {
        Warn "停止时出现问题: $($_.Exception.Message)"
    }
}

