<#
    qr-watch.ps1 —— 二维码图片监听（由 start-napcat.bat 最小化启动，不用手动运行）

    背景：NapCat 在控制台里用字符拼出来的二维码经常扫不出来（NapCat 官方也承认），
          它同时会把真正的二维码写到 cache\qrcode.png。这个脚本负责在图片出现、
          以及每次刷新时自动用看图程序弹出来。

    退出条件：NapCat 的 WebSocket 端口开始监听（= 已登录），或超过超时时间。
    排查用日志：logs\qr-watch.log
#>
param(
    [Parameter(Mandatory = $true)][string]$QrPath,
    [int]$Port = 3001,
    [int]$TimeoutSec = 900,
    [string]$LogFile = ""
)

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host $line
    if ($LogFile) {
        try {
            $dir = Split-Path -Parent $LogFile
            if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
            Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
        } catch { }
    }
}

function Test-Port([int]$p) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $c.Connect("127.0.0.1", $p)
        return $true
    } catch {
        return $false
    } finally {
        $c.Dispose()
    }
}

# 等图片写完（大小连续两次不变），避免打开到半张图
function Wait-FileReady([string]$path, [int]$TimeoutMs = 8000) {
    $last = -1
    $stable = 0
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        $len = -1
        try { $len = (Get-Item -LiteralPath $path -ErrorAction Stop).Length } catch { return $false }
        if ($len -gt 0) {
            if ($len -eq $last) {
                $stable++
                if ($stable -ge 2) { return $true }
            } else {
                $stable = 0
            }
        }
        $last = $len
        Start-Sleep -Milliseconds 300
    }
    Write-Log "等待图片写完超时（最后一次读到 $last 字节）"
    return ($last -gt 0)
}

# 打开图片。返回 $true 表示确实弹出了窗口。
# 不假设 .png 默认关联一定可用，所以带一个 mspaint 兜底。
function Show-Image([string]$path) {
    $before = @(Get-Process -ErrorAction SilentlyContinue |
                Where-Object { $_.MainWindowHandle -ne 0 } |
                ForEach-Object { $_.Id })
    Write-Log "打开前已有 $($before.Count) 个带窗口的进程"

    $methods = @(
        @{ Name = "系统默认看图程序"; Cmd = { Start-Process -FilePath $path } },
        @{ Name = "画图 (mspaint)"; Cmd = { Start-Process -FilePath "mspaint.exe" -ArgumentList "`"$path`"" } }
    )

    foreach ($m in $methods) {
        try {
            & $m.Cmd
            Write-Log "已调用「$($m.Name)」，等待窗口出现…"
        } catch {
            Write-Log "调用「$($m.Name)」抛异常：$($_.Exception.Message)"
            continue
        }

        # UWP 版照片应用冷启动可能要好几秒，最多等 8 秒
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        while ($sw.ElapsedMilliseconds -lt 8000) {
            Start-Sleep -Milliseconds 500
            $after = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 })
            $new = @($after | Where-Object { $before -notcontains $_.Id })
            if ($new.Count -gt 0) {
                Write-Log "「$($m.Name)」弹出了窗口：$(($new | ForEach-Object { $_.ProcessName }) -join ',')"
                return $true
            }
        }
        Write-Log "「$($m.Name)」等了 8 秒没有新窗口"
    }
    return $false
}

Write-Log "开始监听二维码：$QrPath（端口 $Port，超时 ${TimeoutSec}s）"

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$lastMtime = $null
$count = 0

while ((Get-Date) -lt $deadline) {
    if (Test-Port $Port) {
        Write-Log "协议端已就绪（端口 $Port 已监听），退出监听。"
        exit 0
    }

    if (Test-Path -LiteralPath $QrPath) {
        $mtime = (Get-Item -LiteralPath $QrPath).LastWriteTime
        if ($null -eq $lastMtime -or $mtime -ne $lastMtime) {
            Write-Log "发现二维码图片（修改时间 $mtime）"
            if (Wait-FileReady $QrPath) {
                $count++
                # 只有真的弹出窗口才记为已处理，否则下一轮继续重试
                if (Show-Image $QrPath) {
                    $lastMtime = (Get-Item -LiteralPath $QrPath).LastWriteTime
                    Write-Log "第 $count 次弹出成功，用手机 QQ 扫码。"
                } else {
                    Write-Log "第 $count 次弹出失败，稍后重试；也可以手动打开：$QrPath"
                }
            }
        }
    }

    Start-Sleep -Seconds 2
}

Write-Log "超时退出。"
exit 0
