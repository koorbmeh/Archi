# Install Archi as Windows Service
# Requires: NSSM (Non-Sucking Service Manager) - choco install nssm
#
# Customize paths below for your setup, then run as Administrator:
#   .\scripts\install_windows_service.ps1

$serviceName = "ArchiAgent"
$projectRoot = $PSScriptRoot | Split-Path -Parent
$pythonExe = Join-Path $projectRoot "venv\Scripts\python.exe"
$scriptPath = Join-Path $projectRoot "scripts\start_archi.py"
$workingDir = $projectRoot

Write-Host "Installing Archi as Windows service..."
Write-Host "  Python: $pythonExe"
Write-Host "  Script: $scriptPath"
Write-Host "  Working dir: $workingDir"

# Check if NSSM is installed
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: NSSM not found. Install with: choco install nssm"
    exit 1
}

# Install service
nssm install $serviceName $pythonExe $scriptPath
nssm set $serviceName AppDirectory $workingDir
nssm set $serviceName DisplayName "Archi Autonomous Agent"
nssm set $serviceName Description "Archi - AI agent with autonomous operation"
nssm set $serviceName Start SERVICE_AUTO_START

Write-Host ""
Write-Host "Service installed successfully!"
Write-Host "  Start:  nssm start $serviceName"
Write-Host "  Stop:   nssm stop $serviceName"
Write-Host "  Remove: nssm remove $serviceName confirm"
