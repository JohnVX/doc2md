# Monitor the python process CPU every 2 seconds using Windows kernel
# counters (TotalProcessorTime) -- same source as Task Manager.
# Usage: start doc2md in another window, then run:
#   powershell -ExecutionPolicy Bypass -File tests\diag_cpu_windows.ps1
# If limits work, "cores" should stay <= 16 (57% of 28). ~25 means real bug.

$cpuCount = 28
Write-Host "Monitoring python CPU every 2s for 60s. Threshold: 16 cores = 57%"
Write-Host "time      cores   machine%"
1..30 | ForEach-Object {
    $p = Get-Process python -ErrorAction SilentlyContinue |
         Sort-Object -Property CPU -Descending | Select-Object -First 1
    if ($null -eq $p) {
        Write-Host "no python process found"
        Start-Sleep -Seconds 2
        return
    }
    $s1 = $p.TotalProcessorTime.TotalSeconds
    Start-Sleep -Seconds 2
    $p2 = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
    if ($null -eq $p2) { Write-Host "process exited"; return }
    $s2 = $p2.TotalProcessorTime.TotalSeconds
    $cores = [math]::Round(($s2 - $s1) / 2, 2)
    $pct = [math]::Round($cores / $cpuCount * 100, 0)
    $t = Get-Date -Format HH:mm:ss
    Write-Host "$t  $cores  $pct%"
}
