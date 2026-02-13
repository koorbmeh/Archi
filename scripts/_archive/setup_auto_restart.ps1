# Setup Archi to auto-restart after Windows reboot
# Run this script as Administrator: Right-click PowerShell -> Run as Administrator

Write-Host "Setting up Archi auto-restart..." -ForegroundColor Cyan

$archiPath = $PSScriptRoot | Split-Path -Parent
$startScript = Join-Path $archiPath "scripts\start_archi.py"
$pythonExe = Join-Path $archiPath "venv\Scripts\python.exe"
$logPath = Join-Path $archiPath "logs\startup.log"

# Ensure logs directory exists
$logsDir = Join-Path $archiPath "logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

# Create startup batch file (uses watchdog for auto-restart on crash)
$watchdogScript = Join-Path $archiPath "scripts\run_archi_watchdog.ps1"
$batchContent = @"
@echo off
cd /d "$archiPath"
powershell -ExecutionPolicy Bypass -File "$watchdogScript" >> "$logPath" 2>&1
"@

$batchFile = Join-Path $archiPath "scripts\startup_archi.bat"
$batchContent | Out-File -FilePath $batchFile -Encoding ASCII

Write-Host "Created startup batch file: $batchFile" -ForegroundColor Green

# Create scheduled task
$taskName = "ArchiAutoStart"
$taskDescription = "Automatically start Archi after Windows reboot"

# Check if task already exists
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($existingTask) {
    Write-Host "Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Create new task (runs at system startup, before any user logs in)
$action = New-ScheduledTaskAction -Execute $batchFile -WorkingDirectory $archiPath
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $taskDescription

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  Archi Auto-Restart Setup Complete!" -ForegroundColor Green
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host ""
Write-Host "Archi will start automatically when Windows boots (no login required)." -ForegroundColor Green
Write-Host ""
Write-Host "To test: Restart your computer and check if Archi starts." -ForegroundColor Yellow
Write-Host "To disable: Run scripts\remove_auto_restart.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "Startup logs will be in: $logPath" -ForegroundColor Cyan
Write-Host ""
