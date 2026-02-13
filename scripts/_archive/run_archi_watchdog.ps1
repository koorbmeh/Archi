# Archi Watchdog - Auto-restart on crash
# Run: .\scripts\run_archi_watchdog.ps1
# When Archi exits (crash, CUDA error, etc.), waits and restarts automatically.

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $projectRoot)) {
    $projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
$startScript = Join-Path $projectRoot "scripts\start_archi.py"
$logsDir = Join-Path $projectRoot "logs"
$crashLog = Join-Path $logsDir "archi_crashes.log"

if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

$restartDelay = 15
$restartCount = 0

function Write-CrashLog {
    param([string]$msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts | $msg" | Add-Content -Path $crashLog -Encoding UTF8
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Archi Watchdog - Auto-restart on crash" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Archi will automatically restart if it crashes (e.g. CUDA errors)." -ForegroundColor Green
Write-Host "Press Ctrl+C to stop the watchdog." -ForegroundColor Yellow
Write-Host "Crash log: $crashLog" -ForegroundColor Gray
Write-Host ""

while ($true) {
    $restartCount++
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting Archi (restart #$restartCount)..." -ForegroundColor Green

    if (Test-Path $venvPython) {
        & $venvPython $startScript
    } else {
        python $startScript
    }

    $exitCode = $LASTEXITCODE
    $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $msg = "Archi exited with code $exitCode (restart #$restartCount)"
    Write-Host "[$now] $msg" -ForegroundColor Red
    Write-CrashLog $msg

    Write-Host "[$now] Restarting in $restartDelay seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds $restartDelay
}
