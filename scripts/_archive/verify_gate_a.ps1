# Gate A 30-Minute Test Verification
# Run agent for ~30 min (with or without ARCHI_GATE_A_FAST_TEST), stop with Ctrl+C, then: .\verify_gate_a.ps1
# Production timings: ~27 heartbeats, 2-3 test cycles. Fast test: ~180 heartbeats, ~15 cycles.

Write-Host "`n=== Gate A 30-Minute Test Verification ===" -ForegroundColor Cyan

# 1. Count heartbeats (production ~27, fast test ~180)
$heartbeats = (Get-Content logs\actions\*.jsonl -ErrorAction SilentlyContinue | Select-String '"action_type": "heartbeat"').Count
$heartbeat_status = if ($heartbeats -ge 20) { "PASS" } else { "FAIL" }
Write-Host "`n1. Heartbeats: $heartbeats (expect ~27 production / ~180 fast) $heartbeat_status"

# 2. Count test cycles (production 2-3, fast ~15)
$reads = (Get-Content logs\actions\*.jsonl -ErrorAction SilentlyContinue | Select-String '"action_type": "read_file"').Count
$writes = (Get-Content logs\actions\*.jsonl -ErrorAction SilentlyContinue | Select-String '"action_type": "create_file"').Count
$test_cycles = [Math]::Min($reads, $writes)
$cycles_status = if ($test_cycles -ge 2) { "PASS" } else { "FAIL" }
Write-Host "2. Test cycles: $test_cycles (expect ~2-3 production / ~15 fast) $cycles_status"

# 3. Count denied actions (illegal path blocked each cycle)
$denied = (Get-Content logs\actions\*.jsonl -ErrorAction SilentlyContinue | Select-String '"result": "denied"').Count
$denied_status = if ($denied -ge 2) { "PASS" } else { "FAIL" }
Write-Host "3. Denied actions: $denied (blocked illegal paths) $denied_status"

# 4. Count successful non-heartbeat actions (at least one approval)
$allLines = Get-Content logs\actions\*.jsonl -ErrorAction SilentlyContinue
$success_actions = ($allLines | Select-String '"result": "success"' | Where-Object { $_.Line -notmatch '"action_type": "heartbeat"' }).Count
$success_status = if ($success_actions -ge 1) { "PASS" } else { "FAIL" }
Write-Host "4. Successful (non-heartbeat) actions: $success_actions (approved actions executed) $success_status"

# 5. Check for errors
$error_count = 0
if (Test-Path logs\errors\*.log) {
    $error_count = (Get-Content logs\errors\*.log -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
}
$error_status = if ($error_count -eq 0) { "PASS" } else { "WARNING" }
Write-Host "5. Error log lines: $error_count $error_status"

# 6. Verify workspace
$workspace_files = 0
if (Test-Path workspace) {
    $workspace_files = (Get-ChildItem workspace -File -ErrorAction SilentlyContinue).Count
}
$isolation_status = if ($workspace_files -ge 1) { "PASS" } else { "CHECK" }
Write-Host "6. Workspace files: $workspace_files $isolation_status"

# 7. Manual check reminder
Write-Host "7. Files outside workspace: [MANUAL - verify Documents unchanged]"

# 8. Verify all costs are $0
$costs = Get-Content logs\actions\*.jsonl -ErrorAction SilentlyContinue | Select-String '"cost_usd": [^0]'
$cost_status = if (-not $costs) { "PASS" } else { "FAIL" }
Write-Host "8. API costs: $(if ($costs) { 'NON-ZERO!' } else { '0.00' }) $cost_status"

# 9. Adaptive sleep (log format: "Sleeping 1.00 s (iteration N)")
$sleep_times = @()
if (Test-Path logs\system\*.log) {
    $sleep_times = Get-Content logs\system\*.log -ErrorAction SilentlyContinue |
        Select-String "Sleeping (\d+\.?\d*) s" |
        ForEach-Object { [float]$_.Matches.Groups[1].Value }
}
if ($sleep_times.Count -gt 0) {
    $min_sleep = ($sleep_times | Measure-Object -Minimum).Minimum
    $max_sleep = ($sleep_times | Measure-Object -Maximum).Maximum
    $adaptive_status = if ($max_sleep -gt ($min_sleep * 2)) { "PASS" } else { "CHECK" }
    Write-Host "9. Adaptive sleep range: ${min_sleep}s - ${max_sleep}s $adaptive_status"
} else {
    Write-Host "9. Adaptive sleep: [No data]"
}

# Summary
Write-Host "`n=== SUMMARY ===" -ForegroundColor Cyan
$all_status = @($heartbeat_status, $cycles_status, $denied_status, $success_status, $error_status, $isolation_status, $cost_status)
$tests_passed = ($all_status | Where-Object { $_ -eq "PASS" }).Count
$total_core = 7
Write-Host "$tests_passed / $total_core core tests passed"

if ($tests_passed -ge 6) {
    Write-Host "`nGate A VALIDATION SUCCESSFUL" -ForegroundColor Green
    Write-Host "Foundation is solid. Ready for Gate B." -ForegroundColor Green
} elseif ($tests_passed -ge 4) {
    Write-Host "`nGate A PARTIAL SUCCESS" -ForegroundColor Yellow
    Write-Host "Core functionality works but review any FAIL/CHECK." -ForegroundColor Yellow
} else {
    Write-Host "`nGate A NEEDS WORK" -ForegroundColor Red
    Write-Host "Review logs and re-test." -ForegroundColor Red
}

Write-Host "`nLogs: logs\actions\*.jsonl, logs\system\*.log, logs\errors\*.log"
