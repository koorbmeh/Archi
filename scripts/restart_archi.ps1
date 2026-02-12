# Restart Archi - Kill everything and start fresh
# Run: .\scripts\restart_archi.ps1

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $projectRoot)) {
    $projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $projectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Archi Full Restart" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. Kill processes on ports 5000 and 5001 (dashboard, web chat)
function Kill-ProcessOnPort {
    param([int]$port)
    $pids = @()
    try {
        $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        if ($conn) { $pids = $conn.OwningProcess | Sort-Object -Unique }
    } catch {
        # Fallback: netstat -ano
        $netstat = netstat -ano | Select-String ":\s*$port\s+.+LISTENING"
        foreach ($line in $netstat) {
            if ($line -match '\s+(\d+)\s*$') { $pids += [int]$Matches[1] }
        }
    }
    foreach ($procId in $pids) {
        try {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "Killing PID $procId ($($proc.ProcessName)) on port $port" -ForegroundColor Yellow
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
}
Kill-ProcessOnPort 5000
Kill-ProcessOnPort 5001

# 2. Kill Python processes running Archi scripts
$archiScripts = @("run_web_chat", "start_archi", "archi_service", "run_dashboard")
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
    $cmd = if ($_.CommandLine) { $_.CommandLine } else { "" }
    foreach ($script in $archiScripts) {
        if ($cmd -match $script) {
            Write-Host "Killing Archi process PID $($_.ProcessId): $script" -ForegroundColor Yellow
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            break
        }
    }
}

# 3. Brief pause for cleanup
Start-Sleep -Seconds 2

# 4. Start Archi
Write-Host ""
Write-Host "Starting Archi..." -ForegroundColor Green
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython scripts\start_archi.py
} else {
    python scripts\start_archi.py
}
