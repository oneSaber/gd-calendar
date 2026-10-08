<#
    公共函数库：start.ps1 与 update.ps1 共用。

    ⚠️ 编码要求：本文件必须是 **UTF-8 with BOM**。PowerShell 5.1 读无 BOM 的
    脚本时按 GBK 解码，中文会变乱码，字符串里的符号还会被当成运算符。
#>

# Windows 控制台默认 GBK，中文输出要显式设 UTF-8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Step([string]$Text) {
    $script:StepNo++
    Write-Host ""
    Write-Host "[$script:StepNo] $Text" -ForegroundColor Cyan
}
function Ok([string]$Text)   { Write-Host "    OK   $Text" -ForegroundColor Green }
function Info([string]$Text) { Write-Host "    ..   $Text" -ForegroundColor Gray }
function Warn([string]$Text) { Write-Host "    !!   $Text" -ForegroundColor Yellow }
function Fail([string]$Text) { Write-Host "    XX   $Text" -ForegroundColor Red }

function Invoke-Native {
    <#
      统一执行外部程序（python / pip）。

      ⚠️ 踩过的坑：脚本设了 $ErrorActionPreference='Stop'，而原生命令往 stderr
      写东西时，PowerShell 会包成 ErrorRecord 并**直接终止脚本**，真实报错反而
      被吞掉（只剩一句 NativeCommandError）。所以这里临时放宽偏好、显式检查
      退出码；并用 cmd /c 拼命令行，避免 PS 5.1 对带空格参数的传参差异。
    #>
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$Quiet,
        [switch]$PassThruOutput
    )

    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $quoted = ($Arguments | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '""') + '"' } else { $_ }
        }) -join ' '
        $output = & cmd /c ('"' + $Exe + '" ' + $quoted) 2>&1
        $code = $LASTEXITCODE

        if ($PassThruOutput) {
            $output | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        } elseif (-not $Quiet -and $code -ne 0 -and $output) {
            # 失败时打印末尾若干行，否则用户只看到一句「失败了」无从下手
            $tail = @($output) | Select-Object -Last 25
            $tail | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        }
        return [int]$code
    } finally {
        $ErrorActionPreference = $old
    }
}

function Get-ApiJson {
    <#
      请求本地 API 并正确解码 UTF-8。

      ⚠️ 踩过的坑：PS 5.1 的 Invoke-RestMethod 对 JSON 响应默认按 ISO-8859-1 解码，
      中文会变成「å¹¿ä¸å°ä¸æ¼åºæ¥åæ」这种乱码。这里改成取原始字节显式 UTF-8 解码。
      失败返回 $null（调用方兜底），不抛异常。
    #>
    param(
        [Parameter(Mandatory)][string]$Uri,
        [int]$TimeoutSec = 8
    )
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $resp = Invoke-WebRequest -Uri $Uri -TimeoutSec $TimeoutSec -UseBasicParsing
        $text = [System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
        return ($text | ConvertFrom-Json)
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $old
    }
}

function Test-Deps([string]$Exe) {
    <# 返回 $true 表示该解释器能 import 全部必需包 #>
    $probe = 'import fastapi, sqlalchemy, httpx, uvicorn, selectolax, feedparser, apscheduler, greenlet'
    return ((Invoke-Native -Exe $Exe -Arguments @('-c', $probe) -Quiet) -eq 0)
}

function Test-PythonRuns([string]$Exe) {
    return ((Invoke-Native -Exe $Exe -Arguments @(
        '-c', 'import sys;print(sys.version_info[0],sys.version_info[1])'
    ) -Quiet) -eq 0)
}

