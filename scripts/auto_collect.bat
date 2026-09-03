@echo off
REM ============================================================
REM  A股数据自动采集脚本
REM  用途: 供 Windows 任务计划程序调用,定时增量采集股票数据
REM  日志: logs\auto_collect_YYYYMMDD.log
REM ============================================================

setlocal

REM === 配置区 ===
set "PROJECT_DIR=e:\AI\Data\A-Shares-data"
set "PYTHON_EXE=C:\Users\zongb\AppData\Local\Programs\Python\Python313\python.exe"
set "LOG_DIR=%PROJECT_DIR%\logs"

REM === 切换到项目目录 ===
cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [ERROR] 项目目录不存在: %PROJECT_DIR%
    exit /b 1
)

REM === 确保日志目录存在 ===
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM === 生成带时间戳的日志文件名 ===
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value 2^>nul ^| find "="') do set "dt=%%a"
set "YYYY=%dt:~0,4%"
set "MM=%dt:~4,2%"
set "DD=%dt:~6,2%"
set "HH=%dt:~8,2%"
set "MI=%dt:~10,2%"
set "LOG_FILE=%LOG_DIR%\auto_collect_%YYYY%%MM%%DD%_%HH%%MI%.log"

REM === 记录开始时间 ===
echo ============================================================ > "%LOG_FILE%"
echo [%YYYY%-%MM%-%DD% %HH%:%MI%] 开始自动采集 >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

REM === 执行采集 ===
"%PYTHON_EXE%" -m src.main >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%errorlevel%"

REM === 记录结束时间 ===
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value 2^>nul ^| find "="') do set "dt=%%a"
set "YYYY=%dt:~0,4%"
set "MM=%dt:~4,2%"
set "DD=%dt:~6,2%"
set "HH=%dt:~8,2%"
set "MI=%dt:~10,2%"
echo. >> "%LOG_FILE%"
echo [%YYYY%-%MM%-%DD% %HH%:%MI%] 采集结束,退出码: %EXIT_CODE% >> "%LOG_FILE%"

REM === 清理30天前的日志 ===
forfiles /p "%LOG_DIR%" /m "auto_collect_*.log" /d -30 /c "cmd /c del @path" 2>nul

exit /b %EXIT_CODE%
