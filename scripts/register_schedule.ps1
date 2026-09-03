# ============================================================
#  注册 A 股数据自动采集的 Windows 计划任务
#  用法: 以管理员身份运行 PowerShell,执行本脚本
#        powershell -ExecutionPolicy Bypass -File .\register_schedule.ps1
# ============================================================

#Requires -RunAsAdministrator

# === 配置区 ===
$TaskName    = "ASharesData_AutoCollect"
$TaskDesc    = "A股数据自动增量采集(每日收盘后执行)"
$BatPath     = "e:\AI\Data\A-Shares-data\scripts\auto_collect.bat"
$StartTime   = "18:30"   # A股15:00收盘,18:30数据基本更新完毕
$StartBoundary = (Get-Date -Format "yyyy-MM-dd") + "T$StartTime:00"

# === 检查 bat 脚本是否存在 ===
if (-not (Test-Path $BatPath)) {
    Write-Host "[ERROR] 找不到采集脚本: $BatPath" -ForegroundColor Red
    exit 1
}

# === 若任务已存在则先删除(便于重新注册) ===
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "[INFO] 任务已存在,先删除旧任务..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# === 定义动作: 启动 bat 脚本 ===
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$BatPath`""

# === 定义触发器: 每日触发 ===
$Trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $StartTime

# === 定义设置 ===
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

# === 定义主体: 用当前用户登录后运行,最高权限 ===
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Highest

# === 注册任务 ===
Register-ScheduledTask `
    -TaskName $TaskName `
    -Description $TaskDesc `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Force | Out-Null

Write-Host ""
Write-Host "[OK] 计划任务注册成功" -ForegroundColor Green
Write-Host "----------------------------------------"
Write-Host "任务名称:  $TaskName"
Write-Host "执行脚本:  $BatPath"
Write-Host "执行时间:  每日 $StartTime"
Write-Host "最长运行:  2 小时"
Write-Host "失败重试:  2 次,间隔 10 分钟"
Write-Host "----------------------------------------"
Write-Host ""
Write-Host "常用管理命令:"
Write-Host "  查看任务状态:  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "  立即手动执行:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  禁用任务:      Disable-ScheduledTask -TaskName '$TaskName'"
Write-Host "  启用任务:      Enable-ScheduledTask -TaskName '$TaskName'"
Write-Host "  删除任务:      Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host ""
Write-Host "查看执行日志:  e:\AI\Data\A-Shares-data\logs\auto_collect_*.log"