function Resolve-ProjectPython {
    <#
      选一个能用的 Python 解释器。返回 @{ Exe=...; HasDeps=... }。

      优先级：
        1) 项目自带 .venv（如果存在且依赖齐备）
        2) 当前 PATH 上的 python
        3) 本机已有依赖的其它解释器（含 DSH 运行时兜底）
        4) 找一个「能跑但没依赖」的解释器，交给调用方决定是否建 .venv

      设计取舍：不写死绝对路径，换机器也能跑；只有第 3 步的 DSH 兜底是本机专用，
      且放在最后 —— 找不到别的可用解释器时才会用到。
    #>
    param([Parameter(Mandatory)][string]$Root)

    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path $venv) {
        if (Test-Deps $venv) { return @{ Exe = $venv; HasDeps = $true } }
        return @{ Exe = $venv; HasDeps = $false }
    }

    $candidates = New-Object System.Collections.Generic.List[string]

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }

    # 常见安装位置（不假设一定存在，逐个试）
    foreach ($p in @(
        "$env:LOCALAPPDATA\Programs\Python",
        'C:\Python313', 'C:\Python312', 'C:\Python311', 'C:\Python314'
    )) {
        if (Test-Path $p) {
            Get-ChildItem $p -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
                Select-Object -First 4 | ForEach-Object { $candidates.Add($_.FullName) }
        }
    }

    # DSH 运行时兜底（本机专用，放在最后）
    $dsh = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $dsh) { $candidates.Add($dsh) }

    $fallback = $null
    foreach ($exe in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path $exe)) { continue }
        if (Test-Deps $exe) { return @{ Exe = $exe; HasDeps = $true } }
        if (-not $fallback -and (Test-PythonRuns $exe)) { $fallback = $exe }
    }

    if ($fallback) { return @{ Exe = $fallback; HasDeps = $false } }
    return $null
}

function Initialize-ProjectPython {
    <#
      确保拿到一个依赖齐备的 Python。
      返回解释器路径；失败时返回 $null（调用方负责提示并退出）。

      $AutoInstall=$true 时缺依赖直接装，否则先问。
    #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [switch]$AutoInstall
    )

    $pick = Resolve-ProjectPython -Root $Root
    if ($null -eq $pick) {
        Fail '找不到可用的 Python 解释器。请先安装 Python 3.11+ 并加入 PATH。'
        return $null
    }

    $python = $pick.Exe
    if ($pick.HasDeps) { return $python }

    Info "找到解释器（依赖未装）: $python"
    $req = Join-Path $Root 'requirements.txt'
    $venvPython = Join-Path $Root '.venv\Scripts\python.exe'

    $doInstall = [bool]$AutoInstall
    if (-not $AutoInstall) {
        Write-Host ""
        Write-Host "    缺少依赖。可以现在创建独立的 .venv 并安装（只影响项目目录，不动系统 Python）。" -ForegroundColor Yellow
        $ans = Read-Host "    现在安装？(Y/n)"
        $doInstall = ($ans -eq '' -or $ans -match '^[Yy]')
    }

    if (-not $doInstall) {
        Fail '已取消。你也可以手动执行：'
        Write-Host "      python -m venv .venv" -ForegroundColor Gray
        Write-Host "      .venv\Scripts\python -m pip install -r requirements.txt" -ForegroundColor Gray
        return $null
    }

    if (-not (Test-Path $venvPython)) {
        Info '创建虚拟环境 .venv ...'
        $rc = Invoke-Native -Exe $python -Arguments @('-m', 'venv', (Join-Path $Root '.venv'))
        if ($rc -ne 0 -or -not (Test-Path $venvPython)) {
            Fail '创建虚拟环境失败。'
            return $null
        }
    }
    $python = $venvPython

    Info '安装依赖（首次可能需要几分钟）...'
    [void](Invoke-Native -Exe $python -Arguments @(
        '-m','pip','install','--upgrade','pip','--quiet','--disable-pip-version-check'
    ) -Quiet)
    $rc = Invoke-Native -Exe $python -Arguments @(
        '-m','pip','install','-r',$req,'--disable-pip-version-check','--no-warn-script-location'
    ) -Quiet
    if ($rc -ne 0) {
        Fail '依赖安装失败，请看下面的输出：'
        [void](Invoke-Native -Exe $python -Arguments @('-m','pip','install','-r',$req) -PassThruOutput)
        return $null
    }

    if (-not (Test-Deps $python)) {
        Fail '依赖安装后仍无法导入，请检查上面的报错。'
        return $null
    }
    return $python
}
