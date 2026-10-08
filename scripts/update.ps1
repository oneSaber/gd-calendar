<#
.SYNOPSIS
    广东地下演出日历 —— 只更新数据，不启动服务。

.DESCRIPTION
    和 start.ps1 的区别：本脚本**不启动也不停止**任何服务，跑完就退出，
    适合放进 Windows 计划任务做定时更新。

    两种模式（自动判断，无需手动选）：
      * 服务在运行 → 调它的 /api/admin/update 任务接口，**能显示实时进度**，
        并且页面上的「更新数据」按钮会同步看到进度。
      * 服务没运行 → 直接调用 python -m app.cli fetch。

    每次更新前都会先跑数据库结构迁移，避免版本升级后字段缺失。
    需要重算分类标记（新增/调整过标记规则）时加 -Backfill。

.PARAMETER City
    采集城市，逗号分隔。⚠️ 传了就**只会采这些城市**（会覆盖配置里的默认列表）。

.PARAMETER Sources
    采集源，默认 showstart,douban（HTTP 源，约 1–3 分钟）。
    加上浏览器源约需 8 分钟：-Sources showstart,douban,bilibili,weibo

.PARAMETER UseBrowser
    允许使用浏览器源（B站/微博）。仅在调用服务接口时生效。

.PARAMETER Backfill
    更新后按当前分类器重算历史数据的分类标记。

.PARAMETER DryRun
    只显示将要做什么，不真正采集。

.PARAMETER AutoInstall
    缺依赖时不询问，直接装。

.EXAMPLE
    .\scripts\update.ps1
    .\scripts\update.ps1 -Sources showstart,douban,bilibili,weibo -UseBrowser
    .\scripts\update.ps1 -City 广州,深圳
    .\scripts\update.ps1 -Backfill
#>

[CmdletBinding()]
param(
    [string]$City = '',

    [string]$Sources = 'showstart,douban',

    [switch]$UseBrowser,
    [switch]$Backfill,
    [switch]$DryRun,
    [switch]$AutoInstall,

    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$script:StepNo = 0

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir '_common.ps1')

$Root = Split-Path -Parent $ScriptDir
Set-Location $Root

Write-Host ""
Write-Host "==============================================================" -ForegroundColor DarkCyan
Write-Host "  广东地下演出日历 · 更新数据" -ForegroundColor White
Write-Host "==============================================================" -ForegroundColor DarkCyan
Info "项目目录: $Root"

# --------------------------------------------------------------------------- #
# 1. Python 与依赖
# --------------------------------------------------------------------------- #
Step '检查 Python 环境'
$Python = Initialize-ProjectPython -Root $Root -AutoInstall:$AutoInstall
if (-not $Python) { exit 1 }
Ok "Python: $Python"

# --------------------------------------------------------------------------- #
# 2. 数据库结构迁移（幂等，很快）
# --------------------------------------------------------------------------- #
Step '数据库结构检查'
$rc = Invoke-Native -Exe $Python -Arguments @('-m', 'app.cli', 'migrate') -Quiet
if ($rc -ne 0) {
    Warn '迁移失败（继续尝试采集，结构问题会在入库时报错）'
} else {
    Ok '结构已是最新'
}

