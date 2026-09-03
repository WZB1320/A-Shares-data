@echo off
REM ============================================================
REM  A-Shares Data API - One-Click Launcher (double-click to run)
REM  Default URL : http://127.0.0.1:8001
REM  Swagger docs: http://127.0.0.1:8001/docs
REM  Close this window to stop the service.
REM ============================================================

setlocal EnableDelayedExpansion

REM ========== CONFIG ==========
set "PROJECT_DIR=e:\AI\Data\A-Shares-data"
set "PYTHON_EXE=C:\Users\zongb\AppData\Local\Programs\Python\Python313\python.exe"
REM HOST: default 127.0.0.1 (local only, avoids WinError 10013).
REM Change to 0.0.0.0 for LAN access.
set "A_SHARES_API_HOST=127.0.0.1"
set "A_SHARES_API_PORT=8001"
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

REM === Timestamped log file (PowerShell, no wmic dependency) ===
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%I"
set "LOG_FILE=%LOG_DIR%\api_!TS!.log"

REM === Skip duplicate start if port already listening ===
netstat -ano 2>nul | findstr /C:":!A_SHARES_API_PORT! " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
    echo [WARN] Port !A_SHARES_API_PORT! is already listening. API may be running.
    echo Opening docs: http://localhost:!A_SHARES_API_PORT!/docs
    start "" http://localhost:!A_SHARES_API_PORT!/docs
    echo.
    echo To force restart, stop the process on port !A_SHARES_API_PORT! first.
    pause
    exit /b 0
)

call :log "============================================================"
call :log "  A-Shares Data API starting..."
call :log "  Python : %PYTHON_EXE%"
call :log "  Host   : %A_SHARES_API_HOST%"
call :log "  Port   : %A_SHARES_API_PORT% (auto +1..+4 if busy)"
call :log "  URL    : http://localhost:%A_SHARES_API_PORT%"
call :log "  Docs   : http://localhost:%A_SHARES_API_PORT%/docs"
call :log "  Log    : %LOG_FILE%"
call :log "  Close this window to stop the service."
call :log "============================================================"

REM === Open docs in browser after 7s (wait for uvicorn) ===
start "" cmd /c "timeout /t 7 /nobreak >nul & start http://localhost:%A_SHARES_API_PORT%/docs"

REM === Dependency pre-check: fastapi / duckdb / uvicorn ===
"%PYTHON_EXE%" -c "import fastapi, duckdb, uvicorn" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo [ERROR] Missing dependencies: fastapi / duckdb / uvicorn.
    echo Install them first:
    echo     cd /d "%PROJECT_DIR%"
    echo     "%PYTHON_EXE%" -m pip install -r requirements.txt
    echo See log for details: %LOG_FILE%
    pause
    exit /b 1
)
call :log "[OK] Dependency check passed: fastapi / duckdb / uvicorn"

REM === Run API server (foreground; console output + log file) ===
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%PYTHON_EXE%' 'api\main.py' 2>&1 | ForEach-Object { $_; Add-Content -Path '%LOG_FILE%' -Encoding UTF8 -Value $_ }; exit $LASTEXITCODE"
set "EXIT_CODE=!errorlevel!"

REM === Pause on abnormal exit and show log tail ===
if not "!EXIT_CODE!"=="0" (
    echo.
    echo [ERROR] API exited abnormally, exit code: !EXIT_CODE!
    echo Full log: !LOG_FILE!
    echo.
    echo ====== Last 30 lines of log ======
    powershell -NoProfile -Command "Get-Content -Path '!LOG_FILE!' -Tail 30 -Encoding UTF8"
    echo.
    pause
)

endlocal
exit /b !EXIT_CODE!

REM === Log helper: echo to console AND append to log file ===
:log
echo %~1
>> "%LOG_FILE%" echo %~1
goto :eof
