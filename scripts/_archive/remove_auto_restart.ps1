# Remove Archi auto-restart scheduled task
# Run as Administrator to remove the task

$taskName = "ArchiAutoStart"

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($existingTask) {
    Write-Host "Removing Archi auto-restart task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Auto-restart disabled." -ForegroundColor Green
} else {
    Write-Host "No auto-restart task found." -ForegroundColor Cyan
}
