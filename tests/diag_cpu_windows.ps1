# 诊断: 每 2 秒打印 python 进程真实 CPU 核数 (Windows 内核计数器, 同任务管理器).
# 跑法: 先在另一个窗口启动 doc2md 转换, 然后本窗口执行:
#   powershell -ExecutionPolicy Bypass -File tests\diag_cpu_windows.ps1
# 若限制生效, "核数" 应 <= 16 (整机 57%); 若飙到 ~25 才是真 bug.

$cpuCount = 28
Write-Host "监测 python 进程 CPU (每2秒), 共 60 秒. 阈值: 16 核 = 整机 57%"
Write-Host "时间      核数     整机%"
1..30 | ForEach-Object {
    $p = Get-Process python -ErrorAction SilentlyContinue |
         Sort-Object -Property CPU -Descending | Select-Object -First 1
    if ($null -eq $p) {
        Write-Host "未找到 python 进程"
        Start-Sleep -Seconds 2
        return
    }
    $s1 = $p.TotalProcessorTime.TotalSeconds
    Start-Sleep -Seconds 2
    $p2 = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
    if ($null -eq $p2) { Write-Host "进程已退出"; return }
    $s2 = $p2.TotalProcessorTime.TotalSeconds
    $cores = ($s2 - $s1) / 2
    $pct = $cores / $cpuCount * 100
    Write-Host ("{0}  {1,6:N2}  {2,6:N0}%" -f (Get-Date -Format HH:mm:ss), $cores, $pct)
}
