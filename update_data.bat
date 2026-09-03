@echo off
REM ============================================================
REM  A-Shares Data - One-Click Data Update (double-click to run)
REM
REM  What it does:
REM    1. Stops the API service if running (DuckDB needs exclusive
REM       write access during collection)
REM    2. Runs incremental collection: daily data + indicators
REM       + backup for all 13 configured stocks
REM    3. Restarts the API service automatically
REM    4. Shows the result and pauses (window stays open)
REM
REM  Log file: logs\update_YYYYMMDD_HHMMSS.log
REM ============================================================

setlocal EnableDelayedExpansion

REM ========== CONFIG ==========
set "PROJECT_DIR=e:\AI\Data\A-Shares-data"
set "PYTHON_EXE=C:\Users\zongb\AppData\Local\Programs\Python\Python313\python.exe"
set "API_PORT=8001"
set "LOG_DIR=%PROJECT_DIR%\logs"
REM ========== CONFIG END =====

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [ERROR] Project directory not found: %PROJECT_DIR%
    pause
    exit /b 1
)

if not exist "%PROJECT_DIR%\data" mkdir "%PROJECT_DIR%\data"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM === Locate Python (configured path first, then PATH) ===
if exist "%PYTHON_EXE%" goto :py_ok
where python >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%I in ('where python') do set "PYTHON_EXE=%%I"
    goto :py_ok
)
echo [ERROR] Python not found. Please fix PYTHON_EXE at the top of this script.
pause
exit /b 1
:py_ok

REM === Timestamped log file ===
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%I"
set "LOG_FILE=%LOG_DIR%\update_!TS!.log"

call :log "============================================================"
call :log "  A-Shares Data Update starting..."
call :log "  Python : %PYTHON_EXE%"
call :log "  Mode   : incremental (up to latest trading day)"
call :log "  Log    : %LOG_FILE%"
call :log "============================================================"

REM === Step 1: stop API if running (it holds DuckDB read connections) ===
set "API_PID="
set "API_WAS_RUNNING=0"
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr /C:":%API_PORT% " ^| findstr /C:"LISTENING"') do set "API_PID=%%P"
if not "!API_PID!"=="" (
    REM Only kill it if the process is python.exe (our API), otherwise abort
    tasklist /FI "PID eq !API_PID!" 2>nul | findstr /I "python" >nul
    if not errorlevel 1 (
        call :log "[1/3] Stopping API service (PID !API_PID!) for exclusive DB access..."
        taskkill /PID !API_PID! /T /F >nul 2>&1
        set "API_WAS_RUNNING=1"
    ) else (
        call :log "[WARN] Port %API_PORT% is used by a non-Python process (PID !API_PID!)."
        call :log "       It will NOT be stopped. If it is another DB reader, update may fail."
    )
) else (
    call :log "[1/3] API service not running, skip stopping."
)

REM === Step 2: run incremental data update ===
call :log "[2/3] Collecting data (daily + indicators + backup), please wait..."
call :log "      (13 stocks, may take several minutes depending on network)"
echo.
echo   Now updating data to latest... DO NOT close this window.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%PYTHON_EXE%' '-m' 'src.main' 2>&1 | ForEach-Object { $_; Add-Content -Path '%LOG_FILE%' -Encoding UTF8 -Value $_ }; exit $LASTEXITCODE"
set "EXIT_CODE=!errorlevel!"

echo.
call :log "------------------------------------------------------------"

REM === Step 3: restart API if it was running before ===
if "!API_WAS_RUNNING!"=="1" (
    call :log "[3/3] Restarting API service..."
    start "" cmd /c "e:\AI\Data\A-Shares-data\start_api.bat"
    call :log "      API restarting in a new window (docs auto-open in ~7s)."
) else (
    call :log "[3/3] API was not running before, not restarted."
)

REM === Result summary ===
if "!EXIT_CODE!"=="0" (
    call :log "RESULT: Update FINISHED successfully (exit code 0)."
) else (
    call :log "RESULT: Update FAILED, exit code !EXIT_CODE!"
    call :log "        Last 30 lines of log:"
    echo.
    echo ====== Last 30 lines of log ======
    powershell -NoProfile -Command "Get-Content -Path '!LOG_FILE!' -Tail 30 -Encoding UTF8"
)

echo.
echo  Full log: !LOG_FILE!
echo.
echo  Press any key to close this window...
pause >nul

endlocal
exit /b !EXIT_CODE!

REM === Log helper: echo to console AND append to log file ===
:log
echo %~1
>> "%LOG_FILE%" echo %~1
goto :eof