$sourceList = @($Sources.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$cityList = @()
if ($City) { $cityList = @($City.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }

if ($DryRun) {
    Step '演练模式（不会真正采集）'
    Info "采集源: $($sourceList -join ', ')"
    if ($cityList.Count) {
        Info "城市: $($cityList -join ', ')"
    } else {
        Info '城市: 配置里的全部启用城市'
    }
    Info "浏览器源: $(if ($UseBrowser) { '允许' } else { '不使用' })"
    Info "更新后回填分类标记: $(if ($Backfill) { '是' } else { '否' })"
    Write-Host ""
    Ok '演练完成，未做任何修改'
    exit 0
}

# --------------------------------------------------------------------------- #
# 3. 采集
# --------------------------------------------------------------------------- #
# 判断服务是否在运行：直接探测 /api/admin/update，通了就走任务接口（有进度）
$baseUrl = "http://127.0.0.1:$Port"
$probe = Get-ApiJson -Uri "$baseUrl/api/health" -TimeoutSec 3
$serviceUp = ($null -ne $probe)

if ($serviceUp) {
    Step "服务在运行（$baseUrl）—— 通过任务接口采集，可显示实时进度"
    $post = "$baseUrl/api/admin/update"
    $q = @()
    if ($sourceList.Count) { $q += ('sources=' + [uri]::EscapeDataString(($sourceList -join ','))) }
    if ($cityList.Count)   { $q += ('cities='  + [uri]::EscapeDataString(($cityList -join ','))) }
    if ($UseBrowser)       { $q += 'use_browser=true' }
    if ($q.Count) { $post += '?' + ($q -join '&') }

    $started = $null
    try {
        $resp = Invoke-WebRequest -Uri $post -Method POST -TimeoutSec 20 -UseBasicParsing
        $started = ([System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray()) | ConvertFrom-Json).job
    } catch {
        Warn "启动任务失败：$($_.Exception.Message)"
        Warn '改为直接调用命令行采集（无进度显示）。'
        $serviceUp = $false
    }

    if ($serviceUp -and $started) {
        $jobId = $started.job_id
        Info "任务 ID: $jobId"
        $lastMsg = ''
        $seen = 0
        while ($true) {
            Start-Sleep -Seconds 2
            $snap = Get-ApiJson -Uri "$baseUrl/api/admin/update/$jobId" -TimeoutSec 10
            if ($null -eq $snap) { Warn '查询任务状态失败，继续等待…'; continue }
            $j = $snap.job

            if ($j.logs -and $j.logs.Count -gt $seen) {
                for ($i = $seen; $i -lt $j.logs.Count; $i++) {
                    Write-Host "      $($j.logs[$i])" -ForegroundColor DarkGray
                }
                $seen = $j.logs.Count
            }

            $state = $j.state
            if ($state -eq 'running' -or $state -eq 'pending') {
                Write-Host "`r    进度 $($j.percent)%  " -NoNewline -ForegroundColor Cyan
                continue
            }

            Write-Host ""
            if ($state -eq 'done') {
                Ok $j.message
                $r = $j.result
                if ($r) {
                    Info "新增场次 $($r.created) · 更新 $($r.updated) · 冲突 $($r.conflicts)"
                    if ($r.per_source) {
                        $parts = @()
                        foreach ($k in $r.per_source.PSObject.Properties.Name) {
                            $label = $k
                            if ($r.source_labels -and $r.source_labels.$k) { $label = $r.source_labels.$k }
                            $parts += "$label $($r.per_source.$k)"
                        }
                        Info "各源解析: $($parts -join ' / ')"
                    }
                    if ($r.errors -and $r.errors.PSObject.Properties.Name.Count) {
                        Warn "有来源报错: $($r.errors.PSObject.Properties.Name -join ', ')"
                    }
                }
            } elseif ($state -eq 'error') {
                Fail $j.message
                exit 1
            } else {
                Warn "任务结束，状态：$state"
            }
            break
        }
    }
}

if (-not $serviceUp) {
    Step '服务未运行 —— 直接采集'
    $fetchArgs = @('-m', 'app.cli', 'fetch', '--sources', ($sourceList -join ','), '--no-browser')
    if ($cityList.Count) { $fetchArgs += @('--cities', ($cityList -join ',')) }
    if ($UseBrowser) {
        # 去掉 --no-browser，让浏览器源生效
        $fetchArgs = $fetchArgs | Where-Object { $_ -ne '--no-browser' }
    }
    Info "执行: python $($fetchArgs -join ' ')"
    Write-Host ""
    $rc = Invoke-Native -Exe $Python -Arguments $fetchArgs -PassThruOutput
    if ($rc -ne 0) {
        Fail "采集失败（退出码 $rc）"
        exit 1
    }
    Ok '采集完成'
}

# --------------------------------------------------------------------------- #
# 4. 可选：重算分类标记
# --------------------------------------------------------------------------- #
if ($Backfill) {
    Step '回填分类标记（女子乐队 / ACG / 地偶）'
    $rc = Invoke-Native -Exe $Python -Arguments @('-m', 'app.cli', 'backfill')
    if ($rc -ne 0) { Warn '回填失败（不影响已采集的数据）' } else { Ok '回填完成' }
}

Write-Host ""
Write-Host "==============================================================" -ForegroundColor DarkCyan
Ok '更新完成'
if ($serviceUp) {
    Info "刷新页面即可看到新数据：$baseUrl"
} else {
    Info "下次启动服务即可看到新数据：start.bat"
}
Write-Host ""
